from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlencode, urljoin, urlsplit, urlunsplit

from bs4 import BeautifulSoup, NavigableString, Tag

from ..errors import UnsupportedTargetError, WebExplorerError
from ..extract import clean_text
from ..http_client import SafeHttpClient
from ..models import SearchItem, SearchResults, WebLink, WebPage
from ..settings import ExplorerSettings
from ..site_rules import SiteRule
from .base import WebAdapter

_HOST_SUFFIXES = (
    ".readthedocs.io",
    ".readthedocs.org",
    ".readthedocs.com",
    ".readthedocs-hosted.com",
)


@dataclass(frozen=True, slots=True)
class ReadTheDocsSite:
    project: str
    version: str
    language: str
    page_url: str


class ReadTheDocsAdapter(WebAdapter):
    name = "readthedocs"

    def __init__(self, client: SafeHttpClient, settings: ExplorerSettings) -> None:
        self.client = client
        self.settings = settings

    def can_handle(self, url: str, rule: SiteRule | None = None) -> bool:
        if rule and rule.adapter_name == self.name:
            return True
        hostname = (urlsplit(url).hostname or "").lower()
        return any(hostname.endswith(suffix) for suffix in _HOST_SUFFIXES)

    @staticmethod
    def detect_html(html: str) -> bool:
        soup = BeautifulSoup(html, "html.parser")
        return soup.find("meta", attrs={"name": "readthedocs-project-slug"}) is not None

    @staticmethod
    def _meta(soup: BeautifulSoup, name: str) -> str:
        node = soup.find("meta", attrs={"name": name})
        return str(node.get("content", "")).strip() if isinstance(node, Tag) else ""

    @classmethod
    def _site_from_soup(
        cls,
        soup: BeautifulSoup,
        page_url: str,
    ) -> ReadTheDocsSite:
        project = cls._meta(soup, "readthedocs-project-slug")
        version = cls._meta(soup, "readthedocs-version-slug")
        if not project or not version:
            raise UnsupportedTargetError("页面没有 Read the Docs 项目或版本元数据。")
        language = (soup.html.get("lang", "") if soup.html else "") or ""
        path_parts = [part for part in urlsplit(page_url).path.split("/") if part]
        if path_parts and re.fullmatch(r"[a-z]{2}(?:-[a-z]{2})?", path_parts[0], re.IGNORECASE):
            language = path_parts[0]
        return ReadTheDocsSite(
            project=project,
            version=version,
            language=str(language),
            page_url=page_url,
        )

    @staticmethod
    def _main_content(soup: BeautifulSoup) -> Tag:
        for selector in (
            "[itemprop='articleBody']",
            "article[role='main']",
            "main[role='main']",
            "article",
            ".document[role='main']",
            ".rst-content .document",
            "main",
            ".rst-content",
        ):
            node = soup.select_one(selector)
            if isinstance(node, Tag) and len(node.get_text(" ", strip=True)) >= 20:
                return node
        raise UnsupportedTargetError("没有识别到 Read the Docs 正文容器。")

    def _links(self, main: Tag, url: str) -> list[WebLink]:
        links: list[WebLink] = []
        seen: set[str] = set()
        for anchor in main.find_all("a", href=True):
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
                break
        return links

    @staticmethod
    def _readable_text(main: Tag) -> str:
        clone_soup = BeautifulSoup(str(main), "html.parser")
        root = clone_soup.find()
        if not isinstance(root, Tag):
            return ""
        for pre in root.find_all("pre"):
            code = pre.get_text("", strip=False).strip("\n")
            pre.replace_with(NavigableString(f"\n```\n{code}\n```\n"))
        for br in root.find_all("br"):
            br.replace_with(NavigableString("\n"))
        for block in root.find_all(
            [
                "address",
                "blockquote",
                "dd",
                "details",
                "div",
                "dl",
                "dt",
                "figcaption",
                "figure",
                "h1",
                "h2",
                "h3",
                "h4",
                "h5",
                "h6",
                "li",
                "p",
                "section",
                "table",
                "tr",
            ]
        ):
            block.insert_before(NavigableString("\n"))
            block.insert_after(NavigableString("\n"))
        return clean_text(root.get_text(" ", strip=False))

    def parse_page(self, html: str, url: str) -> WebPage:
        soup = BeautifulSoup(html, "html.parser")
        site = self._site_from_soup(soup, url)
        main = self._main_content(soup)
        for selector in (
            "script",
            "style",
            "noscript",
            "template",
            ".headerlink",
            ".viewcode-link",
            ".edit-this-page",
            ".wy-breadcrumbs",
            ".rst-footer-buttons",
            "[role='navigation']",
            "[role='contentinfo']",
            "[aria-hidden='true']",
        ):
            for node in main.select(selector):
                node.decompose()

        heading = main.find("h1")
        title = clean_text(heading.get_text(" ", strip=True)) if heading else ""
        if not title and soup.title:
            title = clean_text(soup.title.get_text(" ", strip=True))

        links = self._links(main, url)
        content = self._readable_text(main)
        truncated = len(content) > self.settings.max_content_chars
        if truncated:
            content = (
                content[: self.settings.max_content_chars].rstrip()
                + "\n\n[正文已截断]"
            )
        return WebPage(
            title=title or url,
            url=url,
            content=content,
            links=links,
            adapter=self.name,
            truncated=truncated,
            metadata={
                "project": site.project,
                "version": site.version,
                "language": site.language,
            },
        )

    async def _fetch_site(self, url: str) -> tuple[ReadTheDocsSite, str]:
        response = await self.client.get(
            url,
            headers={"Accept": "text/html,application/xhtml+xml"},
        )
        html = response.text()
        soup = BeautifulSoup(html, "html.parser")
        return self._site_from_soup(soup, response.url), html

    async def open(self, url: str, rule: SiteRule | None = None) -> WebPage:
        del rule
        site, html = await self._fetch_site(url)
        return self.parse_page(html, site.page_url)

    def parse_search(
        self,
        data: object,
        query: str,
        site: str,
    ) -> SearchResults:
        if not isinstance(data, dict):
            raise WebExplorerError("Read the Docs 搜索 API 返回格式无效。")
        items: list[SearchItem] = []
        for result in data.get("results", []):
            if not isinstance(result, dict):
                continue
            domain = str(result.get("domain", "")).rstrip("/")
            path = str(result.get("path", ""))
            if not domain or not path:
                continue
            snippets = []
            for block in result.get("blocks", []):
                if isinstance(block, dict) and block.get("content"):
                    snippets.append(str(block["content"]))
                if len(snippets) >= 2:
                    break
            items.append(
                SearchItem(
                    title=clean_text(str(result.get("title", ""))) or path,
                    url=urljoin(domain + "/", path.lstrip("/")),
                    snippet=clean_text(" ".join(snippets)),
                    item_type="documentation",
                    metadata={
                        "project": result.get("project", {}),
                        "version": result.get("version", {}),
                    },
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
        target = site if "://" in site else "https://" + site
        discovered, _ = await self._fetch_site(target)
        parsed = urlsplit(discovered.page_url)
        api_url = urlunsplit(
            (
                parsed.scheme,
                parsed.netloc,
                "/_/api/v3/search/",
                urlencode(
                    {
                        "q": f"project:{discovered.project}/{discovered.version} {query}",
                        "page_size": str(self.settings.search_limit),
                    }
                ),
                "",
            )
        )
        response = await self.client.get(
            api_url,
            headers={"Accept": "application/json"},
        )
        return self.parse_search(response.json(), query, site)
