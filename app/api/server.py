# -*- coding: utf-8 -*-
"""FastAPI 服务：/api/chat、/api/tools、/api/session/{sid}、/api/health、/（Web 界面）。"""
import os

from fastapi import FastAPI
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    session_id: str = Field(default="default", min_length=1, max_length=64)
    message: str = Field(min_length=1, max_length=2000)


def create_app(config, store, registry, agent):
    app = FastAPI(title="掌柜管家 · 商户运营 LLM Agent", version="1.0.0")
    web_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                           "..", "web")

    @app.get("/")
    def index():
        return FileResponse(os.path.join(web_dir, "index.html"))

    @app.post("/api/chat")
    def chat(req: ChatRequest):
        r = agent.run(req.message, session_id=req.session_id)
        return {
            "answer": r.answer,
            "intent": r.intent,
            "tool_calls": r.tool_calls,
            "iterations": r.iterations,
            "elapsed_ms": round(r.elapsed_ms, 1),
            "need_clarify": r.need_clarify,
        }

    @app.get("/api/tools")
    def tools():
        return {"count": len(registry.names()),
                "tools": [{"name": t.name, "description": t.description,
                           "parameters": t.parameters}
                          for t in registry._tools.values()]}

    @app.get("/api/session/{sid}")
    def session(sid: str):
        s = agent.sessions.get(sid)
        return {"sid": sid, "turns": len(s.messages),
                "facts": s.facts,
                "episodes": len(s.episodes),
                "messages": s.messages[-20:]}

    @app.get("/api/health")
    def health():
        return {"status": "ok",
                "llm_provider": config["llm"]["provider"],
                "tools": len(registry.names()),
                "orders": len(store.orders)}

    return app
