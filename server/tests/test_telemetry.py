import logging
import time

from app.telemetry import measure


def test_metrics_are_off_by_default(monkeypatch, caplog):
    monkeypatch.delenv("AUDIO_LENS_METRICS", raising=False)
    with caplog.at_level(logging.INFO, logger="audiolens.metrics"), measure("vad"):
        time.sleep(0.001)
    assert not caplog.records


def test_metrics_record_duration_without_content(monkeypatch, caplog):
    monkeypatch.setenv("AUDIO_LENS_METRICS", "1")
    with caplog.at_level(logging.INFO, logger="audiolens.metrics"), measure("vad"):
        time.sleep(0.001)
    assert len(caplog.records) == 1
    assert "stage=vad duration_ms=" in caplog.text
    assert "private-content" not in caplog.text


def test_timing_preserves_exception_and_still_records(monkeypatch, caplog):
    import pytest

    monkeypatch.setenv("AUDIO_LENS_METRICS", "1")
    with caplog.at_level(logging.INFO, logger="audiolens.metrics"):
        with pytest.raises(RuntimeError, match="expected"):
            with measure("translation"):
                raise RuntimeError("expected")
    assert "stage=translation duration_ms=" in caplog.text
    assert "expected" not in caplog.text


async def test_translation_exception_does_not_leak_content(config, caplog):
    from app.session import Session

    class FailingTranslator:
        def translate(self, text, source, target):
            raise ValueError("private-content-token")

    session = Session(config, None, FailingTranslator())
    with caplog.at_level(logging.DEBUG):
        assert await session._translate("private-content-token", "ru") is None
    assert "private-content-token" not in caplog.text
