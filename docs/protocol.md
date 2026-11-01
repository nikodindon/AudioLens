# AudioLens protocol (v1)

Version: `1` · Server: `0.1.0`

Transport: WebSocket. Endpoint: `ws://<host>:8765/ws` (Tailscale/WireGuard in
production, `adb reverse` in development). Audio frames stay on the same socket.

Authentication: the token travels either as a query parameter
(`/ws?token=<TOKEN>`) or as an `Authorization: Bearer <TOKEN>` header. An invalid
token yields an `error` message followed by close code **4401**.

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
| `partials` | no | `true` | no partials are produced in Phase 1 |

### `whisper_model` is a request, not a command

The server only honours a requested model when it appears in
`whisper.allowed_models` (server `config.yaml`). Otherwise it uses the model
from its configuration and reports this in `ready`:

- `whisper_model` — the model actually used;
- `whisper_model_requested` — what the client asked for (`null` if none);
- `whisper_model_accepted` — `true` only if the request was honoured.

Clients must always read `ready` and display the effective model, never assume
their request was accepted.

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

Not emitted in Phase 1 (streaming segmentation lands in Phase 2).

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
  translation engine is configured (Phase 1 default: `translation.engine:
  none`), or when the source language is unknown to the engine. It equals
  `text_src` when source and target languages are identical.
- `src_lang_prob` is Whisper's language-detection confidence for the segment
  (same value for every segment of a Phase 1 session).

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
- `POST /admin/reload` — reload `config.yaml` and the Whisper model. Requires
  `?token=<TOKEN>` or `Authorization: Bearer <TOKEN>`. Returns the effective
  model/device/compute type; `409` when the server was started without a config
  file.
