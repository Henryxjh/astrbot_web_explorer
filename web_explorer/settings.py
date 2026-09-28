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
    user_agent: str = "AstrBot-Web-Explorer/0.5"
    site_rules_path: Path | None = None
    chawan_enabled: bool = False
    chawan_executable: str = "cha"
    chawan_timeout: float = 20.0
    chawan_scripting: bool = False
    chawan_allowed_hosts: tuple[str, ...] = ()
    screenshot_enabled: bool = False
    screenshot_webdriver_path: str = "/usr/lib/qt6/webenginedriver"
    screenshot_browser_path: str = ""
    screenshot_display: str = ""
    screenshot_xvfb_path: str = "Xvfb"
    screenshot_allowed_hosts: tuple[str, ...] = ()
    screenshot_default_width: int = 1280
    screenshot_default_max_height: int = 8000
    screenshot_max_pixels: int = 12_000_000
    screenshot_max_png_bytes: int = 20 * 1024 * 1024
    screenshot_page_load_timeout: float = 30.0
    screenshot_settle_timeout: float = 3.0
    screenshot_queue_limit: int = 5

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
            user_agent=str(config.get("user_agent", "AstrBot-Web-Explorer/0.5")).strip()
            or "AstrBot-Web-Explorer/0.5",
            site_rules_path=rules_path,
            chawan_enabled=bool(config.get("chawan_enabled", False)),
            chawan_executable=str(config.get("chawan_executable", "cha")).strip()
            or "cha",
            chawan_timeout=max(1.0, _number(config, "chawan_timeout", 20.0)),
            chawan_scripting=bool(config.get("chawan_scripting", False)),
            chawan_allowed_hosts=_strings(config.get("chawan_allowed_hosts", [])),
            screenshot_enabled=bool(config.get("screenshot_enabled", False)),
            screenshot_webdriver_path=str(
                config.get("screenshot_webdriver_path", "/usr/lib/qt6/webenginedriver")
            ).strip()
            or "/usr/lib/qt6/webenginedriver",
            screenshot_browser_path=str(
                config.get("screenshot_browser_path", "")
            ).strip(),
            screenshot_display=str(config.get("screenshot_display", "")).strip(),
            screenshot_xvfb_path=str(config.get("screenshot_xvfb_path", "Xvfb")).strip()
            or "Xvfb",
            screenshot_allowed_hosts=_strings(
                config.get("screenshot_allowed_hosts", [])
            ),
            screenshot_default_width=min(
                2560,
                max(320, _integer(config, "screenshot_default_width", 1280)),
            ),
            screenshot_default_max_height=min(
                16_000,
                max(
                    240,
                    _integer(config, "screenshot_default_max_height", 8000),
                ),
            ),
            screenshot_max_pixels=max(
                800 * 800,
                _integer(config, "screenshot_max_pixels", 12_000_000),
            ),
            screenshot_max_png_bytes=max(
                1024 * 1024,
                _integer(
                    config,
                    "screenshot_max_png_bytes",
                    20 * 1024 * 1024,
                ),
            ),
            screenshot_page_load_timeout=max(
                5.0,
                _number(config, "screenshot_page_load_timeout", 30.0),
            ),
            screenshot_settle_timeout=max(
                0.0,
                min(
                    15.0,
                    _number(config, "screenshot_settle_timeout", 3.0),
                ),
            ),
            screenshot_queue_limit=min(
                20,
                max(1, _integer(config, "screenshot_queue_limit", 5)),
            ),
        )
