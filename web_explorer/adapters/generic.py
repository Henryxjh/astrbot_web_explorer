from __future__ import annotations

import json
from urllib.parse import urlsplit

from ..chawan import ChawanRenderer
from ..errors import UnsupportedTargetError, WebExplorerError
from ..extract import extract_html_page, looks_like_javascript_shell
from ..http_client import SafeHttpClient
from ..models import SearchResults, WebPage
from ..settings import ExplorerSettings
from ..site_rules import SiteRule
from .base import WebAdapter


class GenericAdapter(WebAdapter):
    name = "generic"

    def __init__(
        self,
        client: SafeHttpClient,
        settings: ExplorerSettings,
        chawan: ChawanRenderer,
    ) -> None:
        self.client = client
        self.settings = settings
        self.chawan = chawan

    def can_handle(self, url: str, rule: SiteRule | None = None) -> bool:
        del rule
        return urlsplit(url).scheme in {"http", "https"}

    async def open(self, url: str, rule: SiteRule | None = None) -> WebPage:
        del rule
        response = await self.client.get(
            url,
            headers={
                "Accept": "text/html,application/xhtml+xml,text/plain,application/json;q=0.8,*/*;q=0.2"
            },
        )
        content_type = response.headers.get("content-type", "").lower()
        text = response.text()
        if "html" in content_type or "<html" in text[:1000].lower():
            page = extract_html_page(
                text,
                response.url,
                max_content_chars=self.settings.max_content_chars,
                max_links=self.settings.max_links,
                adapter=self.name,
            )
            if looks_like_javascript_shell(
                text, page.content
            ) and self.chawan.can_render(url):
                return await self.chawan.render(url)
            lower = text.lower()
            if (
                "wgPageName" in text
                and "load.php" in lower
                or "generator" in lower
                and "mediawiki" in lower
            ):
                page.metadata["detected_engine"] = "mediawiki"
            return page
        if "json" in content_type:
            try:
                text = json.dumps(response.json(), ensure_ascii=False, indent=2)
            except WebExplorerError:
                pass
        elif not any(
            marker in content_type
            for marker in ("text/", "xml", "javascript", "yaml", "toml")
        ):
            raise UnsupportedTargetError(
                f"通用读取器不支持该内容类型：{content_type or '未知'}"
            )
        truncated = len(text) > self.settings.max_content_chars
        if truncated:
            text = text[: self.settings.max_content_chars].rstrip() + "\n\n[正文已截断]"
        return WebPage(
            title=urlsplit(response.url).path.rsplit("/", 1)[-1] or response.url,
            url=response.url,
            content=text.strip(),
            adapter=self.name,
            truncated=truncated,
        )

    async def search(
        self,
        query: str,
        site: str,
        kind: str = "auto",
        rule: SiteRule | None = None,
    ) -> SearchResults:
        del query, site, kind, rule
        raise UnsupportedTargetError("该网站没有可用的专用搜索适配器。")
