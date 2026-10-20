"""Asistanın kullanabileceği araçlar.

Her araç bir Python fonksiyonudur; şeması OpenAI 'function' biçiminde üretilir.
Onay gerektiren araçlar (kabuk, dosya yazma) `approve` geri çağrısından geçer.
"""

from __future__ import annotations

import html
import json
import re
import subprocess
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Optional

MAX_OUTPUT_CHARS = 8000
HTTP_USER_AGENT = "Mozilla/5.0 (Linux; Android) RebelzAgent/0.1"
Approve = Callable[[str, dict[str, Any]], bool]


def truncate(text: str, limit: int = MAX_OUTPUT_CHARS) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n...[{len(text) - limit} karakter kesildi]"


def resolve_in_workspace(workspace: Path, path: str) -> Path:
    """Yolu çözer; çalışma alanı dışına çıkan yolları reddeder."""
    root = workspace.resolve()
    candidate = (root / path).expanduser().resolve()
    if candidate != root and not candidate.is_relative_to(root):
        raise ValueError("Çalışma alanı dışındaki yollara erişilemez.")
    return candidate


@dataclass
class Tool:
    name: str
    description: str
    parameters: dict[str, Any]
    handler: Callable[..., str]
    needs_approval: bool = False

    def schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


class ToolRegistry:
    def __init__(self, tools: list[Tool]):
        self._tools = {tool.name: tool for tool in tools}

    @property
    def names(self) -> list[str]:
        return list(self._tools)

    def get(self, name: str) -> Optional[Tool]:
        return self._tools.get(name)

    def schemas(self) -> list[dict[str, Any]]:
        return [tool.schema() for tool in self._tools.values()]

    def describe(self) -> str:
        lines = []
        for tool in self._tools.values():
            props = tool.parameters.get("properties", {})
            required = set(tool.parameters.get("required", []))
            params = ", ".join(
                f"{key}{'' if key in required else '?'}: {spec.get('type', 'any')}"
                for key, spec in props.items()
            )
            lines.append(f"- {tool.name}({params}): {tool.description}")
        return "\n".join(lines)

    def execute(self, name: str, arguments: dict[str, Any], approve: Approve) -> str:
        tool = self._tools.get(name)
        if tool is None:
            return f"Hata: '{name}' adlı araç yok. Mevcut araçlar: {', '.join(self.names)}"
        if tool.needs_approval and not approve(name, arguments):
            return "İşlem onaylanmadı, çalıştırılmadı."
        try:
            result = tool.handler(**arguments)
        except TypeError as exc:
            return f"Hata: '{name}' için geçersiz argümanlar: {exc}"
        except Exception as exc:  # Araç hataları modele geri bildirilir, süreç çökmez.
            return f"Hata: {type(exc).__name__}: {exc}"
        return truncate(str(result))


# ---------------------------------------------------------------- yardımcılar

_SCRIPT_STYLE = re.compile(r"<(script|style|noscript)\b.*?</\1>", re.IGNORECASE | re.DOTALL)
_TAG = re.compile(r"<[^>]+>")
_SPACE = re.compile(r"[ \t\r\f\v]+")
_BLANK_LINES = re.compile(r"\n\s*\n\s*\n+")


def html_to_text(markup: str) -> str:
    text = _SCRIPT_STYLE.sub(" ", markup)
    text = re.sub(r"<(br|/p|/div|/h[1-6]|/li|/tr)\b[^>]*>", "\n", text, flags=re.IGNORECASE)
    text = _TAG.sub(" ", text)
    text = html.unescape(text)
    text = _SPACE.sub(" ", text)
    text = re.sub(r" *\n *", "\n", text)
    return _BLANK_LINES.sub("\n\n", text).strip()


def _http_get(url: str, timeout: float = 20.0, limit: int = 2_000_000) -> tuple[str, str]:
    request = urllib.request.Request(url, headers={"User-Agent": HTTP_USER_AGENT})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        content_type = response.headers.get("Content-Type", "")
        charset = response.headers.get_content_charset() or "utf-8"
        raw = response.read(limit)
    return raw.decode(charset, errors="replace"), content_type


def parse_duckduckgo_html(markup: str, max_results: int = 5) -> list[dict[str, str]]:
    links = re.findall(
        r'<a[^>]*class="[^"]*result__a[^"]*"[^>]*href="([^"]+)"[^>]*>(.*?)</a>', markup, re.DOTALL
    )
    snippets = re.findall(r'class="[^"]*result__snippet[^"]*"[^>]*>(.*?)</a>', markup, re.DOTALL)
    results = []
    for index, (href, title_html) in enumerate(links[:max_results]):
        url = href
        if url.startswith("//"):
            url = "https:" + url
        parsed = urllib.parse.urlparse(url)
        target = urllib.parse.parse_qs(parsed.query).get("uddg")
        if target:
            url = target[0]
        snippet = html_to_text(snippets[index]) if index < len(snippets) else ""
        results.append({"title": html_to_text(title_html), "url": url, "snippet": snippet})
    return results


# ---------------------------------------------------------------- araç fabrikası

def build_default_tools(workspace: Path, shell_timeout_max: int = 1800) -> list[Tool]:
    workspace = workspace.expanduser()
    workspace.mkdir(parents=True, exist_ok=True)
    memory_file = workspace / ".rebelz" / "memory.md"

    def run_shell(command: str, timeout: int = 120) -> str:
        seconds = max(1, min(int(timeout), shell_timeout_max))
        try:
            proc = subprocess.run(
                command,
                shell=True,
                cwd=workspace,
                capture_output=True,
                text=True,
                errors="replace",
                timeout=seconds,
            )
        except subprocess.TimeoutExpired as exc:
            partial = exc.stdout.decode(errors="replace") if isinstance(exc.stdout, bytes) else (exc.stdout or "")
            return f"Zaman aşımı ({seconds} sn). Kısmi çıktı:\n{partial}"
        output = proc.stdout
        if proc.stderr:
            output += ("\n" if output else "") + "[stderr]\n" + proc.stderr
        return f"[çıkış kodu {proc.returncode}]\n{output or '(çıktı yok)'}"

    def read_file(path: str) -> str:
        target = resolve_in_workspace(workspace, path)
        if not target.is_file():
            return f"Hata: dosya bulunamadı: {path}"
        return target.read_text(encoding="utf-8", errors="replace")

    def write_file(path: str, content: str) -> str:
        target = resolve_in_workspace(workspace, path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return f"Yazıldı: {target.relative_to(workspace.resolve())} ({len(content)} karakter)"

    def list_dir(path: str = ".") -> str:
        target = resolve_in_workspace(workspace, path)
        if not target.is_dir():
            return f"Hata: klasör bulunamadı: {path}"
        entries = sorted(target.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
        lines = [f"{entry.name}/" if entry.is_dir() else entry.name for entry in entries[:200]]
        return "\n".join(lines) or "(boş)"

    def web_fetch(url: str) -> str:
        parsed = urllib.parse.urlparse(url)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            raise ValueError("Yalnızca http/https adresleri açılabilir.")
        body, content_type = _http_get(url)
        if "html" in content_type or body[:500].lower().lstrip().startswith(("<!doctype", "<html")):
            return html_to_text(body)
        return body

    def web_search(query: str, max_results: int = 5) -> str:
        count = max(1, min(int(max_results), 10))
        url = "https://html.duckduckgo.com/html/?" + urllib.parse.urlencode({"q": query})
        try:
            body, _ = _http_get(url)
        except urllib.error.URLError as exc:
            return f"Arama yapılamadı: {exc}"
        results = parse_duckduckgo_html(body, count)
        if not results:
            return "Sonuç bulunamadı."
        return "\n\n".join(
            f"{i}. {r['title']}\n   {r['url']}\n   {r['snippet']}" for i, r in enumerate(results, 1)
        )

    def now() -> str:
        return datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")

    def remember(note: str) -> str:
        memory_file.parent.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
        with memory_file.open("a", encoding="utf-8") as handle:
            handle.write(f"- [{stamp}] {note.strip()}\n")
        return "Not kaydedildi."

    def recall(query: str = "") -> str:
        if not memory_file.exists():
            return "Henüz kayıtlı not yok."
        lines = memory_file.read_text(encoding="utf-8").splitlines()
        if query.strip():
            needle = query.strip().lower()
            lines = [line for line in lines if needle in line.lower()]
        return "\n".join(lines[-100:]) or "Eşleşen not yok."

    string = {"type": "string"}
    integer = {"type": "integer"}
    return [
        Tool(
            "shell",
            "Çalışma alanında bir kabuk (sh) komutu çalıştırır ve çıktısını döndürür.",
            {"type": "object", "properties": {"command": string, "timeout": integer}, "required": ["command"]},
            run_shell,
            needs_approval=True,
        ),
        Tool(
            "read_file",
            "Çalışma alanındaki bir metin dosyasını okur.",
            {"type": "object", "properties": {"path": string}, "required": ["path"]},
            read_file,
        ),
        Tool(
            "write_file",
            "Çalışma alanında bir dosyayı yazar veya üzerine yazar.",
            {"type": "object", "properties": {"path": string, "content": string}, "required": ["path", "content"]},
            write_file,
            needs_approval=True,
        ),
        Tool(
            "list_dir",
            "Çalışma alanındaki bir klasörün içeriğini listeler.",
            {"type": "object", "properties": {"path": string}},
            list_dir,
        ),
        Tool(
            "web_search",
            "İnternette arama yapar ve ilk sonuçları (başlık, adres, özet) döndürür.",
            {"type": "object", "properties": {"query": string, "max_results": integer}, "required": ["query"]},
            web_search,
        ),
        Tool(
            "web_fetch",
            "Bir web sayfasını açar ve metin içeriğini döndürür.",
            {"type": "object", "properties": {"url": string}, "required": ["url"]},
            web_fetch,
        ),
        Tool(
            "now",
            "Telefondaki mevcut tarih ve saati döndürür.",
            {"type": "object", "properties": {}},
            now,
        ),
        Tool(
            "remember",
            "Kalıcı bir not kaydeder (sonraki oturumlarda da hatırlanır).",
            {"type": "object", "properties": {"note": string}, "required": ["note"]},
            remember,
        ),
        Tool(
            "recall",
            "Kayıtlı notlarda arama yapar; query boşsa son notları döndürür.",
            {"type": "object", "properties": {"query": string}},
            recall,
        ),
    ]
