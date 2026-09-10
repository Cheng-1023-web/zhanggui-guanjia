# -*- coding: utf-8 -*-
"""评测体系：工具选择准确率 / 参数正确率 / 端到端成功率 / 澄清合规 / 闲聊零调用。

评测口径：
- tool_hit        预测的首个工具与期望一致（闲聊=两者都不调工具；多步=步骤序列一致）
- args_hit        首个工具调用的参数满足 expect.args_eq（完全一致）/ args_contains（子集包含）
- e2e_success     tool_hit 且 args_hit（澄清项 = need_clarify；闲聊项 = 零工具调用）
- 执行正确性另计   e2e_exec = e2e_success 且首个工具调用 ok=True（业务侧真实执行成功）
"""
import os
import time

from ..config import get_config, read_json, write_json, ROOT


def load_eval_set(path=None):
    path = path or os.path.join(ROOT, "app", "evaluation", "eval_set.json")
    return read_json(path)


def _match_args(actual, expect):
    if expect is None:
        return True
    for key, val in expect.items():
        if key not in actual:
            return False
        act = actual[key]
        if isinstance(val, float):
            if not isinstance(act, (int, float)) or abs(float(act) - val) > 1e-6:
                return False
        elif act != val:
            return False
    return True


def score_item(item, result):
    """对单条评测打分，返回分项明细。"""
    exp = item["expect"]
    calls = result.tool_calls
    tools_called = [c["tool"] for c in calls]
    detail = {"id": item["id"], "category": item["category"],
              "query": item["query"], "intent": result.intent,
              "tools_called": tools_called, "need_clarify": result.need_clarify}

    if exp.get("clarify"):
        detail["tool_hit"] = result.need_clarify and not tools_called
        detail["args_hit"] = True
        detail["expected"] = "澄清反问（不调用工具）"
    elif "tools" in exp:
        detail["tool_hit"] = tools_called == exp["tools"]
        detail["args_hit"] = True
        detail["expected"] = exp["tools"]
    elif exp.get("tool") is None:
        detail["tool_hit"] = len(tools_called) == 0
        detail["args_hit"] = True
        detail["expected"] = "零工具调用（闲聊拒答）"
    else:
        first = calls[0] if calls else None
        detail["tool_hit"] = bool(first) and first["tool"] == exp["tool"]
        if detail["tool_hit"] and first is not None:
            ok = True
            if "args_eq" in exp:
                ok = _match_eq(first["args"], exp["args_eq"])
            if "args_contains" in exp:
                ok = ok and _match_args(first["args"], exp["args_contains"])
            detail["args_hit"] = ok
        else:
            detail["args_hit"] = False
        detail["args"] = first["args"] if first else None
        detail["expected"] = [exp["tool"]]
        if not detail["args_hit"] and detail["tool_hit"]:
            detail["expected_args"] = exp.get("args_eq") or exp.get("args_contains")

    detail["e2e_success"] = bool(detail["tool_hit"] and detail["args_hit"])
    if detail["e2e_success"] and calls:
        detail["e2e_exec"] = bool(calls[0]["ok"])
    elif detail["e2e_success"] and (exp.get("clarify") or exp.get("tool") is None):
        detail["e2e_exec"] = True
    else:
        detail["e2e_exec"] = False
    detail["elapsed_ms"] = round(result.elapsed_ms, 1)
    detail["answer_preview"] = result.answer[:60].replace("\n", " ")
    return detail


def _match_eq(actual, expect):
    if expect is None:
        return True
    if set(actual.keys()) != set(expect.keys()):
        return False
    return _match_args(actual, expect)


def run_eval(agent, eval_set=None, report_path=None):
    """跑全量评测。返回 (summary, details)。"""
    eval_set = eval_set or load_eval_set()
    details = []
    for item in eval_set:
        result = agent.run(item["query"], session_id="eval-{}".format(item["id"]))
        details.append(score_item(item, result))

    n = len(details)
    tool_hits = sum(1 for d in details if d["tool_hit"])
    args_hits = sum(1 for d in details if d["args_hit"] and d["tool_hit"])
    e2e = sum(1 for d in details if d["e2e_success"])
    e2e_exec = sum(1 for d in details if d["e2e_exec"])
    latencies = [d["elapsed_ms"] for d in details]

    by_cat = {}
    for d in details:
        c = by_cat.setdefault(d["category"], {"n": 0, "e2e": 0})
        c["n"] += 1
        c["e2e"] += 1 if d["e2e_success"] else 0

    summary = {
        "total": n,
        "tool_selection_acc": round(tool_hits / n, 4),
        "args_acc": round(args_hits / n, 4),
        "e2e_success_rate": round(e2e / n, 4),
        "e2e_exec_rate": round(e2e_exec / n, 4),
        "avg_latency_ms": round(sum(latencies) / n, 1),
        "p95_latency_ms": round(sorted(latencies)[int(n * 0.95) - 1], 1),
        "by_category": {k: "{}/{}".format(v["e2e"], v["n"])
                        for k, v in by_cat.items()},
    }
    if report_path:
        write_json(os.path.join(ROOT, report_path),
                   {"summary": summary, "details": details})
    return summary, details


def print_report(summary, details):
    print("=" * 64)
    print("掌柜管家 · Agent 工具调用评测报告")
    print("=" * 64)
    print("评测集规模          : {} 条".format(summary["total"]))
    print("工具选择准确率      : {:.1%}".format(summary["tool_selection_acc"]))
    print("参数抽取正确率      : {:.1%}".format(summary["args_acc"]))
    print("端到端成功率        : {:.1%}".format(summary["e2e_success_rate"]))
    print("端到端执行成功率    : {:.1%}".format(summary["e2e_exec_rate"]))
    print("平均延迟 / P95      : {:.0f}ms / {:.0f}ms".format(
        summary["avg_latency_ms"], summary["p95_latency_ms"]))
    print("-" * 64)
    print("分场景成功率：")
    for cat, v in summary["by_category"].items():
        print("  {:<8} {}".format(cat, v))
    fails = [d for d in details if not d["e2e_success"]]
    if fails:
        print("-" * 64)
        print("失败用例（{} 条）：".format(len(fails)))
        for d in fails:
            expected = d.get("expected", "—")
            if isinstance(expected, list):
                expected = expected[0] if expected else "—"
            line = "  [{}] {} → 调用了 {}，期望 {}".format(
                d["id"], d["query"][:22], d["tools_called"] or "澄清/闲聊", expected)
            if d.get("tool_hit") and not d.get("args_hit"):
                line += "（参数不符，期望含 {}）".format(d.get("expected_args"))
            print(line)
