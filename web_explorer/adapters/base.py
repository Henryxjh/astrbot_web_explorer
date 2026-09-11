from __future__ import annotations

from abc import ABC, abstractmethod

from ..models import SearchResults, WebPage
from ..site_rules import SiteRule


class WebAdapter(ABC):
    name: str

    @abstractmethod
    def can_handle(self, url: str, rule: SiteRule | None = None) -> bool:
        raise NotImplementedError

    @abstractmethod
    async def open(self, url: str, rule: SiteRule | None = None) -> WebPage:
        raise NotImplementedError

    async def search(
        self,
        query: str,
        site: str,
        kind: str = "auto",
        rule: SiteRule | None = None,
    ) -> SearchResults:
        raise NotImplementedError
