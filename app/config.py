"""Runtime configuration loaded from environment variables."""
from __future__ import annotations

import os


class Settings:
    def __init__(self) -> None:
        # SQLAlchemy URL, e.g. postgresql+psycopg2://radio:radio@db:5432/radio
        self.database_url: str = os.getenv(
            "DATABASE_URL",
            "postgresql+psycopg2://radio:radio@localhost:5432/radio",
        )
        # Non-negative guard band, in the same unit as the frequency numbers
        # (the API itself is unit-agnostic; use MHz, kHz, ... consistently).
        guard_raw = os.getenv("GUARD_BAND", "0")
        self.guard_band: float = float(guard_raw)
        if self.guard_band < 0:
            raise ValueError(f"GUARD_BAND must be non-negative, got {guard_raw}")

        self.app_host: str = os.getenv("APP_HOST", "0.0.0.0")
        self.app_port: int = int(os.getenv("APP_PORT", "8000"))


settings = Settings()
