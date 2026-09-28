from __future__ import annotations

import base64

import mcp
from astrbot.api import AstrBotConfig
from astrbot.api.event import AstrMessageEvent, MessageChain, filter
from astrbot.api.star import Context, Star, StarTools
from astrbot.core.star.filter.command import GreedyStr

from .web_explorer import ExplorerSettings, WebExplorerError, WebExplorerService
from .web_explorer.screenshot import (
    BrowserScreenshotService,
    parse_webshot_arguments,
)


class WebExplorerPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig) -> None:
        super().__init__(context, config)
        self.config = config
        data_dir = StarTools.get_data_dir("astrbot_web_explorer")
        self.service = WebExplorerService(
            ExplorerSettings.from_config(config, data_dir),
            self.logger,
        )
        self.screenshots = BrowserScreenshotService(
            self.service.settings,
            self.logger,
        )

    async def initialize(self) -> None:
        await self.service.initialize()
        self.logger.info(
            "Web Explorer initialized; site rules: %s",
            self.service.settings.site_rules_path,
        )
        if self.screenshots.enabled:
            self.logger.info(
                "Web screenshot enabled; allowed hosts: %s",
                ", ".join(self.screenshots.allowlist.patterns) or "[none]",
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

        当前支持 GitHub、Arch 官方仓库/AUR、MediaWiki、MC百科和 Read the Docs。
        GitHub 的 kind 可填写 auto、repositories、issues、code 或 commits；Arch
        搜索可填写 auto、all、official 或 aur；其他网站填写 auto。

        Args:
            query(string): 搜索关键词和限定条件。
            site(string): github、arch、arch-official、aur、仓库 URL 或站点 URL。
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

    @filter.llm_tool(name="capture_web_page")
    async def capture_web_page(
        self,
        event: AstrMessageEvent,
        url: str,
    ) -> mcp.types.CallToolResult:
        """截取网页当前顶部的 800×800 视图，并把图片返回给视觉模型检查。

        仅在需要观察网页视觉布局、画面或动态渲染结果时调用。读取网页文字时优先使用
        open_web_page。截图不会自动发送给用户。

        Args:
            url(string): 要截图的完整 http/https 网页地址。
        """
        del event
        try:
            screenshot = await self.screenshots.capture_for_model(url)
            text = (
                f"网页截图完成：{screenshot.width}×{screenshot.height}；"
                f"最终 URL：{screenshot.url}。请查看随工具结果返回的图片。"
            )
            return mcp.types.CallToolResult(
                content=[
                    mcp.types.TextContent(type="text", text=text),
                    mcp.types.ImageContent(
                        type="image",
                        data=base64.b64encode(screenshot.png).decode("ascii"),
                        mimeType="image/png",
                    ),
                ]
            )
        except WebExplorerError as exc:
            return mcp.types.CallToolResult(
                content=[
                    mcp.types.TextContent(
                        type="text",
                        text=f"网页截图失败：{exc}",
                    )
                ],
                isError=True,
            )

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

    @filter.command("webshot")
    async def webshot_command(self, event: AstrMessageEvent, value: GreedyStr):
        """网页截图：/webshot URL [--width N] [--max-height N]"""
        event.stop_event()
        try:
            arguments = parse_webshot_arguments(str(value).strip())
            screenshot = await self.screenshots.capture_for_user(
                arguments.url,
                arguments.width,
                arguments.max_height,
            )
        except WebExplorerError as exc:
            yield event.plain_result(f"网页截图失败：{exc}")
            return

        caption = f"网页截图：{screenshot.width}×{screenshot.height}"
        if screenshot.truncated:
            caption += f"；页面约 {screenshot.document_height}px，已按最大高度截断"
        chain = MessageChain().base64_image(
            base64.b64encode(screenshot.png).decode("ascii")
        )
        chain.message(caption)
        yield event.chain_result(chain.chain)

    @filter.regex(r"^/webshot(?:\s|$)")
    async def webshot_slash_command(self, event: AstrMessageEvent):
        message = event.get_message_str().strip()
        async for result in self.webshot_command(
            event,
            message[len("/webshot") :],
        ):
            yield result

    async def terminate(self) -> None:
        try:
            await self.screenshots.close()
        finally:
            await self.service.close()
