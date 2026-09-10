# -*- coding: utf-8 -*-
"""ReAct Agent 引擎：多阶段工作流闭环。

    意图识别 → 任务规划 → 工具选择 → Function Calling → 结果处理 → 自然语言回复

双路径实现：
- rule 路径（默认，零依赖离线可复现）：RulePlanner 产出确定性 Plan，
  引擎按步执行工具、渲染结果、组装回复；
- llm 路径（provider=openai，接 Ollama/vLLM 等 OpenAI 兼容服务）：
  模型按 ReAct 范式（Thought → Action → Observation → … → Final Answer）
  逐轮决策，Action 阶段经 JSON Structured Output 约束 + 校验修复 + 重试。

引擎、记忆、工具注册表对两条路径完全复用；每轮轨迹写入 Scratchpad，
任务结束折叠为情景片段写入长期记忆。
"""
import json
import time
from dataclasses import dataclass, field

from ..llm.client import LLMClient
from ..structured import call_structured, validate
from .memory import Scratchpad, SessionStore
from .planner import RulePlanner

ACTION_SCHEMA = lambda tool_names: {  # noqa: E731
    "type": "object",
    "properties": {
        "thought": {"type": "string"},
        "tool": {"type": "string", "enum": tool_names + ["finish"]},
        "args": {"type": "object"},
    },
    "required": ["thought", "tool"],
    "additionalProperties": False,
}


@dataclass
class AgentResult:
    query: str
    answer: str
    intent: str = ""
    tool_calls: list = field(default_factory=list)   # [{tool,args,ok,error}]
    iterations: int = 0
    elapsed_ms: float = 0.0
    need_clarify: bool = False
    trace: list = field(default_factory=list)        # ReAct 全轨迹（thought/action/obs）
    new_facts: list = field(default_factory=list)

    def to_dict(self):
        return {"query": self.query, "answer": self.answer, "intent": self.intent,
                "tool_calls": self.tool_calls, "iterations": self.iterations,
                "elapsed_ms": round(self.elapsed_ms, 1),
                "need_clarify": self.need_clarify, "trace": self.trace}


class ReActAgent:
    def __init__(self, config, registry, store):
        self.config = config
        self.registry = registry
        self.store = store
        self.llm = LLMClient(config)
        self.sessions = SessionStore(config)
        self.planner = RulePlanner(store, config)
        self.max_iters = config["agent"]["max_iters"]
        self.use_structured = True   # False = 对照组（无 JSON 约束的自由文本输出）

    # ------------------------------------------------------------------ 入口
    def run(self, query, session_id="default", context=None):
        t0 = time.time()
        session = self.sessions.get(session_id)

        # 1) 记忆层：事实抽取 + 历史召回（短期上下文 + 长期记忆注入）
        new_facts = session.extract_facts(query)
        mem_ctx = session.memory_context(query)
        history, hist_tokens = session.short_term()

        result = AgentResult(query=query, answer="")
        scratch = Scratchpad()

        if self.llm.enabled:
            self._run_llm(query, session, history, mem_ctx, scratch, result,
                          structured=self.use_structured)
        else:
            self._run_rule(query, session, mem_ctx, scratch, result)

        # 2) 结果处理：写回对话历史 + 记忆折叠 + 会话持久化
        session.add_message("user", query)
        session.add_message("assistant", result.answer)
        if result.tool_calls:
            session.add_message("assistant",
                                "[工具轨迹] " + json.dumps(
                                    [{k: c[k] for k in ("tool", "args", "ok")}
                                     for c in result.tool_calls],
                                    ensure_ascii=False))
        summary = scratch.summary()
        if summary:
            session.add_episode(summary)
        result.new_facts = new_facts
        result.iterations = len(result.tool_calls)
        result.elapsed_ms = (time.time() - t0) * 1000
        session.save()
        return result

    # ------------------------------------------------------------------ 规则路径
    def _run_rule(self, query, session, mem_ctx, scratch, result):
        plan = self.planner.plan(query, session)
        result.intent = plan.intent
        result.need_clarify = plan.need_clarify
        result.trace.append({"phase": "plan", "thought": plan.thought,
                             "steps": len(plan.steps)})

        if plan.need_clarify:
            result.answer = plan.clarify_question
            return

        if plan.intent == "chitchat":
            result.answer = self._chitchat_reply(query)
            return

        obs_texts, failed = [], []
        for i, step in enumerate(plan.steps):
            if i >= self.max_iters:
                break
            obs = self.registry.execute(step.tool, step.args,
                                        self._ctx(session))
            result.tool_calls.append({"tool": step.tool, "args": step.args,
                                      "ok": obs.ok, "error": obs.error})
            scratch.add(step.thought, step.tool, step.args,
                        obs.text if obs.ok else obs.error)
            result.trace.append({"phase": "act", "thought": step.thought,
                                 "tool": step.tool, "args": step.args,
                                 "ok": obs.ok, "error": obs.error,
                                 "elapsed_ms": round(obs.elapsed_ms, 1)})
            if obs.ok:
                obs_texts.append(obs.text)
            else:
                failed.append((step.tool, obs.error))

        result.answer = self._compose(plan, obs_texts, failed)

    def _compose(self, plan, obs_texts, failed):
        parts = []
        if obs_texts:
            if len(obs_texts) == 1:
                parts.append(obs_texts[0])
            else:
                for i, t in enumerate(obs_texts, 1):
                    parts.append("【第{}步】\n{}".format(i, t))
        if failed:
            parts.append("⚠️ 部分操作未完成：" +
                         "；".join("{}（{}）".format(t, e) for t, e in failed))
        answer = "\n\n".join(parts).strip()
        if plan.intent == "composite_report_campaign" and not failed:
            answer += "\n\n如需落地执行，告诉我「按方案建券」即可。"
        return answer or "暂无结果。"

    def _chitchat_reply(self, query):
        name = self.config["merchant"]["name"]
        return ("我是「{}」的运营管家，帮你管商品、盯订单、做营销。\n"
                "可以试试：查库存预警、改价、处理退款、创建优惠券、"
                "看销售报表、要一份清仓/拉新方案。".format(name))

    def _ctx(self, session):
        from ..tools.base import ToolContext
        return ToolContext(self.store, self.config, session)

    # ------------------------------------------------------------------ LLM 路径
    SYSTEM_TEMPLATE = (
        "你是「{merchant}」的商户运营管家，面向中小商户处理商品、订单、营销等运营任务。\n"
        "可用工具及其 JSON Schema：\n{tools}\n\n"
        "{memory}\n"
        "工作方式（ReAct）：每轮输出一个 JSON 对象 {{\"thought\": ..., \"tool\": ..., \"args\": ...}}；\n"
        "tool 填工具名，全部信息收集完成后 tool 填 \"finish\" 结束并汇总回复。\n"
        "只输出 JSON，不要 markdown 围栏。缺关键信息（商品名/订单号/价格）时也选 \"finish\"，"
        "在 thought 里说明需要向用户澄清什么。")

    def _run_llm(self, query, session, history, mem_ctx, scratch, result,
                 structured=True):
        tool_names = self.registry.names()
        sys_prompt = self.SYSTEM_TEMPLATE.format(
            merchant=self.config["merchant"]["name"],
            tools=json.dumps(self.registry.schemas(), ensure_ascii=False, indent=1),
            memory=("记忆上下文：\n" + mem_ctx) if mem_ctx else "（暂无历史记忆）")
        msgs = [{"role": "system", "content": sys_prompt}] + history + [
            {"role": "user", "content": query}]

        finished = False
        for it in range(self.max_iters):
            if structured:
                schema = ACTION_SCHEMA(tool_names)
                action, meta = call_structured(
                    self.llm, msgs + [{"role": "user", "content":
                                       "输出下一轮 ReAct JSON 动作。"}],
                    schema, max_retries=self.config["structured"]["max_retries"])
                result.trace.append({"phase": "llm_action", "attempts": meta["attempts"],
                                     "repaired": meta["repaired"],
                                     "success": meta["success"]})
                if action is None:
                    result.answer = ("这次没解析出有效的工具调用，换个说法试试？"
                                     "（例如：查一下库存不足的商品）")
                    return
            else:
                # 对照组：无 JSON 约束的自由文本输出（评测结构化约束带来的稳定性提升）
                text, _, _ = self.llm.chat(msgs + [
                    {"role": "user", "content":
                     "输出下一轮动作，格式：\nTOOL: 工具名（或 finish）\nARGS: {参数json}"}])
                action = self._parse_loose(text)
                result.trace.append({"phase": "llm_action_loose",
                                     "success": action is not None})

            tool = action.get("tool", "finish")
            thought = action.get("thought", "")
            if tool == "finish":
                finished = True
                result.answer = self._llm_final(msgs, scratch)
                return

            args = action.get("args") or {}
            # 参数校验兜底：schema 错误直接作为 observation 反馈给模型（而非执行失败）
            t = self.registry.get(tool)
            if t is None:
                obs_text = "错误：未知工具 {}，请从工具清单中选择".format(tool)
                obs_ok = False
            else:
                errs = validate(args, t.parameters)
                if errs:
                    obs_text = "参数校验失败：{}；schema：{}".format(
                        "; ".join(errs), json.dumps(t.parameters, ensure_ascii=False))
                    obs_ok = False
                else:
                    obs = self.registry.execute(tool, args, self._ctx(session))
                    obs_ok = obs.ok
                    obs_text = obs.text if obs.ok else obs.error
                    result.tool_calls.append({"tool": tool, "args": args,
                                              "ok": obs.ok, "error": obs.error})
            scratch.add(thought, tool, args, obs_text)
            result.trace.append({"phase": "act", "thought": thought,
                                 "tool": tool, "args": args, "ok": obs_ok})
            msgs = msgs + [
                {"role": "assistant", "content": json.dumps(action, ensure_ascii=False)},
                {"role": "user", "content": "Observation: " + obs_text +
                 "\n继续输出下一轮 ReAct JSON 动作；信息足够则选 finish。"}]
        if not finished:
            result.answer = self._llm_final(msgs, scratch)

    def _parse_loose(self, text):
        """无约束输出的宽松解析（对照组用）：解析失败返回 None。"""
        import re
        m_tool = re.search(r"TOOL\s*[:：]\s*(\w+)", text or "")
        if not m_tool:
            return None
        action = {"tool": m_tool.group(1), "thought": ""}
        m_args = re.search(r"ARGS\s*[:：]\s*(\{.*\})", text or "", re.S)
        if m_args:
            try:
                action["args"] = json.loads(m_args.group(1))
            except json.JSONDecodeError:
                return None  # JSON 坏了且无修复/重试机制 —— 对照组的典型失败形态
        return action

    def _llm_final(self, msgs, scratch):
        try:
            text, _, _ = self.llm.chat(msgs + [{
                "role": "user",
                "content": "基于以上 Observation 汇总为面向商户的中文回复："
                           "先给结论，再给关键数据；如有未完成事项明确说明。"}], max_tokens=768)
            return text.strip()
        except Exception as e:
            obs = [e["observation"] for e in scratch.entries if e["observation"]]
            if obs:
                return "\n\n".join(obs) + "\n\n（模型汇总失败：{}）".format(e)
            return "服务暂时不可用：{}".format(e)
