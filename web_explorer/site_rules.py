from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .errors import WebExplorerError

DEFAULT_RULES = {"version": 1, "sites": []}


@dataclass(frozen=True, slots=True)
class SiteRule:
    host: str
    adapter: str
    driver: str = ""
    api_base: str = ""
    priority: int = 100
    fallback: bool = True
    enabled: bool = True
    options: dict[str, Any] = field(default_factory=dict)

    @property
    def adapter_name(self) -> str:
        adapter = self.adapter.strip().lower()
        driver = self.driver.strip().lower()
        aliases = {
            ("wiki", "mediawiki"): "mediawiki",
            ("code_forge", "github"): "github",
            ("game_wiki", "mcmod"): "mcmod",
            ("documentation", "readthedocs"): "readthedocs",
        }
        return aliases.get((adapter, driver), driver or adapter)

    def matches(self, hostname: str) -> bool:
        hostname = hostname.rstrip(".").lower()
        pattern = self.host.rstrip(".").lower()
        if pattern.startswith("*."):
            suffix = pattern[1:]
            return hostname.endswith(suffix) and hostname != suffix[1:]
        return hostname == pattern


class SiteRuleStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.rules: list[SiteRule] = []
        self._mtime_ns = -1

    def ensure_exists(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.exists():
            return
        self.path.write_text(
            json.dumps(DEFAULT_RULES, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    def load(self) -> None:
        self.ensure_exists()
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise WebExplorerError(f"站点规则配置无法读取：{exc}") from exc
        if not isinstance(raw, dict) or raw.get("version") != 1:
            raise WebExplorerError("站点规则配置必须是 version=1 的 JSON 对象。")
        sites = raw.get("sites", [])
        if not isinstance(sites, list):
            raise WebExplorerError("站点规则配置中的 sites 必须是列表。")
        parsed: list[SiteRule] = []
        for index, item in enumerate(sites):
            if not isinstance(item, dict):
                raise WebExplorerError(f"sites[{index}] 必须是对象。")
            host = str(item.get("host", "")).strip().lower()
            adapter = str(item.get("adapter", "")).strip().lower()
            if not host or not adapter or "://" in host or "/" in host:
                raise WebExplorerError(
                    f"sites[{index}] 的 host/adapter 无效；host 只能填写域名。"
                )
            options = item.get("options", {})
            if not isinstance(options, dict):
                raise WebExplorerError(f"sites[{index}].options 必须是对象。")
            try:
                priority = int(item.get("priority", 100))
            except (TypeError, ValueError) as exc:
                raise WebExplorerError(f"sites[{index}].priority 必须是整数。") from exc
            for boolean_key in ("fallback", "enabled"):
                if boolean_key in item and not isinstance(item[boolean_key], bool):
                    raise WebExplorerError(
                        f"sites[{index}].{boolean_key} 必须是布尔值。"
                    )
            parsed.append(
                SiteRule(
                    host=host,
                    adapter=adapter,
                    driver=str(item.get("driver", "")).strip().lower(),
                    api_base=str(item.get("api_base", "")).strip(),
                    priority=priority,
                    fallback=bool(item.get("fallback", True)),
                    enabled=bool(item.get("enabled", True)),
                    options=options,
                )
            )
        self.rules = sorted(parsed, key=lambda rule: rule.priority, reverse=True)
        try:
            self._mtime_ns = self.path.stat().st_mtime_ns
        except OSError:
            self._mtime_ns = -1

    def reload_if_changed(self) -> bool:
        try:
            mtime_ns = self.path.stat().st_mtime_ns
        except OSError:
            mtime_ns = -1
        if mtime_ns == self._mtime_ns:
            return False
        self.load()
        return True

    def match(self, hostname: str) -> list[SiteRule]:
        return [rule for rule in self.rules if rule.enabled and rule.matches(hostname)]
