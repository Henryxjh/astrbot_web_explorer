from __future__ import annotations

from astrbot.api import AstrBotConfig
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, StarTools
from astrbot.core.star.filter.command import GreedyStr

from .web_explorer import ExplorerSettings, WebExplorerError, WebExplorerService


class WebExplorerPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig) -> None:
        super().__init__(context, config)
        self.config = config
        data_dir = StarTools.get_data_dir("astrbot_web_explorer")
        self.service = WebExplorerService(
            ExplorerSettings.from_config(config, data_dir),
            self.logger,
        )

    async def initialize(self) -> None:
        await self.service.initialize()
        self.logger.info(
            "Web Explorer initialized; site rules: %s",
            self.service.settings.site_rules_path,
        )

    @staticmethod
    def _scope(event: AstrMessageEvent) -> str:
        return str(event.unified_msg_origin or "unknown")

    @filter.llm_tool(name="open_web_page")
    async def open_web_page(self, event: AstrMessageEvent, url: str) -> str:
        """读取公开网页，并返回正文、页面 ID 及可继续访问的编号链接。

        优先使用站点专用 API；没有专用适配器时使用通用网页正文提取。
        对已知 URL 使用本工具，不要声称自己无法访问网页。

        Args:
            url(string): 要读取的完整 http/https 网页地址。
        """
        try:
            page = await self.service.open(self._scope(event), url)
            return self.service.format_page(page)
        except WebExplorerError as exc:
            return f"网页读取失败：{exc}"

    @filter.llm_tool(name="search_web_site")
    async def search_web_site(
        self,
        event: AstrMessageEvent,
        query: str,
        site: str,
        kind: str,
    ) -> str:
        """在具有专用适配器的网站内搜索内容。

        当前支持 GitHub 和 MediaWiki。GitHub 的 kind 可填写 auto、repositories、
        issues、code 或 commits；其他网站填写 auto。

        Args:
            query(string): 搜索关键词和限定条件。
            site(string): github、github.com、仓库 URL 或 Wiki 站点 URL。
            kind(string): 搜索类型；不确定时填写 auto。
        """
        del event
        try:
            results = await self.service.search(query, site, kind)
            return self.service.format_search(results)
        except WebExplorerError as exc:
            return f"站内搜索失败：{exc}"

    @filter.llm_tool(name="follow_web_link")
    async def follow_web_link(
        self,
        event: AstrMessageEvent,
        page_id: str,
        link_id: str,
    ) -> str:
        """继续访问先前网页结果中的编号链接。

        Args:
            page_id(string): open_web_page 返回的页面 ID，例如 p_ab12cd34。
            link_id(string): 页面中的链接编号，例如 L1。
        """
        try:
            page = await self.service.follow(
                self._scope(event),
                page_id,
                link_id,
            )
            return self.service.format_page(page)
        except WebExplorerError as exc:
            return f"链接访问失败：{exc}"

    @filter.llm_tool(name="find_in_web_page")
    async def find_in_web_page(
        self,
        event: AstrMessageEvent,
        page_id: str,
        query: str,
    ) -> str:
        """在先前读取并缓存的页面正文中查找关键词。

        Args:
            page_id(string): open_web_page 返回的页面 ID。
            query(string): 要查找的文字。
        """
        try:
            return self.service.find(self._scope(event), page_id, query)
        except WebExplorerError as exc:
            return f"页面查找失败：{exc}"

    @filter.command("webopen")
    async def webopen_command(self, event: AstrMessageEvent, value: GreedyStr):
        """测试网页读取：/webopen URL"""
        event.stop_event()
        url = str(value).strip()
        if not url:
            yield event.plain_result("用法：/webopen <URL>")
            return
        yield event.plain_result(await self.open_web_page(event, url))

    @filter.regex(r"^/webopen(?:\s|$)")
    async def webopen_slash_command(self, event: AstrMessageEvent):
        message = event.get_message_str().strip()
        async for result in self.webopen_command(event, message[len("/webopen") :]):
            yield result

    @filter.command("websearch")
    async def websearch_command(self, event: AstrMessageEvent, value: GreedyStr):
        """测试站内搜索：/websearch SITE QUERY"""
        event.stop_event()
        parts = str(value).strip().split(maxsplit=1)
        if len(parts) != 2:
            yield event.plain_result("用法：/websearch <站点或仓库 URL> <关键词>")
            return
        site, query = parts
        yield event.plain_result(await self.search_web_site(event, query, site, "auto"))

    @filter.regex(r"^/websearch(?:\s|$)")
    async def websearch_slash_command(self, event: AstrMessageEvent):
        message = event.get_message_str().strip()
        async for result in self.websearch_command(
            event,
            message[len("/websearch") :],
        ):
            yield result

    @filter.command("webfollow")
    async def webfollow_command(self, event: AstrMessageEvent, value: GreedyStr):
        """测试链接跟随：/webfollow PAGE_ID LINK_ID"""
        event.stop_event()
        parts = str(value).strip().split(maxsplit=1)
        if len(parts) != 2:
            yield event.plain_result("用法：/webfollow <页面 ID> <链接编号>")
            return
        page_id, link_id = parts
        yield event.plain_result(await self.follow_web_link(event, page_id, link_id))

    @filter.regex(r"^/webfollow(?:\s|$)")
    async def webfollow_slash_command(self, event: AstrMessageEvent):
        message = event.get_message_str().strip()
        async for result in self.webfollow_command(
            event,
            message[len("/webfollow") :],
        ):
            yield result

    @filter.command("webfind")
    async def webfind_command(self, event: AstrMessageEvent, value: GreedyStr):
        """测试页面查找：/webfind PAGE_ID QUERY"""
        event.stop_event()
        parts = str(value).strip().split(maxsplit=1)
        if len(parts) != 2:
            yield event.plain_result("用法：/webfind <页面 ID> <关键词>")
            return
        page_id, query = parts
        yield event.plain_result(await self.find_in_web_page(event, page_id, query))

    @filter.regex(r"^/webfind(?:\s|$)")
    async def webfind_slash_command(self, event: AstrMessageEvent):
        message = event.get_message_str().strip()
        async for result in self.webfind_command(
            event,
            message[len("/webfind") :],
        ):
            yield result

    async def terminate(self) -> None:
        await self.service.close()
