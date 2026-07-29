"""
logging_utils.py — Centralised logging configuration.

All agents call `get_logger(__name__)` to obtain their named logger, which
integrates with the root logger configured once at startup by run_demo.py.

Rich integration:
    If the `rich` package is installed, log output is rendered with colour,
    icons, and pretty tracebacks in the terminal.  If rich is not available
    (e.g. in CI or minimal containers), the standard StreamHandler is used
    as a graceful fallback — no code changes needed.
"""
from __future__ import annotations

import logging
import sys


def configure_root_logger(level: int = logging.INFO) -> None:
    """Configure the root logger once at application startup.

    This should be called exactly once, before any agents are imported.
    run_demo.py calls it at the top of the file.  The `force=True` argument
    removes any existing handlers so this is safe to call in tests too.

    Args:
        level: Logging level (logging.DEBUG, logging.INFO, etc.).
    """
    try:
        from rich.logging import RichHandler

        # RichHandler renders coloured, formatted log messages in the terminal.
        handler: logging.Handler = RichHandler(
            rich_tracebacks=True,   # Pretty-print exceptions with syntax highlight
            show_path=False,        # Don’t show file path — keeps output compact
            markup=True,            # Allow [bold] and [red] markup in log messages
        )
        fmt = "%(message)s"         # Rich handles timestamp/level itself
    except ImportError:
        # Fallback when rich is not installed
        handler = logging.StreamHandler(sys.stdout)
        fmt = "[%(asctime)s] %(levelname)s %(name)s — %(message)s"

    logging.basicConfig(
        level=level,
        format=fmt,
        datefmt="[%X]",
        handlers=[handler],
        force=True,   # Override any previously configured handlers
    )


def get_logger(name: str) -> logging.Logger:
    """Return a named logger for a module.

    Usage in any agent::

        logger = logging.getLogger("deck_generator.content_agent")

    The hierarchy (deck_generator → deck_generator.content_agent) means that
    any handler or level set on the parent logger automatically applies to all
    child loggers, enabling fine-grained control per module if needed.
    """
    return logging.getLogger(name)
