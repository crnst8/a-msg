from dataclasses import dataclass, field
from typing import Any


@dataclass
class PlatformEntry:
    name: str = ""
    label: str = ""
    adapter_factory: Any = None
    check_fn: Any = None
    validate_config: Any = None
    is_connected: Any = None
    required_env: list = field(default_factory=list)
    install_hint: str = ""
    allowed_users_env: str = ""
    max_message_length: int = 0
    pii_safe: bool = False
    emoji: str = ""
    platform_hint: str = ""
    env_enablement_fn: Any = None
    cron_deliver_env_var: str = ""
    parse_target_ref_fn: Any = None
    standalone_sender_fn: Any = None
    display_tier: str = ""
    display_overrides: dict = field(default_factory=dict)
