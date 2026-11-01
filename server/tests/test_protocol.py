from __future__ import annotations

import json

import pytest
from app.protocol import (
    ClientMessageError,
    ConfigMessage,
    FinalMessage,
    PingMessage,
    StartMessage,
    StopMessage,
    parse_client_message,
)


def test_start_defaults() -> None:
    message = parse_client_message(json.dumps({"type": "start"}))
    assert isinstance(message, StartMessage)
    assert message.protocol == 1
    assert message.audio.sample_rate == 16000
    assert message.audio.channels == 1
    assert message.source_lang == "auto"
    assert message.whisper_model is None


def test_start_full_message() -> None:
    message = parse_client_message(
        json.dumps(
            {
                "type": "start",
                "protocol": 1,
                "audio": {"format": "pcm_s16le", "sample_rate": 16000, "channels": 1},
                "source_lang": "ja",
                "target_lang": "fr",
                "whisper_model": "small",
                "translate": True,
                "partials": False,
            }
        )
    )
    assert isinstance(message, StartMessage)
    assert message.whisper_model == "small"
    assert message.partials is False


@pytest.mark.parametrize(
    "payload",
    [
        {"type": "start", "audio": {"format": "pcm_f32le"}},
        {"type": "start", "audio": {"sample_rate": 48000}},
        {"type": "start", "audio": {"channels": 2}},
    ],
)
def test_start_rejects_unsupported_audio(payload: dict) -> None:
    with pytest.raises(ClientMessageError):
        parse_client_message(json.dumps(payload))


def test_start_ignores_unknown_fields_for_forward_compat() -> None:
    message = parse_client_message(json.dumps({"type": "start", "future_flag": True}))
    assert isinstance(message, StartMessage)


def test_control_messages() -> None:
    assert isinstance(parse_client_message('{"type":"stop"}'), StopMessage)
    assert isinstance(parse_client_message('{"type":"ping"}'), PingMessage)
    config = parse_client_message('{"type":"config","target_lang":"en"}')
    assert isinstance(config, ConfigMessage)
    assert config.target_lang == "en"


def test_unknown_type_is_rejected() -> None:
    with pytest.raises(ClientMessageError):
        parse_client_message('{"type":"dance"}')


def test_invalid_json_is_rejected() -> None:
    with pytest.raises(ClientMessageError):
        parse_client_message("{not json")


def test_final_message_serialises_null_translation() -> None:
    message = FinalMessage(
        segment_id=0,
        start_ms=0,
        end_ms=1000,
        src_lang="ja",
        src_lang_prob=0.9,
        text_src="こんにちは",
    )
    payload = json.loads(message.model_dump_json())
    assert payload["text_tgt"] is None
    assert payload["type"] == "final"
