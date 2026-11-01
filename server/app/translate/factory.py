from __future__ import annotations

from .argos import ArgosTranslator
from .base import Translator
from .nllb import NLLBTranslator
from .ollama import OllamaTranslator


def build_translator(config) -> Translator | None:
    engine = getattr(config.translation, "engine", "none")
    if engine == "none":
        return None
    if engine == "nllb":
        return NLLBTranslator(
            model_name=config.translation.model,
            device=config.translation.device,
            compute_type=config.translation.compute_type,
        )
    if engine == "ollama":
        return OllamaTranslator(
            base_url=config.translation.ollama_url or "",
            model=config.translation.ollama_model or "",
        )
    if engine == "argos":
        return ArgosTranslator()
    return None
