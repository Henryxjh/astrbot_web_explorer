from __future__ import annotations

import json
import re
from dataclasses import dataclass
from urllib.parse import parse_qs, quote, urlencode, urljoin, urlsplit, urlunsplit

from bs4 import BeautifulSoup

from ..errors import UnsupportedTargetError, WebExplorerError
from ..extract import clean_text, extract_html_page
from ..http_client import SafeHttpClient
from ..models import SearchItem, SearchResults, WebPage
from ..settings import ExplorerSettings
from ..site_rules import SiteRule
from .base import WebAdapter

_WG_PAGE_NAME = re.compile(r'"wgPageName"\s*:\s*("(?:\\.|[^"\\])*")')
_WG_SCRIPT_PATH = re.compile(r'"wgScriptPath"\s*:\s*("(?:\\.|[^"\\])*")')


@dataclass(slots=True)
class MediaWikiSite:
    api_base: str
    page_title: str
    page_url: str


class MediaWikiAdapter(WebAdapter):
    name = "mediawiki"

    def __init__(self, client: SafeHttpClient, settings: ExplorerSettings) -> None:
        self.client = client
        self.settings = settings
        self._discoveries: dict[str, tuple[str, str]] = {}

    def can_handle(self, url: str, rule: SiteRule | None = None) -> bool:
        if rule and rule.adapter_name == self.name:
            return True
        host = (urlsplit(url).hostname or "").lower()
        return host.endswith((".wikipedia.org", ".wikimedia.org"))

    @staticmethod
    def detect_html(html: str) -> bool:
        soup = BeautifulSoup(html, "html.parser")
        generator = soup.find(
            "meta", attrs={"name": re.compile(r"^generator$", re.IGNORECASE)}
        )
        if generator and "mediawiki" in str(generator.get("content", "")).lower():
            return True
        return "wgPageName" in html and "load.php" in html

    @staticmethod
    def _json_config_value(pattern: re.Pattern[str], html: str) -> str:
        match = pattern.search(html)
        if not match:
            return ""
        try:
            return str(json.loads(match.group(1)))
        except (json.JSONDecodeError, TypeError):
            return ""

    @staticmethod
    def _title_from_url(url: str) -> str:
        parsed = urlsplit(url)
        query_title = parse_qs(parsed.query).get("title", [""])[0]
        if query_title:
            return query_title.replace("_", " ")
        parts = [part for part in parsed.path.split("/") if part]
        if "wiki" in parts:
            index = parts.index("wiki")
            if index + 1 < len(parts):
                return "/".join(parts[index + 1 :]).replace("_", " ")
        return (parts[-1] if parts else "Main Page").replace("_", " ")

    async def discover(
        self,
        url: str,
        rule: SiteRule | None = None,
    ) -> MediaWikiSite:
        parsed = urlsplit(url)
        origin = urlunsplit((parsed.scheme, parsed.netloc, "", "", ""))
        if rule and rule.api_base:
            return MediaWikiSite(
                api_base=rule.api_base,
                page_title=self._title_from_url(url),
                page_url=url,
            )
        cached = self._discoveries.get(origin)
        if cached:
            api_base, article_prefix = cached
            return MediaWikiSite(
                api_base=api_base,
                page_title=self._title_from_url(url),
                page_url=urljoin(
                    origin,
                    article_prefix
                    + quote(self._title_from_url(url).replace(" ", "_"), safe="/():"),
                ),
            )

        response = await self.client.get(url, headers={"Accept": "text/html"})
        html = response.text()
        if not self.detect_html(html):
            raise UnsupportedTargetError("页面未识别为 MediaWiki。")
        soup = BeautifulSoup(html, "html.parser")
        page_title = self._json_config_value(_WG_PAGE_NAME, html)
        if not page_title:
            page_title = self._title_from_url(response.url)
        api_base = ""
        edit_uri = soup.find("link", rel=lambda value: value and "EditURI" in value)
        if edit_uri and edit_uri.get("href"):
            candidate = urljoin(response.url, str(edit_uri["href"]))
            if "api.php" in candidate:
                api_base = candidate.split("api.php", 1)[0] + "api.php"
        script_path = self._json_config_value(_WG_SCRIPT_PATH, html)
        if not api_base:
            api_base = origin + script_path.rstrip("/") + "/api.php"

        canonical = soup.find("link", rel="canonical")
        page_url = response.url
        article_prefix = "/wiki/"
        if canonical and canonical.get("href"):
            page_url = urljoin(response.url, str(canonical["href"]))
            quoted_title = quote(page_title.replace(" ", "_"), safe="/():")
            canonical_path = urlsplit(page_url).path
            if quoted_title and canonical_path.endswith(quoted_title):
                article_prefix = canonical_path[: -len(quoted_title)]
        self._discoveries[origin] = (api_base, article_prefix)
        return MediaWikiSite(
            api_base=api_base, page_title=page_title, page_url=page_url
        )

    async def _api_get(self, api_base: str, params: dict[str, str]):
        joiner = "&" if "?" in api_base else "?"
        response = await self.client.get(
            api_base + joiner + urlencode(params),
            headers={"Accept": "application/json"},
        )
        data = response.json()
        if not isinstance(data, dict):
            raise WebExplorerError("MediaWiki API 返回格式无效。")
        if data.get("error"):
            error = data["error"]
            raise WebExplorerError(
                f"MediaWiki API 错误：{error.get('code', '')} {error.get('info', '')}"
            )
        return data

    async def open(self, url: str, rule: SiteRule | None = None) -> WebPage:
        site = await self.discover(url, rule)
        data = await self._api_get(
            site.api_base,
            {
                "action": "parse",
                "page": site.page_title,
                "prop": "text|links|externallinks|tocdata",
                "redirects": "1",
                "format": "json",
                "formatversion": "2",
            },
        )
        parsed = data.get("parse") or {}
        html = parsed.get("text") or ""
        if not html:
            raise WebExplorerError("MediaWiki API 没有返回页面正文。")
        page = extract_html_page(
            html,
            site.page_url,
            max_content_chars=self.settings.max_content_chars,
            max_links=self.settings.max_links,
            adapter=self.name,
        )
        page.title = parsed.get("title") or site.page_title
        page.metadata = {
            "page_id": parsed.get("pageid"),
            "revision_id": parsed.get("revid"),
            "api_base": site.api_base,
        }
        return page

    async def search(
        self,
        query: str,
        site: str,
        kind: str = "auto",
        rule: SiteRule | None = None,
    ) -> SearchResults:
        del kind
        target = site if "://" in site else f"https://{site}"
        discovered = await self.discover(target, rule)
        data = await self._api_get(
            discovered.api_base,
            {
                "action": "query",
                "list": "search",
                "srsearch": query,
                "srlimit": str(self.settings.search_limit),
                "srprop": "snippet|titlesnippet|sectiontitle|timestamp",
                "format": "json",
                "formatversion": "2",
            },
        )
        api_parsed = urlsplit(discovered.api_base)
        index_path = api_parsed.path.rsplit("/", 1)[0] + "/index.php"
        items: list[SearchItem] = []
        for result in (data.get("query") or {}).get("search", []):
            title = str(result.get("title", ""))
            item_url = urlunsplit(
                (
                    api_parsed.scheme,
                    api_parsed.netloc,
                    index_path,
                    urlencode({"title": title}),
                    "",
                )
            )
            snippet_html = str(result.get("snippet", ""))
            snippet = clean_text(
                BeautifulSoup(snippet_html, "html.parser").get_text(" ")
            )
            items.append(
                SearchItem(
                    title=title,
                    url=item_url,
                    snippet=snippet,
                    item_type="wiki_page",
                    metadata={"timestamp": result.get("timestamp", "")},
                )
            )
        return SearchResults(
            query=query,
            site=site,
            items=items,
            adapter=self.name,
        )
