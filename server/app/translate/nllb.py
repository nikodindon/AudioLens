from __future__ import annotations

import logging
from typing import Any

from .base import passthrough_if_same, to_nllb

logger = logging.getLogger(__name__)


class NLLBTranslator:
    engine = "nllb"

    def __init__(self, model_name: str, device: str = "cpu", compute_type: str = "int8") -> None:
        self.model_name = model_name
        self.device = device
        self.compute_type = compute_type
        self._tokenizer: Any = None
        self._model: Any = None

    def _load(self) -> None:
        if self._model is not None:
            return
        import torch  # noqa: F401
        from transformers import AutoModelForSeq2SeqLM, AutoTokenizer

        logger.info("loading NLLB model=%s device=%s", self.model_name, self.device)
        self._tokenizer = AutoTokenizer.from_pretrained(self.model_name)
        self._model = AutoModelForSeq2SeqLM.from_pretrained(self.model_name)
        self._model.to(self.device)
        self._model.eval()

    def translate(self, text: str, src_lang: str, tgt_lang: str) -> str | None:
        if not text.strip():
            return None
        passthrough = passthrough_if_same(text, src_lang, tgt_lang)
        if passthrough is not None:
            return passthrough
        self._load()

        assert self._tokenizer is not None
        assert self._model is not None
        tokenizer = self._tokenizer
        model = self._model
        assert tokenizer is not None
        nllb_src = to_nllb(src_lang)
        nllb_tgt = to_nllb(tgt_lang)
        tokenizer.src_lang = nllb_src
        encoded = tokenizer(text, return_tensors="pt").to(self.device)
        generated = model.generate(
            **encoded,
            forced_bos_token_id=tokenizer.convert_tokens_to_ids(nllb_tgt),
            max_new_tokens=256,
        )
        result = tokenizer.batch_decode(generated, skip_special_tokens=True)[0].strip()
        return result or text
