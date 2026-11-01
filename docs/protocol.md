# AudioLens protocol (v1)

Version: `1` · Server: `0.1.0`

Transport: WebSocket. Endpoint: `ws://<host>:8765/ws`. Audio frames stay on the same socket.

Authentication: Android sends the token in the handshake header
`Authorization: Bearer <TOKEN>`. A query-string token is accepted only when
`server.allow_query_token: true` (default `false`); prefer the header and avoid
URLs containing credentials. An invalid token yields an `error` followed by
close code **4401** and a growing per-IP delay.

The token is compared in constant time. Never commit it; generate one with
`openssl rand -hex 16`.

## Audio format

- PCM signed 16-bit little-endian (`pcm_s16le`)
- 16 000 Hz, mono
- frames of 20–100 ms (640–3200 bytes); an odd byte count is rejected
- a session is capped at `server.max_audio_seconds` (default 300 s)

## Client → server

### `start` (first text frame, required)

```json
{
  "type": "start",
  "protocol": 1,
  "audio": { "format": "pcm_s16le", "sample_rate": 16000, "channels": 1 },
  "source_lang": "auto",
  "target_lang": "fr",
  "whisper_model": "small",
  "translate": true,
  "partials": true
}
```

| Field | Required | Default | Notes |
|---|---|---|---|
| `type` | yes | – | `"start"` |
| `protocol` | no | `1` | must match the server protocol |
| `audio` | no | 16 kHz mono s16le | other formats are rejected |
| `source_lang` | no | `"auto"` | ISO 639-1 code or `auto` |
| `target_lang` | no | `"fr"` | ISO 639-1 code |
| `whisper_model` | no | `null` | **request only**, see below |
| `translate` | no | `true` | |
| `partials` | no | `true` | the server can emit partials in live mode; Android currently displays finals |
| `mode` | no | `"live"` | `"live"` or `"clip"` (clip = accumulation jusqu’à stop, final puis `done` si un final est produit) |

### `whisper_model` is a request, not a command

The server only honours a requested model when it appears in
`whisper.allowed_models` (server `config.yaml`). Otherwise it uses the model
from its configuration and reports this in `ready`:

- `whisper_model` — the model actually used;
- `whisper_model_requested` — what the client asked for (`null` if none);
- `whisper_model_accepted` — `true` only if the request was honoured.

Clients should inspect `ready` to determine the effective model. The current
Android interface does not yet display this acceptance detail.

### Other client messages (text JSON)

```json
{ "type": "config", "target_lang": "en", "translate": true, "partials": false }
{ "type": "stop" }
{ "type": "ping" }
```

- `config` updates live session settings (applies to the next transcription).
- `stop` finalises the session: pending audio is transcribed, `final` messages
  are sent, then the server closes the socket with code 1000.
- Unknown fields are ignored for forward compatibility; unknown `type` values
  produce an `error` but do not close the connection.

## Server → client

### `ready` (sent once, after the model is loaded)

```json
{
  "type": "ready",
  "session_id": "4f1c2a9b7d0e",
  "server_version": "0.1.0",
  "protocol": 1,
  "whisper_model": "small",
  "whisper_model_requested": "medium",
  "whisper_model_accepted": false,
  "whisper_device": "cuda",
  "whisper_compute_type": "int8_float32",
  "translation_engine": "none",
  "translate": true
}
```

`whisper_device` / `whisper_compute_type` are the *effective* values after any
CPU fallback. A GTX 1050 (Pascal) reports `int8_float32`; CPU reports `int8`.

### `partial`

```json
{ "type": "partial", "segment_id": 3, "text": "こんにちは、今日は", "lang": "ja" }
```

Emitted when enabled in live mode. The current Android overlay ignores partials.

### `final`

```json
{
  "type": "final",
  "segment_id": 0,
  "start_ms": 41200,
  "end_ms": 44350,
  "src_lang": "ja",
  "src_lang_prob": 0.97,
  "text_src": "こんにちは、今日は天気がいいですね。",
  "text_tgt": "Bonjour, il fait beau aujourd'hui."
}
```

- `segment_id` increases within a session; a `final` supersedes any `partial`
  with the same id.
- `start_ms` / `end_ms` are relative to the beginning of the session audio.
- `text_tgt` is `null` when translation is disabled (`translate: false`), when no
  translation engine is configured (`translation.engine: none`), or when the source language is unknown to the engine. It equals
  `text_src` when source and target languages are identical.
- `src_lang_prob` is Whisper's language-detection confidence for the segment
  (the detected language may be locked during a session).

### `error`

```json
{ "type": "error", "code": "invalid_audio", "message": "…" }
```

Codes: `unauthorized`, `session_limit`, `bad_request`, `unsupported_protocol`,
`unsupported_audio`, `bad_audio_frame`, `audio_too_long`, `session_closed`,
`model_unavailable`, `internal_error`, `timeout`.

### `pong`

```json
{ "type": "pong" }
```

## Close codes

| Code | Meaning |
|---|---|
| 1000 | normal end (after `stop`) |
| 4400 | bad request / unsupported protocol |
| 4401 | invalid or missing token |
| 4408 | no `start` received within 15 s |
| 4429 | too many concurrent sessions |
| 1011 | transcription or model failure |

## Versioning

`protocol` is bumped for breaking changes. Additive message types/fields do not
require a bump. Clients should ignore unknown fields.

## Admin endpoints

- `GET /health` — public status (no audio, no transcript, no secrets).

`POST /transcribe` (multipart `file`, `Authorization: Bearer <TOKEN>`)
transcodes an audio/video file with ffmpeg to 16 kHz mono PCM, runs the clip
pipeline and returns `{ "src_lang": "ru", "segments": [ { "start_ms":..., "end_ms":..., "text_src":..., "text_tgt":... } ] }`.
- `POST /admin/reload` — reload `config.yaml` and the Whisper model. Requires
  `Authorization: Bearer <TOKEN>`; query token only if explicitly enabled. Returns the effective
  model/device/compute type; `409` when the server was started without a config
  file.

### Android send queue and shutdown

Android limits OkHttp's pending WebSocket payload to 256,000 bytes (about eight
seconds of 16 kHz mono PCM16, including any queued control text). When the next
frame would exceed that limit, or a send is rejected, capture stops and reports
that audio was not sent through the application and a notification. Already
queued frames are sent before `stop`; the rejected frame and subsequent audio
are not sent. This is an explicit interruption, not a promise of lossless
streaming or automatic reconnection. The queue limit bounds client buffering,
but does not measure audio already accepted by the network or server.

On a normal stop Android stops capture, sends `stop`, and keeps the connection
open for final messages and the server close. It waits at most 60 seconds;
failed or timed-out finalization is reported as potentially incomplete. An
operating-system process kill cannot guarantee delivery of the final segment.
Clip mode buffers its recording until `stop`, bounded by
`server.max_audio_seconds`; long clips increase memory and inference time.
