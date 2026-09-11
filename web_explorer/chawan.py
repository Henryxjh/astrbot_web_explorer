from __future__ import annotations

import asyncio
import re
import unicodedata
from urllib.parse import urlsplit

from .errors import ResponseTooLargeError, WebExplorerError
from .models import WebPage
from .settings import ExplorerSettings

_ANSI_OSC_RE = re.compile(r"\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)")
_ANSI_STRING_RE = re.compile(r"\x1b[PX^_].*?\x1b\\", re.DOTALL)
_ANSI_CSI_RE = re.compile(r"\x1b(?:\[[0-?]*[ -/]*[@-~]|[@-Z\\-_])")
_IMAGE_MARKER_RE = re.compile(r"\[img\]", re.IGNORECASE)
_LAYOUT_GLYPH_RE = re.compile(r"[\u2500-\u259f]+")
_HORIZONTAL_SPACE_RE = re.compile(r"[^\S\n]+")
_EXCESS_BLANK_LINES_RE = re.compile(r"\n{3,}")


def clean_chawan_dump(value: str) -> str:
    """Turn Chawan's terminal-oriented dump into compact model-readable text."""
    text = value.replace("\r\n", "\n").replace("\r", "\n")
    text = _ANSI_OSC_RE.sub("", text)
    text = _ANSI_STRING_RE.sub("", text)
    text = _ANSI_CSI_RE.sub("", text)

    # Backspace is sometimes used to overprint terminal glyphs.
    while "\b" in text:
        updated = re.sub(r"[^\n]\x08", "", text)
        if updated == text:
            text = text.replace("\b", "")
            break
        text = updated

    text = _IMAGE_MARKER_RE.sub("", text)
    text = _LAYOUT_GLYPH_RE.sub(" ", text)
    text = "".join(
        char
        for char in text
        if char in "\n\t" or unicodedata.category(char) not in {"Cc", "Cf"}
    )
    lines = []
    for line in text.splitlines():
        line = _HORIZONTAL_SPACE_RE.sub(" ", line).strip()
        # Chawan may leave a bare image link or list bullet after hiding an image.
        if re.fullmatch(r"(?:[•*+-]\s*)?\[\d+\]|[•*+-]", line):
            continue
        lines.append(line)
    return _EXCESS_BLANK_LINES_RE.sub("\n\n", "\n".join(lines)).strip()


def _host_matches(hostname: str, pattern: str) -> bool:
    hostname = hostname.rstrip(".").lower()
    pattern = pattern.rstrip(".").lower()
    if pattern.startswith("*."):
        return hostname.endswith(pattern[1:]) and hostname != pattern[2:]
    return hostname == pattern


class ChawanRenderer:
    def __init__(self, settings: ExplorerSettings) -> None:
        self.settings = settings

    def can_render(self, url: str) -> bool:
        if not self.settings.chawan_enabled:
            return False
        hostname = (urlsplit(url).hostname or "").lower()
        return any(
            _host_matches(hostname, pattern)
            for pattern in self.settings.chawan_allowed_hosts
        )

    async def render(self, url: str) -> WebPage:
        if not self.can_render(url):
            raise WebExplorerError("Chawan 未启用，或目标域名不在 Chawan 白名单中。")
        args = [
            self.settings.chawan_executable,
            "-M",
            "-d",
            "-o",
            "buffer.mark-links=true",
            "-o",
            "buffer.styling=false",
            "-o",
            "display.format-mode=[]",
        ]
        if self.settings.chawan_scripting:
            args.extend(["-o", "buffer.scripting=true"])
        args.append(url)
        try:
            process = await asyncio.create_subprocess_exec(
                *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except (FileNotFoundError, OSError) as exc:
            raise WebExplorerError(f"无法启动 Chawan：{exc}") from exc

        async def read_limited(
            stream: asyncio.StreamReader | None,
            limit: int,
        ) -> bytes:
            if stream is None:
                return b""
            output = bytearray()
            while True:
                chunk = await stream.read(64 * 1024)
                if not chunk:
                    return bytes(output)
                output.extend(chunk)
                if len(output) > limit:
                    raise ResponseTooLargeError("Chawan 输出超过大小限制。")

        try:
            stdout, stderr, return_code = await asyncio.wait_for(
                asyncio.gather(
                    read_limited(process.stdout, self.settings.max_download_bytes),
                    read_limited(process.stderr, 64 * 1024),
                    process.wait(),
                ),
                timeout=self.settings.chawan_timeout,
            )
        except TimeoutError as exc:
            process.kill()
            await process.wait()
            raise WebExplorerError("Chawan 渲染超时。") from exc
        except Exception:
            process.kill()
            await process.wait()
            raise

        if return_code != 0:
            detail = stderr.decode("utf-8", errors="replace").strip()[:500]
            raise WebExplorerError(f"Chawan 渲染失败（{return_code}）：{detail}")
        text = clean_chawan_dump(stdout.decode("utf-8", errors="replace"))
        truncated = len(text) > self.settings.max_content_chars
        if truncated:
            text = text[: self.settings.max_content_chars].rstrip() + "\n\n[正文已截断]"
        return WebPage(
            title=url,
            url=url,
            content=text,
            adapter="chawan",
            rendered_with_javascript=self.settings.chawan_scripting,
            truncated=truncated,
        )
