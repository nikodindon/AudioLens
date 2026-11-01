"""FastAPI application and ``/ws`` endpoint for AudioLens.

Run with:  python -m app.main --config config.yaml
or:        uvicorn app.main:create_app --factory --host 127.0.0.1 --port 8765
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import hmac
import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from starlette.websockets import WebSocketState

from . import __version__
from .config import Config, ConfigError, load_config, resolve_config_path
from .protocol import (
    PROTOCOL_VERSION,
    ClientMessageError,
    ConfigMessage,
    ErrorMessage,
    PingMessage,
    PongMessage,
    ReadyMessage,
    StartMessage,
    StopMessage,
    parse_client_message,
)
from .session import AudioError, Session
from .stt import ModelLoadError, ModelSpec, WhisperEngine

logger = logging.getLogger(__name__)

START_TIMEOUT_S = 15.0
WS_UNAUTHORIZED = 4401
WS_BAD_REQUEST = 4400
WS_TOO_MANY_SESSIONS = 4429
WS_TIMEOUT = 4408
WS_INTERNAL_ERROR = 1011


def configure_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )


def _token_matches(provided: str | None, expected: str) -> bool:
    if not provided:
        return False
    return hmac.compare_digest(provided, expected)


def _extract_token(websocket: WebSocket) -> str | None:
    token = websocket.query_params.get("token")
    if token:
        return token
    authorization = websocket.headers.get("authorization", "")
    if authorization.lower().startswith("bearer "):
        return authorization[7:].strip()
    return None


def _extract_http_token(request: Request) -> str | None:
    token = request.query_params.get("token")
    if token:
        return token
    authorization = request.headers.get("authorization", "")
    if authorization.lower().startswith("bearer "):
        return authorization[7:].strip()
    return None


async def _send(websocket: WebSocket, model: Any) -> None:
    if websocket.application_state == WebSocketState.CONNECTED:
        await websocket.send_text(model.model_dump_json(exclude_none=False))


async def _fail(websocket: WebSocket, code: str, message: str, close_code: int) -> None:
    await _send(websocket, ErrorMessage(code=code, message=message))
    with contextlib.suppress(RuntimeError):
        await websocket.close(code=close_code)


def create_app(
    config: Config | None = None,
    engine: WhisperEngine | None = None,
    config_path: str | None = None,
) -> FastAPI:
    """Application factory. Config/engine injection is used by tests."""

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        conf = config or load_config(config_path)
        try:
            resolved = resolve_config_path(config_path, dict(os.environ))
        except ConfigError:
            # Injected config without a backing file (tests): hot reload unavailable.
            resolved = None
        configure_logging(conf.server.log_level)
        logger.info("AudioLens server %s starting (protocol %s)", __version__, PROTOCOL_VERSION)
        logger.info("effective configuration: %s", conf.safe_dict())
        app.state.config = conf
        app.state.config_path = resolved
        app.state.sessions = 0
        if engine is not None:
            app.state.engine = engine
        else:
            active_engine = WhisperEngine(conf)
            spec = await active_engine.ensure_model(None)
            logger.info(
                "default Whisper model=%s device=%s compute_type=%s fallback=%s",
                spec.model,
                spec.device,
                spec.compute_type,
                spec.fallback_reason or "none",
            )
            app.state.engine = active_engine
        if conf.translation.engine != "none":
            logger.warning(
                "translation.engine=%s is configured but translation lands in Phase 3; "
                "text_tgt will be null for now",
                conf.translation.engine,
            )
        try:
            yield
        finally:
            logger.info("AudioLens server stopping")

    app = FastAPI(title="AudioLens", version=__version__, lifespan=lifespan)

    @app.get("/health")
    async def health() -> dict[str, Any]:
        current: Config = app.state.config
        spec: ModelSpec | None = app.state.engine.current_spec
        return {
            "status": "ok",
            "server_version": __version__,
            "protocol": PROTOCOL_VERSION,
            "model": spec.model if spec else current.whisper.model,
            "device": spec.device if spec else None,
            "compute_type": spec.compute_type if spec else None,
            "loaded_models": app.state.engine.loaded_models(),
            "active_sessions": app.state.sessions,
            "max_sessions": current.server.max_sessions,
            "translation_engine": current.translation.engine,
        }

    @app.post("/admin/reload")
    async def reload_config(request: Request) -> dict[str, Any]:
        current: Config = app.state.config
        if not _token_matches(_extract_http_token(request), current.token):
            raise HTTPException(status_code=401, detail="invalid token")
        if app.state.config_path is None:
            raise HTTPException(status_code=409, detail="no config file to reload")
        new_config = load_config(app.state.config_path)
        app.state.config = new_config
        app.state.engine.update_config(new_config)
        spec = await app.state.engine.reload()
        logger.info(
            "config reloaded from %s; model=%s device=%s compute_type=%s",
            app.state.config_path,
            spec.model,
            spec.device,
            spec.compute_type,
        )
        return {
            "status": "reloaded",
            "config_path": str(app.state.config_path),
            "model": spec.model,
            "device": spec.device,
            "compute_type": spec.compute_type,
        }

    @app.websocket("/ws")
    async def websocket_endpoint(websocket: WebSocket) -> None:
        await websocket.accept()
        current: Config = app.state.config
        if not _token_matches(_extract_token(websocket), current.token):
            logger.warning("rejected WebSocket: invalid or missing token")
            await _fail(websocket, "unauthorized", "invalid or missing token", WS_UNAUTHORIZED)
            return
        if app.state.sessions >= current.server.max_sessions:
            await _fail(
                websocket,
                "session_limit",
                f"too many sessions (max {current.server.max_sessions})",
                WS_TOO_MANY_SESSIONS,
            )
            return

        try:
            first = await asyncio.wait_for(websocket.receive(), timeout=START_TIMEOUT_S)
        except TimeoutError:
            await _fail(websocket, "timeout", "expected a `start` message", WS_TIMEOUT)
            return

        if first.get("type") == "websocket.disconnect":
            return
        if first.get("text") is None:
            await _fail(
                websocket, "bad_request", "first message must be a JSON `start`", WS_BAD_REQUEST
            )
            return

        try:
            message = parse_client_message(first["text"])
        except ClientMessageError as exc:
            await _fail(websocket, "bad_request", str(exc), WS_BAD_REQUEST)
            return
        if not isinstance(message, StartMessage):
            await _fail(websocket, "bad_request", "first message must be a `start`", WS_BAD_REQUEST)
            return
        if message.protocol != PROTOCOL_VERSION:
            await _fail(
                websocket,
                "unsupported_protocol",
                f"unsupported protocol {message.protocol}, server speaks {PROTOCOL_VERSION}",
                WS_BAD_REQUEST,
            )
            return

        engine_ref: WhisperEngine = app.state.engine
        try:
            spec = await engine_ref.ensure_model(message.whisper_model)
        except ModelLoadError as exc:
            logger.error("model load failed: %s", exc)
            await _fail(websocket, "model_unavailable", str(exc), WS_INTERNAL_ERROR)
            return
        except Exception:
            logger.exception("unexpected error while loading the model")
            await _fail(
                websocket, "model_unavailable", "could not load the model", WS_INTERNAL_ERROR
            )
            return

        session = Session(current, engine_ref)
        session.set_live_config(
            source_lang=message.source_lang,
            target_lang=message.target_lang,
            translate=message.translate,
            partials=message.partials,
        )
        app.state.sessions += 1
        logger.info(
            "session %s started requested_model=%s effective_model=%s device=%s clients=%d",
            session.id,
            message.whisper_model,
            spec.model,
            spec.device,
            app.state.sessions,
        )
        try:
            await _send(
                websocket,
                ReadyMessage(
                    session_id=session.id,
                    whisper_model=spec.model,
                    whisper_model_requested=message.whisper_model,
                    whisper_model_accepted=spec.model_accepted,
                    whisper_device=spec.device,
                    whisper_compute_type=spec.compute_type,
                    translation_engine=current.translation.engine,
                    translate=message.translate,
                ),
            )
            await _run_session(websocket, session)
        except WebSocketDisconnect:
            logger.info("session %s disconnected", session.id)
        finally:
            app.state.sessions -= 1

    async def _run_session(websocket: WebSocket, session: Session) -> None:
        while True:
            try:
                incoming = await websocket.receive()
            except WebSocketDisconnect:
                logger.info(
                    "session %s disconnected with %.1fs buffered", session.id, session.audio_seconds
                )
                return
            if incoming.get("type") == "websocket.disconnect":
                logger.info(
                    "session %s disconnected with %.1fs buffered", session.id, session.audio_seconds
                )
                return

            data = incoming.get("bytes")
            if data is not None:
                try:
                    session.accept_audio(data)
                except AudioError as exc:
                    await _fail(websocket, exc.code, exc.message, WS_BAD_REQUEST)
                    return
                continue

            text = incoming.get("text")
            if text is None:
                continue
            try:
                incoming_message = parse_client_message(text)
            except ClientMessageError as exc:
                await _send(websocket, ErrorMessage(code="bad_request", message=str(exc)))
                continue

            if isinstance(incoming_message, PingMessage):
                await _send(websocket, PongMessage())
            elif isinstance(incoming_message, ConfigMessage):
                session.set_live_config(
                    source_lang=incoming_message.source_lang,
                    target_lang=incoming_message.target_lang,
                    translate=incoming_message.translate,
                    partials=incoming_message.partials,
                )
            elif isinstance(incoming_message, StopMessage):
                logger.info(
                    "session %s stop requested (%.1fs audio)", session.id, session.audio_seconds
                )
                try:
                    finals = await session.finalize()
                except Exception:
                    await _fail(
                        websocket, "internal_error", "transcription failed", WS_INTERNAL_ERROR
                    )
                    return
                for final in finals:
                    await _send(websocket, final)
                with contextlib.suppress(RuntimeError):
                    await websocket.close(code=1000)
                return
            elif isinstance(incoming_message, StartMessage):
                await _send(
                    websocket,
                    ErrorMessage(code="bad_request", message="session already started"),
                )

    return app


def _load_app_config(explicit_path: str | None = None) -> Config:
    try:
        return load_config(explicit_path)
    except ConfigError as exc:
        raise SystemExit(f"AudioLens configuration error:\n{exc}") from exc


app = create_app()


def main() -> None:
    parser = argparse.ArgumentParser(description="AudioLens server")
    parser.add_argument("--config", help="path to config.yaml", default=None)
    parser.add_argument("--host", help="override server.host", default=None)
    parser.add_argument("--port", type=int, help="override server.port", default=None)
    args = parser.parse_args()

    config = _load_app_config(args.config)
    import uvicorn

    uvicorn.run(
        create_app(config=config, config_path=args.config),
        host=args.host or config.server.host,
        port=args.port or config.server.port,
        log_level=config.server.log_level.lower(),
    )


if __name__ == "__main__":
    main()
