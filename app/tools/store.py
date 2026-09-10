# -*- coding: utf-8 -*-
"""业务数据仓库：商品 / 订单 / 评价的加载、查询与变更（含变更审计日志）。

相对时间解析（今天/昨天/最近N天/上周/上月/本周…）在这里统一实现，
且以 config.business.today 为「今天」，保证评测确定性可复现。
"""
import json
import os
import re
from datetime import date, datetime, timedelta

STATUS_CN = {
    "pending_payment": "待付款",
    "pending_ship": "待发货",
    "shipped": "已发货",
    "completed": "已完成",
    "refund_requested": "退款申请中",
    "refunded": "已退款",
}
STATUS_EN = {v: k for k, v in STATUS_CN.items()}

_REFUNDABLE = {"refund_requested"}


class Store:
    def __init__(self, data_dir, today=None, mutation_log=None):
        self.data_dir = data_dir
        if isinstance(today, str):
            today = date.fromisoformat(today)
        self.today = today or date.today()
        self.products = self._load("products.json")
        self.orders = self._load("orders.json")
        self.reviews = self._load("reviews.json")
        self._product_idx = {p["id"]: p for p in self.products}
        self._order_idx = {o["order_id"]: o for o in self.orders}
        self.mutation_log = mutation_log or os.path.join(data_dir, "mutations.jsonl")

    def _load(self, name):
        with open(os.path.join(self.data_dir, name), "r", encoding="utf-8") as f:
            return json.load(f)

    # ---------- 商品 ----------
    def find_product(self, ref):
        """按 id 或名称（含模糊包含）找商品。"""
        if not ref:
            return None
        p = self._product_idx.get(ref.strip().upper())
        if p:
            return p
        cands = [x for x in self.products if ref in x["name"]]
        return cands[0] if len(cands) == 1 else (cands[0] if cands else None)

    def query_products(self, keyword=None, category=None, max_price=None,
                       sort_by="sales_30d", limit=5):
        rows = list(self.products)
        if keyword:
            rows = [p for p in rows
                    if keyword in p["name"] or keyword in p["category"]]
        if category:
            rows = [p for p in rows if p["category"] == category]
        if max_price is not None:
            rows = [p for p in rows if p["price"] <= max_price]
        key = {"sales": "sales_30d", "price": "price",
               "rating": "rating", "stock": "stock"}.get(sort_by, "sales_30d")
        rows.sort(key=lambda p: p[key], reverse=(key != "price"))
        return rows[:limit]

    def update_price(self, product_id, new_price):
        p = self._product_idx[product_id]
        old = p["price"]
        p["price"] = round(float(new_price), 2)
        self._log_mutation("update_price", {"product_id": product_id,
                                            "old_price": old, "new_price": p["price"]})
        return p

    def update_stock(self, product_id, add_qty):
        p = self._product_idx[product_id]
        old = p["stock"]
        p["stock"] = int(p["stock"] + add_qty)
        self._log_mutation("restock", {"product_id": product_id,
                                       "old_stock": old, "add": add_qty,
                                       "new_stock": p["stock"]})
        return p

    def low_stock(self, threshold):
        rows = [p for p in self.products if p["stock"] < threshold]
        rows.sort(key=lambda p: p["stock"])
        return rows

    # ---------- 订单 ----------
    def find_order(self, oid):
        return self._order_idx.get(oid.strip().upper().replace(" ", ""))

    def query_orders(self, order_id=None, status=None, start=None, end=None, limit=10):
        if order_id:
            o = self.find_order(order_id)
            return [o] if o else []
        rows = self.orders
        if status:
            rows = [o for o in rows if o["status"] == status]
        if start:
            rows = [o for o in rows if o["created_at"][:10] >= start.isoformat()]
        if end:
            rows = [o for o in rows if o["created_at"][:10] <= end.isoformat()]
        rows = sorted(rows, key=lambda o: o["created_at"], reverse=True)
        return rows[:limit]

    def order_stats(self, start=None, end=None):
        rows = [o for o in self.orders
                if (not start or o["created_at"][:10] >= start.isoformat())
                and (not end or o["created_at"][:10] <= end.isoformat())]
        paid = [o for o in rows if o["status"] not in ("pending_payment",)]
        gmv = sum(o["amount"] for o in paid)
        count = len(paid)
        by_status = {}
        for o in rows:
            by_status[o["status"]] = by_status.get(o["status"], 0) + 1
        prod_cnt = {}
        for o in paid:
            prod_cnt[o["product_id"]] = prod_cnt.get(o["product_id"], 0) + o["amount"]
        top = sorted(prod_cnt.items(), key=lambda kv: kv[1], reverse=True)[:3]
        return {"count": count, "gmv": round(gmv, 2),
                "aov": round(gmv / count, 2) if count else 0.0,
                "by_status": by_status,
                "top_products": [(pid, round(v, 2)) for pid, v in top]}

    def process_refund(self, order_id, reason):
        o = self.find_order(order_id)
        if o is None:
            return None, "订单 {} 不存在".format(order_id)
        if o["status"] not in _REFUNDABLE:
            return None, ("订单 {} 当前状态为「{}」，只有「退款申请中」的订单可直接退款；"
                          "已完成订单需先在订单后台发起退款申请".format(
                              order_id, STATUS_CN[o["status"]]))
        o["status"] = "refunded"
        o["refund_reason"] = reason
        self._log_mutation("refund", {"order_id": order_id, "reason": reason,
                                      "amount": o["amount"]})
        return o, None

    # ---------- 评价 ----------
    def query_reviews(self, product_id=None, max_star=2, limit=8):
        rows = list(self.reviews)
        if product_id:
            rows = [r for r in rows if r["product_id"] == product_id]
        if max_star is not None:
            rows = [r for r in rows if r["star"] <= max_star]
        rows.sort(key=lambda r: (r["star"], r["created_at"]))
        return rows[:limit]

    # ---------- 报表 ----------
    def sales_report(self, start=None, end=None, group_by="category"):
        rows = [o for o in self.orders
                if o["status"] not in ("pending_payment",)
                and (not start or o["created_at"][:10] >= start.isoformat())
                and (not end or o["created_at"][:10] <= end.isoformat())]
        groups = {}
        for o in rows:
            if group_by == "category":
                p = self._product_idx[o["product_id"]]
                key = p["category"]
            elif group_by == "product":
                key = o["product_id"]
            else:  # day
                key = o["created_at"][:10]
            g = groups.setdefault(key, {"count": 0, "gmv": 0.0})
            g["count"] += 1
            g["gmv"] += o["amount"]
        out = [{"group": k, "count": v["count"], "gmv": round(v["gmv"], 2)}
               for k, v in groups.items()]
        if group_by == "day":
            out.sort(key=lambda x: x["group"])
        else:
            out.sort(key=lambda x: x["gmv"], reverse=True)
        return out

    # ---------- 相对时间解析 ----------
    def parse_range(self, text):
        """从中文文本解析 (start_date, end_date, 表达式说明)。解析失败返回 (None, None, None)。"""
        t = (text or "").strip()
        d = self.today
        if "前天" in t:
            x = d - timedelta(days=2)
            return x, x, "前天({})".format(x)
        if "昨天" in t:
            x = d - timedelta(days=1)
            return x, x, "昨天({})".format(x)
        if "今天" in t or "当日" in t:
            return d, d, "今天({})".format(d)
        m = re.search(r"最近\s*(\d+)\s*天|近\s*(\d+)\s*天", t)
        if m:
            n = int(m.group(1) or m.group(2))
            return d - timedelta(days=n - 1), d, "最近{}天".format(n)
        if "上月" in t or "上个月" in t:
            first = d.replace(day=1) - timedelta(days=1)
            first = first.replace(day=1)
            nxt = (first.replace(day=28) + timedelta(days=4)).replace(day=1)
            return first, nxt - timedelta(days=1), "上月({})".format(first.strftime("%Y-%m"))
        if "本月" in t or "这个月" in t:
            first = d.replace(day=1)
            return first, d, "本月({}起)".format(first.strftime("%Y-%m"))
        if "上周" in t and not re.search(r"上周[一二三四五六日天]", t):
            mon_this = d - timedelta(days=d.weekday())
            mon_last = mon_this - timedelta(days=7)
            return mon_last, mon_last + timedelta(days=6), "上周"
        m = re.search(r"上周([一二三四五六日天])", t)
        if m:
            wk = "一二三四五六日天".index(m.group(1)) % 7
            mon_this = d - timedelta(days=d.weekday())
            target = mon_this - timedelta(days=7) + timedelta(days=wk)
            return target, target, "上周{}".format(m.group(1))
        m = re.search(r"(\d{4})[-/年](\d{1,2})[-/月](\d{1,2})日?", t)
        if m:
            x = date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
            return x, x, str(x)
        m = re.search(r"(\d{1,2})月(\d{1,2})日", t)
        if m:
            x = date(d.year, int(m.group(1)), int(m.group(2)))
            return x, x, str(x)
        m = re.search(r"(\d{4})[-/年](\d{1,2})月?", t)
        if m and ("月" in t or "/" in t or "-" in t or "年" in t):
            y, mo = int(m.group(1)), int(m.group(2))
            first = date(y, mo, 1)
            nxt = date(y + (mo == 12), (mo % 12) + 1, 1)
            return first, nxt - timedelta(days=1), "{}月".format(mo)
        return None, None, None

    # ---------- 变更审计 ----------
    def _log_mutation(self, kind, payload):
        rec = {"ts": datetime.now().isoformat(timespec="seconds"),
               "kind": kind, **payload}
        with open(self.mutation_log, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
