"""Base classes and helpers for translation engines (Phase 3)."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

# Whisper (ISO 639-1) -> NLLB-200 language code. Covers the ~30 most used languages.
WHISPER_TO_NLLB = {
    "af": "afr_Latn",
    "ar": "arb_Arab",
    "az": "azj_Latn",
    "be": "bel_Cyrl",
    "bg": "bul_Cyrl",
    "bn": "ben_Beng",
    "ca": "cat_Latn",
    "cs": "ces_Latn",
    "cy": "cym_Latn",
    "da": "dan_Latn",
    "de": "deu_Latn",
    "el": "ell_Grek",
    "en": "eng_Latn",
    "eo": "epo_Latn",
    "es": "spa_Latn",
    "et": "est_Latn",
    "fa": "pes_Arab",
    "fi": "fin_Latn",
    "fr": "fra_Latn",
    "ga": "gle_Latn",
    "gl": "glg_Latn",
    "gu": "guj_Gujr",
    "he": "heb_Hebr",
    "hi": "hin_Deva",
    "hr": "hrv_Latn",
    "hu": "hun_Latn",
    "hy": "hye_Armn",
    "id": "ind_Latn",
    "is": "isl_Latn",
    "it": "ita_Latn",
    "ja": "jpn_Jpan",
    "ka": "kat_Geor",
    "kk": "kaz_Cyrl",
    "km": "khm_Khmr",
    "ko": "kor_Hang",
    "lo": "lao_Laoo",
    "lt": "lit_Latn",
    "lv": "lav_Latn",
    "mk": "mkd_Cyrl",
    "ml": "mal_Mlym",
    "mn": "mon_Cyrl",
    "mr": "mar_Deva",
    "ms": "msa_Latn",
    "mt": "mlt_Latn",
    "my": "mya_Mymr",
    "ne": "npi_Deva",
    "nl": "nld_Latn",
    "no": "nob_Latn",
    "pl": "pol_Latn",
    "pt": "por_Latn",
    "ro": "ron_Latn",
    "ru": "rus_Cyrl",
    "sk": "slk_Latn",
    "sl": "slv_Latn",
    "sq": "sqi_Latn",
    "sr": "srp_Cyrl",
    "sv": "swe_Latn",
    "sw": "swh_Latn",
    "ta": "tam_Taml",
    "te": "tel_Telu",
    "th": "tha_Thai",
    "tr": "tur_Latn",
    "uk": "ukr_Cyrl",
    "ur": "urd_Arab",
    "vi": "vie_Latn",
    "zh": "zho_Hans",
}

# Human-facing 2-letter codes -> NLLB for configuration (target_lang).
SAME_LANGUAGE = object()


@runtime_checkable
class Translator(Protocol):
    engine: str

    def translate(self, text: str, src_lang: str, tgt_lang: str) -> str | None: ...


def to_nllb(lang: str) -> str:
    lang = lang.strip().lower()
    if "_" in lang and len(lang) > 6:
        return lang  # already an NLLB code
    if lang in WHISPER_TO_NLLB:
        return WHISPER_TO_NLLB[lang]
    return f"{lang}_Latn"


def supports_pair(src_lang: str, tgt_lang: str) -> bool:
    try:
        to_nllb(src_lang)
        to_nllb(tgt_lang)
        return True
    except Exception:
        return False


def passthrough_if_same(text: str, src_lang: str, tgt_lang: str) -> str | None:
    if src_lang.lower() == tgt_lang.lower():
        return text
    nllb_src = to_nllb(src_lang)
    nllb_tgt = to_nllb(tgt_lang)
    if nllb_src.split("_")[0] == nllb_tgt.split("_")[0]:
        return text
    return None
