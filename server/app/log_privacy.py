"""Protect framework logs from query-string credentials."""

import logging
import re


class QueryRedactionFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        # Uvicorn logs WebSocket request paths, including optional query tokens.
        message = record.getMessage()
        record.msg = re.sub(r"\?[^\s\"']+", "?<redacted>", message)
        record.args = ()
        return True
