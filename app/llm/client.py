# -*- coding: utf-8 -*-
"""LLM 客户端：任意 OpenAI 兼容服务（Ollama / vLLM / 其它），标准库实现零额外依赖。

provider=rule 时不发起网络请求，Agent 走规则基线路径（离线可复现）。
"""
import json
import time
import urllib.error
import urllib.request


class LLMClient:
    def __init__(self, cfg):
        self.cfg = cfg.get("llm", {})
        self.enabled = self.cfg.get("provider") == "openai"

    @property
    def model(self):
        return self.cfg.get("model", "")

    def chat(self, messages, temperature=None, max_tokens=None, timeout=None):
        """调用 /chat/completions，返回文本。失败抛异常（由上层决定回退策略）。"""
        if not self.enabled:
            raise RuntimeError("LLM 未启用（provider=rule）")
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": self.cfg.get("temperature", 0.2) if temperature is None else temperature,
            "max_tokens": self.cfg.get("max_tokens", 512) if max_tokens is None else max_tokens,
            "stream": False,
        }
        # OpenAI 兼容的 JSON 约束：vLLM 用 guided_json 时由上层传 extra；Ollama 支持 format=json
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            self.cfg["api_base"].rstrip("/") + "/chat/completions",
            data=body,
            headers={"Content-Type": "application/json",
                     "Authorization": "Bearer " + self.cfg.get("api_key", "")},
            method="POST")
        t0 = time.time()
        with urllib.request.urlopen(req, timeout=timeout or self.cfg.get("timeout", 60)) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        elapsed = time.time() - t0
        text = data["choices"][0]["message"]["content"]
        usage = data.get("usage", {})
        return text, usage, elapsed

    def chat_stream(self, messages, temperature=None, max_tokens=None):
        """流式调用，逐段 yield (delta, is_done, ttft_ms)。用于压测与对话 UI。"""
        payload = {
            "model": self.model, "messages": messages, "stream": True,
            "temperature": self.cfg.get("temperature", 0.2) if temperature is None else temperature,
            "max_tokens": self.cfg.get("max_tokens", 512) if max_tokens is None else max_tokens,
        }
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            self.cfg["api_base"].rstrip("/") + "/chat/completions",
            data=body,
            headers={"Content-Type": "application/json",
                     "Authorization": "Bearer " + self.cfg.get("api_key", "")},
            method="POST")
        t0 = time.time()
        ttft = None
        with urllib.request.urlopen(req, timeout=self.cfg.get("timeout", 120)) as resp:
            for raw in resp:
                line = raw.decode("utf-8").strip()
                if not line.startswith("data:"):
                    continue
                chunk = line[5:].strip()
                if chunk == "[DONE]":
                    break
                try:
                    obj = json.loads(chunk)
                except json.JSONDecodeError:
                    continue
                delta = obj["choices"][0].get("delta", {}).get("content") or ""
                if delta and ttft is None:
                    ttft = (time.time() - t0) * 1000
                if delta:
                    yield delta, False, ttft
        yield "", True, ttft
