# -*- coding: utf-8 -*-
"""意图识别 + 任务规划器（规则基线路径，确定性可复现）。

输出统一 Plan 结构：
- 单步任务 → steps=[{tool,args}]；
- 复合任务 → 自动分解为多步（如「统计销售并给最差品类出方案」）；
- 信息不足 → need_clarify + 反问话术（不硬调工具）；
- 闲聊     → intent=chitchat，不调用任何工具。

多轮指代（它/那个订单）通过 Session 上的 last_product / last_order 解析，
按会话隔离，不跨会话泄漏。

LLM 路径（provider=openai）复用同一 Plan 结构，由 ReAct 引擎驱动。
"""
import re
from dataclasses import dataclass, field

from ..tools.store import STATUS_EN

_NUM = r"(\d+(?:\.\d+)?)"
PRODUCT_ID_RE = re.compile(r"P\d{4}", re.I)
ORDER_ID_RE = re.compile(r"SO\d{8}-\d{3,4}", re.I)

CATEGORY_WORDS = {"饮品": ["饮品", "饮料", "喝的"], "烘焙": ["烘焙", "面包"],
                  "小食": ["小食", "小吃", "炸"], "水果": ["水果", "鲜果"],
                  "甜品": ["甜品", "糖水"]}


@dataclass
class Step:
    tool: str
    args: dict = field(default_factory=dict)
    thought: str = ""


@dataclass
class Plan:
    intent: str
    steps: list = field(default_factory=list)
    need_clarify: bool = False
    clarify_question: str = ""
    thought: str = ""


class RulePlanner:
    def __init__(self, store, config):
        self.store = store
        self.config = config

    # ---------------------------------------------------------------- helpers
    def _find_product_ref(self, q):
        m = PRODUCT_ID_RE.search(q)
        if m:
            p = self.store.find_product(m.group(0).upper())
            if p:
                return p
        best = None
        for p in self.store.products:
            if p["name"] in q:
                if best is None or len(p["name"]) > len(best["name"]):
                    best = p
        return best

    def _find_product_keyword(self, q):
        """从 query 中找商品名片段（≥2 字），用于 keyword 检索。"""
        best = None
        for p in self.store.products:
            name = p["name"]
            for L in range(len(name) - 1, 1, -1):
                for i in range(len(name) - L + 1):
                    sub = name[i:i + L]
                    if sub in q and (best is None or len(sub) > len(best)):
                        best = sub
        return best

    def _resolve_product(self, q, session=None):
        p = self._find_product_ref(q)
        if p:
            return p
        if re.search(r"它|这个商品|那个商品|该商品|这款", q) and session and \
                getattr(session, "last_product", None):
            return session.last_product
        return None

    def _category(self, q):
        for cat, words in CATEGORY_WORDS.items():
            if any(w in q for w in words):
                return cat
        return None

    # ---------------------------------------------------------------- main
    def plan(self, query, session=None):
        q = query.strip()
        low = q.lower()

        # 0) 复合任务分解（先于单意图，避免被子意图截胡）
        plan = self._plan_composite(q)
        if plan:
            return plan

        # 1) 闲聊（不调工具）
        if re.search(r"你是谁|自我介绍|你好|您好|在吗|谢谢|再见|拜拜|天气|笑话|写一(首|个|篇)|"
                     r"讲个故事|会什么|能做什么", low):
            return Plan("chitchat", thought="问候/闲聊类请求，无需调用业务工具")

        # 2) 改价
        if re.search(r"改价|调价|降价|涨价|价格改|价格调|改成|调到|降到|卖\s*" + _NUM + r"\s*(?:元|块)", low):
            return self._plan_update_price(q, session)

        # 3) 退款
        if "退款" in low:
            return self._plan_refund(q, session)

        # 4) 补货（先于库存，"补"字优先）
        if re.search(r"补\s*\d+|补货|进货|加库存|上货", low):
            return self._plan_restock(q, session)

        # 5) 库存
        if re.search(r"库存|缺货|还剩|还余|货够不够|存量", low):
            return self._plan_inventory(q, session)

        # 6) 优惠券
        if re.search(r"优惠券?|满减|立减|折扣|打折|[0-9.]+\s*折|无门槛", low):
            return self._plan_coupon(q)

        # 7) 活动策划
        if re.search(r"活动|促销|方案|建议|策划|怎么办|怎么搞|卖不动|拉新|复购|回头客|清仓|滞销", low):
            return self._plan_campaign(q)

        # 8) 评价
        if re.search(r"差评|评价|口碑|星级", low):
            return self._plan_reviews(q, session)

        # 9) 报表
        if re.search(r"报表|复盘|汇总|占比|结构|每天的销售|每日销售|按(品类|商品|日期)", low):
            return self._plan_report(q)

        # 10) 订单统计
        if re.search(r"多少单|卖了多少|营业额|流水|GMV|gmv|客单价|订单量|销量.{0,6}(多少|如何)", low):
            return self._plan_stats(q)

        # 11) 订单查询
        if re.search(r"订单|单号|SO\d{4}", low) or ORDER_ID_RE.search(low):
            return self._plan_orders(q)

        # 12) 商品查询（兜底到商品域）
        if re.search(r"商品|产品|有哪些|什么(饮品|小食|水果|甜品)|多少钱|价格|卖得(最好|最差)|"
                     r"排行|卖价|定价", low):
            return self._plan_products(q, session)

        # 13) 澄清
        return Plan("clarify", need_clarify=True,
                    clarify_question=("这条我没太确定想做什么：是要查商品/订单、改价、看库存、"
                                      "处理退款，还是做营销（券/活动/报表）？描述里带上商品名或订单号会更准。"),
                    thought="意图不确定，反问澄清而非猜测")

    # ---------------------------------------------------------------- intents
    def _extract_price(self, q):
        """提取目标价格：支持 9.9 / 11元 / 9块9 / 15块 等表达。"""
        m = re.search(r"(\d+)\s*块\s*(\d+)", q)           # 9块9 → 9.9
        if m:
            return float("{}.{}".format(m.group(1), m.group(2)))
        m = re.search(r"(\d+(?:\.\d+)?)\s*(?:元|块)", q)  # 11元 / 15块
        if m:
            return float(m.group(1))
        m = re.search(r"(\d+\.\d+)", q)                   # 裸小数
        if m:
            return float(m.group(1))
        return None

    def _plan_update_price(self, q, session=None):
        p = self._resolve_product(q, session)
        if p is None:
            return Plan("update_price", need_clarify=True,
                        clarify_question="想改哪个商品的价格？请提供商品名或编号（如 P1003）。",
                        thought="缺少商品指代，反问澄清")
        if not re.search(r"改|调|降|涨|价", q):
            return Plan("update_price", need_clarify=True,
                        clarify_question="「{}」想改成多少钱？".format(p["name"]),
                        thought="缺少目标价格，反问澄清")
        price = self._extract_price(q)
        if price is None:
            return Plan("update_price", need_clarify=True,
                        clarify_question="「{}」想改成多少钱？".format(p["name"]),
                        thought="缺少目标价格，反问澄清")
        session.last_product = p if session else None
        return Plan("update_price",
                    steps=[Step("update_price",
                                {"product_ref": p["id"], "new_price": price},
                                "把{}价格改为{}元".format(p["name"], price))],
                    thought="识别为改价任务，目标商品「{}」目标价 {}".format(p["name"], price))

    def _plan_refund(self, q, session=None):
        m = ORDER_ID_RE.search(q)
        if m:
            oid = m.group(0).upper()
            if session:
                session.last_order = oid
            rm = re.search(r"(?:理由|原因|因为|理由是|原因是)[:：]?(.+)", q)
            reason = (re.sub(r"^[：:是\s]+", "", rm.group(1))[:40] if rm
                      else "用户申请")
            return Plan("process_refund",
                        steps=[Step("process_refund", {"order_id": oid, "reason": reason},
                                    "处理订单{}退款".format(oid))],
                        thought="识别为退款处理，订单号 {}".format(oid))
        if re.search(r"多少|有哪些|查|几笔|列表", q):
            return Plan("query_refunds",
                        steps=[Step("query_orders", {"status": "refund_requested"},
                                    "列出退款申请中的订单")],
                        thought="退款查询类请求，列出退款申请中的订单")
        if re.search(r"那个|这单|该订单|它", q) and session and \
                getattr(session, "last_order", None):
            oid = session.last_order
            return Plan("process_refund",
                        steps=[Step("process_refund", {"order_id": oid, "reason": "用户申请"},
                                    "处理订单{}退款".format(oid))],
                        thought="沿用本会话上轮订单号 {}".format(oid))
        return Plan("process_refund", need_clarify=True,
                    clarify_question="请提供要退款的订单号（如 SO20260905-0042），我来处理。",
                    thought="缺少订单号，反问澄清")

    def _plan_restock(self, q, session=None):
        p = self._resolve_product(q, session)
        m = re.search(r"补\s*(?:货)?\s*(\d+)|(?:进货|上货|加)\s*(\d+)", q)
        if p is None:
            return Plan("restock_product", need_clarify=True,
                        clarify_question="要给哪个商品补货？请提供商品名或编号。",
                        thought="缺少商品指代，反问澄清")
        if m is None:
            return Plan("restock_product", need_clarify=True,
                        clarify_question="「{}」要补多少件？".format(p["name"]),
                        thought="缺少补货数量，反问澄清")
        qty = int(float(m.group(1) or m.group(2)))
        session.last_product = p if session else None
        return Plan("restock_product",
                    steps=[Step("restock_product", {"product_ref": p["id"], "add_qty": qty},
                                "给{}补货{}件".format(p["name"], qty))],
                    thought="识别为补货任务：{} +{}".format(p["name"], qty))

    def _plan_inventory(self, q, session=None):
        p = self._resolve_product(q, session)
        if p:
            session.last_product = p if session else None
            return Plan("check_inventory",
                        steps=[Step("check_inventory", {"product_ref": p["id"]},
                                    "查{}的库存".format(p["name"]))],
                        thought="查询单品库存「{}」".format(p["name"]))
        # 「库存最紧张的商品」= 按库存升序列出（而非只看低于预警线的子集）
        if re.search(r"最紧张|最缺", q):
            return Plan("check_inventory",
                        steps=[Step("check_inventory", {"low_only": False},
                                    "按库存升序列出最紧张的商品")],
                        thought="库存紧张度排行，low_only=False")
        low_only = bool(re.search(r"不足|紧张|缺|告警|预警|危险|快没", q))
        return Plan("check_inventory",
                    steps=[Step("check_inventory", {"low_only": low_only},
                                "查库存{}清单".format("预警" if low_only else "整体"))],
                    thought="库存查询：low_only={}".format(low_only))

    def _plan_coupon(self, q):
        args = {}
        mt = re.search(r"(\d+(?:\.\d+)?)\s*折", q)
        if mt:
            args["type"] = "rate"
            args["discount"] = round(float(mt.group(1)) / 10, 3)
        else:
            md = re.search(r"(?:减|立减|优惠)\s*" + _NUM, q)
            mt2 = re.search(r"满\s*" + _NUM, q)
            if md:
                args["type"] = "money"
                args["discount"] = float(md.group(1))
            if mt2:
                args.setdefault("type", "money")
                args["threshold"] = float(mt2.group(1))
            if "discount" not in args and mt2 is None and "无门槛" in q:
                mn = re.search(_NUM + r"\s*(?:元|块)", q)
                args.update({"type": "money", "threshold": 0})
                if mn:
                    args["discount"] = float(mn.group(1))
        if "discount" not in args:
            return Plan("create_coupon", need_clarify=True,
                        clarify_question="想创建什么券？例如「满20减5」或「8.8折」。",
                        thought="缺少券面参数，反问澄清")
        if "无门槛" in q:
            args.setdefault("threshold", 0)
        mq = re.search(r"(?:限量?|限)\s*(\d+)\s*张?|(\d+)\s*张", q)
        if mq:
            args["quota"] = int(mq.group(1) or mq.group(2))
        mdays = re.search(r"(?:有效|用)\s*(\d+)\s*天|(\d+)\s*天(?:有效|内)", q)
        if mdays:
            args["valid_days"] = int(mdays.group(1) or mdays.group(2))
        return Plan("create_coupon",
                    steps=[Step("create_coupon", args, "创建优惠券{}".format(args))],
                    thought="识别为创建优惠券任务")

    def _plan_campaign(self, q):
        if re.search(r"卖不动|滞销|清仓|压货", q):
            goal = "清仓去库"
        elif re.search(r"拉新|新客|新用户|引流", q):
            goal = "拉新引流"
        else:
            goal = "复购召回"
        cat = self._category(q)
        args = {"goal": goal}
        if cat:
            args["category"] = cat
        mb = re.search(r"预算\s*" + _NUM, q)
        if mb:
            args["budget"] = float(mb.group(1))
        return Plan("suggest_campaign",
                    steps=[Step("suggest_campaign", args, "生成「{}」方案".format(goal))],
                    thought="营销策划请求，目标定位「{}」".format(goal))

    def _plan_reviews(self, q, session=None):
        p = self._resolve_product(q, session)
        args = {}
        if p:
            session.last_product = p if session else None
            args["product_ref"] = p["id"]
        if re.search(r"好评|全部评价|怎么样|如何", q) and "差" not in q:
            args["max_star"] = 3
        return Plan("query_reviews",
                    steps=[Step("query_reviews", args, "查询评价{}".format(args))],
                    thought="评价查询请求")

    def _plan_report(self, q):
        args = {}
        if re.search(r"每天|每日|按天|按日", q):
            args["group_by"] = "day"
        elif re.search(r"哪款|哪个商品|按商品|单品", q):
            args["group_by"] = "product"
        else:
            args["group_by"] = "category"
        args["date_expr"] = self._default_date_expr(q)
        return Plan("sales_report",
                    steps=[Step("sales_report", args, "生成销售报表")],
                    thought="报表请求，维度 {}".format(args["group_by"]))

    def _plan_stats(self, q):
        return Plan("order_stats",
                    steps=[Step("order_stats", {"date_expr": self._default_date_expr(q)},
                                "统计订单指标")],
                    thought="订单统计请求")

    def _plan_orders(self, q):
        m = ORDER_ID_RE.search(q)
        if m:
            oid = m.group(0).upper()
            return Plan("query_orders",
                        steps=[Step("query_orders", {"order_id": oid},
                                    "查询订单{}".format(oid))],
                        thought="按订单号查询 {}".format(oid))
        args = {}
        for status in ["待付款", "待发货", "已发货", "已完成", "退款申请中", "已退款"]:
            if status in q:
                args["status"] = STATUS_EN[status]
                break
        args["date_expr"] = self._default_date_expr(q)
        return Plan("query_orders",
                    steps=[Step("query_orders", args, "按条件查订单")],
                    thought="订单列表查询 {}".format(args))

    def _plan_products(self, q, session=None):
        args = {}
        p = self._find_product_ref(q)
        if p and re.search(r"多少钱|价格|卖价|定价", q):
            session.last_product = p if session else None
            return Plan("query_products",
                        steps=[Step("query_products", {"keyword": p["name"]},
                                    "查{}的价格".format(p["name"]))],
                        thought="单品价格查询")
        cat = self._category(q)
        if cat:
            args["category"] = cat
        mmax = re.search(r"(\d+(?:\.\d+)?)\s*(?:元|块)\s*(?:以内|以下|之内)"
                         r"|(?:以内|以下|之内)\s*(\d+(?:\.\d+)?)", q)
        if mmax:
            args["max_price"] = float(mmax.group(1) or mmax.group(2))
        if re.search(r"卖得最好|最畅销|销量.{0,4}(高|好)|top\s*\d*|排行", q, re.I):
            args["sort_by"] = "sales"
        elif re.search(r"评分|星级|口碑最好", q):
            args["sort_by"] = "rating"
        mlim = re.search(r"前(\d+)", q)
        if mlim:
            args["limit"] = int(mlim.group(1))
        kw = self._find_product_keyword(q)
        if kw:
            args["keyword"] = kw
        if not args:
            args["limit"] = 5
        return Plan("query_products",
                    steps=[Step("query_products", args, "商品查询 {}".format(args))],
                    thought="商品查询请求")

    # ---------------------------------------------------------------- composite
    def _plan_composite(self, q):
        # 模式1：统计/报表 + 给(最差/滞销)品类出方案
        if re.search(r"(统计|报表|复盘|看下|看一下)", q) and \
                re.search(r"(最差|滞销|清仓|活动|方案|促销)", q):
            steps = [Step("sales_report",
                          {"group_by": "category",
                           "date_expr": "上月" if ("上月" in q or "上个月" in q) else "最近30天"},
                          "先统计销售概况，定位弱势品类"),
                     Step("suggest_campaign", {"goal": "清仓去库"},
                          "针对弱势品类生成清仓方案")]
            return Plan("composite_report_campaign", steps=steps,
                        thought="复合任务：先统计后策划，分解为 2 步")
        return None

    # ---------------------------------------------------------------- utils
    def _default_date_expr(self, q):
        m = re.search(r"上周[一二三四五六日天]", q)
        if m:
            return m.group(0)
        for expr, pat in [("昨天", r"昨天"), ("今天", r"今天|当日"),
                          ("上月", r"上月|上个月"), ("本月", r"本月|这个月"),
                          ("最近7天", r"最近7天|近7天|最近一周|上周")]:
            if re.search(pat, q):
                return expr
        m = re.search(r"(\d{1,2}月\d{1,2}日)", q)
        if m:
            return m.group(1)
        m = re.search(r"(\d{4}[-/年]?\d{1,2}月?)", q)
        if m:
            return m.group(1)
        return "最近30天"
