# -*- coding: utf-8 -*-
"""订单域工具：订单查询 / 订单统计 / 退款处理。"""
from .base import Tool
from .store import STATUS_CN


def _fmt_order(o, c):
    p = c.store._product_idx[o["product_id"]]
    lines = ["订单 {}（{}）".format(o["order_id"], STATUS_CN[o["status"]]),
             "- 商品：{} × {} = {}".format(p["name"], o["qty"], "¥{:.2f}".format(o["amount"])),
             "- 渠道：{}，下单时间：{}".format(o["channel"], o["created_at"].replace("T", " "))]
    rvs = [r for r in c.store.reviews if r["order_id"] == o["order_id"]]
    for r in rvs:
        lines.append("- 评价：{}星 「{}」".format(r["star"], r["comment"]))
    return "\n".join(lines)


def build_query_orders(ctx):
    def handler(args, c):
        start = end = None
        if args.get("date_expr"):
            start, end, expr = c.store.parse_range(args["date_expr"])
            if start is None:
                return None, "无法识别时间表达「{}」，可以说 今天/昨天/最近7天/具体日期".format(
                    args["date_expr"])
        status = args.get("status")
        if status:
            status = status if status in STATUS_CN else STATUS_EN.get(status, status)
            if status not in STATUS_CN:
                return None, "未知订单状态「{}」".format(status)
        rows = c.store.query_orders(order_id=args.get("order_id"), status=status,
                                    start=start, end=end,
                                    limit=args.get("limit", 10))
        if not rows:
            return [], "没有符合条件的订单"
        if args.get("order_id"):
            return rows, _fmt_order(rows[0], c)
        head = "共 {} 笔订单".format(len(rows))
        if status:
            head += "（状态：{}）".format(STATUS_CN[status])
        if start:
            head += "，时间范围 {} ~ {}".format(start, end)
        lines = [head]
        for o in rows:
            p = c.store._product_idx[o["product_id"]]
            lines.append("- {} {}×{} {} {}".format(
                o["order_id"], p["name"], o["qty"],
                "¥{:.2f}".format(o["amount"]), STATUS_CN[o["status"]]))
        return rows, "\n".join(lines)
    return Tool(
        name="query_orders",
        description="查询订单。支持按订单号精确查询，或按状态+时间范围列出订单。",
        parameters={
            "type": "object",
            "properties": {
                "order_id": {"type": "string", "description": "订单号，如 SO20260901-0012"},
                "status": {"type": "string",
                           "enum": list(STATUS_CN.keys()),
                           "description": "订单状态"},
                "date_expr": {"type": "string",
                              "description": "时间表达，如 今天/昨天/最近7天/2026-09-01"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 50},
            },
            "additionalProperties": False,
        },
        handler=handler,
        examples=["查订单SO20260901-0012", "今天有哪些待发货订单"])


def build_order_stats(ctx):
    def handler(args, c):
        start = end = None
        if args.get("date_expr"):
            start, end, expr = c.store.parse_range(args["date_expr"])
            if start is None:
                return None, "无法识别时间表达「{}」".format(args["date_expr"])
        s = c.store.order_stats(start=start, end=end)
        lines = []
        period = "{} ~ {}".format(start, end) if start else "全部时间"
        lines.append("【{}】订单统计".format(period))
        lines.append("- 有效订单 {} 笔，GMV ¥{:.2f}，客单价 ¥{:.2f}".format(
            s["count"], s["gmv"], s["aov"]))
        status_line = "、".join("{} {}笔".format(STATUS_CN[k], v)
                                for k, v in sorted(s["by_status"].items(),
                                                   key=lambda kv: -kv[1]))
        lines.append("- 状态分布：" + status_line)
        prods = ["{} ¥{:.2f}".format(c.store._product_idx[pid]["name"], v)
                 for pid, v in s["top_products"]]
        lines.append("- 销售额 Top3：" + "；".join(prods))
        return s, "\n".join(lines)
    return Tool(
        name="order_stats",
        description="统计订单核心指标：订单量、GMV、客单价、状态分布、Top 商品。",
        parameters={
            "type": "object",
            "properties": {
                "date_expr": {"type": "string",
                              "description": "时间表达，如 昨天/最近7天/上月/2026-08"},
            },
            "additionalProperties": False,
        },
        handler=handler,
        examples=["昨天卖了多少单", "最近7天营业额"])


def build_process_refund(ctx):
    def handler(args, c):
        o, err = c.store.process_refund(args["order_id"], args.get("reason", "用户申请"))
        if err:
            return None, err
        return o, ("退款完成：订单 {} 已退款 ¥{:.2f}（理由：{}）\n"
                   "提示：可在「营销-复购召回」里给该客户发一张小额券做挽回".format(
                       o["order_id"], o["amount"], o.get("refund_reason")))
    return Tool(
        name="process_refund",
        description="处理退款：把「退款申请中」的订单标记为已退款。需要订单号；已完成订单不可直接退款。",
        parameters={
            "type": "object",
            "properties": {
                "order_id": {"type": "string", "description": "订单号"},
                "reason": {"type": "string", "description": "退款理由"},
            },
            "required": ["order_id"],
            "additionalProperties": False,
        },
        handler=handler,
        examples=["SO20260905-0003申请退款，理由是质量问题"])
