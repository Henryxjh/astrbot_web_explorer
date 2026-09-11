from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any


def _number(config: Mapping[str, Any], key: str, default: float) -> float:
    try:
        return float(config.get(key, default))
    except (TypeError, ValueError):
        return default


def _integer(config: Mapping[str, Any], key: str, default: int) -> int:
    try:
        return int(config.get(key, default))
    except (TypeError, ValueError):
        return default


def _strings(value: Any) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    return tuple(str(item).strip().lower() for item in value if str(item).strip())


@dataclass(frozen=True, slots=True)
class ExplorerSettings:
    github_token: str = ""
    request_timeout: float = 20.0
    max_download_bytes: int = 2 * 1024 * 1024
    max_content_chars: int = 30_000
    max_links: int = 100
    max_redirects: int = 5
    search_limit: int = 10
    cache_ttl_seconds: float = 1800.0
    cache_max_pages: int = 100
    allow_private_network: bool = False
    user_agent: str = "AstrBot-Web-Explorer/0.1"
    site_rules_path: Path | None = None
    chawan_enabled: bool = False
    chawan_executable: str = "cha"
    chawan_timeout: float = 20.0
    chawan_scripting: bool = False
    chawan_allowed_hosts: tuple[str, ...] = ()

    @classmethod
    def from_config(
        cls,
        config: Mapping[str, Any],
        data_dir: Path,
    ) -> ExplorerSettings:
        configured_rules_path = str(config.get("site_rules_path", "")).strip()
        rules_path = (
            Path(configured_rules_path).expanduser()
            if configured_rules_path
            else data_dir / "site_rules.json"
        )
        return cls(
            github_token=str(config.get("github_token", "")).strip(),
            request_timeout=max(1.0, _number(config, "request_timeout", 20.0)),
            max_download_bytes=max(
                16_384, _integer(config, "max_download_bytes", 2 * 1024 * 1024)
            ),
            max_content_chars=max(1_000, _integer(config, "max_content_chars", 30_000)),
            max_links=max(1, _integer(config, "max_links", 100)),
            max_redirects=max(0, _integer(config, "max_redirects", 5)),
            search_limit=min(50, max(1, _integer(config, "search_limit", 10))),
            cache_ttl_seconds=max(60.0, _number(config, "cache_ttl_seconds", 1800.0)),
            cache_max_pages=max(5, _integer(config, "cache_max_pages", 100)),
            allow_private_network=bool(config.get("allow_private_network", False)),
            user_agent=str(config.get("user_agent", "AstrBot-Web-Explorer/0.1")).strip()
            or "AstrBot-Web-Explorer/0.1",
            site_rules_path=rules_path,
            chawan_enabled=bool(config.get("chawan_enabled", False)),
            chawan_executable=str(config.get("chawan_executable", "cha")).strip()
            or "cha",
            chawan_timeout=max(1.0, _number(config, "chawan_timeout", 20.0)),
            chawan_scripting=bool(config.get("chawan_scripting", False)),
            chawan_allowed_hosts=_strings(config.get("chawan_allowed_hosts", [])),
        )
