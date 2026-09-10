# -*- coding: utf-8 -*-
"""应用配置入口。"""
from .config import load_config, get_config, ROOT, DATA_DIR
from .tools.store import Store
from .tools import build_registry
from .agent import ReActAgent


def build_app(config=None):
    """组装完整应用：store + registry + agent。"""
    config = config or get_config()
    store = Store(DATA_DIR, today=config["business"].get("today"))
    registry = build_registry()
    agent = ReActAgent(config, registry, store)
    return config, store, registry, agent
