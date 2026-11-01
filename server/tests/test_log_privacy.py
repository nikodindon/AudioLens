import logging

from app.log_privacy import QueryRedactionFilter


def test_websocket_query_credentials_are_removed():
    record = logging.LogRecord(
        "uvicorn.error",
        logging.INFO,
        "",
        0,
        "WebSocket %s [accepted]",
        ("/ws?token=private-credential&x=value",),
        None,
    )
    assert QueryRedactionFilter().filter(record)
    assert "private-credential" not in record.getMessage()
    assert "/ws?<redacted>" in record.getMessage()
