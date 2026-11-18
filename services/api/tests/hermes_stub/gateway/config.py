from dataclasses import dataclass, field
from typing import Any


class Platform(str):
    pass


@dataclass
class PlatformConfig:
    extra: dict[str, Any] = field(default_factory=dict)
