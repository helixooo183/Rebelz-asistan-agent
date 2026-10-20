"""Ayarlar. Tüm değerler ortam değişkenlerinden okunur (REBELZ_*)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping, Optional

DEFAULT_BASE_URL = "http://127.0.0.1:8080/v1"
AUTONOMY_MODES = ("confirm", "full")


def _int(env: Mapping[str, str], key: str, default: int) -> int:
    raw = env.get(key)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ValueError(f"{key} bir tam sayı olmalı, alınan: {raw!r}") from exc


def _float(env: Mapping[str, str], key: str, default: float) -> float:
    raw = env.get(key)
    if raw is None or raw.strip() == "":
        return default
    try:
        return float(raw)
    except ValueError as exc:
        raise ValueError(f"{key} bir sayı olmalı, alınan: {raw!r}") from exc


@dataclass(frozen=True)
class Settings:
    # Yerel OpenAI uyumlu sunucu (llama.cpp llama-server). Dışarıdan kota yok.
    base_url: str = DEFAULT_BASE_URL
    model: str = "local"
    api_key: str = "local"
    llm_timeout: float = 900.0  # Telefonda yanıt yavaş olabilir.

    # Çalışma alanı: asistanın dosya işlemleri bu klasörle sınırlıdır.
    workspace: Path = field(default_factory=lambda: Path.home() / "rebelz-workspace")

    # confirm: yıkıcı işlemler (kabuk, dosya yazma) için onay sorulur.
    # full: hiçbir onay sorulmaz, asistan tamamen otonom çalışır.
    autonomy: str = "confirm"

    # 0 = sınırsız. Aynı araç çağrısının üst üste tekrarı ayrıca engellenir.
    max_steps: int = 0

    # Modelin bağlam penceresine sığmak için geçmiş mesajların karakter bütçesi.
    context_chars: int = 24000

    # Web arayüzü
    web_host: str = "127.0.0.1"
    web_port: int = 8787
    web_token: Optional[str] = None

    @classmethod
    def from_env(cls, environ: Optional[Mapping[str, str]] = None) -> "Settings":
        env = os.environ if environ is None else environ

        autonomy = env.get("REBELZ_AUTONOMY", "confirm").strip().lower()
        if autonomy not in AUTONOMY_MODES:
            raise ValueError(f"REBELZ_AUTONOMY 'confirm' veya 'full' olmalı, alınan: {autonomy!r}")

        workspace_raw = env.get("REBELZ_WORKSPACE", "").strip()
        workspace = Path(workspace_raw).expanduser() if workspace_raw else Path.home() / "rebelz-workspace"

        token = env.get("REBELZ_WEB_TOKEN", "").strip() or None

        return cls(
            base_url=env.get("REBELZ_BASE_URL", DEFAULT_BASE_URL).strip().rstrip("/"),
            model=env.get("REBELZ_MODEL", "local").strip() or "local",
            api_key=env.get("REBELZ_API_KEY", "local").strip() or "local",
            llm_timeout=_float(env, "REBELZ_LLM_TIMEOUT", 900.0),
            workspace=workspace,
            autonomy=autonomy,
            max_steps=_int(env, "REBELZ_MAX_STEPS", 0),
            context_chars=_int(env, "REBELZ_CONTEXT_CHARS", 24000),
            web_host=env.get("REBELZ_WEB_HOST", "127.0.0.1").strip() or "127.0.0.1",
            web_port=_int(env, "REBELZ_WEB_PORT", 8787),
            web_token=token,
        )
