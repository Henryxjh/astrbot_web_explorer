class WebExplorerError(Exception):
    """A user-facing web exploration error."""


class UnsupportedTargetError(WebExplorerError):
    """No adapter can perform the requested operation."""


class UnsafeTargetError(WebExplorerError):
    """The target violates the configured network policy."""


class ResponseTooLargeError(WebExplorerError):
    """The remote response exceeded the configured size limit."""
