"""Otonom ajan döngüsü: model -> araç çağrıları -> sonuçlar -> ... -> nihai yanıt."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Optional, Protocol

from .llm import LLMError
from .tools import Approve, ToolRegistry

REPEAT_LIMIT = 3  # Aynı araç çağrısı bu kadar üst üste tekrarlanırsa döngü sayılır.
TOOL_FALLBACK_STATUSES = (400, 404, 422, 500, 501)


class ChatBackend(Protocol):
    def chat(self, messages: list[dict[str, Any]], tools: Optional[list[dict[str, Any]]] = None) -> dict[str, Any]:
        ...


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]
    native: bool  # Model yapılandırılmış tool_calls mi döndürdü, yoksa metin içinde mi yazdı?
    error: Optional[str] = None


def default_system_prompt(now: Optional[datetime] = None) -> str:
    today = (now or datetime.now().astimezone()).strftime("%Y-%m-%d %H:%M %Z")
    return (
        "Sen Rebelz'sin: kullanıcının kendi telefonunda çalışan, kişisel ve otonom bir asistansın.\n"
        f"Bugünün tarihi ve saati: {today}.\n"
        "- Kullanıcıyla Türkçe konuş; kullanıcı başka bir dilde yazarsa o dilde cevap ver.\n"
        "- Bir görevi tamamlamak için araçları kendin seç ve sırayla kullan. Bilgi eksikse önce araçlarla bak.\n"
        "- Güncel bilgi için web_search, sayfa içeriği için web_fetch kullan. Tahmin etme.\n"
        "- Araç hatası alırsan nedenini anla ve farklı bir yaklaşım dene.\n"
        "- Yıkıcı veya geri alınamaz işlemlerden (silme, üzerine yazma) önce kullanıcıya açıkça sor.\n"
        "- İş bittiğinde kısa ve net bir özet ver. Gereksiz uzatma."
    )


def _tool_catalog_message(registry: ToolRegistry) -> dict[str, str]:
    return {
        "role": "system",
        "content": (
            "Araç kullanmak istersen yanıtının TAMAMINI şu JSON biçiminde yaz ve başka metin ekleme:\n"
            '{"name": "arac_adi", "arguments": {...}}\n'
            "Araç gerekmiyorsa normal metinle cevap ver.\nKullanılabilir araçlar:\n"
            + registry.describe()
        ),
    }


def parse_text_tool_calls(content: str, known: set[str]) -> list[tuple[str, dict[str, Any], Optional[str]]]:
    """Modelin metin içine yazdığı {"name":..., "arguments":...} çağrılarını çıkarır."""
    decoder = json.JSONDecoder()
    found: list[tuple[str, dict[str, Any], Optional[str]]] = []
    position = 0
    while True:
        start = content.find("{", position)
        if start < 0:
            break
        try:
            obj, end = decoder.raw_decode(content, start)
        except ValueError:
            position = start + 1
            continue
        position = end
        if not isinstance(obj, dict) or obj.get("name") not in known:
            continue
        raw_args = obj.get("arguments", obj.get("parameters", {}))
        if isinstance(raw_args, str):
            try:
                raw_args = json.loads(raw_args)
            except ValueError:
                found.append((obj["name"], {}, "Hata: araç argümanları geçerli JSON değil."))
                continue
        if not isinstance(raw_args, dict):
            found.append((obj["name"], {}, "Hata: araç argümanları bir nesne olmalı."))
            continue
        found.append((obj["name"], raw_args, None))
    return found


class Agent:
    def __init__(
        self,
        llm: ChatBackend,
        tools: ToolRegistry,
        approve: Approve,
        *,
        max_steps: int = 0,
        context_chars: int = 24000,
        system_prompt: Optional[str] = None,
    ):
        self.llm = llm
        self.tools = tools
        self.approve = approve
        self.max_steps = max_steps
        self.context_chars = context_chars
        self.system_prompt = system_prompt or default_system_prompt()
        self.native_tools = True
        self.trace: list[dict[str, Any]] = []
        self.messages: list[dict[str, Any]] = [{"role": "system", "content": self.system_prompt}]

    def reset(self) -> None:
        self.messages = [{"role": "system", "content": self.system_prompt}]
        self.trace = []

    def ask(self, text: str) -> str:
        """Kullanıcı mesajını işler; asistan nihai cevaba ulaşana kadar araçları çalıştırır."""
        self.trace = []
        self.messages.append({"role": "user", "content": text})
        steps = 0
        last_signature: Optional[str] = None
        repeats = 0

        while True:
            if self.max_steps and steps >= self.max_steps:
                return self._stop(f"Adım limitine ulaşıldı ({self.max_steps}). Devam etmek için tekrar yaz.")

            message = self._call_llm()
            content = message.get("content") or ""
            if not isinstance(content, str):
                content = str(content)
            native_raw = message.get("tool_calls") or []
            calls = self._collect_calls(native_raw, content)

            if not calls:
                self.messages.append({"role": "assistant", "content": content})
                self._trim()
                return content.strip() or "(boş yanıt)"

            if native_raw and self.native_tools:
                self.messages.append({
                    "role": "assistant",
                    "content": content,
                    "tool_calls": [
                        {
                            "id": call.id,
                            "type": "function",
                            "function": {
                                "name": call.name,
                                "arguments": json.dumps(call.arguments, ensure_ascii=False),
                            },
                        }
                        for call in calls
                    ],
                })
            else:
                self.messages.append({"role": "assistant", "content": content})

            stop_reason: Optional[str] = None
            for call in calls:
                steps += 1
                signature = call.name + json.dumps(call.arguments, sort_keys=True, ensure_ascii=False)
                repeats = repeats + 1 if signature == last_signature else 0
                last_signature = signature

                if call.error:
                    result = call.error
                elif repeats >= REPEAT_LIMIT:
                    result = "Bu araç çağrısı üst üste tekrarlandığı için çalıştırılmadı. Farklı bir yaklaşım dene."
                    stop_reason = (
                        f"'{call.name}' aracı aynı argümanlarla {REPEAT_LIMIT + 1} kez çağrıldı; "
                        "döngü engellendi. Farklı bir yol deneyip tekrar yaz."
                    )
                else:
                    result = self.tools.execute(call.name, call.arguments, self.approve)

                self.trace.append({"tool": call.name, "args": call.arguments, "result": result[:500]})
                if call.native and self.native_tools:
                    self.messages.append({"role": "tool", "tool_call_id": call.id, "content": result})
                else:
                    self.messages.append({"role": "user", "content": f"Araç sonucu ({call.name}):\n{result}"})

            if stop_reason:
                return self._stop(stop_reason)
            self._trim()

    # ------------------------------------------------------------ iç yardımcılar

    def _collect_calls(self, native_raw: list[Any], content: str) -> list[ToolCall]:
        calls: list[ToolCall] = []
        for index, raw in enumerate(native_raw):
            function = raw.get("function", {}) if isinstance(raw, dict) else {}
            name = function.get("name", "")
            raw_args = function.get("arguments", {})
            error = None
            if isinstance(raw_args, str):
                try:
                    raw_args = json.loads(raw_args) if raw_args.strip() else {}
                except ValueError:
                    raw_args, error = {}, "Hata: araç argümanları geçerli JSON değil."
            if not isinstance(raw_args, dict):
                raw_args, error = {}, "Hata: araç argümanları bir nesne olmalı."
            call_id = raw.get("id") if isinstance(raw, dict) and raw.get("id") else f"call_{index}"
            calls.append(ToolCall(call_id, name, raw_args, native=True, error=error))
        if calls:
            return calls

        known = set(self.tools.names)
        for index, (name, arguments, error) in enumerate(parse_text_tool_calls(content, known)):
            calls.append(ToolCall(f"text_{index}", name, arguments, native=False, error=error))
        return calls

    def _call_llm(self) -> dict[str, Any]:
        self._trim()
        if self.native_tools:
            try:
                return self.llm.chat(self.messages, tools=self.tools.schemas())
            except LLMError as exc:
                if exc.status not in TOOL_FALLBACK_STATUSES:
                    raise
                # Model/sunucu yerel araç çağrısını desteklemiyor olabilir; metin modunu dene.
                self.native_tools = False
        messages = [self.messages[0], _tool_catalog_message(self.tools)] + self.messages[1:]
        return self.llm.chat(messages, tools=None)

    def _stop(self, text: str) -> str:
        if not self.messages or self.messages[-1]["role"] != "assistant":
            self.messages.append({"role": "assistant", "content": text})
        self._trim()
        return text

    def _trim(self) -> None:
        """Bağlam bütçesini aşarsa en eski turları atar; mevcut kullanıcı mesajı korunur."""

        def size(items: list[dict[str, Any]]) -> int:
            return sum(len(json.dumps(item, ensure_ascii=False)) for item in items)

        system, rest = self.messages[0], self.messages[1:]
        budget = self.context_chars - len(json.dumps(system, ensure_ascii=False))
        last_user = max((i for i, m in enumerate(rest) if m["role"] == "user"), default=0)
        while size(rest) > budget and last_user > 0:
            rest.pop(0)
            last_user -= 1
            # Geçmişin başı bir kullanıcı mesajına denk gelene kadar at (tool mesajı yetim kalmasın).
            while last_user > 0 and rest[0]["role"] != "user":
                rest.pop(0)
                last_user -= 1
        self.messages = [system] + rest
