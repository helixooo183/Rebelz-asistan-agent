"""Terminal (Termux) arayüzü."""

from __future__ import annotations

import sys

from .config import Settings
from .llm import LLMError
from .service import make_agent, make_approver, make_registry

HELP = """Komutlar:
  /yardim   bu yardım metni
  /araclar  kullanılabilir araçlar
  /sifirla  sohbet geçmişini temizle (kalıcı notlar silinmez)
  /cik      çıkış
"""


def _interactive_approve(name: str, arguments: dict) -> bool:
    print(f"\n⚠️  Onay isteniyor: {name} {arguments}")
    try:
        answer = input("Çalıştırılsın mı? [e/H] ").strip().lower()
    except EOFError:
        return False
    return answer in ("e", "evet", "y", "yes")


def run_cli(settings: Settings) -> int:
    approve = make_approver(settings, interactive=_interactive_approve)
    registry = make_registry(settings)
    agent = make_agent(settings, approve, registry)

    print(f"Rebelz hazır. Model: {settings.base_url} | Otonomi: {settings.autonomy}")
    print(f"Çalışma alanı: {settings.workspace}")
    print("Yardım için /yardim yaz.\n")

    while True:
        try:
            line = input("sen> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if not line:
            continue
        if line in ("/cik", "/çık", "/exit", "/quit"):
            return 0
        if line in ("/yardim", "/yardım", "/help"):
            print(HELP)
            continue
        if line in ("/araclar", "/araçlar", "/tools"):
            print(registry.describe())
            continue
        if line in ("/sifirla", "/sıfırla", "/reset"):
            agent.reset()
            print("Sohbet sıfırlandı.")
            continue

        try:
            reply = agent.ask(line)
        except KeyboardInterrupt:
            print("\n(işlem iptal edildi)")
            continue
        except LLMError as exc:
            print(f"Hata: {exc}", file=sys.stderr)
            continue
        print(f"\nrebelz> {reply}\n")
