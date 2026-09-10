# -*- coding: utf-8 -*-
"""生成确定性模拟商户数据：商品 / 订单 / 评价。

随机种子固定，输出可复现。数据为虚构奶茶轻食店「青柠小铺」，
不含任何真实企业数据或个人信息。
"""
import json
import random
import os
from datetime import date, datetime, timedelta

random.seed(42)

HERE = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(os.path.dirname(HERE), "data")
TODAY = date(2026, 9, 9)  # 数据截止日（config.yaml business.today 与之一致，保证相对时间解析确定性）

PRODUCTS = [
    # id, 名称, 类目, 售价, 成本, 库存, 近30天销量, 评分
    ("P1001", "招牌柠檬茶", "饮品", 12.0, 4.5, 86, 912, 4.8),
    ("P1002", "满杯百香果", "饮品", 13.0, 5.0, 64, 745, 4.7),
    ("P1003", "栀子绿茶", "饮品", 10.0, 3.5, 30, 398, 4.5),
    ("P1004", "厚芋泥波波", "饮品", 16.0, 6.8, 41, 655, 4.9),
    ("P1005", "生椰拿铁咖啡", "饮品", 15.0, 6.0, 52, 512, 4.6),
    ("P1006", "杨枝甘露", "饮品", 18.0, 7.5, 12, 431, 4.8),
    ("P1007", "芋泥麻薯杯", "烘焙", 14.0, 5.5, 38, 366, 4.7),
    ("P1008", "提拉米苏", "烘焙", 15.0, 6.2, 26, 210, 4.4),
    ("P1009", "芒果糯米糍", "烘焙", 12.0, 4.8, 45, 288, 4.3),
    ("P1010", "蛋挞(4只装)", "烘焙", 11.0, 3.8, 60, 502, 4.5),
    ("P1011", "小薯条", "小食", 8.0, 2.5, 90, 689, 4.2),
    ("P1012", "鸡翅(2只)", "小食", 12.0, 5.0, 73, 598, 4.4),
    ("P1013", "脆脆鸡米花", "小食", 9.0, 3.2, 66, 476, 4.1),
    ("P1014", "鲜切水果杯", "水果", 10.0, 5.2, 22, 340, 4.6),
    ("P1015", "椰奶冻", "甜品", 9.0, 3.0, 18, 265, 4.5),
]

CHANNELS = ["堂食", "外卖", "小程序"]
STATUSES = (
    ["completed"] * 52 + ["shipped"] * 16 + ["pending_ship"] * 12
    + ["pending_payment"] * 6 + ["refund_requested"] * 8 + ["refunded"] * 6
)
BAD_REVIEWS = [
    "等了40分钟才做好，太慢了",
    "甜度失控，齁得慌",
    "包装漏了，洒了一半",
    "分量比上次少了很多",
    "点错了想要去冰的",
    "味道还行但是太贵了",
    "外卖送来冰全化了",
    "鸡米花不脆，是软的",
]
GOOD_REVIEWS = [
    "好喝，回购第三次了", "芋泥很足，好评", "出品稳定，下次还点",
    "柠檬茶很清爽", "性价比不错", "孩子很喜欢",
]


def gen_products():
    out = []
    for pid, name, cat, price, cost, stock, sales, rating in PRODUCTS:
        out.append({
            "id": pid, "name": name, "category": cat,
            "price": price, "cost": cost, "stock": stock,
            "sales_30d": sales, "rating": rating,
        })
    return out


def gen_orders():
    orders, reviews = [], []
    rid = 0
    for i in range(120):
        days_ago = random.randint(0, 59)
        d = TODAY - timedelta(days=days_ago)
        prod = random.choice(PRODUCTS)
        qty = random.randint(1, 4)
        status = random.choice(STATUSES)
        oid = "SO{}-{:04d}".format(d.strftime("%Y%m%d"), i + 1)
        orders.append({
            "order_id": oid,
            "product_id": prod[0],
            "qty": qty,
            "amount": round(prod[3] * qty, 2),
            "status": status,
            "channel": random.choice(CHANNELS),
            "created_at": "{}T{:02d}:{:02d}:00".format(
                d.isoformat(), random.randint(9, 21), random.randint(0, 59)),
        })
        if status in ("completed", "refunded") and random.random() < 0.5:
            rid += 1
            if random.random() < 0.42:
                star = random.choice([1, 1, 2, 3])
                comment = random.choice(BAD_REVIEWS)
            else:
                star = random.choice([4, 5, 5])
                comment = random.choice(GOOD_REVIEWS)
            reviews.append({
                "review_id": "R{:04d}".format(rid),
                "order_id": oid,
                "product_id": prod[0],
                "star": star,
                "comment": comment,
                "created_at": "{}T12:00:00".format(d.isoformat()),
            })
    orders.sort(key=lambda o: o["created_at"])
    return orders, reviews


if __name__ == "__main__":
    os.makedirs(DATA_DIR, exist_ok=True)
    products = gen_products()
    orders, reviews = gen_orders()
    with open(os.path.join(DATA_DIR, "products.json"), "w", encoding="utf-8") as f:
        json.dump(products, f, ensure_ascii=False, indent=2)
    with open(os.path.join(DATA_DIR, "orders.json"), "w", encoding="utf-8") as f:
        json.dump(orders, f, ensure_ascii=False, indent=2)
    with open(os.path.join(DATA_DIR, "reviews.json"), "w", encoding="utf-8") as f:
        json.dump(reviews, f, ensure_ascii=False, indent=2)
    print("products: {}  orders: {}  reviews: {}".format(
        len(products), len(orders), len(reviews)))
