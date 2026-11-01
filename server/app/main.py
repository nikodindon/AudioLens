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

import numpy as np
from fastapi import (
    FastAPI,
    File,
    HTTPException,
    Query,
    Request,
    UploadFile,
    WebSocket,
    WebSocketDisconnect,
)
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
from .translate import build_translator

logger = logging.getLogger(__name__)

START_TIMEOUT_S = 15.0
WS_UNAUTHORIZED = 4401
WS_BAD_REQUEST = 4400
WS_TOO_MANY_SESSIONS = 4429
WS_TIMEOUT = 4408
WS_INTERNAL_ERROR = 1011
_FAILED_ATTEMPTS: dict[str, int] = {}


def configure_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )


def _token_matches(provided: str | None, expected: str) -> bool:
    if not provided:
        return False
    return hmac.compare_digest(provided, expected)


def _extract_token(websocket: WebSocket, allow_query: bool) -> str | None:
    if allow_query:
        token = websocket.query_params.get("token")
        if token:
            return token
    authorization = websocket.headers.get("authorization", "")
    if authorization.lower().startswith("bearer "):
        return authorization[7:].strip()
    return None


def _extract_token_for_http(request: Request, config: Config) -> str | None:
    # allow_query_token controls query tokens on HTTP too
    token = request.query_params.get("token") if config.server.allow_query_token else None
    if token:
        return token
    authorization = request.headers.get("authorization", "")
    if authorization.lower().startswith("bearer "):
        return authorization[7:].strip()
    return None


def _unused_extract_http_token(request: Request) -> str | None:
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
        app.state.translator = build_translator(conf)
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

    @app.post("/transcribe")
    async def transcribe_file(
        request: Request,
        file: UploadFile = File(...),
        target_lang: str | None = Query(default=None),
        source_lang: str = Query(default="auto"),
        whisper_model: str | None = Query(default=None),
    ) -> dict[str, Any]:
        current: Config = app.state.config
        if not _token_matches(_extract_token_for_http(request, current), current.token):
            raise HTTPException(status_code=401, detail="invalid token")
        data = await file.read()
        if len(data) > 25 * 1024 * 1024:
            raise HTTPException(status_code=413, detail="file too large (max 25 MB)")
        import os
        import subprocess
        import tempfile

        tmpdir = tempfile.mkdtemp(prefix="audiolens_")
        try:
            suffix = os.path.splitext(file.filename or "")[1] or ".bin"
            src_path = os.path.join(tmpdir, f"input{suffix}")
            wav_path = os.path.join(tmpdir, "converted.wav")

            def _write_src() -> None:
                with open(src_path, "wb") as fh:
                    fh.write(data)

            await asyncio.to_thread(_write_src)
            proc = await asyncio.to_thread(
                subprocess.run,
                [
                    "ffmpeg",
                    "-y",
                    "-i",
                    src_path,
                    "-ac",
                    "1",
                    "-ar",
                    "16000",
                    "-sample_fmt",
                    "s16",
                    wav_path,
                ],
                capture_output=True,
            )
            wav_exists = await asyncio.to_thread(os.path.isfile, wav_path)
            if proc.returncode != 0 or not wav_exists:
                raise HTTPException(
                    status_code=415, detail="unsupported or unreadable audio/video file"
                )
            import wave

            with wave.open(wav_path, "rb") as wav:
                frames = wav.readframes(wav.getnframes())
                duration = wav.getnframes() / wav.getframerate()
            if duration > current.server.max_audio_seconds:
                raise HTTPException(status_code=413, detail=f"audio too long ({duration:.0f}s)")
            audio = np.frombuffer(frames, dtype="<i2").astype(np.float32) / 32768.0
            engine: WhisperEngine = app.state.engine
            try:
                spec = await engine.ensure_model(whisper_model)
            except ModelLoadError as exc:
                raise HTTPException(status_code=503, detail=str(exc)) from exc
            result = await engine.transcribe(
                audio,
                language=None if source_lang == "auto" else source_lang,
                task="transcribe",
                beam_size=current.whisper.beam_size,
            )
            effective_target = target_lang or current.translation.target_lang
            segments = []
            for index, segment in enumerate(result.segments):
                text_src = segment.text.strip()
                if not text_src:
                    continue
                text_tgt = None
                if current.translation is not None:
                    translator = getattr(app.state, "translator", None)
                    if translator is not None:
                        try:
                            text_tgt = await asyncio.to_thread(
                                translator.translate, text_src, result.language, effective_target
                            )
                        except Exception:
                            text_tgt = None
                if result.language == effective_target:
                    text_tgt = text_tgt or text_src
                segments.append(
                    {
                        "segment_id": index,
                        "start_ms": round(segment.start_s * 1000),
                        "end_ms": round(segment.end_s * 1000),
                        "text_src": text_src,
                        "text_tgt": text_tgt,
                    }
                )
            return {
                "src_lang": result.language,
                "src_lang_prob": round(float(result.language_probability), 4),
                "duration_s": round(result.duration_s, 2),
                "model": spec.model,
                "device": spec.device,
                "compute_type": spec.compute_type,
                "segments": segments,
            }
        finally:
            import shutil

            shutil.rmtree(tmpdir, ignore_errors=True)

    @app.post("/admin/reload")
    async def reload_config(request: Request) -> dict[str, Any]:
        current: Config = app.state.config
        if not _token_matches(_extract_token_for_http(request, current), current.token):
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
        client_ip = websocket.client.host if websocket.client else "unknown"
        delay = min(2.0, 0.1 * (2 ** min(_FAILED_ATTEMPTS.get(client_ip, 0), 4)))
        if delay:
            await asyncio.sleep(delay)
        if not _token_matches(
            _extract_token(websocket, current.server.allow_query_token), current.token
        ):
            logger.warning("rejected WebSocket: invalid or missing token")
            _FAILED_ATTEMPTS[client_ip] = _FAILED_ATTEMPTS.get(client_ip, 0) + 1
            await _fail(websocket, "unauthorized", "invalid or missing token", WS_UNAUTHORIZED)
            return
        _FAILED_ATTEMPTS[client_ip] = 0
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

        session = Session(current, engine_ref, app.state.translator)
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
                try:
                    generated = await session.process()
                except Exception:
                    logger.exception("processing audio failed for session %s", session.id)
                    await _fail(
                        websocket, "internal_error", "transcription failed", WS_INTERNAL_ERROR
                    )
                    return
                for produced in generated:
                    await _send(websocket, produced)
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
