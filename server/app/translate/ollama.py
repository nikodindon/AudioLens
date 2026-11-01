from __future__ import annotations

import json as _json
import urllib.request

from .base import passthrough_if_same


class OllamaTranslator:
    engine = "ollama"

    def __init__(
        self,
        base_url: str,
        model: str,
        http_timeout: float = 30.0,
        ollama_url: str | None = None,
        ollama_model: str | None = None,
    ) -> None:
        self.base_url = (base_url or ollama_url or "").rstrip("/")
        self.model = model or ollama_model or ""
        self.http_timeout = http_timeout

    def translate(self, text: str, src_lang: str, tgt_lang: str) -> str | None:

        passthrough = passthrough_if_same(text, src_lang, tgt_lang)
        if passthrough is not None:
            return passthrough
        if not (self.base_url and self.model):
            return None
        prompt = (
            f"Translate the following text from {src_lang} to {tgt_lang}. "
            f"Return only the translation, no explanation.\n\n{text}"
        )
        payload = _json.dumps({"model": self.model, "prompt": prompt, "stream": False}).encode()
        request = urllib.request.Request(
            f"{self.base_url}/api/generate",
            data=payload,
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=self.http_timeout) as response:
                body = _json.loads(response.read().decode())
        except Exception:
            return None
        return (body.get("response") or text).strip()
