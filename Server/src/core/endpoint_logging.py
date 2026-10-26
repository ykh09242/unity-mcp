"""Render operator endpoints without URL credentials in application or HTTPX logs."""

import logging
from urllib.parse import urlsplit, urlunsplit

try:
    import httpx
except ImportError:
    httpx = None  # Telemetry retains its standard-library sender fallback.


def endpoint_for_log(endpoint: str) -> str:
    """Drop all userinfo, query and fragments; malformed URLs have no display value."""
    try:
        parsed = urlsplit(endpoint)
        host = parsed.hostname
        port = parsed.port
        if (
            parsed.scheme not in ("http", "https")
            or not host
            or any(char.isspace() or ord(char) < 32 for char in endpoint)
            or "\\" in endpoint
        ):
            return "<invalid endpoint>"
        authority = f"[{host}]" if ":" in host else host
        if port is not None:
            authority += f":{port}"
        return urlunsplit((parsed.scheme, authority, parsed.path, "", ""))
    except ValueError:
        return "<invalid endpoint>"


class _HttpxEndpointFilter(logging.Filter):
    """HTTPX emits URL objects in request diagnostics even for successful requests."""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.args, tuple):
            record.args = tuple(
                endpoint_for_log(str(value))
                if httpx is not None and isinstance(value, httpx.URL)
                else value
                for value in record.args
            )
        return True


def protect_httpx_endpoint_logs() -> None:
    """Install once on the producer so every attached handler sees redacted URLs."""
    logger = logging.getLogger("httpx")
    if not any(isinstance(item, _HttpxEndpointFilter) for item in logger.filters):
        logger.addFilter(_HttpxEndpointFilter())
