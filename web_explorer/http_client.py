from __future__ import annotations

import ipaddress
import socket
from dataclasses import dataclass
from typing import Any
from urllib.parse import urljoin, urlsplit

import aiohttp
from aiohttp.abc import AbstractResolver

from .errors import (
    ResponseTooLargeError,
    UnsafeTargetError,
    WebExplorerError,
)
from .settings import ExplorerSettings


def _is_forbidden_ip(address: str) -> bool:
    ip = ipaddress.ip_address(address)
    return not ip.is_global


class SafeResolver(AbstractResolver):
    """Validate the addresses actually handed to aiohttp's connector."""

    def __init__(self, allow_private_network: bool) -> None:
        self.allow_private_network = allow_private_network
        self._resolver = aiohttp.resolver.DefaultResolver()

    async def resolve(
        self,
        host: str,
        port: int = 0,
        family: socket.AddressFamily = socket.AF_UNSPEC,
    ) -> list[dict[str, Any]]:
        results = await self._resolver.resolve(host, port, family)
        if not self.allow_private_network:
            for result in results:
                address = str(result["host"])
                if _is_forbidden_ip(address):
                    raise UnsafeTargetError(
                        f"目标域名解析到了不允许访问的地址：{address}"
                    )
        return results

    async def close(self) -> None:
        await self._resolver.close()


@dataclass(slots=True)
class HttpResponse:
    url: str
    status: int
    headers: dict[str, str]
    body: bytes
    charset: str | None = None

    def text(self) -> str:
        encoding = self.charset or "utf-8"
        try:
            return self.body.decode(encoding, errors="replace")
        except LookupError:
            return self.body.decode("utf-8", errors="replace")

    def json(self) -> Any:
        import json

        try:
            return json.loads(self.text())
        except json.JSONDecodeError as exc:
            raise WebExplorerError("远端返回的内容不是有效 JSON。") from exc


class SafeHttpClient:
    def __init__(self, settings: ExplorerSettings) -> None:
        self.settings = settings
        self._session: aiohttp.ClientSession | None = None

    async def start(self) -> None:
        if self._session is not None and not self._session.closed:
            return
        resolver = SafeResolver(self.settings.allow_private_network)
        connector = aiohttp.TCPConnector(
            resolver=resolver,
            use_dns_cache=False,
            limit=10,
        )
        self._session = aiohttp.ClientSession(
            connector=connector,
            timeout=aiohttp.ClientTimeout(total=self.settings.request_timeout),
            headers={"User-Agent": self.settings.user_agent},
            trust_env=False,
        )

    async def close(self) -> None:
        if self._session is not None:
            await self._session.close()
            self._session = None

    def validate_url(self, url: str) -> str:
        try:
            parsed = urlsplit(url.strip())
            port = parsed.port
        except ValueError as exc:
            raise UnsafeTargetError(f"URL 无效：{exc}") from exc
        if parsed.scheme not in {"http", "https"}:
            raise UnsafeTargetError("只允许访问 http 或 https URL。")
        if not parsed.hostname:
            raise UnsafeTargetError("URL 缺少主机名。")
        if parsed.username is not None or parsed.password is not None:
            raise UnsafeTargetError("URL 中不能包含用户名或密码。")
        if port is not None and not 1 <= port <= 65535:
            raise UnsafeTargetError("URL 端口无效。")
        if not self.settings.allow_private_network:
            try:
                if _is_forbidden_ip(parsed.hostname):
                    raise UnsafeTargetError("不允许访问本机、内网或保留地址。")
            except ValueError:
                pass
        return url.strip()

    async def get(
        self,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        sensitive_origin: str = "",
    ) -> HttpResponse:
        await self.start()
        assert self._session is not None
        current_url = self.validate_url(url)
        base_headers = dict(headers or {})

        for redirect_index in range(self.settings.max_redirects + 1):
            request_headers = dict(base_headers)
            current = urlsplit(current_url)
            hostname = (current.hostname or "").lower()
            if sensitive_origin and (
                hostname != sensitive_origin.lower() or current.scheme != "https"
            ):
                request_headers.pop("Authorization", None)
            try:
                async with self._session.get(
                    current_url,
                    headers=request_headers,
                    allow_redirects=False,
                ) as response:
                    if 300 <= response.status < 400 and response.headers.get(
                        "Location"
                    ):
                        if redirect_index >= self.settings.max_redirects:
                            raise WebExplorerError("网页重定向次数超过限制。")
                        current_url = self.validate_url(
                            urljoin(str(response.url), response.headers["Location"])
                        )
                        continue

                    length = response.headers.get("Content-Length")
                    if length:
                        try:
                            if int(length) > self.settings.max_download_bytes:
                                raise ResponseTooLargeError(
                                    "网页响应超过最大下载大小。"
                                )
                        except ValueError:
                            pass

                    body = bytearray()
                    async for chunk in response.content.iter_chunked(64 * 1024):
                        body.extend(chunk)
                        if len(body) > self.settings.max_download_bytes:
                            raise ResponseTooLargeError("网页响应超过最大下载大小。")
                    result = HttpResponse(
                        url=str(response.url),
                        status=response.status,
                        headers={
                            key.lower(): value
                            for key, value in response.headers.items()
                        },
                        body=bytes(body),
                        charset=response.charset,
                    )
                    if response.status >= 400:
                        message = result.text().strip().replace("\n", " ")[:300]
                        suffix = f"：{message}" if message else ""
                        rate_details = []
                        if response.headers.get("Retry-After"):
                            rate_details.append(
                                f"Retry-After={response.headers['Retry-After']}"
                            )
                        if response.headers.get("X-RateLimit-Remaining") == "0":
                            rate_details.append("API 配额已耗尽")
                        if response.headers.get("X-RateLimit-Reset"):
                            rate_details.append(
                                f"reset={response.headers['X-RateLimit-Reset']}"
                            )
                        if rate_details:
                            suffix += "（" + "，".join(rate_details) + "）"
                        raise WebExplorerError(
                            f"远端返回 HTTP {response.status}{suffix}"
                        )
                    return result
            except (aiohttp.ClientError, TimeoutError, OSError) as exc:
                raise WebExplorerError(f"网页请求失败：{exc}") from exc

        raise WebExplorerError("网页重定向次数超过限制。")
