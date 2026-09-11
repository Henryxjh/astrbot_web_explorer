from __future__ import annotations

import re
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup, Tag

from .models import WebLink, WebPage

_BLANK_LINES = re.compile(r"\n{3,}")
_SPACES = re.compile(r"[ \t\f\v]+")


def clean_text(value: str) -> str:
    lines = []
    for line in value.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        normalized = _SPACES.sub(" ", line).strip()
        if normalized:
            lines.append(normalized)
        elif lines and lines[-1] != "":
            lines.append("")
    return _BLANK_LINES.sub("\n\n", "\n".join(lines)).strip()


def _select_content(soup: BeautifulSoup) -> Tag:
    for selector in (
        "article",
        "main",
        "[role='main']",
        ".mw-parser-output",
        "#main-content",
        ".content",
    ):
        node = soup.select_one(selector)
        if isinstance(node, Tag) and len(node.get_text(" ", strip=True)) >= 80:
            return node
    return soup.body if isinstance(soup.body, Tag) else soup


def extract_html_page(
    html: str,
    url: str,
    *,
    max_content_chars: int,
    max_links: int,
    adapter: str = "generic",
    rendered_with_javascript: bool = False,
) -> WebPage:
    soup = BeautifulSoup(html, "html.parser")
    title = ""
    if soup.title:
        title = clean_text(soup.title.get_text(" ", strip=True))
    if not title:
        heading = soup.find("h1")
        if heading:
            title = clean_text(heading.get_text(" ", strip=True))
    title = title or urlsplit(url).hostname or url

    for selector in (
        "script",
        "style",
        "noscript",
        "template",
        "svg",
        "canvas",
        "form",
        "nav",
        "header",
        "footer",
        "aside",
        "[hidden]",
        "[aria-hidden='true']",
        "[style*='display:none']",
        "[style*='display: none']",
        "[style*='visibility:hidden']",
        "[style*='visibility: hidden']",
    ):
        for node in soup.select(selector):
            node.decompose()

    content_node = _select_content(soup)
    links: list[WebLink] = []
    seen: set[str] = set()
    for anchor in content_node.find_all("a", href=True):
        href = str(anchor.get("href", "")).strip()
        absolute = urljoin(url, href)
        parsed = urlsplit(absolute)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            continue
        absolute = parsed._replace(fragment="").geturl()
        if absolute in seen:
            continue
        seen.add(absolute)
        text = clean_text(anchor.get_text(" ", strip=True)) or absolute
        links.append(WebLink(text=text[:300], url=absolute))
        if len(links) >= max_links:
            break

    content = clean_text(content_node.get_text("\n", strip=True))
    truncated = len(content) > max_content_chars
    if truncated:
        content = content[:max_content_chars].rstrip() + "\n\n[正文已截断]"
    return WebPage(
        title=title[:500],
        url=url,
        content=content,
        links=links,
        adapter=adapter,
        rendered_with_javascript=rendered_with_javascript,
        truncated=truncated,
    )


def looks_like_javascript_shell(html: str, extracted_text: str) -> bool:
    lower = html.lower()
    shell_markers = ('id="root"', "id='root'", 'id="__next"', 'id="app"')
    javascript_warning = (
        "enable javascript" in lower or "启用javascript" in lower.replace(" ", "")
    )
    return len(extracted_text) < 200 and (
        javascript_warning or any(marker in lower for marker in shell_markers)
    )
