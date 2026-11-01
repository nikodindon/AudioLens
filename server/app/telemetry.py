"""Optional local timing diagnostics; never records audio, text or credentials."""

from __future__ import annotations

import logging
import os
import time
from collections.abc import Iterator
from contextlib import contextmanager

logger = logging.getLogger("audiolens.metrics")


def enabled() -> bool:
    return os.environ.get("AUDIO_LENS_METRICS") == "1"


@contextmanager
def measure(stage: str) -> Iterator[None]:
    if not enabled():
        yield
        return
    started = time.monotonic()
    try:
        yield
    finally:
        logger.info("stage=%s duration_ms=%.3f", stage, (time.monotonic() - started) * 1000)
