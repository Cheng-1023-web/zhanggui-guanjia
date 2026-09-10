# -*- coding: utf-8 -*-
"""营销域工具：优惠券创建 / 活动策划建议 / 销售报表 / 评价查询。"""
import re

from .base import Tool
from .store import STATUS_CN

_COUPON_SEQ = {"n": 0}


def build_create_coupon(ctx):
    def handler(args, c):
        ctype = args["type"]
        threshold = float(args.get("threshold") or 0)
        discount = float(args["discount"])
        quota = args.get("quota")
        days = int(args.get("valid_days") or 7)
        if ctype == "money" and discount >= threshold and threshold > 0:
            return None, ("参数不合理：满减券的减免金额（{}元）不能大于等于门槛（{}元）".format(
                discount, threshold))
        if ctype == "rate" and not (0.3 <= discount < 1.0):
            return None, "参数不合理：折扣券 discount 应为 0.3~1.0 的折扣率（如 8.8折=0.88）"
        _COUPON_SEQ["n"] += 1
        code = "CUP-{:04d}".format(_COUPON_SEQ["n"])
        name = args.get("name") or (
            "满{:.0f}减{:.0f}券".format(threshold, discount) if ctype == "money"
            else "{:.1f}折券".format(discount * 10))
        data = {"code": code, "name": name, "type": ctype, "threshold": threshold,
                "discount": discount, "quota": quota, "valid_days": days}
        desc = ("满{:.0f}可用，立减{:.0f}元".format(threshold, discount)
                if ctype == "money" else "无门槛按 {:.1f} 折结算".format(discount * 10))
        quota_s = "限量 {} 张".format(quota) if quota else "不限量"
        text = ("优惠券已创建 ✅\n- 券码：{}\n- 名称：{}\n- 规则：{}（{}）\n"
                "- 有效期：{} 天\n建议投放渠道：小程序弹窗 + 外卖店铺公告".format(
                    code, name, desc, quota_s, days))
        return data, text
    return Tool(
        name="create_coupon",
        description="创建优惠券。type=money 为满减券（threshold 满 N 元 / discount 减 M 元），"
                    "type=rate 为折扣券（discount 为折扣率，8.8折=0.88）。返回券码。",
        parameters={
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "券名称，可不填自动生成"},
                "type": {"type": "string", "enum": ["money", "rate"]},
                "threshold": {"type": "number", "minimum": 0,
                              "description": "使用门槛（元），无门槛填 0"},
                "discount": {"type": "number", "exclusiveMinimum": 0,
                             "description": "money=减免金额（元）；rate=折扣率(0.3~1.0)"},
                "quota": {"type": "integer", "minimum": 1,
                          "description": "限量张数，不限量可不填"},
                "valid_days": {"type": "integer", "minimum": 1,
                               "description": "有效天数，默认 7"},
            },
            "required": ["type", "discount"],
            "additionalProperties": False,
        },
        handler=handler,
        examples=["创建满20减5的券，限量100张", "全场8.8折券，用3天"])


def build_suggest_campaign(ctx):
    """规则式营销建议引擎：基于真实店铺数据（滞销/低销商品、类目结构）给出方案。"""

    def handler(args, c):
        goal = args.get("goal") or "复购召回"
        category = args.get("category")
        budget = args.get("budget")
        prods = c.store.products
        if category:
            prods = [p for p in prods if p["category"] == category]
        bottom = sorted(prods, key=lambda p: p["sales_30d"])[:3]
        top = sorted(prods, key=lambda p: -p["sales_30d"])[:1]
        low_stock = c.store.low_stock(c.config["inventory"]["low_stock_threshold"])
        low_names = "、".join(p["name"] for p in low_stock[:3]) or "无"
        b = "预算约 {} 元内".format(budget) if budget else "低成本"
        plans = {
            "清仓去库": (
                "核心思路：用限时组合券把滞销品带出去，同时避免影响畅销品价格体系。",
                ["对滞销品「{b0}」「{b1}」做第二件半价/买二送一机制（不动标价，保护毛利）",
                 "发一张满25减6的品类券，仅限「{cat}」类目使用，引导连带购买",
                 "门店海报 + 小程序首页置顶 3 天，突出「最后X份」的稀缺感"],
                "预期 7 天内滞销品周销量提升 40%~80%；注意补货节奏，避免清仓后断货：当前低库存品有 {}",),
            "拉新引流": (
                "核心思路：低门槛无门槛券做首单转化，配合高毛利爆品做内容种草。",
                ["发「{t0}」5元无门槛新人券，首单转化率通常可提升 8~15 个百分点",
                 "外卖平台设置「{t0}+小食」引流套餐，压低客单门槛",
                 "鼓励到店顾客扫码进群，进群再发一张 3 元券，形成二次触点"],
                "预期两周新增私域用户 100+，首单转化率提升约 10 个百分点；券预算控制在 GMV 的 3% 内",),
            "复购召回": (
                "核心思路：对 30 天未复购客户定向发券，用熟客折扣代替价格战。",
                ["筛选 30 天未复购客户，定向发满 15 减 4 券，有效期 5 天（短有效期促即时转化）",
                 "对曾购买「{t0}」的客户推新口味/关联品上新提醒",
                 "会员日（每周固定一天）双倍积分，培养周期性复购"],
                "预期复购率提升 5~10 个百分点；先小流量 A/B 两天再全量",),
        }
        key = goal if goal in plans else ("清仓去库" if "清" in goal else goal)
        if key not in plans:
            key = "复购召回"
        intro, steps, effect = plans[key]
        fmt = {"b0": bottom[0]["name"] if bottom else "滞销品A",
               "b1": bottom[1]["name"] if len(bottom) > 1 else "滞销品B",
               "t0": top[0]["name"] if top else "招牌产品",
               "cat": category or (bottom[0]["category"] if bottom else "全店")}
        data = {"goal": key, "bottom": [p["name"] for p in bottom],
                "top": [p["name"] for p in top], "steps": steps}
        lines = ["【{}方案】（{}数据驱动）".format(key, b), intro, ""]
        for i, s in enumerate(steps, 1):
            lines.append("{}. {}".format(i, s.format(**fmt)))
        lines.append("")
        lines.append(effect.format(low_names))
        lines.append("")
        lines.append("（可以继续说「按这个方案创建满25减6券」来落地执行）")
        return data, "\n".join(lines)

    return Tool(
        name="suggest_campaign",
        description="基于店铺真实销售数据生成营销活动方案。goal 支持：清仓去库/拉新引流/复购召回。",
        parameters={
            "type": "object",
            "properties": {
                "goal": {"type": "string",
                         "enum": ["清仓去库", "拉新引流", "复购召回"]},
                "category": {"type": "string",
                             "enum": ["饮品", "烘焙", "小食", "水果", "甜品"]},
                "budget": {"type": "number", "minimum": 0, "description": "预算（元）"},
            },
            "additionalProperties": False,
        },
        handler=handler,
        examples=["柠檬茶卖不动有什么建议", "帮我策划一个拉新活动"])


def build_sales_report(ctx):
    def handler(args, c):
        start = end = None
        if args.get("date_expr"):
            start, end, expr = c.store.parse_range(args["date_expr"])
            if start is None:
                return None, "无法识别时间表达「{}」".format(args["date_expr"])
        group_by = args.get("group_by", "category")
        rows = c.store.sales_report(start=start, end=end, group_by=group_by)
        if not rows:
            return [], "该时间段没有有效订单"
        period = "{} ~ {}".format(start, end) if start else "全部时间"
        label = {"category": "品类", "product": "商品", "day": "日期"}.get(group_by, group_by)
        lines = ["【销售报表 {}】按{}汇总".format(period, label)]
        total = sum(r["gmv"] for r in rows)
        for r in rows[:10]:
            name = r["group"]
            if group_by == "product":
                name = c.store._product_idx.get(name, {}).get("name", name)
            lines.append("- {}：{} 单，GMV ¥{:.2f}（占比 {:.0f}%）".format(
                name, r["count"], r["gmv"], r["gmv"] / total * 100 if total else 0))
        lines.append("合计 {} 单，GMV ¥{:.2f}".format(sum(r["count"] for r in rows), total))
        return rows, "\n".join(lines)
    return Tool(
        name="sales_report",
        description="销售报表：按品类/商品/日期维度汇总销量与 GMV。",
        parameters={
            "type": "object",
            "properties": {
                "date_expr": {"type": "string", "description": "时间表达，如 上月/2026-08"},
                "group_by": {"type": "string", "enum": ["category", "product", "day"],
                             "description": "汇总维度，默认品类"},
            },
            "additionalProperties": False,
        },
        handler=handler,
        examples=["上个月的销售报表按品类汇总", "9月1日到3日每天的销售额"])


def build_query_reviews(ctx):
    def handler(args, c):
        max_star = args.get("max_star", 2)
        rows = c.store.query_reviews(product_id=args.get("product_ref") and
                                     (c.store.find_product(args["product_ref"]) or {})
                                     .get("id"),
                                     max_star=max_star,
                                     limit=args.get("limit", 8))
        if not rows:
            return [], "没有评分 ≤ {} 星的评价，口碑良好".format(max_star)
        lines = ["共 {} 条差评（≤{}星），建议逐条回复并排查共性问题：".format(len(rows), max_star)]
        for r in rows:
            pname = c.store._product_idx.get(r["product_id"], {}).get("name", r["product_id"])
            lines.append("- [{}] {} {}星：「{}」".format(
                r["review_id"], pname, r["star"], r["comment"]))
        themes = _cluster_themes(rows)
        if themes:
            lines.append("")
            lines.append("共性问题归因：" + "；".join(themes))
        return rows, "\n".join(lines)
    return Tool(
        name="query_reviews",
        description="查询差评/评价。max_star 为评分上限（默认2，即只看1-2星差评），可按商品过滤。",
        parameters={
            "type": "object",
            "properties": {
                "product_ref": {"type": "string", "description": "商品名称或编号，可不填"},
                "max_star": {"type": "integer", "minimum": 1, "maximum": 5},
                "limit": {"type": "integer", "minimum": 1, "maximum": 30},
            },
            "additionalProperties": False,
        },
        handler=handler,
        examples=["最近有哪些差评", "P1009的评价怎么样"])


_THEME_RULES = [
    (r"慢|等.*分钟", "出餐速度慢"),
    (r"甜", "甜度控制"),
    (r"漏|洒|包装", "包装问题"),
    (r"少|分量", "分量不稳定"),
    (r"点错|去冰", "下单/备注链路易错"),
    (r"贵|价", "价格感知"),
    (r"化|不脆|软", "出品口感/温度"),
]


def _cluster_themes(rows):
    text = " ".join(r["comment"] for r in rows)
    return [name for pat, name in _THEME_RULES if re.search(pat, text)]
