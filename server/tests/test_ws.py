from __future__ import annotations

import json

import pytest
from app.config import Config
from app.main import create_app
from app.stt import ModelSpec, Segment, Transcription
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from .conftest import TOKEN


class FakeEngine:
    def __init__(self, spec: ModelSpec) -> None:
        self.spec = spec
        self.config = None
        self.reload_calls = 0

    def update_config(self, config) -> None:
        self.config = config

    def loaded_models(self) -> list[str]:
        return [self.spec.model]

    @property
    def current_spec(self) -> ModelSpec:
        return self.spec

    async def ensure_model(self, requested_model: str | None = None) -> ModelSpec:
        if requested_model:
            return ModelSpec(
                model=requested_model if requested_model in ("tiny", "base") else self.spec.model,
                device="cpu",
                compute_type="int8",
                requested_model=requested_model,
            )
        return self.spec

    async def transcribe(self, audio, **kwargs) -> Transcription:
        return Transcription(
            segments=[Segment(start_s=0.0, end_s=1.0, text=" hi")],
            language="en",
            language_probability=0.9,
            duration_s=audio.size / 16000,
            inference_s=0.05,
            spec=self.spec,
        )

    async def reload(self) -> ModelSpec:
        self.reload_calls += 1
        return self.spec


def make_client(config: Config) -> TestClient:
    app = create_app(config=config, engine=FakeEngine(ModelSpec("small", "cpu", "int8")))
    return TestClient(app)


START = {
    "type": "start",
    "protocol": 1,
    "audio": {"format": "pcm_s16le", "sample_rate": 16000, "channels": 1},
    "source_lang": "auto",
    "target_lang": "fr",
    "whisper_model": "small",
    "translate": True,
    "partials": True,
}


def test_health_is_public(config: Config) -> None:
    with make_client(config) as client:
        response = client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["model"] == "small"
    assert body["active_sessions"] == 0


def test_invalid_token_is_rejected(config: Config) -> None:
    with make_client(config) as client:
        with client.websocket_connect("/ws?token=wrong") as ws:
            message = ws.receive_json()
            assert message["type"] == "error"
            assert message["code"] == "unauthorized"
            with pytest.raises(WebSocketDisconnect) as excinfo:
                ws.receive_json()
            assert excinfo.value.code == 4401


def test_start_stream_stop_roundtrip(config: Config) -> None:
    with make_client(config) as client:
        with client.websocket_connect(f"/ws?token={TOKEN}") as ws:
            ws.send_text(json.dumps(START))
            ready = ws.receive_json()
            assert ready["type"] == "ready"
            assert ready["whisper_model"] == "small"
            assert ready["whisper_model_accepted"] is True
            assert ready["whisper_device"] == "cpu"
            assert ready["whisper_compute_type"] == "int8"

            ws.send_bytes(b"\x01\x00" * 16000)
            ws.send_text('{"type":"ping"}')
            assert ws.receive_json() == {"type": "pong"}

            ws.send_text('{"type":"stop"}')
            messages = []
            try:
                while True:
                    messages.append(ws.receive_json())
            except WebSocketDisconnect:
                pass
            finals = [m for m in messages if m["type"] == "final"]
            assert finals, messages
            assert finals[0]["text_src"] == "hi"
            assert finals[0]["segment_id"] == 0


def test_disallowed_model_falls_back_to_config(config: Config) -> None:
    with make_client(config) as client:
        with client.websocket_connect(f"/ws?token={TOKEN}") as ws:
            payload = dict(START, whisper_model="large-v3")
            ws.send_text(json.dumps(payload))
            ready = ws.receive_json()
            assert ready["whisper_model"] == "small"
            assert ready["whisper_model_requested"] == "large-v3"
            assert ready["whisper_model_accepted"] is False


def test_token_can_be_sent_as_bearer_header(config: Config) -> None:
    with make_client(config) as client:
        with client.websocket_connect("/ws", headers={"Authorization": f"Bearer {TOKEN}"}) as ws:
            ws.send_text(json.dumps(START))
            assert ws.receive_json()["type"] == "ready"


def test_bad_first_message_is_rejected(config: Config) -> None:
    with make_client(config) as client:
        with client.websocket_connect(f"/ws?token={TOKEN}") as ws:
            ws.send_text('{"type":"ping"}')
            message = ws.receive_json()
            assert message["code"] == "bad_request"


def test_unsupported_protocol_is_rejected(config: Config) -> None:
    with make_client(config) as client:
        with client.websocket_connect(f"/ws?token={TOKEN}") as ws:
            ws.send_text(json.dumps(dict(START, protocol=99)))
            message = ws.receive_json()
            assert message["code"] == "unsupported_protocol"


def test_session_limit(config_factory) -> None:
    config = config_factory(server={"max_sessions": 1})
    with make_client(config) as client:
        with client.websocket_connect(f"/ws?token={TOKEN}") as first:
            first.send_text(json.dumps(START))
            assert first.receive_json()["type"] == "ready"
            with client.websocket_connect(f"/ws?token={TOKEN}") as second:
                message = second.receive_json()
                assert message["code"] == "session_limit"


def test_reload_requires_token(config: Config) -> None:
    with make_client(config) as client:
        assert client.post("/admin/reload").status_code == 401
        assert client.post(f"/admin/reload?token={TOKEN}").status_code == 200
        assert client.get("/health").json()["model"] == "small"
