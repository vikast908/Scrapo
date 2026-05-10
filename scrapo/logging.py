"""structlog configuration.

The library never configures logging on import (that is the caller's call).
But the CLI and the MCP server are applications, so they call
:func:`configure_logging` once at startup. Honors ``SCRAPO_LOG_LEVEL`` and
``SCRAPO_LOG_FORMAT`` (``console`` or ``json``).
"""

from __future__ import annotations

import logging
import os
import sys

import structlog

_configured = False


def configure_logging(level: str | None = None, fmt: str | None = None) -> None:
    """Set up structlog once. Safe to call multiple times."""
    global _configured
    if _configured:
        return

    level_name = (level or os.environ.get("SCRAPO_LOG_LEVEL", "INFO")).upper()
    log_level = getattr(logging, level_name, logging.INFO)
    fmt = (fmt or os.environ.get("SCRAPO_LOG_FORMAT", "console")).lower()

    logging.basicConfig(format="%(message)s", stream=sys.stderr, level=log_level)

    renderer: structlog.types.Processor = (
        structlog.processors.JSONRenderer()
        if fmt == "json"
        else structlog.dev.ConsoleRenderer(colors=sys.stderr.isatty())
    )
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            renderer,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(log_level),
        logger_factory=structlog.PrintLoggerFactory(file=sys.stderr),
        cache_logger_on_first_use=True,
    )
    _configured = True
