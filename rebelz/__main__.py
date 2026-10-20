"""Komut satırı girişi: python -m rebelz [cli|web] [--host H] [--port P]"""

from __future__ import annotations

import argparse
import dataclasses
import sys

from .cli import run_cli
from .config import Settings
from .server import run_web


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="rebelz", description="Telefonunda çalışan kotasız otonom asistan.")
    parser.add_argument("mode", nargs="?", choices=["cli", "web"], default="cli",
                        help="cli: terminal sohbeti, web: telefon tarayıcısı arayüzü (varsayılan: cli)")
    parser.add_argument("--host", help="web arayüzü adresi (varsayılan: REBELZ_WEB_HOST veya 127.0.0.1)")
    parser.add_argument("--port", type=int, help="web arayüzü portu (varsayılan: REBELZ_WEB_PORT veya 8787)")
    args = parser.parse_args(argv)

    try:
        settings = Settings.from_env()
    except ValueError as exc:
        print(f"Ayar hatası: {exc}", file=sys.stderr)
        return 2

    overrides = {}
    if args.host:
        overrides["web_host"] = args.host
    if args.port:
        overrides["web_port"] = args.port
    if overrides:
        settings = dataclasses.replace(settings, **overrides)

    if args.mode == "web":
        return run_web(settings)
    return run_cli(settings)


if __name__ == "__main__":
    raise SystemExit(main())
