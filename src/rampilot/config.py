"""Persistent user settings.

Settings live in $XDG_CONFIG_HOME/rampilot/config.json and are validated on
load.  A malformed or unreadable file never prevents the app from starting.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace
import json
import os
from pathlib import Path
import tempfile


def config_dir() -> Path:
    root = os.environ.get("XDG_CONFIG_HOME")
    if root:
        return Path(root) / "rampilot"
    return Path.home() / ".config" / "rampilot"


@dataclass(frozen=True, slots=True)
class Config:
    update_interval: int = 1  # seconds
    process_interval: int = 2  # seconds
    warning_threshold: int = 75  # percent of RAM in use
    critical_threshold: int = 90
    notifications: bool = True
    keep_in_background: bool = False
    show_tray: bool = True

    def validated(self) -> "Config":
        interval = self.update_interval
        if interval not in (1, 2, 5):
            interval = min((1, 2, 5), key=lambda value: abs(value - interval))
        process_interval = self.process_interval
        if process_interval not in (2, 3, 5):
            process_interval = min((2, 3, 5), key=lambda value: abs(value - process_interval))
        warning = max(40, min(95, int(self.warning_threshold)))
        critical = max(warning + 1, min(99, int(self.critical_threshold)))
        return replace(
            self,
            update_interval=interval,
            process_interval=process_interval,
            warning_threshold=warning,
            critical_threshold=critical,
            notifications=bool(self.notifications),
            keep_in_background=bool(self.keep_in_background),
            show_tray=bool(self.show_tray),
        )


class ConfigStore:
    def __init__(self, path: Path | None = None):
        self.path = path or (config_dir() / "config.json")
        self.config = Config()
        self.last_error: str | None = None

    def load(self) -> Config:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                raise ValueError("settings root is not an object")
            allowed = set(Config.__dataclass_fields__)
            values = {key: value for key, value in raw.items() if key in allowed}
            self.config = Config(**values).validated()
        except FileNotFoundError:
            self.config = Config()
        except (OSError, ValueError, TypeError) as exc:
            self.last_error = f"Could not load settings: {exc}. Using defaults."
            self.config = Config()
        return self.config

    def save(self, config: Config | None = None) -> bool:
        if config is not None:
            self.config = config.validated()
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            payload = json.dumps(asdict(self.config), indent=2) + "\n"
            fd, tmp_name = tempfile.mkstemp(
                prefix=".config-", suffix=".json", dir=str(self.path.parent)
            )
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as handle:
                    handle.write(payload)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(tmp_name, self.path)
                os.chmod(self.path, 0o600)
            except BaseException:
                try:
                    os.unlink(tmp_name)
                except OSError:
                    pass
                raise
            self.last_error = None
            return True
        except OSError as exc:
            self.last_error = f"Could not save settings: {exc}"
            return False
