# -*- coding: utf-8 -*-
"""三层记忆机制：

1. 短期上下文记忆  ShortTermMemory —— 滑动窗口 + token 预算裁剪，
   只保留最近 N 轮对话且不超上下文预算；
2. 任务草稿板      Scratchpad     —— 单次 ReAct 循环内的
   Thought/Action/Observation 中间态，任务结束即折叠为一条结论；
3. 长期记忆        LongTermMemory —— 事实（店名/主营/偏好）+ 情景片段，
   片段向量化（字符 bigram 哈希向量，零依赖）后按余弦相似度召回，
   检索 Top-K 注入上下文；会话结束持久化到 data/sessions/。

记忆分层写入 / 读取均通过 Session 对象完成。
"""
import hashlib
import json
import math
import os
import re
import time

from ..config import SESSION_DIR


# ---------------------------------------------------------------- token 估算
def estimate_tokens(text):
    """粗估 token 数：中文约 1 字 ≈ 0.6 token（Qwen 词表经验值），英文按 4 字符/token。"""
    if not text:
        return 0
    zh = len(re.findall(r"[\u4e00-\u9fff]", text))
    other = len(text) - zh
    return int(zh * 0.6 + other / 4.0) + 1


# ---------------------------------------------------------------- 哈希向量
def hash_vector(text, dim=256):
    """字符 bigram 哈希向量（词频加权，L2 归一化）。零依赖、确定性。"""
    vec = [0.0] * dim
    t = re.sub(r"\s+", "", text or "")
    grams = [t[i:i + 2] for i in range(max(len(t) - 1, 0))] or [t]
    for g in grams:
        h = int(hashlib.md5(g.encode("utf-8")).hexdigest(), 16)
        vec[h % dim] += 1.0
    norm = math.sqrt(sum(v * v for v in vec)) or 1.0
    return [v / norm for v in vec]


def cosine(a, b):
    return sum(x * y for x, y in zip(a, b))


# ---------------------------------------------------------------- 会话与记忆
class Session:
    """一个会话 = 短期消息历史 + 长期记忆（事实 + 情景片段）。"""

    def __init__(self, sid, config):
        self.sid = sid
        self.config = config
        self.messages = []      # [{"role","content","ts"}]
        self.facts = []         # ["店名叫…", …]
        self.episodes = []      # [{"text","vec","ts"}]
        self.created_at = time.time()
        self.updated_at = self.created_at

    # ---- 短期上下文 ----
    def short_term(self):
        cfg = self.config["agent"]
        msgs = self.messages[-cfg["max_history_turns"] * 2:]
        budget = cfg["max_context_tokens"]
        picked, used = [], 0
        for m in reversed(msgs):
            t = estimate_tokens(m["content"])
            if used + t > budget and picked:
                break
            picked.insert(0, {"role": m["role"], "content": m["content"]})
            used += t
        return picked, used

    # ---- 写入 ----
    def add_message(self, role, content):
        self.messages.append({"role": role, "content": content,
                              "ts": time.time()})
        self.updated_at = time.time()

    def add_episode(self, text):
        if not text or len(text) < 4:
            return
        dim = self.config["memory"]["longterm_dim"]
        self.episodes.append({"text": text, "vec": hash_vector(text, dim),
                              "ts": time.time()})
        self.updated_at = time.time()

    FACT_PATTERNS = [
        (r"(?<!我)(?:我们)?(?:店|本店)?(?:名|名字)?叫([\u4e00-\u9fa5A-Za-z0-9]{2,12})", "店名"),
        (r"(?:主营|主打|卖的是|做的是)([\u4e00-\u9fa5A-Za-z0-9]{2,12})", "主营"),
        (r"记住[:：,，]?(.+)", "用户嘱咐"),
        (r"以后(?:要|请|记得)?(.+)", "用户偏好"),
        (r"(?:默认|习惯)(?:用|选|发)?(.{2,20})", "默认偏好"),
    ]

    def extract_facts(self, user_text):
        """从用户话语中抽取长期事实（规则式，可解释）。"""
        new = []
        for pat, label in self.FACT_PATTERNS:
            for m in re.finditer(pat, user_text):
                fact = "{}：{}".format(label, m.group(1).strip()[:30])
                if fact not in self.facts:
                    self.facts.append(fact)
                    new.append(fact)
        limit = self.config["memory"]["max_facts"]
        self.facts = self.facts[-limit:]
        return new

    # ---- 读取 ----
    def recall(self, query, k=None):
        """按相似度召回最相关的 k 条历史情景片段。"""
        k = k or self.config["memory"]["recall_topk"]
        if not self.episodes:
            return []
        qv = hash_vector(query, self.config["memory"]["longterm_dim"])
        scored = sorted(((cosine(qv, e["vec"]), e) for e in self.episodes),
                        key=lambda x: -x[0])
        return [e["text"] for s, e in scored[:k] if s > 0.05]

    def memory_context(self, query):
        """注入 Prompt 的记忆上下文块。"""
        parts = []
        if self.facts:
            parts.append("已记住的店铺信息：" + "；".join(self.facts[-8:]))
        recalled = self.recall(query)
        if recalled:
            parts.append("相关历史交互：" + "；".join(recalled))
        return "\n".join(parts)

    # ---- 持久化 ----
    def save(self):
        os.makedirs(SESSION_DIR, exist_ok=True)
        data = {"sid": self.sid, "messages": self.messages, "facts": self.facts,
                "episodes": [{"text": e["text"], "ts": e["ts"]} for e in self.episodes],
                "created_at": self.created_at, "updated_at": self.updated_at}
        with open(os.path.join(SESSION_DIR, self.sid + ".json"), "w",
                  encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    @classmethod
    def load(cls, sid, config):
        path = os.path.join(SESSION_DIR, sid + ".json")
        s = cls(sid, config)
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            s.messages = data.get("messages", [])
            s.facts = data.get("facts", [])
            s.episodes = [{"text": e["text"], "ts": e.get("ts", 0),
                           "vec": hash_vector(e["text"], config["memory"]["longterm_dim"])}
                          for e in data.get("episodes", [])]
            s.created_at = data.get("created_at", s.created_at)
            s.updated_at = data.get("updated_at", s.updated_at)
        return s


class SessionStore:
    def __init__(self, config):
        self.config = config
        self._cache = {}

    def get(self, sid):
        if sid not in self._cache:
            self._cache[sid] = Session.load(sid, self.config)
        return self._cache[sid]

    def persist(self, sid):
        if sid in self._cache:
            self._cache[sid].save()


class Scratchpad:
    """任务草稿板：一次 ReAct 循环内的中间轨迹。"""

    def __init__(self):
        self.entries = []

    def add(self, thought, tool, args, observation):
        self.entries.append({"thought": thought, "tool": tool,
                             "args": args, "observation": observation})

    def render(self):
        lines = []
        for i, e in enumerate(self.entries, 1):
            lines.append("Thought {}: {}".format(i, e["thought"]))
            lines.append("Action {}: {}".format(i, e["tool"]))
            lines.append("Observation {}: {}".format(
                i, (e["observation"] or "")[:400]))
        return "\n".join(lines)

    def summary(self):
        """任务结束后的折叠结论（写回长期记忆）。"""
        tools = [e["tool"] for e in self.entries if e["tool"]]
        if not tools:
            return None
        first_obs = next((e["observation"] for e in self.entries
                          if e["observation"]), "")
        return "用户任务：调用了 {}，结果：{}".format(
            "+".join(dict.fromkeys(tools)), first_obs[:80])
