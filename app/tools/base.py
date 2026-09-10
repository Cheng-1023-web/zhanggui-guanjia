# -*- coding: utf-8 -*-
"""工具基类与注册表：业务函数 → LLM 可调用的结构化 Tool。"""
import time
from dataclasses import dataclass, field


@dataclass
class Observation:
    """一次工具执行的完整观测结果。"""
    tool: str
    args: dict
    ok: bool
    data: object = None
    text: str = ""          # 渲染后的自然语言结果（喂给 LLM / 直接回复用户）
    error: str = ""
    elapsed_ms: float = 0.0

    def to_dict(self):
        return {"tool": self.tool, "args": self.args, "ok": self.ok,
                "data": self.data, "text": self.text, "error": self.error,
                "elapsed_ms": round(self.elapsed_ms, 1)}


@dataclass
class Tool:
    """一个可被 LLM Function Calling 调用的业务工具。"""
    name: str
    description: str
    parameters: dict            # JSON Schema（properties / required / additionalProperties=False）
    handler: object             # callable(args: dict, ctx) -> (data, text)
    examples: list = field(default_factory=list)

    def openai_schema(self):
        """OpenAI tools 格式的 function 定义。"""
        return {"type": "function", "function": {
            "name": self.name, "description": self.description,
            "parameters": self.parameters}}


class Registry:
    def __init__(self):
        self._tools = {}

    def register(self, tool: Tool):
        self._tools[tool.name] = tool

    def get(self, name):
        return self._tools.get(name)

    def names(self):
        return list(self._tools.keys())

    def schemas(self):
        return [t.openai_schema() for t in self._tools.values()]

    def tool_card(self):
        """给规则路由 / 人工查看用的紧凑工具清单。"""
        lines = []
        for t in self._tools.values():
            req = ",".join(t.parameters.get("required", []))
            lines.append("- {}：{}（必填: {}）".format(t.name, t.description, req or "无"))
        return "\n".join(lines)

    def execute(self, name, args, ctx):
        """校验参数 → 执行 → 观测。参数非法直接返回错误观测（不执行业务函数）。"""
        tool = self._tools.get(name)
        if tool is None:
            return Observation(name, args, False, error="未知工具: {}".format(name))
        from .. import structured
        errs = structured.validate(args, tool.parameters)
        if errs:
            return Observation(name, args, False,
                               error="参数校验失败: " + "; ".join(errs))
        t0 = time.time()
        try:
            data, text = tool.handler(args, ctx)
            return Observation(name, args, True, data=data, text=text,
                               elapsed_ms=(time.time() - t0) * 1000)
        except Exception as e:  # 业务函数兜底：不让单工具异常打断 Agent 循环
            return Observation(name, args, False,
                               error="执行异常: {}".format(e),
                               elapsed_ms=(time.time() - t0) * 1000)


class ToolContext:
    """工具执行上下文：数据仓库 + 配置 + 会话记忆。"""

    def __init__(self, store, config, session=None):
        self.store = store
        self.config = config
        self.session = session
