# -*- coding: utf-8 -*-
"""JSON Structured Output 修复链路的独立实测。

做法：以评测集 40 条的工具参数为合法样本，程序化注入 4 类 LLM 常见输出污染
（markdown 围栏 / 中文引号 / 尾逗号 / 前后噪声文本），各生成一条污染样本；
分别用「裸 json.loads」（对照组）与「repair + validate」（本模块）解析，
统计解析成功率。全程无 LLM 参与，确定性可复现。
"""
import copy
import json
import os
import random

from ..config import ROOT, read_json, write_json
from ..structured import parse_structured

random.seed(7)

CORRUPTIONS = ["fence", "smart_quotes", "trailing_comma", "noise", "combined"]


def corrupt(text, kind):
    """对合法 JSON 注入一类 LLM 常见输出污染。"""
    if kind == "fence":
        return "```json\n{}\n```".format(text)
    if kind == "smart_quotes":
        # 成对替换第一组引号为中文引号（LLM 中文语境常见病灶）
        return text.replace('"', "\u201c", 1).replace('"', "\u201d", 1)
    if kind == "trailing_comma":
        idx = text.rstrip().rfind("}")
        return text[:idx] + ",}" + text[idx + 1:]
    if kind == "noise":
        return "好的，我调用工具如下：\n" + text + "\n以上。"
    if kind == "combined":
        s = text.replace('"', "\u201c", 1).replace('"', "\u201d", 1)
        return "```json\n" + s + "\n```" + "\n希望符合要求。"
    raise ValueError(kind)


def build_tool_schema(tool_name):
    from ..tools import build_registry
    reg = build_registry()
    return reg.get(tool_name).parameters


def sample_args(item, reg):
    exp = item["expect"]
    tool = exp.get("tool")
    if not tool:
        return None
    args = dict(exp.get("args_contains") or exp.get("args_eq") or {})
    t = reg.get(tool)
    # 补齐必填字段，构造一个 schema 合法的样本
    for req in t.parameters.get("required", []):
        if req in args:
            continue
        prop = t.parameters["properties"][req]
        if prop.get("enum"):
            args[req] = prop["enum"][0]
        elif prop.get("type") == "string":
            args[req] = "测试"
        elif prop.get("type") == "number":
            args[req] = 10.0
        elif prop.get("type") == "integer":
            args[req] = 10
        elif prop.get("type") == "boolean":
            args[req] = True
    return tool, args


def run_structured_eval(report_path="data/logs/structured_eval_report.json"):
    from ..tools import build_registry
    reg = build_registry()
    eval_set = read_json(os.path.join(ROOT, "app", "evaluation", "eval_set.json"))
    cases = []
    for item in eval_set:
        sa = sample_args(item, reg)
        if sa is None:
            continue
        tool, args = sa
        valid = json.dumps({"thought": "t", "tool": tool, "args": args},
                           ensure_ascii=False)
        schema = {"type": "object",
                  "properties": {"thought": {"type": "string"},
                                 "tool": {"type": "string"},
                                 "args": reg.get(tool).parameters},
                  "required": ["tool", "args"]}
        for kind in CORRUPTIONS:
            cases.append({"id": item["id"], "corruption": kind,
                          "text": corrupt(valid, kind), "schema": schema})

    n = len(cases)
    raw_ok = repair_ok = 0
    details = []
    for c in cases:
        try:
            json.loads(c["text"])
            raw = True
        except json.JSONDecodeError:
            raw = False
        val, info = parse_structured(c["text"], c["schema"])
        ours = val is not None
        raw_ok += raw
        repair_ok += ours
        details.append({"id": c["id"], "corruption": c["corruption"],
                        "raw_ok": raw, "repair_ok": ours,
                        "repairs": info.get("repaired", [])})
    summary = {
        "samples": n,
        "raw_json_loads_rate": round(raw_ok / n, 4),
        "repair_validate_rate": round(repair_ok / n, 4),
        "by_corruption": {},
    }
    for kind in CORRUPTIONS:
        sub = [d for d in details if d["corruption"] == kind]
        summary["by_corruption"][kind] = {
            "raw": "{}/{}".format(sum(d["raw_ok"] for d in sub), len(sub)),
            "repaired": "{}/{}".format(sum(d["repair_ok"] for d in sub), len(sub)),
        }
    write_json(os.path.join(ROOT, report_path), {"summary": summary,
                                                 "details": details})
    return summary


if __name__ == "__main__":
    print(json.dumps(run_structured_eval(), ensure_ascii=False, indent=2))
