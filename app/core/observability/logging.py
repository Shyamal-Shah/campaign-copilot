from __future__ import annotations

import json
import logging
import sys

_logger = logging.getLogger("campaign_copilot")
_configured = False


def configure_logging(level: int = logging.INFO) -> None:
    """Attach a single stdout handler that emits the message verbatim (we pre-format JSON)."""
    global _configured
    if _configured:
        return
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter("%(message)s"))
    _logger.addHandler(handler)
    _logger.setLevel(level)
    _logger.propagate = False
    _configured = True


def log_event(trace_id: str | None, event: str, **fields) -> None:
    """Emit one structured log line."""
    configure_logging()
    _logger.info(json.dumps({"trace_id": trace_id, "event": event, **fields}, default=str))
