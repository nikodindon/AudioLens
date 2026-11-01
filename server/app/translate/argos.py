from __future__ import annotations

import logging

from .base import passthrough_if_same

logger = logging.getLogger(__name__)


class ArgosTranslator:
    engine = "argos"

    def translate(self, text: str, src_lang: str, tgt_lang: str) -> str | None:
        passthrough = passthrough_if_same(text, src_lang, tgt_lang)
        if passthrough is not None:
            return passthrough
        try:
            import argostranslate.package
            import argostranslate.translate
        except ImportError:
            return None
        try:
            argostranslate.package.update_package_index()
            available = argostranslate.package.get_available_packages()
            package = next(
                p for p in available if p.from_code == src_lang and p.to_code == tgt_lang
            )
            argostranslate.package.install_from_path(package.download())
        except Exception:  # pragma: no cover - network/package dependent
            logger.warning("Argos could not install %s->%s", src_lang, tgt_lang)
        try:
            return argostranslate.translate.translate(text, src_lang, tgt_lang)
        except Exception:
            return None
