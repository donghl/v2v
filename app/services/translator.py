"""
Subtitle translation using MarianMT or NLLB-200.

Supports batch translation for efficiency and respects segment boundaries
so that timing information is preserved.
"""

from pathlib import Path

import torch

from app.config.settings import settings
from app.services.speech_to_text import Segment
from app.utils.logger import logger


class TranslationError(Exception):
    """Raised when translation fails."""


def _get_translation_device() -> str:
    """Resolve translation device from settings."""
    device = settings.TRANSLATION_DEVICE
    if device == "auto":
        return "cuda" if torch.cuda.is_available() else "cpu"
    return device


# Language code maps for different engines
MARIAN_LANG_MAP: dict[str, str] = {
    "zh": "zh",       # Chinese
    "en": "en",       # English
    "ja": "ja",       # Japanese  (Marian uses Helsinki-NLP/opus-mt-*-ja)
    "ko": "ko",       # Korean
    "es": "es",       # Spanish
    "ar": "ar",       # Arabic
    "fr": "fr",       # French
    "de": "de",       # German
    "ru": "ru",       # Russian
    "pt": "pt",       # Portuguese
    "it": "it",       # Italian
    "nl": "nl",       # Dutch
    "pl": "pl",       # Polish
    "th": "th",       # Thai
    "vi": "vi",       # Vietnamese
    "id": "id",       # Indonesian
    "ms": "ms",       # Malay
    "hi": "hi",       # Hindi
    "tr": "tr",       # Turkish
}


def translate_segments(
    segments: list[Segment],
    source_lang: str,
    target_lang: str,
    temp_dir: Path,
) -> list[Segment]:
    """
    Translate every segment's text while preserving timing.

    Args:
        segments: Original transcribed segments.
        source_lang: Detected source language code (e.g. 'en').
        target_lang: Target language code (e.g. 'zh').
        temp_dir: Directory for caching.

    Returns:
        New list of segments with translated text.
    """
    if not segments:
        return []

    engine = settings.TRANSLATION_ENGINE
    logger.info(f"Translating {len(segments)} segments: {source_lang} → {target_lang} (engine={engine})")

    # Try the configured engine first, then fall back to engines that need no
    # network (marian) before Google — a blocked/unreachable engine should not
    # silently produce untranslated output.
    chain = [engine] + [e for e in ("marian", "google") if e != engine]
    errors: list[str] = []
    texts: list[str] | None = None
    for eng in chain:
        try:
            if eng == "marian":
                texts = _translate_marian([s["text"] for s in segments], source_lang, target_lang)
            elif eng == "nllb":
                texts = _translate_nllb([s["text"] for s in segments], source_lang, target_lang)
            elif eng == "google":
                texts = _translate_google([s["text"] for s in segments], source_lang, target_lang)
            else:
                raise TranslationError(f"Unknown translation engine: {eng}")
            if len(texts) != len(segments):
                raise TranslationError(
                    f"{eng} returned {len(texts)} texts for {len(segments)} segments"
                )
            if eng != engine:
                logger.warning(
                    f"Engine '{engine}' failed ({'; '.join(errors)}) — fell back to '{eng}'"
                )
            break
        except Exception as e:
            errors.append(f"{eng}: {e}")
            logger.warning(f"Engine '{eng}' failed: {e}")
            texts = None

    if texts is None:
        raise TranslationError("All translation engines failed: " + " | ".join(errors))

    translated: list[Segment] = []
    for seg, txt in zip(segments, texts):
        translated.append(Segment(
            start=seg["start"],
            end=seg["end"],
            text=txt,
        ))

    return translated


# ---------------------------------------------------------------------------
# MarianMT (local, no API key)
# ---------------------------------------------------------------------------

def _translate_marian(texts: list[str], source_lang: str, target_lang: str) -> list[str]:
    """Translate using Helsinki-NLP MarianMT models via transformers (GPU-accelerated)."""
    try:
        from transformers import MarianMTModel, MarianTokenizer
    except ImportError:
        raise TranslationError("transformers not installed. Run: pip install transformers sentencepiece")

    device = _get_translation_device()
    src = MARIAN_LANG_MAP.get(source_lang, source_lang)
    tgt = MARIAN_LANG_MAP.get(target_lang, target_lang)

    model_name = f"Helsinki-NLP/opus-mt-{src}-{tgt}"

    try:
        tokenizer = MarianTokenizer.from_pretrained(model_name)
        model = MarianMTModel.from_pretrained(model_name)
    except Exception:
        # Fallback: try English-pivoted model
        logger.warning(f"Model {model_name} not found, trying English pivot ...")
        model_name = f"Helsinki-NLP/opus-mt-{src}-en"
        try:
            tokenizer = MarianTokenizer.from_pretrained(model_name)
            model = MarianMTModel.from_pretrained(model_name)
        except Exception as e:
            raise TranslationError(
                f"No Marian model available for {src}→{tgt}. "
                f"Try setting TRANSLATION_ENGINE=google or download manually. Error: {e}"
            )
        model = model.to(device)
        # Two-step: src→en, then en→tgt
        texts = _marian_translate_batch(texts, tokenizer, model, device)
        model_name = f"Helsinki-NLP/opus-mt-en-{tgt}"
        try:
            tokenizer2 = MarianTokenizer.from_pretrained(model_name)
            model2 = MarianMTModel.from_pretrained(model_name)
        except Exception as e:
            raise TranslationError(f"No Marian model for en→{tgt}: {e}")
        model2 = model2.to(device)
        return _marian_translate_batch(texts, tokenizer2, model2, device)

    model = model.to(device)
    return _marian_translate_batch(texts, tokenizer, model, device)


def _marian_translate_batch(texts: list[str], tokenizer, model, device: str = "cpu") -> list[str]:
    """Translate a list of texts in batches (GPU-accelerated when device='cuda')."""
    results: list[str] = []
    batch_size = 16
    with torch.no_grad():
        for i in range(0, len(texts), batch_size):
            batch = texts[i:i + batch_size]
            non_empty = [t for t in batch if t.strip()]
            if not non_empty:
                results.extend(batch)
                continue
            inputs = tokenizer(non_empty, return_tensors="pt", padding=True, truncation=True)
            inputs = {k: v.to(device) for k, v in inputs.items()}
            outputs = model.generate(**inputs)
            translated_batch = [tokenizer.decode(o, skip_special_tokens=True) for o in outputs]
            result_idx = 0
            for t in batch:
                if t.strip():
                    results.append(translated_batch[result_idx])
                    result_idx += 1
                else:
                    results.append(t)
    return results


# ---------------------------------------------------------------------------
# NLLB-200 (local, best quality)
# ---------------------------------------------------------------------------

NLLB_LANG_MAP: dict[str, str] = {
    "zh": "zho_Hans",
    "en": "eng_Latn",
    "ja": "jpn_Jpan",
    "ko": "kor_Hang",
    "es": "spa_Latn",
    "ar": "arb_Arab",
    "fr": "fra_Latn",
    "de": "deu_Latn",
    "ru": "rus_Cyrl",
    "pt": "por_Latn",
    "it": "ita_Latn",
    "nl": "nld_Latn",
    "pl": "pol_Latn",
    "th": "tha_Thai",
    "vi": "vie_Latn",
    "id": "ind_Latn",
    "hi": "hin_Deva",
    "tr": "tur_Latn",
}


def _translate_nllb(texts: list[str], source_lang: str, target_lang: str) -> list[str]:
    """Translate using Meta NLLB-200 via transformers (GPU-accelerated)."""
    try:
        from transformers import AutoTokenizer, AutoModelForSeq2SeqLM
    except ImportError:
        raise TranslationError("transformers not installed. Run: pip install transformers sentencepiece")

    device = _get_translation_device()
    model_name = "facebook/nllb-200-distilled-1.3B"

    try:
        tokenizer = AutoTokenizer.from_pretrained(model_name, src_lang=NLLB_LANG_MAP.get(source_lang, source_lang))
        model = AutoModelForSeq2SeqLM.from_pretrained(model_name)
    except Exception as e:
        raise TranslationError(f"Failed to load NLLB model: {e}")

    model = model.to(device)
    if device == "cuda":
        model = model.half()  # float16

    tgt_code = NLLB_LANG_MAP.get(target_lang, target_lang)
    results: list[str] = []
    batch_size = 16  # increased for 4090

    with torch.no_grad():
        for i in range(0, len(texts), batch_size):
            batch = [t for t in texts[i:i + batch_size] if t.strip()]
            if not batch:
                continue
            inputs = tokenizer(batch, return_tensors="pt", padding=True, truncation=True)
            inputs = {k: v.to(device) for k, v in inputs.items()}
            outputs = model.generate(**inputs, forced_bos_token_id=tokenizer.convert_tokens_to_ids(tgt_code))
            results.extend([tokenizer.decode(o, skip_special_tokens=True) for o in outputs])

    return results


# ---------------------------------------------------------------------------
# Google Translate (free, no API key, best fallback)
# ---------------------------------------------------------------------------

# deep-translator uses different language codes than our internal ones
DEEP_TRANSLATOR_MAP: dict[str, str] = {
    "zh": "zh-CN",
    "ja": "ja",
    "ko": "ko",
    "en": "en",
    "es": "es",
    "ar": "ar",
    "fr": "fr",
    "de": "de",
    "ru": "ru",
    "pt": "pt",
    "it": "it",
    "nl": "nl",
    "pl": "pl",
    "th": "th",
    "vi": "vi",
    "id": "id",
    "hi": "hi",
    "tr": "tr",
    "ms": "ms",
}


def _translate_google(texts: list[str], source_lang: str, target_lang: str) -> list[str]:
    """Translate using deep-translator (Google Translate wrapper, no API key)."""
    import time

    try:
        from deep_translator import GoogleTranslator
    except ImportError:
        raise TranslationError(
            "deep-translator not installed. Run: pip install deep-translator"
        )

    src = DEEP_TRANSLATOR_MAP.get(source_lang, source_lang)
    tgt = DEEP_TRANSLATOR_MAP.get(target_lang, target_lang)

    try:
        translator = GoogleTranslator(source=src, target=tgt)
    except Exception as e:
        raise TranslationError(f"Failed to initialise GoogleTranslator: {e}")

    consecutive_failures = 0

    def _translate_one(text: str, max_retries: int = 3) -> str:
        nonlocal consecutive_failures
        for attempt in range(max_retries):
            try:
                result = translator.translate(text)
                consecutive_failures = 0
                return result
            except Exception:
                if attempt < max_retries - 1:
                    time.sleep(1.5 ** attempt)  # exponential backoff
        consecutive_failures += 1
        if consecutive_failures >= 3:
            # Circuit breaker: Google is unreachable, stop burning retries on
            # every segment and let translate_segments fall back to an
            # offline engine instead.
            raise TranslationError(
                f"Google Translate unreachable ({consecutive_failures} consecutive failures)"
            )
        logger.warning(f"Google translate failed after {max_retries} retries: '{text[:50]}...'")
        return text  # keep original on isolated failure

    results: list[str] = []
    for text in texts:
        if not text.strip():
            results.append(text)
        else:
            results.append(_translate_one(text))

    return results
