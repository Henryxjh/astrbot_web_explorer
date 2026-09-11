from __future__ import annotations

import secrets
import time
from collections import OrderedDict
from dataclasses import dataclass

from .models import WebPage


@dataclass(slots=True)
class _CacheEntry:
    scope: str
    page: WebPage
    expires_at: float


class PageCache:
    def __init__(self, ttl_seconds: float, max_pages: int) -> None:
        self.ttl_seconds = ttl_seconds
        self.max_pages = max_pages
        self._items: OrderedDict[str, _CacheEntry] = OrderedDict()

    def _prune(self) -> None:
        now = time.monotonic()
        expired = [key for key, value in self._items.items() if value.expires_at <= now]
        for key in expired:
            self._items.pop(key, None)
        while len(self._items) > self.max_pages:
            self._items.popitem(last=False)

    def put(self, scope: str, page: WebPage) -> WebPage:
        self._prune()
        page_id = f"p_{secrets.token_hex(4)}"
        page.page_id = page_id
        self._items[page_id] = _CacheEntry(
            scope=scope,
            page=page,
            expires_at=time.monotonic() + self.ttl_seconds,
        )
        self._prune()
        return page

    def get(self, scope: str, page_id: str) -> WebPage | None:
        self._prune()
        entry = self._items.get(page_id)
        if entry is None or entry.scope != scope:
            return None
        self._items.move_to_end(page_id)
        entry.expires_at = time.monotonic() + self.ttl_seconds
        return entry.page

    def clear(self) -> None:
        self._items.clear()
