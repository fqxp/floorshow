"""Controller-side access to config.yaml (the controller is the writer; the renderer hot-reloads)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..common import paths
from ..common.config import Config, ConfigError, load_config, update_config


class ConfigStore:
    def __init__(self, path: Path | None = None):
        self.path = Path(path) if path else paths.config_file()

    def load(self) -> Config:
        try:
            return load_config(self.path)
        except ConfigError:
            return Config()

    def update(self, updates: dict[str, Any]) -> None:
        update_config(self.path, updates)
