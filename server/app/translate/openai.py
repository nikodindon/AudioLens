from __future__ import annotations

import json as _json
import logging
import os
import urllib.request

from .base import passthrough_if_same

logger = logging.getLogger(__name__)


class OpenAICompatibleTranslator:
    engine = "openai"

    def __init__(
        self,
        base_url: str,
        model: str,
        api_key: str | None = None,
        http_timeout: float = 30.0,
    ) -> None:
        self.base_url = (base_url or "").rstrip("/")
        self.model = model or ""
        self.api_key = api_key or os.environ.get("OPENAI_API_KEY")
        self.http_timeout = http_timeout

    def translate(self, text: str, src_lang: str, tgt_lang: str) -> str | None:
        passthrough = passthrough_if_same(text, src_lang, tgt_lang)
        if passthrough is not None:
            return passthrough
        if not (self.base_url and self.model):
            return None

        prompt = (
            f"Translate the following text from {src_lang} to {tgt_lang}. "
            "Return only the concise translation, no explanation or reasoning."
        )
        payload = _json.dumps(
            {
                "model": self.model,
                "messages": [
                    {"role": "user", "content": f"{prompt}\n\n{text}"}
                ],
                "temperature": 0,
                "max_tokens": 2048,
            }
        ).encode()
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        request = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            data=payload,
            headers=headers,
        )
        try:
            with urllib.request.urlopen(request, timeout=self.http_timeout) as response:
                raw = response.read().decode()
                body = _json.loads(raw)
                logger.debug("openai translator response raw=%s", raw[:1000])
        except Exception as exc:
            logger.exception("openai translator request failed: %s", exc)
            return None
        choices = body.get("choices") or []
        if not choices:
            logger.warning("openai translator returned no choices: %s", body)
            return None
        message = choices[0].get("message") or {}
        content = (message.get("content") or "").strip()
        if not content:
            logger.warning("openai translator empty content body=%s", body)
        return content or None
