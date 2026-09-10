# -*- coding: utf-8 -*-
"""掌柜管家 CLI：run / chat / tools / eval / serve / demo / stats"""
import argparse
import json
import sys

from . import build_app
from .config import load_config, LOG_DIR
import os


def cmd_run(args):
    config, store, registry, agent = build_app()
    r = agent.run(args.query, session_id=args.session)
    if args.trace:
        print(json.dumps(r.trace, ensure_ascii=False, indent=2))
    print(r.answer)
    print("\n[intent={}] tools={} iterations={} elapsed={:.0f}ms".format(
        r.intent, [c["tool"] for c in r.tool_calls], r.iterations, r.elapsed_ms))
    return 0


def cmd_chat(args):
    config, store, registry, agent = build_app()
    sid = args.session
    print("掌柜管家已上线（会话: {}）。输入 q 退出。".format(sid))
    print("试试：查库存预警 / 满仓减券 / 订单退款 / 上月销售报表\n")
    while True:
        try:
            q = input("你：").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if q.lower() in ("q", "quit", "exit"):
            break
        if not q:
            continue
        r = agent.run(q, session_id=sid)
        print("管家：{}\n".format(r.answer))
    return 0


def cmd_tools(args):
    config, store, registry, agent = build_app()
    print(registry.tool_card())
    print("\n共 {} 个工具".format(len(registry.names())))
    if args.json:
        print(json.dumps(registry.schemas(), ensure_ascii=False, indent=2))
    return 0


def cmd_eval(args):
    config, store, registry, agent = build_app()
    from .evaluation.metrics import run_eval, print_report, load_eval_set
    mode = getattr(args, "mode", "rule")
    if mode != "rule":
        if not agent.llm.enabled:
            print("llm 模式需要先在 config.yaml 把 llm.provider 设为 openai"
                  "（Ollama/vLLM 等 OpenAI 兼容服务）")
            return 2
        agent.use_structured = (mode == "llm")
    eval_set = load_eval_set(args.eval_set) if args.eval_set else None
    summary, details = run_eval(
        agent, eval_set,
        report_path=None if args.no_report else config["evaluation"]["report_path"])
    print("[mode: {}]".format(mode))
    print_report(summary, details)
    if not args.no_report:
        print("\n报告已写入: {}/{}".format(LOG_DIR, os.path.basename(
            config["evaluation"]["report_path"])))
    return 0


def cmd_serve(args):
    config, store, registry, agent = build_app()
    from .api.server import create_app
    app = create_app(config, store, registry, agent)
    import uvicorn
    host = args.host or config["server"]["host"]
    port = args.port or config["server"]["port"]
    print("掌柜管家服务启动: http://{}:{} （Web 界面见根路径）".format(host, port))
    uvicorn.run(app, host=host, port=port, log_level="warning")
    return 0


DEMO_QUERIES = [
    "现在哪些商品库存不足",
    "把P1003的价格改成9块9",
    "订单SO20260805-0103申请了退款，帮我处理一下，理由是商品质量问题",
    "创建一张满20减5的优惠券，限量100张，有效期7天",
    "统计上个月的销售，然后给卖得最差的品类出个清仓方案",
    "最近有哪些差评",
]


def cmd_demo(args):
    config, store, registry, agent = build_app()
    print("store: {} 商品 / {} 订单 / {} 评价；registry: {} 工具；llm: {}\n".format(
        len(store.products), len(store.orders), len(store.reviews),
        len(registry.names()), agent.llm.model if agent.llm.enabled else "规则基线"))
    for q in DEMO_QUERIES:
        print("=" * 60)
        print("用户：", q)
        r = agent.run(q, session_id="demo")
        print("管家：", r.answer)
        print("[intent={}] tools={} {:.0f}ms\n".format(
            r.intent, [c["tool"] for c in r.tool_calls], r.elapsed_ms))
    return 0


def cmd_stats(args):
    config, store, registry, agent = build_app()
    print(json.dumps({
        "products": len(store.products),
        "orders": len(store.orders),
        "reviews": len(store.reviews),
        "tools": registry.names(),
        "llm_provider": config["llm"]["provider"],
    }, ensure_ascii=False, indent=2))
    return 0


def main(argv=None):
    p = argparse.ArgumentParser(prog="app.cli", description="掌柜管家 · 商户运营 LLM Agent")
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("run", help="单条问答")
    sp.add_argument("-q", "--query", required=True)
    sp.add_argument("-s", "--session", default="default")
    sp.add_argument("--trace", action="store_true")
    sp.set_defaults(fn=cmd_run)

    sp = sub.add_parser("chat", help="交互式多轮对话")
    sp.add_argument("-s", "--session", default="default")
    sp.set_defaults(fn=cmd_chat)

    sp = sub.add_parser("tools", help="列出工具与 JSON Schema")
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(fn=cmd_tools)

    sp = sub.add_parser("eval", help="运行工具调用评测")
    sp.add_argument("--eval-set", default=None)
    sp.add_argument("--no-report", action="store_true")
    sp.add_argument("--mode", choices=["rule", "llm", "llm-unstructured"],
                    default="rule",
                    help="rule=规则基线；llm=JSON约束+重试；llm-unstructured=无约束对照")
    sp.set_defaults(fn=cmd_eval)

    sp = sub.add_parser("serve", help="启动 FastAPI 服务")
    sp.add_argument("--host", default=None)
    sp.add_argument("--port", type=int, default=None)
    sp.set_defaults(fn=cmd_serve)

    sp = sub.add_parser("demo", help="演示 6 个典型场景")
    sp.set_defaults(fn=cmd_demo)

    sp = sub.add_parser("stats", help="数据与工具统计")
    sp.set_defaults(fn=cmd_stats)

    args = p.parse_args(argv)
    load_config()
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
