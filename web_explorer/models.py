from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class WebLink:
    text: str
    url: str


@dataclass(slots=True)
class WebPage:
    title: str
    url: str
    content: str
    links: list[WebLink] = field(default_factory=list)
    adapter: str = "generic"
    rendered_with_javascript: bool = False
    truncated: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)
    page_id: str = ""


@dataclass(slots=True)
class SearchItem:
    title: str
    url: str
    snippet: str = ""
    item_type: str = "page"
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class SearchResults:
    query: str
    site: str
    items: list[SearchItem]
    adapter: str
