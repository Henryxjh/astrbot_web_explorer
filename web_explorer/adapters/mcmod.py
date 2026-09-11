from __future__ import annotations

import re
from collections.abc import Iterable
from urllib.parse import urlencode, urljoin, urlsplit

from bs4 import BeautifulSoup, Tag

from ..errors import UnsupportedTargetError, WebExplorerError
from ..extract import clean_text, extract_html_page
from ..http_client import SafeHttpClient
from ..models import SearchItem, SearchResults, WebLink, WebPage
from ..settings import ExplorerSettings
from ..site_rules import SiteRule
from .base import WebAdapter

_MCMOD_HOSTS = {"mcmod.cn", "www.mcmod.cn", "m.mcmod.cn", "search.mcmod.cn"}
_CLASS_PATH = re.compile(r"^/class/\d+\.html$")
_ITEM_PATH = re.compile(r"^/item/\d+\.html$")


class McmodAdapter(WebAdapter):
    """Read the server-rendered, useful portions of MC百科 pages."""

    name = "mcmod"

    def __init__(self, client: SafeHttpClient, settings: ExplorerSettings) -> None:
        self.client = client
        self.settings = settings

    def can_handle(self, url: str, rule: SiteRule | None = None) -> bool:
        if rule and rule.adapter_name == self.name:
            return True
        return (urlsplit(url).hostname or "").lower() in _MCMOD_HOSTS

    @staticmethod
    def _title(soup: BeautifulSoup, fallback: str) -> str:
        if not soup.title:
            return fallback
        title = clean_text(soup.title.get_text(" ", strip=True))
        for marker in (" - MC百科", " | MC百科"):
            if marker in title:
                title = title.split(marker, 1)[0]
        return title or fallback

    def _links(self, url: str, nodes: Iterable[Tag]) -> list[WebLink]:
        links: list[WebLink] = []
        seen: set[str] = set()
        for node in nodes:
            for anchor in node.find_all("a", href=True):
                href = str(anchor.get("href", "")).strip()
                absolute = urljoin(url, href)
                parsed = urlsplit(absolute)
                if parsed.scheme not in {"http", "https"} or not parsed.hostname:
                    continue
                absolute = parsed._replace(fragment="").geturl()
                if absolute in seen:
                    continue
                seen.add(absolute)
                label = clean_text(anchor.get_text(" ", strip=True)) or absolute
                links.append(WebLink(text=label[:300], url=absolute))
                if len(links) >= self.settings.max_links:
                    return links
        return links

    @staticmethod
    def _section(label: str, node: Tag | None) -> str:
        if node is None:
            return ""
        text = clean_text(node.get_text(" ", strip=True))
        return f"【{label}】\n{text}" if text else ""

    def _parse_class(self, soup: BeautifulSoup, url: str) -> WebPage:
        info = soup.select_one(".class-info-left")
        body = soup.select_one(".class-menu-main li.text-area.common-text")
        if body is None:
            body = soup.select_one(".class-menu-main .common-text")
        sections = [self._section("基本信息", info), self._section("模组介绍", body)]
        content = "\n\n".join(section for section in sections if section)
        if not content:
            raise UnsupportedTargetError("没有识别到 MC百科模组页正文。")
        nodes = [node for node in (info, body) if isinstance(node, Tag)]
        return WebPage(
            title=self._title(soup, url),
            url=url,
            content=content,
            links=self._links(url, nodes),
            adapter=self.name,
            metadata={"page_type": "class"},
        )

    def _parse_item(self, soup: BeautifulSoup, url: str) -> WebPage:
        command = soup.select_one(".item-give")
        dictionary = soup.select_one(".item-dict")
        info = soup.select_one(".item-info-table")
        body = soup.select_one(".item-content.common-text, .item-content")
        summary_nodes = [
            node for node in (command, dictionary, info) if isinstance(node, Tag)
        ]
        summary = clean_text(
            "\n".join(node.get_text(" ", strip=True) for node in summary_nodes)
        )
        sections = [f"【资料信息】\n{summary}" if summary else ""]
        sections.append(self._section("正文", body))
        content = "\n\n".join(section for section in sections if section)
        if not content:
            raise UnsupportedTargetError("没有识别到 MC百科资料页正文。")
        nodes = summary_nodes + ([body] if isinstance(body, Tag) else [])
        return WebPage(
            title=self._title(soup, url),
            url=url,
            content=content,
            links=self._links(url, nodes),
            adapter=self.name,
            metadata={"page_type": "item"},
        )

    def _parse_generic(self, html: str, url: str) -> WebPage:
        soup = BeautifulSoup(html, "html.parser")
        for selector in (
            ".header-container",
            "footer",
            ".common-nav",
            ".comment-frame",
            ".common-comment",
            ".class-rating-block",
            ".class-edit-block",
        ):
            for node in soup.select(selector):
                node.decompose()
        page = extract_html_page(
            str(soup),
            url,
            max_content_chars=self.settings.max_content_chars,
            max_links=self.settings.max_links,
            adapter=self.name,
        )
        page.metadata["page_type"] = "generic"
        return page

    def parse_page(self, html: str, url: str) -> WebPage:
        soup = BeautifulSoup(html, "html.parser")
        path = urlsplit(url).path
        if _CLASS_PATH.fullmatch(path):
            return self._parse_class(soup, url)
        if _ITEM_PATH.fullmatch(path):
            return self._parse_item(soup, url)
        return self._parse_generic(html, url)

    async def open(self, url: str, rule: SiteRule | None = None) -> WebPage:
        del rule
        response = await self.client.get(
            url,
            headers={"Accept": "text/html,application/xhtml+xml"},
        )
        content_type = response.headers.get("content-type", "").lower()
        if "html" not in content_type and "<html" not in response.text()[:1000].lower():
            raise UnsupportedTargetError("MC百科适配器只支持 HTML 页面。")
        return self.parse_page(response.text(), response.url)

    def parse_search(self, html: str, query: str, site: str) -> SearchResults:
        soup = BeautifulSoup(html, "html.parser")
        items: list[SearchItem] = []
        for result in soup.select(".search-result-list .result-item"):
            head = result.select_one(".head")
            anchors = head.find_all("a", href=True) if isinstance(head, Tag) else []
            anchor = next(
                (
                    candidate
                    for candidate in anchors
                    if clean_text(candidate.get_text(" ", strip=True))
                ),
                None,
            )
            if not isinstance(anchor, Tag):
                continue
            item_url = urljoin("https://search.mcmod.cn/", str(anchor["href"]))
            title = clean_text(head.get_text(" ", strip=True))
            body = result.select_one(".body")
            snippet = (
                clean_text(body.get_text(" ", strip=True))
                if isinstance(body, Tag)
                else ""
            )
            path = urlsplit(item_url).path
            item_type = path.strip("/").split("/", 1)[0] or "page"
            items.append(
                SearchItem(
                    title=title,
                    url=item_url,
                    snippet=snippet,
                    item_type=item_type,
                )
            )
            if len(items) >= self.settings.search_limit:
                break
        return SearchResults(query=query, site=site, items=items, adapter=self.name)

    async def search(
        self,
        query: str,
        site: str,
        kind: str = "auto",
        rule: SiteRule | None = None,
    ) -> SearchResults:
        del kind, rule
        response = await self.client.get(
            "https://search.mcmod.cn/s?" + urlencode({"key": query}),
            headers={"Accept": "text/html,application/xhtml+xml"},
        )
        results = self.parse_search(response.text(), query, site)
        if not results.items:
            raise WebExplorerError("MC百科站内搜索没有返回可解析的结果。")
        return results
