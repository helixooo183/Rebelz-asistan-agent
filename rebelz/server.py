"""Telefon tarayıcısından kullanılan web arayüzü (sadece standart kütüphane)."""

from __future__ import annotations

import hmac
import json
import logging
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from .config import Settings
from .llm import LLMError
from .service import ChatService

STATIC_DIR = Path(__file__).parent / "static"
MAX_BODY = 64 * 1024
LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}
log = logging.getLogger("rebelz.web")


def is_loopback(host: str) -> bool:
    return host in LOOPBACK_HOSTS


def make_handler(service: ChatService, token: str | None) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        server_version = "Rebelz/0.1"

        def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
            log.info("%s - %s", self.address_string(), format % args)

        # ------------------------------------------------------------ yardımcılar
        def _send_json(self, status: int, payload: dict[str, Any]) -> None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _authorized(self) -> bool:
            if not token:
                return True
            header = self.headers.get("Authorization", "")
            supplied = header[7:] if header.startswith("Bearer ") else ""
            return hmac.compare_digest(supplied.encode(), token.encode())

        def _read_json(self) -> dict[str, Any] | None:
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                length = -1
            if length < 0 or length > MAX_BODY:
                self._send_json(413, {"error": "İstek çok büyük veya geçersiz."})
                return None
            try:
                data = json.loads(self.rfile.read(length) or b"{}")
            except ValueError:
                self._send_json(400, {"error": "Geçersiz JSON."})
                return None
            if not isinstance(data, dict):
                self._send_json(400, {"error": "JSON nesnesi bekleniyor."})
                return None
            return data

        # ------------------------------------------------------------ yöntemler
        def do_GET(self) -> None:  # noqa: N802
            path = urlparse(self.path).path
            if path in ("/", "/index.html"):
                body = (STATIC_DIR / "index.html").read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)
                return
            if path == "/api/health":
                self._send_json(200, {
                    "ok": True,
                    "model": service.settings.model,
                    "autonomy": service.settings.autonomy,
                    "auth_required": bool(token),
                })
                return
            self._send_json(404, {"error": "Bulunamadı."})

        def do_POST(self) -> None:  # noqa: N802
            path = urlparse(self.path).path
            if path not in ("/api/chat", "/api/reset"):
                self._send_json(404, {"error": "Bulunamadı."})
                return
            if not self._authorized():
                self._send_json(401, {"error": "Yetkisiz: erişim anahtarı gerekli."})
                return
            data = self._read_json()
            if data is None:
                return
            session = str(data.get("session") or "default")[:100]

            if path == "/api/reset":
                service.reset(session)
                self._send_json(200, {"ok": True})
                return

            message = data.get("message")
            if not isinstance(message, str) or not message.strip():
                self._send_json(400, {"error": "'message' boş olamaz."})
                return
            try:
                result = service.chat(session, message.strip())
            except LLMError as exc:
                self._send_json(502, {"error": str(exc)})
                return
            except Exception as exc:  # Beklenmeyen hata: sunucu ayakta kalsın.
                log.exception("chat failed")
                self._send_json(500, {"error": f"Beklenmeyen hata: {type(exc).__name__}: {exc}"})
                return
            self._send_json(200, result)

    return Handler


def create_server(settings: Settings, service: ChatService | None = None) -> ThreadingHTTPServer:
    if not is_loopback(settings.web_host) and not settings.web_token:
        raise SystemExit(
            "Güvenlik: sunucu yerel ağa açılıyorsa REBELZ_WEB_TOKEN ayarlamak zorunludur."
        )
    service = service or ChatService(settings)
    handler = make_handler(service, settings.web_token)
    server = ThreadingHTTPServer((settings.web_host, settings.web_port), handler)
    server.daemon_threads = True
    return server


def run_web(settings: Settings) -> int:
    logging.basicConfig(level=logging.WARNING)
    server = create_server(settings)
    host, port = server.server_address[:2]
    print(f"Rebelz web arayüzü: http://{host}:{port}")
    print("Telefonda bu adresi tarayıcıda aç. Durdurmak için Ctrl+C.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0
