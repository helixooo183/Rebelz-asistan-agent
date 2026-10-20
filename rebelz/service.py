"""Ayarlardan ajan kuran ve oturumları yöneten ortak katman (CLI ve web kullanır)."""

from __future__ import annotations

import threading
from typing import Any, Optional

from .agent import Agent
from .config import Settings
from .llm import ChatClient
from .tools import Approve, ToolRegistry, build_default_tools


def make_approver(settings: Settings, interactive: Optional[Approve] = None) -> Approve:
    if settings.autonomy == "full":
        return lambda name, args: True
    if interactive is not None:
        return interactive
    return lambda name, args: False  # Onay kanalı yoksa yıkıcı işlemler reddedilir.


def make_registry(settings: Settings) -> ToolRegistry:
    return ToolRegistry(build_default_tools(settings.workspace))


def make_agent(settings: Settings, approve: Approve, registry: Optional[ToolRegistry] = None) -> Agent:
    client = ChatClient(settings.base_url, settings.model, settings.api_key, settings.llm_timeout)
    return Agent(
        client,
        registry or make_registry(settings),
        approve,
        max_steps=settings.max_steps,
        context_chars=settings.context_chars,
    )


class ChatService:
    """Birden çok web oturumunu (session id) yönetir; her oturum kendi geçmişini tutar."""

    MAX_SESSIONS = 50

    def __init__(self, settings: Settings):
        self.settings = settings
        self.registry = make_registry(settings)
        self.approve = make_approver(settings)
        self._sessions: dict[str, tuple[Agent, threading.Lock]] = {}
        self._guard = threading.Lock()

    def _session(self, session_id: str) -> tuple[Agent, threading.Lock]:
        with self._guard:
            if session_id not in self._sessions:
                if len(self._sessions) >= self.MAX_SESSIONS:
                    oldest = next(iter(self._sessions))
                    del self._sessions[oldest]
                agent = make_agent(self.settings, self.approve, self.registry)
                self._sessions[session_id] = (agent, threading.Lock())
            return self._sessions[session_id]

    def chat(self, session_id: str, text: str) -> dict[str, Any]:
        agent, lock = self._session(session_id)
        with lock:
            reply = agent.ask(text)
            return {"reply": reply, "trace": list(agent.trace)}

    def reset(self, session_id: str) -> None:
        agent, lock = self._session(session_id)
        with lock:
            agent.reset()
