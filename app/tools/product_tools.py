# -*- coding: utf-8 -*-
"""商品域工具：查询 / 改价 / 库存检查 / 补货登记。"""
from .base import Tool


def _price(p):
    return "¥{:.2f}".format(p)


def build_query_products(ctx):
    def handler(args, c):
        sort_map = {"sales": "sales", "price": "price", "rating": "rating", "stock": "stock"}
        rows = c.store.query_products(
            keyword=args.get("keyword"),
            category=args.get("category"),
            max_price=args.get("max_price"),
            sort_by=sort_map.get(args.get("sort_by", "sales"), "sales"),
            limit=args.get("limit", 5))
        if not rows:
            return [], "没有找到符合条件的商品"
        lines = ["共找到 {} 个商品：".format(len(rows))]
        for p in rows:
            lines.append("- {}（{}）价格 {}，近30天销量 {}，评分 {:.1f}，库存 {}".format(
                p["name"], p["id"], _price(p["price"]),
                p["sales_30d"], p["rating"], p["stock"]))
        return rows, "\n".join(lines)
    return Tool(
        name="query_products",
        description="查询商品列表。支持按关键词/类目/价格上限过滤，按销量/价格/评分/库存排序。",
        parameters={
            "type": "object",
            "properties": {
                "keyword": {"type": "string", "description": "商品名称关键词"},
                "category": {"type": "string",
                             "enum": ["饮品", "烘焙", "小食", "水果", "甜品"]},
                "max_price": {"type": "number", "minimum": 0,
                              "description": "价格上限（元）"},
                "sort_by": {"type": "string", "enum": ["sales", "price", "rating", "stock"],
                            "description": "排序字段，默认按销量"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 20},
            },
            "additionalProperties": False,
        },
        handler=handler,
        examples=["有哪些饮品", "卖得最好的商品", "30块以内的小食"])


def build_update_price(ctx):
    def handler(args, c):
        p = c.store.find_product(args["product_ref"])
        if p is None:
            return None, "没有找到商品「{}」，请确认商品名称或编号".format(args["product_ref"])
        new = float(args["new_price"])
        if abs(new - p["price"]) < 1e-9:
            return p, "{} 当前价格就是 {}，无需修改".format(p["name"], _price(new))
        old = p["price"]
        c.store.update_price(p["id"], new)
        margin = (new - p["cost"]) / new * 100 if new > 0 else 0
        return p, ("已改价：{}（{}）{} → {}\n毛利率变化：{:.0f}% → {:.0f}%".format(
            p["name"], p["id"], _price(old), _price(new),
            (old - p["cost"]) / old * 100, margin))
    return Tool(
        name="update_price",
        description="修改商品价格。需要商品（名称或编号）和新价格。",
        parameters={
            "type": "object",
            "properties": {
                "product_ref": {"type": "string", "description": "商品名称或商品编号，如 P1003"},
                "new_price": {"type": "number", "exclusiveMinimum": 0,
                              "description": "新价格（元）"},
            },
            "required": ["product_ref", "new_price"],
            "additionalProperties": False,
        },
        handler=handler,
        examples=["把P1003改价到9.9", "招牌柠檬茶降价到11元"])


def build_check_inventory(ctx):
    def handler(args, c):
        th = c.config["inventory"]["low_stock_threshold"]
        if args.get("low_only"):
            rows = c.store.low_stock(th)
            if not rows:
                return [], "库存均不低于 {}，暂无缺货风险".format(th)
            lines = ["以下 {} 个商品库存低于预警线 {}：".format(len(rows), th)]
            for p in rows:
                lines.append("- {}（{}）库存 {}，日均销约 {:.0f} 件，建议尽快补货".format(
                    p["name"], p["id"], p["stock"], p["sales_30d"] / 30.0))
            return rows, "\n".join(lines)
        if args.get("product_ref"):
            p = c.store.find_product(args["product_ref"])
            if p is None:
                return None, "没有找到商品「{}」".format(args["product_ref"])
            state = "偏低" if p["stock"] < th else "正常"
            return p, "{}（{}）当前库存 {}（预警线 {}，状态{}），近30天销量 {}".format(
                p["name"], p["id"], p["stock"], th, state, p["sales_30d"])
        rows = sorted(c.store.products, key=lambda p: p["stock"])[:args.get("limit", 5)]
        lines = ["库存最紧张的商品："]
        for p in rows:
            lines.append("- {}（{}）库存 {}".format(p["name"], p["id"], p["stock"]))
        return rows, "\n".join(lines)
    return Tool(
        name="check_inventory",
        description="查库存。可查单个商品库存、库存不足（低于预警线）清单、或库存最紧张的商品。",
        parameters={
            "type": "object",
            "properties": {
                "product_ref": {"type": "string", "description": "商品名称或编号"},
                "low_only": {"type": "boolean",
                             "description": "true=只看低于预警线的商品"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 20},
            },
            "additionalProperties": False,
        },
        handler=handler,
        examples=["哪些商品库存不足", "查一下P1007的库存"])


def build_restock_product(ctx):
    def handler(args, c):
        p = c.store.find_product(args["product_ref"])
        if p is None:
            return None, "没有找到商品「{}」".format(args["product_ref"])
        qty = int(args["add_qty"])
        c.store.update_stock(p["id"], qty)
        return p, "补货完成：{}（{}）库存 {} → {}（+{}）".format(
            p["name"], p["id"], p["stock"] - qty, p["stock"], qty)
    return Tool(
        name="restock_product",
        description="商品补货登记，把指定数量加到当前库存上。",
        parameters={
            "type": "object",
            "properties": {
                "product_ref": {"type": "string", "description": "商品名称或编号"},
                "add_qty": {"type": "integer", "minimum": 1, "description": "补货数量"},
            },
            "required": ["product_ref", "add_qty"],
            "additionalProperties": False,
        },
        handler=handler,
        examples=["给P1005补货80件", "椰奶冻补100份"])


from .base import Tool  # noqa: E402  (放底部避免循环导入告警)
