import functools
import inspect
import logging
import time
from typing import Callable, Any

logger = logging.getLogger("mcp-for-unity-server")


def log_execution(name: str, type_label: str):
    """Log execution metadata without persisting request, result or exception payloads."""

    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        def _sync_wrapper(*args, **kwargs) -> Any:
            started = time.monotonic()
            logger.info("%s '%s' started", type_label, name)
            try:
                result = func(*args, **kwargs)
                logger.info(
                    "%s '%s' completed in %.3fs", type_label, name, time.monotonic() - started
                )
                return result
            except Exception as e:
                logger.info("%s '%s' failed (%s)", type_label, name, type(e).__name__)
                raise

        @functools.wraps(func)
        async def _async_wrapper(*args, **kwargs) -> Any:
            started = time.monotonic()
            logger.info("%s '%s' started", type_label, name)
            try:
                result = await func(*args, **kwargs)
                logger.info(
                    "%s '%s' completed in %.3fs", type_label, name, time.monotonic() - started
                )
                return result
            except Exception as e:
                logger.info("%s '%s' failed (%s)", type_label, name, type(e).__name__)
                raise

        return _async_wrapper if inspect.iscoroutinefunction(func) else _sync_wrapper

    return decorator
