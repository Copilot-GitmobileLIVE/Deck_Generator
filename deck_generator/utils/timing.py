"""
timing.py — Lightweight timing utilities for agent performance tracking.

Each agent that calls an LLM or external API wraps its main operation in
a `timer()` context manager so wall-clock time is always logged.  This makes
it easy to identify slow steps when profiling the pipeline.

Usage::

    from deck_generator.utils.timing import timer

    with timer("ContentAgent.run", logger):
        result = await chain.ainvoke(...)   # Timed block
    # Outputs: [TIMING] ContentAgent.run: 4.23s
"""
from __future__ import annotations

import logging
import time
from contextlib import contextmanager
from typing import Generator, Optional


@contextmanager
def timer(
    name: str,
    logger: Optional[logging.Logger] = None,
) -> Generator[None, None, None]:
    """Context manager that logs elapsed wall-clock time on exit.

    Args:
        name:   Label for the timed block, included in the log message.
        logger: If provided, the message is sent to this logger at INFO level.
                If None, the message is printed to stdout (useful in scripts).

    Example::

        with timer("ContentAgent.run", logger):
            response = await chain.ainvoke(inputs)
        # Logs: [TIMING] ContentAgent.run: 4.23s
    """
    start = time.perf_counter()   # High-resolution monotonic clock
    try:
        yield  # Execute the body of the 'with' block
    finally:
        # This block always runs, even if an exception is raised inside the
        # 'with' block, so timing is always recorded.
        elapsed = time.perf_counter() - start
        msg = f"[TIMING] {name}: {elapsed:.2f}s"
        if logger:
            logger.info(msg)
        else:
            print(msg)
