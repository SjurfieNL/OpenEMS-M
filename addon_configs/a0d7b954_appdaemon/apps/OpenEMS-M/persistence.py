"""Gedeelde robuuste JSON-opslag voor OpenEMS-M."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path


class JsonStore:
    """Leest en schrijft JSON atomair en bewaart beschadigde brondata."""

    def __init__(self, path, *, app=None, timezone=None, label="Gegevens"):
        self.path = Path(path)
        self.app = app
        self.timezone = timezone
        self.label = label

    def load(self, default_factory):
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(value, dict):
                return value
            raise ValueError("hoofdniveau is geen JSON-object")
        except FileNotFoundError:
            return default_factory()
        except (OSError, ValueError, TypeError) as error:
            self._backup_corrupt(error)
            return default_factory()

    def save(self, value):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary.write_text(
            json.dumps(value, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        temporary.replace(self.path)

    def _backup_corrupt(self, error):
        if not self.path.exists():
            return
        now = datetime.now(self.timezone) if self.timezone else datetime.now()
        backup = self.path.with_name(
            f"{self.path.stem}.corrupt-{now:%Y%m%d-%H%M%S}"
            f"{self.path.suffix}"
        )
        try:
            self.path.replace(backup)
            message = (
                f"{self.label} was onleesbaar en is bewaard als "
                f"{backup.name}: {error}"
            )
        except OSError as backup_error:
            message = (
                f"{self.label} is onleesbaar; back-up maken mislukte: "
                f"{backup_error}"
            )
        if self.app is not None and hasattr(self.app, "log"):
            self.app.log(f"[Opslag] {message}", level="WARNING")
