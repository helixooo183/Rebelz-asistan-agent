"""OpenAI uyumlu /chat/completions istemcisi (sadece standart kütüphane)."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any, Optional


class LLMError(RuntimeError):
    def __init__(self, message: str, status: Optional[int] = None):
        super().__init__(message)
        self.status = status


class ChatClient:
    def __init__(self, base_url: str, model: str, api_key: str = "local", timeout: float = 900.0):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key = api_key
        self.timeout = timeout

    def chat(self, messages: list[dict[str, Any]], tools: Optional[list[dict[str, Any]]] = None) -> dict[str, Any]:
        """Bir sohbet tamamlaması ister ve asistan mesajını (dict) döndürür."""
        body: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": 0.2,
            "stream": False,
        }
        if tools:
            body["tools"] = tools
            body["tool_choice"] = "auto"

        request = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                raw = response.read()
        except urllib.error.HTTPError as exc:
            detail = exc.read()[:400].decode("utf-8", errors="replace")
            raise LLMError(f"Model sunucusu HTTP {exc.code} döndürdü: {detail}", status=exc.code) from exc
        except (urllib.error.URLError, OSError) as exc:
            raise LLMError(
                f"Model sunucusuna bağlanılamadı ({self.base_url}). "
                f"llama-server çalışıyor mu? Ayrıntı: {exc}"
            ) from exc

        try:
            data = json.loads(raw)
            message = data["choices"][0]["message"]
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise LLMError("Model sunucusundan beklenmeyen bir yanıt geldi.") from exc
        if not isinstance(message, dict):
            raise LLMError("Model sunucusundan beklenmeyen bir yanıt geldi.")
        return message
