from __future__ import annotations

import asyncio
import base64
import ipaddress
import logging
import os
import select
import shlex
import shutil
import socket
import struct
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from .errors import WebExplorerError
from .settings import ExplorerSettings

_MIN_WIDTH = 320
_MAX_WIDTH = 2560
_MIN_HEIGHT = 240
_MAX_HEIGHT = 16_000
_MODEL_SIZE = 800
_PROVISIONAL_HEIGHT = 800
_STABLE_SECONDS = 0.75
_POLL_SECONDS = 0.25
_MAX_HEIGHT_ADJUSTMENTS = 2


def _canonical_host(value: str) -> str:
    host = value.strip().rstrip(".").lower()
    if not host:
        raise WebExplorerError("域名不能为空。")
    try:
        return host.encode("idna").decode("ascii")
    except UnicodeError as exc:
        raise WebExplorerError(f"域名无法转换为 IDNA：{value}") from exc


class HostAllowlist:
    def __init__(self, patterns: tuple[str, ...]) -> None:
        normalized: list[str] = []
        for raw_pattern in patterns:
            pattern = raw_pattern.strip().lower()
            wildcard = pattern.startswith("*.")
            host = _canonical_host(pattern[2:] if wildcard else pattern)
            if "://" in raw_pattern or "/" in host or ":" in host:
                raise WebExplorerError(
                    f"截图域名白名单项无效：{raw_pattern}；只能填写域名。"
                )
            if wildcard:
                try:
                    ipaddress.ip_address(host)
                except ValueError:
                    pass
                else:
                    raise WebExplorerError("截图白名单不能对 IP 地址使用通配符。")
            normalized.append(("*." if wildcard else "") + host)
        self.patterns = tuple(dict.fromkeys(normalized))

    def matches(self, hostname: str) -> bool:
        host = _canonical_host(hostname)
        for pattern in self.patterns:
            if pattern.startswith("*."):
                suffix = pattern[1:]
                if host.endswith(suffix) and host != suffix[1:]:
                    return True
            elif host == pattern:
                return True
        return False

    def to_environment(self) -> str:
        return "\n".join(self.patterns)


@dataclass(frozen=True, slots=True)
class ScreenshotResult:
    png: bytes
    width: int
    height: int
    url: str
    document_height: int
    truncated: bool
    settle_timed_out: bool


@dataclass(frozen=True, slots=True)
class WebshotArguments:
    url: str
    width: int | None = None
    max_height: int | None = None


def parse_webshot_arguments(value: str) -> WebshotArguments:
    try:
        tokens = shlex.split(value)
    except ValueError as exc:
        raise WebExplorerError(f"截图指令参数无法解析：{exc}") from exc
    if not tokens:
        raise WebExplorerError("用法：/webshot <URL> [--width N] [--max-height N]")
    url = tokens[0]
    parsed: dict[str, int] = {}
    index = 1
    while index < len(tokens):
        token = tokens[index]
        if token.startswith("--width="):
            name, raw_number = "width", token.split("=", 1)[1]
        elif token.startswith("--max-height="):
            name, raw_number = "max_height", token.split("=", 1)[1]
        elif token in {"--width", "--max-height"}:
            if index + 1 >= len(tokens):
                raise WebExplorerError(f"{token} 后缺少数字。")
            name = "width" if token == "--width" else "max_height"
            index += 1
            raw_number = tokens[index]
        else:
            raise WebExplorerError(f"未知截图参数：{token}")
        if name in parsed:
            shown_name = "--width" if name == "width" else "--max-height"
            raise WebExplorerError(f"截图参数重复：{shown_name}")
        try:
            parsed[name] = int(raw_number)
        except ValueError as exc:
            raise WebExplorerError(f"{token} 必须填写整数。") from exc
        index += 1
    return WebshotArguments(url=url, **parsed)


class BrowserScreenshotService:
    def __init__(self, settings: ExplorerSettings, logger: logging.Logger) -> None:
        self.settings = settings
        self.logger = logger
        self.allowlist = HostAllowlist(settings.screenshot_allowed_hosts)
        self._executor = ThreadPoolExecutor(
            max_workers=1,
            thread_name_prefix="astrbot-web-screenshot",
        )
        self._pending_lock = asyncio.Lock()
        self._pending = 0
        self._closed = False
        self._driver: Any = None
        self._xvfb: subprocess.Popen[bytes] | None = None
        self._display = ""

    @property
    def enabled(self) -> bool:
        return self.settings.screenshot_enabled

    async def capture_for_model(self, url: str) -> ScreenshotResult:
        return await self._submit(url, _MODEL_SIZE, _MODEL_SIZE, False)

    async def capture_for_user(
        self,
        url: str,
        width: int | None = None,
        max_height: int | None = None,
    ) -> ScreenshotResult:
        requested_width = width or self.settings.screenshot_default_width
        requested_height = max_height or self.settings.screenshot_default_max_height
        if not _MIN_WIDTH <= requested_width <= _MAX_WIDTH:
            raise WebExplorerError(f"截图宽度必须在 {_MIN_WIDTH}～{_MAX_WIDTH} 之间。")
        if not _MIN_HEIGHT <= requested_height <= _MAX_HEIGHT:
            raise WebExplorerError(
                f"截图最大高度必须在 {_MIN_HEIGHT}～{_MAX_HEIGHT} 之间。"
            )
        pixel_height = self.settings.screenshot_max_pixels // requested_width
        effective_height = min(requested_height, max(_MIN_HEIGHT, pixel_height))
        return await self._submit(url, requested_width, effective_height, True)

    async def _submit(
        self,
        url: str,
        width: int,
        height: int,
        whole_document: bool,
    ) -> ScreenshotResult:
        if not self.enabled:
            raise WebExplorerError("网页截图功能尚未启用。")
        if self._closed:
            raise WebExplorerError("网页截图服务已经关闭。")
        async with self._pending_lock:
            if self._pending >= self.settings.screenshot_queue_limit:
                raise WebExplorerError("网页截图队列已满，请稍后再试。")
            self._pending += 1
        try:
            loop = asyncio.get_running_loop()
            return await loop.run_in_executor(
                self._executor,
                self._capture_with_retry,
                url,
                width,
                height,
                whole_document,
            )
        finally:
            async with self._pending_lock:
                self._pending -= 1

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(self._executor, self._shutdown_sync)
        self._executor.shutdown(wait=True, cancel_futures=True)

    def _capture_with_retry(
        self,
        raw_url: str,
        width: int,
        height: int,
        whole_document: bool,
    ) -> ScreenshotResult:
        url = self._validate_url_sync(raw_url)
        last_error: Exception | None = None
        for attempt in range(2):
            try:
                self._ensure_driver_sync()
                return self._capture_once(url, width, height, whole_document)
            except WebExplorerError:
                raise
            except Exception as exc:  # noqa: BLE001 - browser failures vary by backend
                last_error = exc
                self.logger.warning(
                    "Screenshot browser attempt %d failed: %s",
                    attempt + 1,
                    exc,
                )
                self._stop_driver_sync()
        raise WebExplorerError(f"网页截图失败：{last_error}") from last_error

    def _ensure_driver_sync(self) -> None:
        if self._driver is not None:
            try:
                _ = self._driver.current_url
                return
            except Exception:  # noqa: BLE001 - stale Selenium sessions vary by backend
                self._stop_driver_sync()

        driver_path = Path(self.settings.screenshot_webdriver_path)
        browser_path = Path(self.settings.screenshot_browser_path)
        for label, path in (
            ("WebEngineDriver", driver_path),
            ("Qt WebEngine 浏览器", browser_path),
        ):
            if not path.is_file() or not os.access(path, os.X_OK):
                raise WebExplorerError(f"{label} 不存在或不可执行：{path}")

        display = self.settings.screenshot_display
        if not display:
            display = self._start_xvfb_sync()
        self._display = display

        from selenium import webdriver
        from selenium.webdriver.chrome.service import Service

        env = os.environ.copy()
        env["DISPLAY"] = display
        env["QTWEBENGINE_REMOTE_DEBUGGING"] = str(self._free_tcp_port())
        env["QTWEBENGINE_CHROMIUM_FLAGS"] = "--remote-allow-origins=*"
        env["ASTRBOT_WEB_EXPLORER_ALLOWED_HOSTS"] = self.allowlist.to_environment()
        env["ASTRBOT_WEB_EXPLORER_ALLOW_PRIVATE_NETWORK"] = (
            "1" if self.settings.allow_private_network else "0"
        )
        service = Service(
            executable_path=str(driver_path),
            env=env,
            log_output=subprocess.DEVNULL,
        )
        options = webdriver.ChromeOptions()
        options.binary_location = str(browser_path)
        self._driver = webdriver.Chrome(service=service, options=options)
        self._driver.set_page_load_timeout(self.settings.screenshot_page_load_timeout)

    def _start_xvfb_sync(self) -> str:
        executable = shutil.which(self.settings.screenshot_xvfb_path)
        if executable is None:
            raise WebExplorerError(f"找不到 Xvfb：{self.settings.screenshot_xvfb_path}")
        read_fd, write_fd = os.pipe()
        try:
            self._xvfb = subprocess.Popen(
                [
                    executable,
                    "-displayfd",
                    str(write_fd),
                    "-screen",
                    "0",
                    "1920x1080x24",
                    "-nolisten",
                    "tcp",
                ],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                pass_fds=(write_fd,),
                start_new_session=True,
            )
        finally:
            os.close(write_fd)
        try:
            ready, _, _ = select.select([read_fd], [], [], 5.0)
            if not ready:
                raise WebExplorerError("等待 Xvfb 分配 DISPLAY 超时。")
            display_number = os.read(read_fd, 32).decode().strip()
            if not display_number.isdigit():
                raise WebExplorerError("Xvfb 没有返回有效的 DISPLAY。")
            return f":{display_number}"
        except Exception:
            self._stop_xvfb_sync()
            raise
        finally:
            os.close(read_fd)

    @staticmethod
    def _free_tcp_port() -> int:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind(("127.0.0.1", 0))
            return int(sock.getsockname()[1])

    def _validate_url_sync(self, raw_url: str) -> str:
        url = raw_url.strip()
        if not url:
            raise WebExplorerError("截图 URL 不能为空。")
        if "://" not in url:
            url = "https://" + url
        try:
            parsed = urlsplit(url)
            port = parsed.port
        except ValueError as exc:
            raise WebExplorerError(f"截图 URL 无效：{exc}") from exc
        if parsed.scheme.lower() not in {"http", "https"}:
            raise WebExplorerError("网页截图只允许 http 或 https URL。")
        if not parsed.hostname:
            raise WebExplorerError("截图 URL 缺少域名。")
        if parsed.username is not None or parsed.password is not None:
            raise WebExplorerError("截图 URL 不能包含用户名或密码。")
        if port is not None and not 1 <= port <= 65535:
            raise WebExplorerError("截图 URL 端口无效。")
        host = _canonical_host(parsed.hostname)
        if not self.allowlist.matches(host):
            raise WebExplorerError(f"域名不在网页截图白名单中：{host}")
        if not self.settings.allow_private_network:
            self._require_public_host(
                host, port or (443 if parsed.scheme == "https" else 80)
            )
        return url

    @staticmethod
    def _require_public_host(host: str, port: int) -> None:
        try:
            literal = ipaddress.ip_address(host)
        except ValueError:
            literal = None
        if literal is not None:
            if not literal.is_global:
                raise WebExplorerError("网页截图不允许访问本机、内网或保留地址。")
            return
        try:
            addresses = socket.getaddrinfo(
                host,
                port,
                type=socket.SOCK_STREAM,
            )
        except OSError as exc:
            raise WebExplorerError(f"截图域名解析失败：{host}：{exc}") from exc
        if not addresses:
            raise WebExplorerError(f"截图域名没有可用地址：{host}")
        for address in addresses:
            ip = ipaddress.ip_address(address[4][0])
            if not ip.is_global:
                raise WebExplorerError(f"截图域名解析到了不允许访问的地址：{ip}")

    def _capture_once(
        self,
        url: str,
        width: int,
        max_height: int,
        whole_document: bool,
    ) -> ScreenshotResult:
        driver = self._driver
        if driver is None:
            raise RuntimeError("截图浏览器尚未启动。")
        provisional_height = _MODEL_SIZE if not whole_document else _PROVISIONAL_HEIGHT
        self._set_viewport(driver, width, provisional_height)
        try:
            driver.get(url)
            current_url = self._validate_url_sync(driver.current_url)
            document_height = self._measure_page(driver)["height"]
            if whole_document:
                target_height = min(max_height, max(1, document_height))
            else:
                target_height = _MODEL_SIZE
            self._set_viewport(driver, width, target_height)
            target_height, document_height, timed_out, current_url = self._settle(
                driver,
                width,
                target_height,
                max_height,
                whole_document,
                current_url,
            )
            encoded = driver.execute_cdp_cmd(
                "Page.captureScreenshot",
                {
                    "format": "png",
                    "fromSurface": True,
                    "captureBeyondViewport": True,
                    "clip": {
                        "x": 0,
                        "y": 0,
                        "width": width,
                        "height": target_height,
                        "scale": 1,
                    },
                },
            )["data"]
            png = base64.b64decode(encoded, validate=True)
            actual_width, actual_height = self._png_size(png)
            if (actual_width, actual_height) != (width, target_height):
                raise RuntimeError(
                    "浏览器返回了错误尺寸的截图："
                    f"{actual_width}x{actual_height}，预期 {width}x{target_height}"
                )
            if len(png) > self.settings.screenshot_max_png_bytes:
                raise WebExplorerError(
                    "生成的 PNG 超过配置的最大文件大小，请降低宽度或最大高度。"
                )
            return ScreenshotResult(
                png=png,
                width=actual_width,
                height=actual_height,
                url=current_url,
                document_height=document_height,
                truncated=whole_document and document_height > target_height,
                settle_timed_out=timed_out,
            )
        finally:
            try:
                driver.execute_cdp_cmd("Emulation.clearDeviceMetricsOverride", {})
                driver.get("about:blank")
            except Exception:  # noqa: BLE001 - cleanup must not mask capture errors
                self._stop_driver_sync()

    def _settle(
        self,
        driver: Any,
        width: int,
        target_height: int,
        max_height: int,
        whole_document: bool,
        current_url: str,
    ) -> tuple[int, int, bool, str]:
        timeout = self.settings.screenshot_settle_timeout
        if timeout <= 0:
            return (
                target_height,
                self._measure_page(driver)["height"],
                False,
                current_url,
            )
        deadline = time.monotonic() + timeout
        stable_since: float | None = None
        last_height: int | None = None
        adjustments = 0
        document_height = target_height
        last_checked_url = current_url

        while time.monotonic() < deadline:
            time.sleep(min(_POLL_SECONDS, max(0.0, deadline - time.monotonic())))
            observed_url = driver.current_url
            if observed_url != last_checked_url:
                last_checked_url = self._validate_url_sync(observed_url)
            metrics = self._measure_page(driver)
            document_height = metrics["height"]
            if whole_document:
                desired_height = min(max_height, max(1, document_height))
                if (
                    desired_height != target_height
                    and adjustments < _MAX_HEIGHT_ADJUSTMENTS
                ):
                    target_height = desired_height
                    adjustments += 1
                    self._set_viewport(driver, width, target_height)
                    stable_since = None
                    last_height = None
                    continue

            now = time.monotonic()
            if document_height == last_height:
                stable_since = stable_since or now
            else:
                stable_since = now
                last_height = document_height
            if (
                stable_since is not None
                and now - stable_since >= _STABLE_SECONDS
                and metrics["fonts_loaded"]
                and metrics["pending_images"] == 0
            ):
                return target_height, document_height, False, last_checked_url

        return target_height, document_height, True, last_checked_url

    @staticmethod
    def _set_viewport(driver: Any, width: int, height: int) -> None:
        driver.execute_cdp_cmd(
            "Emulation.setDeviceMetricsOverride",
            {
                "width": width,
                "height": height,
                "deviceScaleFactor": 1,
                "mobile": False,
            },
        )

    @staticmethod
    def _measure_page(driver: Any) -> dict[str, Any]:
        result = driver.execute_script(
            """
            const root = document.documentElement;
            const body = document.body;
            const height = Math.max(
              root ? root.scrollHeight : 0,
              root ? root.offsetHeight : 0,
              body ? body.scrollHeight : 0,
              body ? body.offsetHeight : 0,
              window.innerHeight || 0
            );
            const pendingImages = Array.from(document.images || []).filter((img) => {
              const rect = img.getBoundingClientRect();
              return rect.bottom >= 0 && rect.top <= window.innerHeight && !img.complete;
            }).length;
            return {
              height,
              fontsLoaded: !document.fonts || document.fonts.status === "loaded",
              pendingImages,
            };
            """
        )
        return {
            "height": max(1, int(result.get("height") or 1)),
            "fonts_loaded": bool(result.get("fontsLoaded", True)),
            "pending_images": max(0, int(result.get("pendingImages") or 0)),
        }

    @staticmethod
    def _png_size(data: bytes) -> tuple[int, int]:
        if data[:8] != b"\x89PNG\r\n\x1a\n" or data[12:16] != b"IHDR":
            raise RuntimeError("浏览器截图不是有效的 PNG。")
        return struct.unpack(">II", data[16:24])

    def _stop_driver_sync(self) -> None:
        driver, self._driver = self._driver, None
        if driver is not None:
            try:
                driver.quit()
            except Exception as exc:  # noqa: BLE001 - best-effort process cleanup
                self.logger.warning("Failed to quit screenshot browser: %s", exc)
        if not self.settings.screenshot_display:
            self._stop_xvfb_sync()

    def _stop_xvfb_sync(self) -> None:
        process, self._xvfb = self._xvfb, None
        if process is None or process.poll() is not None:
            return
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)

    def _shutdown_sync(self) -> None:
        self._stop_driver_sync()
        self._stop_xvfb_sync()
