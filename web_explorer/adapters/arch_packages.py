from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any
from urllib.parse import quote, unquote, urlsplit

from ..errors import UnsupportedTargetError, WebExplorerError
from ..http_client import SafeHttpClient
from ..models import SearchItem, SearchResults, WebLink, WebPage
from ..settings import ExplorerSettings
from ..site_rules import SiteRule
from .base import WebAdapter

_OFFICIAL_ORIGIN = "https://archlinux.org"
_AUR_ORIGIN = "https://aur.archlinux.org"


def _text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [text for item in value if (text := _text(item))]


def _joined(value: Any, empty: str = "无") -> str:
    values = _list(value)
    return "、".join(values) if values else empty


def _version(item: dict[str, Any]) -> str:
    version = _text(item.get("Version"))
    if version:
        return version
    epoch = item.get("epoch")
    prefix = f"{epoch}:" if epoch not in (None, 0, "0") else ""
    pkgver = _text(item.get("pkgver"))
    pkgrel = _text(item.get("pkgrel"))
    return prefix + pkgver + (f"-{pkgrel}" if pkgrel else "")


def _timestamp(value: Any) -> str:
    if value in (None, "", 0):
        return ""
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, UTC).strftime("%Y-%m-%d %H:%M UTC")
    return _text(value)


def _size(value: Any) -> str:
    try:
        size = float(value)
    except (TypeError, ValueError):
        return ""
    units = ("B", "KiB", "MiB", "GiB")
    for unit in units:
        if size < 1024 or unit == units[-1]:
            return f"{size:.1f} {unit}"
        size /= 1024
    return ""


def _integer(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


class ArchPackagesAdapter(WebAdapter):
    name = "arch_packages"

    def __init__(self, client: SafeHttpClient, settings: ExplorerSettings) -> None:
        self.client = client
        self.settings = settings

    def can_handle(self, url: str, rule: SiteRule | None = None) -> bool:
        if rule and rule.adapter_name == self.name:
            return True
        host = (urlsplit(url).hostname or "").lower()
        return host in {"archlinux.org", "www.archlinux.org", "aur.archlinux.org"}

    async def _get_json(self, url: str) -> dict[str, Any]:
        response = await self.client.get(url, headers={"Accept": "application/json"})
        data = response.json()
        if not isinstance(data, dict):
            raise WebExplorerError("Arch 软件包 API 返回格式无效。")
        return data

    @staticmethod
    def _official_url(item: dict[str, Any]) -> str:
        repo = quote(_text(item.get("repo")), safe="")
        arch = quote(_text(item.get("arch")), safe="")
        name = quote(_text(item.get("pkgname")), safe="")
        return f"{_OFFICIAL_ORIGIN}/packages/{repo}/{arch}/{name}/"

    @staticmethod
    def _aur_url(item: dict[str, Any]) -> str:
        return f"{_AUR_ORIGIN}/packages/{quote(_text(item.get('Name')), safe='')}/"

    def parse_official_search(
        self, data: dict[str, Any], query: str, site: str
    ) -> SearchResults:
        raw_results = data.get("results", [])
        if not isinstance(raw_results, list):
            raise WebExplorerError("Arch 官方软件包搜索返回格式无效。")
        exact = query.casefold()
        packages = [item for item in raw_results if isinstance(item, dict)]
        packages.sort(
            key=lambda item: (
                _text(item.get("pkgname")).casefold() != exact,
                _text(item.get("pkgname")).casefold(),
                _text(item.get("repo")).casefold(),
                _text(item.get("arch")).casefold(),
            )
        )
        items: list[SearchItem] = []
        for package in packages:
            name = _text(package.get("pkgname"))
            if not name:
                continue
            repo = _text(package.get("repo")) or "unknown"
            arch = _text(package.get("arch")) or "unknown"
            maintainers = _joined(package.get("maintainers"), "无人维护")
            outdated = "；已标记过期" if package.get("flag_date") else ""
            description = _text(package.get("pkgdesc")) or "无描述"
            items.append(
                SearchItem(
                    title=f"[官方 {repo}/{arch}] {name} {_version(package)}",
                    url=self._official_url(package),
                    snippet=(
                        f"{description}\n维护者：{maintainers}；"
                        f"更新：{_timestamp(package.get('last_update')) or '未知'}{outdated}"
                    ),
                    item_type="arch_official",
                    metadata={"source": "official", "repo": repo, "arch": arch},
                )
            )
        return SearchResults(query=query, site=site, items=items, adapter=self.name)

    def parse_aur_search(
        self, data: dict[str, Any], query: str, site: str
    ) -> SearchResults:
        if data.get("type") == "error":
            raise WebExplorerError(f"AUR RPC 错误：{_text(data.get('error'))}")
        raw_results = data.get("results", [])
        if not isinstance(raw_results, list):
            raise WebExplorerError("AUR 搜索返回格式无效。")
        exact = query.casefold()
        packages = [item for item in raw_results if isinstance(item, dict)]
        packages.sort(
            key=lambda item: (
                _text(item.get("Name")).casefold() != exact,
                -_float(item.get("Popularity")),
                -_integer(item.get("NumVotes")),
                _text(item.get("Name")).casefold(),
            )
        )
        items: list[SearchItem] = []
        for package in packages:
            name = _text(package.get("Name"))
            if not name:
                continue
            maintainer = _text(package.get("Maintainer")) or "无人维护"
            status = []
            if not package.get("Maintainer"):
                status.append("孤儿包")
            if package.get("OutOfDate"):
                status.append("已标记过期")
            status_text = f"；状态：{'、'.join(status)}" if status else ""
            description = _text(package.get("Description")) or "无描述"
            items.append(
                SearchItem(
                    title=f"[AUR] {name} {_version(package)}",
                    url=self._aur_url(package),
                    snippet=(
                        f"{description}\n维护者：{maintainer}；"
                        f"票数：{_integer(package.get('NumVotes'))}；"
                        f"流行度：{_float(package.get('Popularity')):.2f}"
                        f"{status_text}\n"
                        "注意：AUR 是用户维护的软件包，安装前应检查 PKGBUILD。"
                    ),
                    item_type="aur",
                    metadata={"source": "aur"},
                )
            )
        return SearchResults(query=query, site=site, items=items, adapter=self.name)

    @staticmethod
    def _mode(site: str, kind: str) -> str:
        requested = kind.strip().lower()
        aliases = {
            "all": "all",
            "official": "official",
            "arch-official": "official",
            "aur": "aur",
        }
        if requested not in {"", "auto"}:
            if requested not in aliases:
                raise WebExplorerError(
                    "Arch 搜索类型只支持 auto、all、official 或 aur。"
                )
            return aliases[requested]
        site_name = site.strip().lower().rstrip("/")
        if site_name in {"aur", "aur.archlinux.org", "https://aur.archlinux.org"}:
            return "aur"
        if site_name == "arch-official":
            return "official"
        host = (urlsplit(site if "://" in site else f"https://{site}").hostname or "").lower()
        return "aur" if host == "aur.archlinux.org" else "all"

    async def search(
        self,
        query: str,
        site: str,
        kind: str = "auto",
        rule: SiteRule | None = None,
    ) -> SearchResults:
        del rule
        mode = self._mode(site, kind)
        official_url = (
            f"{_OFFICIAL_ORIGIN}/packages/search/json/?q={quote(query, safe='')}"
        )
        aur_url = (
            f"{_AUR_ORIGIN}/rpc/v5/search/{quote(query, safe='')}?by=name-desc"
        )
        if mode == "official":
            data = await self._get_json(official_url)
            results = self.parse_official_search(data, query, site)
        elif mode == "aur":
            data = await self._get_json(aur_url)
            results = self.parse_aur_search(data, query, site)
        else:
            responses = await asyncio.gather(
                self._get_json(official_url),
                self._get_json(aur_url),
                return_exceptions=True,
            )
            official_data, aur_data = responses
            if isinstance(official_data, Exception) and isinstance(aur_data, Exception):
                raise WebExplorerError(
                    f"Arch 官方仓库和 AUR 搜索均失败：{official_data}；{aur_data}"
                )
            official_items = (
                []
                if isinstance(official_data, Exception)
                else self.parse_official_search(official_data, query, site).items
            )
            aur_items = (
                []
                if isinstance(aur_data, Exception)
                else self.parse_aur_search(aur_data, query, site).items
            )
            results = SearchResults(
                query=query,
                site=site,
                items=official_items + aur_items,
                adapter=self.name,
            )
        results.items = results.items[: self.settings.search_limit]
        return results

    @staticmethod
    def _official_page(item: dict[str, Any], page_url: str) -> WebPage:
        name = _text(item.get("pkgname"))
        if not name:
            raise WebExplorerError("Arch 官方软件包详情缺少包名。")
        lines = [
            "来源：Arch Linux 官方仓库",
            f"包名：{name}",
            f"版本：{_version(item)}",
            f"仓库：{_text(item.get('repo')) or '未知'}",
            f"架构：{_text(item.get('arch')) or '未知'}",
            f"描述：{_text(item.get('pkgdesc')) or '无'}",
            f"维护者：{_joined(item.get('maintainers'), '无人维护')}",
            f"打包者：{_text(item.get('packager')) or '未知'}",
            f"许可证：{_joined(item.get('licenses'))}",
            f"构建时间：{_timestamp(item.get('build_date')) or '未知'}",
            f"更新时间：{_timestamp(item.get('last_update')) or '未知'}",
            f"过期标记：{_timestamp(item.get('flag_date')) or '无'}",
            f"下载大小：{_size(item.get('compressed_size')) or '未知'}",
            f"安装大小：{_size(item.get('installed_size')) or '未知'}",
            f"依赖：{_joined(item.get('depends'))}",
            f"可选依赖：{_joined(item.get('optdepends'))}",
            f"构建依赖：{_joined(item.get('makedepends'))}",
            f"检查依赖：{_joined(item.get('checkdepends'))}",
            f"提供：{_joined(item.get('provides'))}",
            f"冲突：{_joined(item.get('conflicts'))}",
            f"替换：{_joined(item.get('replaces'))}",
        ]
        links = [WebLink("Arch 软件包页面", page_url)]
        if upstream := _text(item.get("url")):
            links.append(WebLink("上游项目", upstream))
        return WebPage(
            title=f"{name} {_version(item)} — Arch 官方仓库",
            url=page_url,
            content="\n".join(lines),
            links=links,
            adapter="arch_packages",
            metadata={"source": "official"},
        )

    @staticmethod
    def _aur_page(item: dict[str, Any], page_url: str) -> WebPage:
        name = _text(item.get("Name"))
        if not name:
            raise WebExplorerError("AUR 软件包详情缺少包名。")
        package_base = _text(item.get("PackageBase")) or name
        maintainer = _text(item.get("Maintainer")) or "无人维护（孤儿包）"
        lines = [
            "安全提示：这是 AUR 用户维护的软件包，不属于 Arch Linux 官方仓库；安装前应检查 PKGBUILD 及相关文件。",
            "",
            "来源：Arch User Repository (AUR)",
            f"包名：{name}",
            f"包基础名：{package_base}",
            f"版本：{_version(item)}",
            f"描述：{_text(item.get('Description')) or '无'}",
            f"维护者：{maintainer}",
            f"提交者：{_text(item.get('Submitter')) or '未知'}",
            f"许可证：{_joined(item.get('License'))}",
            f"首次提交：{_timestamp(item.get('FirstSubmitted')) or '未知'}",
            f"最后修改：{_timestamp(item.get('LastModified')) or '未知'}",
            f"过期标记：{_timestamp(item.get('OutOfDate')) or '无'}",
            f"票数：{_integer(item.get('NumVotes'))}",
            f"流行度：{_float(item.get('Popularity')):.2f}",
            f"依赖：{_joined(item.get('Depends'))}",
            f"可选依赖：{_joined(item.get('OptDepends'))}",
            f"构建依赖：{_joined(item.get('MakeDepends'))}",
            f"检查依赖：{_joined(item.get('CheckDepends'))}",
            f"关键词：{_joined(item.get('Keywords'))}",
        ]
        links = [
            WebLink("AUR 软件包页面", page_url),
            WebLink("AUR Git 仓库", f"{_AUR_ORIGIN}/{quote(package_base, safe='')}.git"),
            WebLink(
                "下载快照",
                f"{_AUR_ORIGIN}/cgit/aur.git/snapshot/{quote(package_base, safe='')}.tar.gz",
            ),
        ]
        if upstream := _text(item.get("URL")):
            links.append(WebLink("上游项目", upstream))
        return WebPage(
            title=f"{name} {_version(item)} — AUR",
            url=page_url,
            content="\n".join(lines),
            links=links,
            adapter="arch_packages",
            metadata={"source": "aur", "package_base": package_base},
        )

    async def open(self, url: str, rule: SiteRule | None = None) -> WebPage:
        del rule
        parsed = urlsplit(url)
        host = (parsed.hostname or "").lower()
        parts = [part for part in parsed.path.split("/") if part]
        if host in {"archlinux.org", "www.archlinux.org"}:
            if len(parts) < 4 or parts[0] != "packages":
                raise UnsupportedTargetError("该 Arch 页面不是官方软件包详情页。")
            repo, arch, name = (unquote(part) for part in parts[1:4])
            page_url = f"{_OFFICIAL_ORIGIN}/packages/{quote(repo, safe='')}/{quote(arch, safe='')}/{quote(name, safe='')}/"
            data = await self._get_json(page_url + "json/")
            return self._official_page(data, page_url)
        if host == "aur.archlinux.org":
            if len(parts) < 2 or parts[0] != "packages":
                raise UnsupportedTargetError("该 AUR 页面不是软件包详情页。")
            name = unquote(parts[1])
            data = await self._get_json(
                f"{_AUR_ORIGIN}/rpc/v5/info/{quote(name, safe='')}"
            )
            results = data.get("results", [])
            if not isinstance(results, list) or not results:
                raise WebExplorerError("AUR 中没有找到该软件包。")
            item = results[0]
            if not isinstance(item, dict):
                raise WebExplorerError("AUR 软件包详情返回格式无效。")
            page_url = f"{_AUR_ORIGIN}/packages/{quote(name, safe='')}/"
            return self._aur_page(item, page_url)
        raise UnsupportedTargetError("URL 不属于 Arch 官方软件包站或 AUR。")
