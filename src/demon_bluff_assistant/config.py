from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    openai_api_key: str | None = field(default=None, repr=False)
    openai_model: str = "gpt-5.6-terra"
    debug_save_screenshots: bool = False
    hotkey: str = "Ctrl+Shift+D"
    data_dir: Path = Path("data")
    solver_world_limit: int = 5_000

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            openai_api_key=os.getenv("OPENAI_API_KEY") or None,
            openai_model=os.getenv("OPENAI_MODEL", "gpt-5.6-terra"),
            debug_save_screenshots=_env_bool("DEBUG_SAVE_SCREENSHOTS"),
            data_dir=Path(os.getenv("DEMON_BLUFF_DATA_DIR", "data")),
            solver_world_limit=int(os.getenv("SOLVER_WORLD_LIMIT", "5000")),
        )

    def public_dict(self) -> dict[str, object]:
        return {
            "model": self.openai_model,
            "openai_configured": bool(self.openai_api_key),
            "debug_save_screenshots": self.debug_save_screenshots,
            "hotkey": self.hotkey,
        }

