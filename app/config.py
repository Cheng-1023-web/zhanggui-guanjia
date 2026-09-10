# -*- coding: utf-8 -*-
"""配置加载：config.yaml → 合并默认值 → 全局单例。"""
import json
import os

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(ROOT, "data")
SESSION_DIR = os.path.join(DATA_DIR, "sessions")
LOG_DIR = os.path.join(DATA_DIR, "logs")

DEFAULTS = {
    "merchant": {"name": "青柠小铺", "type": "奶茶轻食"},
    "business": {"today": "2026-09-09"},
    "agent": {"max_iters": 6, "max_history_turns": 6, "max_context_tokens": 1200},
    "memory": {"longterm_dim": 256, "recall_topk": 3, "max_facts": 20},
    "llm": {
        "provider": "rule",           # rule=规则基线(离线) | openai=任意 OpenAI 兼容服务(Ollama/vLLM)
        "api_base": "http://127.0.0.1:11434/v1",
        "api_key": "ollama",
        "model": "qwen2.5:7b-instruct-q4_K_M",
        "temperature": 0.2,
        "max_tokens": 512,
        "timeout": 60,
    },
    "structured": {"max_retries": 2},
    "inventory": {"low_stock_threshold": 40},
    "server": {"host": "127.0.0.1", "port": 8010},
    "evaluation": {"report_path": "data/logs/eval_report.json"},
}

_cfg = None


def _deep_merge(base, override):
    out = dict(base)
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def load_config(path=None):
    """加载配置（yaml 可选），与默认值深度合并。"""
    global _cfg
    cfg = dict(DEFAULTS)
    cfg_path = path or os.path.join(ROOT, "config.yaml")
    if os.path.exists(cfg_path):
        with open(cfg_path, "r", encoding="utf-8") as f:
            cfg = _deep_merge(cfg, yaml.safe_load(f) or {})
    # 环境变量覆盖（docker-compose 注入）
    for env_key, path in [
            ("GUANJIA_LLM_PROVIDER", ("llm", "provider")),
            ("GUANJIA_LLM_API_BASE", ("llm", "api_base")),
            ("GUANJIA_LLM_API_KEY", ("llm", "api_key")),
            ("GUANJIA_LLM_MODEL", ("llm", "model"))]:
        v = os.environ.get(env_key)
        if v:
            d = cfg
            for k in path[:-1]:
                d = d[k]
            d[path[-1]] = v

    _cfg = cfg
    os.makedirs(SESSION_DIR, exist_ok=True)
    os.makedirs(LOG_DIR, exist_ok=True)
    return cfg


def get_config():
    if _cfg is None:
        return load_config()
    return _cfg


def read_json(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def write_json(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
