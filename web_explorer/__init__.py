"""Core implementation for the Web Explorer plugin."""

from .errors import WebExplorerError
from .screenshot import BrowserScreenshotService, ScreenshotResult
from .service import WebExplorerService
from .settings import ExplorerSettings

__all__ = [
    "BrowserScreenshotService",
    "ExplorerSettings",
    "ScreenshotResult",
    "WebExplorerError",
    "WebExplorerService",
]
