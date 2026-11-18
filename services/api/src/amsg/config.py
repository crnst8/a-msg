"""Service configuration: flags and environment over OS defaults. Nothing is written to the app bundle."""

from __future__ import annotations

import json
import os
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path


def default_data_dir() -> Path:
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "a-msg"
    xdg = os.environ.get("XDG_DATA_HOME")
    return (Path(xdg) if xdg else Path.home() / ".local" / "share") / "amsg"


def default_log_dir(data_dir: Path) -> Path:
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Logs" / "a-msg"
    return data_dir / "logs"


@dataclass(slots=True)
class Settings:
    data_dir: Path = field(default_factory=default_data_dir)
    log_dir: Path | None = None
    host: str = "127.0.0.1"
    port: int = 8710
    # "hermes" routes through the plugin; "fake" enables the labelled development provider.
    provider: str = "hermes"
    dev_mode: bool = False
    public_url: str | None = None
    upload_limit: int = 100 * 1024 * 1024
    json_limit: int = 2 * 1024 * 1024
    max_concurrent_runs: int = 2
    event_retention_days: int = 7
    heartbeat_seconds: float = 15.0
    delta_flush_seconds: float = 0.1
    connector_lease_seconds: int = 900
    context_budget_chars: int = 8000
    workspace_roots: list[Path] = field(default_factory=list)
    # Browser access. web_origin (for example https://ag.example.net) turns on password sign-in and session
    # cookies; web_listen adds a second listener ("address:port") for the HTTPS proxy in front of it.
    web_origin: str | None = None
    web_listen: str | None = None
    web_root: Path | None = None
    # Proxies whose X-Forwarded-For is believed. Requests from anywhere else are judged by their own address.
    trusted_proxies: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.data_dir = Path(self.data_dir).expanduser()
        if self.log_dir is None:
            self.log_dir = default_log_dir(self.data_dir)
        self.log_dir = Path(self.log_dir).expanduser()
        self.workspace_roots = [Path(p).expanduser() for p in self.workspace_roots]
        if self.web_origin:
            self.web_origin = self.web_origin.rstrip("/")
        if self.web_root is not None:
            self.web_root = Path(self.web_root).expanduser()

    @property
    def web_enabled(self) -> bool:
        return bool(self.web_origin)

    @property
    def web_files(self) -> Path | None:
        """The built interface: web_root, or `web` beside the packaged executable."""
        if self.web_root is not None:
            return self.web_root if (self.web_root / "index.html").is_file() else None
        if getattr(sys, "frozen", False):
            bundled = Path(sys.executable).resolve().parent / "web"
            if (bundled / "index.html").is_file():
                return bundled
        return None

    @property
    def db_path(self) -> Path:
        return self.data_dir / "amsg.db"

    @property
    def object_dir(self) -> Path:
        return self.data_dir / "objects"

    @property
    def tmp_dir(self) -> Path:
        return self.data_dir / "tmp"

    @property
    def backup_dir(self) -> Path:
        return self.data_dir / "backups"

    @property
    def config_file(self) -> Path:
        return self.data_dir / "service.json"

    def ensure_dirs(self) -> None:
        for path in (self.data_dir, self.object_dir, self.tmp_dir, self.backup_dir, self.log_dir):
            path.mkdir(parents=True, exist_ok=True, mode=0o700)

    def save(self) -> None:
        self.ensure_dirs()
        keys = (
            "host",
            "port",
            "provider",
            "public_url",
            "upload_limit",
            "workspace_roots",
            "web_origin",
            "web_listen",
            "web_root",
            "trusted_proxies",
        )
        values = {k: v for k, v in asdict(self).items() if k in keys}
        values["workspace_roots"] = [str(p) for p in self.workspace_roots]
        values["web_root"] = str(self.web_root) if self.web_root else None
        self.config_file.write_text(json.dumps(values, indent=2) + "\n")
        self.config_file.chmod(0o600)


def load_settings(**overrides: object) -> Settings:
    """Precedence: explicit overrides, AMSG_* environment, service.json, defaults."""
    overrides = {k: v for k, v in overrides.items() if v is not None}
    env = os.environ
    data_dir = Path(str(overrides.get("data_dir") or env.get("AMSG_DATA_DIR") or default_data_dir()))
    values: dict[str, object] = {}
    file = data_dir / "service.json"
    if file.exists():
        values.update(json.loads(file.read_text()))
    mapping = {
        "AMSG_HOST": ("host", str),
        "AMSG_PORT": ("port", int),
        "AMSG_PROVIDER": ("provider", str),
        "AMSG_PUBLIC_URL": ("public_url", str),
        "AMSG_UPLOAD_LIMIT": ("upload_limit", int),
        "AMSG_WEB_ORIGIN": ("web_origin", str),
        "AMSG_WEB_LISTEN": ("web_listen", str),
        "AMSG_WEB_ROOT": ("web_root", str),
    }
    for key, (name, cast) in mapping.items():
        if key in env:
            values[name] = cast(env[key])
    if env.get("AMSG_WORKSPACE_ROOTS"):
        values["workspace_roots"] = env["AMSG_WORKSPACE_ROOTS"].split(os.pathsep)
    if env.get("AMSG_TRUSTED_PROXIES"):
        values["trusted_proxies"] = [p.strip() for p in env["AMSG_TRUSTED_PROXIES"].split(",") if p.strip()]
    values.update(overrides)
    values["data_dir"] = data_dir
    return Settings(**values)  # type: ignore[arg-type]
