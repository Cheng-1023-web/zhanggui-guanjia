# -*- coding: utf-8 -*-
"""JSON Structured Output 约束模块。

三件事：
1. validate()      —— 轻量 JSON Schema 校验（type/required/enum/properties/items/范围），
                      不引入 jsonschema 依赖；
2. repair_json()   —— 修复 LLM 输出常见格式病（markdown 围栏 / 中文引号 / 尾逗号 / 前后噪声文本）；
3. call_structured()—— 「生成 → 修复 → 校验 → 带错误反馈重试」闭环，
                       把格式错误作为反馈注入下一轮 Prompt，提升工具调用稳定性。
"""
import json
import re

_TYPES = {"string": str, "number": (int, float), "integer": int,
          "boolean": bool, "array": list, "object": dict}


def validate(instance, schema, path="$"):
    """按 JSON Schema 子集校验，返回错误列表（空列表 = 通过）。"""
    errors = []
    t = schema.get("type")
    if t:
        py = _TYPES[t]
        if t == "integer" and isinstance(instance, bool):
            errors.append("{}: 期望 integer，实际是 boolean".format(path))
            return errors
        if not isinstance(instance, py):
            errors.append("{}: 期望 {}，实际是 {}".format(
                path, t, type(instance).__name__))
            return errors
    if "enum" in schema and instance not in schema["enum"]:
        errors.append("{}: 值 {!r} 不在枚举 {} 内".format(path, instance, schema["enum"]))
    if isinstance(instance, (int, float)) and not isinstance(instance, bool):
        if "minimum" in schema and instance < schema["minimum"]:
            errors.append("{}: {} 低于最小值 {}".format(path, instance, schema["minimum"]))
        if "maximum" in schema and instance > schema["maximum"]:
            errors.append("{}: {} 超过最大值 {}".format(path, instance, schema["maximum"]))
    if t == "array" and isinstance(instance, list):
        if "minItems" in schema and len(instance) < schema["minItems"]:
            errors.append("{}: 数组长度 {} < minItems {}".format(
                path, len(instance), schema["minItems"]))
        item_schema = schema.get("items", {})
        for i, item in enumerate(instance):
            errors.extend(validate(item, item_schema, "{}[{}]".format(path, i)))
    if isinstance(instance, dict):
        for key in schema.get("required", []):
            if key not in instance:
                errors.append("{}: 缺少必填字段 {!r}".format(path, key))
        props = schema.get("properties", {})
        if schema.get("additionalProperties") is False:
            for key in instance:
                if key not in props:
                    errors.append("{}: 不允许的多余字段 {!r}".format(path, key))
        for key, sub in props.items():
            if key in instance:
                errors.extend(validate(instance[key], sub, "{}.{}".format(path, key)))
    return errors


_FENCE_RE = re.compile(r"```(?:json|JSON)?\s*(.*?)```", re.S)


def repair_json(text):
    """修复 LLM 常见输出格式病，返回 (修复后的 json 字符串或 None, 修复动作列表)。"""
    if text is None:
        return None, []
    fixes = []
    raw = text.strip()
    # 1) markdown 围栏包裹
    m = _FENCE_RE.search(raw)
    if m:
        raw = m.group(1).strip()
        fixes.append("剥离 markdown 围栏")
    # 2) 中文引号 → 英文引号
    if "\u201c" in raw or "\u201d" in raw or "\u2018" in raw or "\u2019" in raw:
        raw = (raw.replace("\u201c", '"').replace("\u201d", '"')
                  .replace("\u2018", "'").replace("\u2019", "'"))
        fixes.append("中文引号转英文引号")
    # 3) 截取首个平衡的 {...}（丢弃前后解释性文本）
    start = raw.find("{")
    if start == -1:
        return None, fixes + ["未找到 JSON 对象"]
    depth, end = 0, -1
    in_str = False
    esc = False
    for i in range(start, len(raw)):
        ch = raw[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                end = i
                break
    if end == -1:
        return None, fixes + ["花括号不平衡"]
    body = raw[start:end + 1]
    if start > 0:
        fixes.append("剥离 JSON 前噪声文本")
    if end < len(raw.rstrip()) - 1:
        fixes.append("剥离 JSON 后噪声文本")
    # 4) 尾逗号
    patched = re.sub(r",\s*([}\]])`, r"\1", body)
    if patched != body:
        fixes.append("移除尾逗号")
    return patched, fixes


def parse_structured(text, schema):
    """解析+修复+校验一体。返回 (value|None, info dict)。"""
    info = {"repaired": [], "validation_errors": [], "parsed": False}
    body, fixes = repair_json(text)
    info["repaired"] = fixes
    if body is None:
        info["validation_errors"] = ["无法从输出中提取 JSON 对象"]
        return None, info
    try:
        value = json.loads(body)
    except json.JSONDecodeError as e:
        info["validation_errors"] = ["JSON 解析失败: {}".format(e)]
        return None, info
    errs = validate(value, schema)
    info["validation_errors"] = errs
    info["parsed"] = True
    return (value if not errs else None), info


REPAIR_INSTRUCTION = (
    "你上一次的输出未能通过 JSON Schema 校验。"
    "请严格只输出一个符合 schema 的 JSON 对象，不要任何解释、不要 markdown 围栏。\n"
    "上一轮输出：\n{last}\n\n"
    "校验错误：\n{errors}\n\n"
    "目标 JSON Schema：\n{schema}"
)


def call_structured(llm_client, messages, schema, max_retries=2, temperature=0.2):
    """带校验-修复-重试闭环的结构化调用。

    返回 (value|None, meta)：meta 含 attempts / repaired / errors / success。
    """
    meta = {"attempts": 0, "repaired": [], "errors": [], "success": False}
    msgs = list(messages)
    last_text = None
    for attempt in range(max_retries + 1):
        meta["attempts"] = attempt + 1
        if attempt > 0:
            msgs = list(messages) + [{
                "role": "user",
                "content": REPAIR_INSTRUCTION.format(
                    last=last_text, errors="\n".join(meta["errors"][-4:]),
                    schema=json.dumps(schema, ensure_ascii=False)),
            }]
        text = llm_client.chat(msgs, temperature=temperature)
        last_text = text
        value, info = parse_structured(text, schema)
        meta["repaired"].extend(info.get("repaired", []))
        if value is not None:
            meta["success"] = True
            return value, meta
        meta["errors"].extend(info.get("validation_errors", []))
    return None, meta
