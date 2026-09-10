# -*- coding: utf-8 -*-
"""工具注册入口：构建包含全部业务工具的 Registry。"""
from .base import Registry, Tool, ToolContext
from . import product_tools, order_tools, marketing_tools


def build_registry(ctx=None):
    reg = Registry()
    builders = [
        # 商品域
        product_tools.build_query_products,
        product_tools.build_update_price,
        product_tools.build_check_inventory,
        product_tools.build_restock_product,
        # 订单域
        order_tools.build_query_orders,
        order_tools.build_order_stats,
        order_tools.build_process_refund,
        # 营销域
        marketing_tools.build_create_coupon,
        marketing_tools.build_suggest_campaign,
        marketing_tools.build_sales_report,
        marketing_tools.build_query_reviews,
    ]
    for b in builders:
        reg.register(b(ctx))
    return reg


__all__ = ["Registry", "Tool", "ToolContext", "build_registry"]
