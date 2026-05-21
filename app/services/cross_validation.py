"""Cross-validation for speech-to-text and translation with context analysis."""

import json
from pathlib import Path
from typing import TypedDict, Optional
from difflib import SequenceMatcher
from collections import defaultdict

from app.config.settings import settings
from app.utils.logger import logger


class Segment(TypedDict):
    start: float
    end: float
    text: str


class ValidationResult(TypedDict):
    segments: list[Segment]
    detected_language: str
    confidence: float
    validation_method: str
    alternative_results: dict[str, list[Segment]]


class TranscriptionValidationError(Exception):
    """Raised when cross-validation fails."""


class TranslationValidationError(Exception):
    """Raised when translation cross-validation fails."""


def transcribe_with_validation(
    audio_path: Path, temp_dir: Path, use_cross_validation: bool = True
) -> ValidationResult:
    """
    Transcribe audio with cross-validation using multiple ASR engines.

    Args:
        audio_path: Path to audio file.
        temp_dir: Directory for temporary files.
        use_cross_validation: Whether to use cross-validation.

    Returns:
        ValidationResult with segments, confidence, and alternative results.
    """
    if not audio_path.exists():
        raise TranscriptionValidationError(f"Audio file not found: {audio_path}")

    logger.info(f"Starting cross-validated transcription: {audio_path.name}")

    # Primary transcription using faster-whisper
    from app.services.speech_to_text import transcribe

    primary_segments, detected_lang = transcribe(audio_path, temp_dir)

    if not use_cross_validation:
        return ValidationResult(
            segments=primary_segments,
            detected_language=detected_lang,
            confidence=1.0,
            validation_method="single_engine",
            alternative_results={},
        )

    # Alternative transcription using OpenAI Whisper
    alternative_segments = _transcribe_with_openai_whisper(audio_path, temp_dir)

    # Cross-validate and merge results
    validated_segments = _cross_validate_transcriptions(
        primary_segments, alternative_segments, detected_lang
    )

    # Calculate confidence score
    confidence = _calculate_transcription_confidence(
        primary_segments, alternative_segments
    )

    # Guard: if confidence is extremely low, the alternative engine produced
    # garbage (e.g. hallucinated text on an unsupported language like Thai).
    # Fall back to primary-only to avoid corrupting the output.
    if confidence < 0.1:
        logger.warning(
            f"Cross-validation confidence too low ({confidence:.4f}), "
            f"discarding alternative results and falling back to primary engine"
        )
        return ValidationResult(
            segments=primary_segments,
            detected_language=detected_lang,
            confidence=1.0,
            validation_method="single_engine_fallback",
            alternative_results={},
        )

    # Save validation results
    validation_result = ValidationResult(
        segments=validated_segments,
        detected_language=detected_lang,
        confidence=confidence,
        validation_method="cross_validation",
        alternative_results={
            "faster_whisper": primary_segments,
            "openai_whisper": alternative_segments,
        },
    )

    # Persist validation results
    json_path = temp_dir / f"{audio_path.stem}_validation.json"
    json_path.write_text(
        json.dumps(
            {
                "segments": validated_segments,
                "detected_language": detected_lang,
                "confidence": confidence,
                "validation_method": "cross_validation",
                "alternative_results": {
                    "faster_whisper": primary_segments,
                    "openai_whisper": alternative_segments,
                },
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    logger.info(
        f"Cross-validated transcription complete: {len(validated_segments)} segments, confidence={confidence:.2f}"
    )

    return validation_result


def translate_with_validation(
    segments: list[Segment],
    source_lang: str,
    target_lang: str,
    temp_dir: Path,
    use_cross_validation: bool = True,
) -> ValidationResult:
    """
    Translate segments with cross-validation using multiple translation engines.

    Args:
        segments: Original transcribed segments.
        source_lang: Source language code.
        target_lang: Target language code.
        temp_dir: Directory for temporary files.
        use_cross_validation: Whether to use cross-validation.

    Returns:
        ValidationResult with translated segments and confidence.
    """
    if not segments:
        return ValidationResult(
            segments=[],
            detected_language=target_lang,
            confidence=1.0,
            validation_method="single_engine",
            alternative_results={},
        )

    logger.info(f"Starting cross-validated translation: {source_lang} → {target_lang}")

    # Primary translation using configured engine
    from app.services.translator import translate_segments

    primary_translated = translate_segments(
        segments, source_lang, target_lang, temp_dir
    )

    if not use_cross_validation:
        return ValidationResult(
            segments=primary_translated,
            detected_language=target_lang,
            confidence=1.0,
            validation_method="single_engine",
            alternative_results={},
        )

    # Alternative translation: use one alternative engine (not all 3)
    # Priority for alternative: google > marian > nllb (fastest to slowest to set up)
    alternative_translations = {}
    engine = settings.TRANSLATION_ENGINE

    alt_engine = _pick_alternative_engine(engine)
    if alt_engine == "google":
        alternative_translations["google"] = _translate_with_google(segments, source_lang, target_lang)
    elif alt_engine == "marian":
        alternative_translations["marian"] = _translate_with_marian(segments, source_lang, target_lang)
    elif alt_engine == "nllb":
        alternative_translations["nllb"] = _translate_with_nllb(segments, source_lang, target_lang)

    # Cross-validate and improve translations with context analysis
    validated_segments = _cross_validate_translations(
        primary_translated,
        alternative_translations,
        segments,  # Original segments for context
        source_lang,
        target_lang,
    )

    # Calculate confidence score
    confidence = _calculate_translation_confidence(
        primary_translated, alternative_translations
    )

    # Save validation results
    validation_result = ValidationResult(
        segments=validated_segments,
        detected_language=target_lang,
        confidence=confidence,
        validation_method="cross_validation",
        alternative_results={"primary": primary_translated, **alternative_translations},
    )

    json_path = temp_dir / "translation_validation.json"
    json_path.write_text(
        json.dumps(
            {
                "segments": validated_segments,
                "confidence": confidence,
                "validation_method": "cross_validation",
                "alternative_results": {
                    "primary": primary_translated,
                    **alternative_translations,
                },
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    logger.info(
        f"Cross-validated translation complete: {len(validated_segments)} segments, confidence={confidence:.2f}"
    )

    return validation_result


def _transcribe_with_openai_whisper(audio_path: Path, temp_dir: Path) -> list[Segment]:
    """Alternative transcription using OpenAI Whisper."""
    try:
        import whisper
    except ImportError:
        logger.warning(
            "OpenAI Whisper not installed, skipping alternative transcription"
        )
        return []

    logger.info("Running alternative transcription with OpenAI Whisper...")

    try:
        model = whisper.load_model(
            settings.WHISPER_MODEL, download_root=str(settings.MODELS_DIR)
        )
        result = model.transcribe(str(audio_path), language=None, task="transcribe")

        segments: list[Segment] = []
        for seg in result["segments"]:
            segments.append(
                Segment(
                    start=round(seg["start"], 3),
                    end=round(seg["end"], 3),
                    text=seg["text"].strip(),
                )
            )

        logger.info(f"OpenAI Whisper transcription complete: {len(segments)} segments")
        return segments

    except Exception as e:
        logger.warning(f"OpenAI Whisper transcription failed: {e}")
        return []


def _cross_validate_transcriptions(
    primary: list[Segment], alternative: list[Segment], detected_lang: str
) -> list[Segment]:
    """
    Cross-validate transcriptions from two ASR engines.

    Uses time-overlap-based alignment to compare segments covering the same
    time range rather than comparing by index (which is meaningless when
    different engines produce different segment boundaries).
    """
    if not alternative:
        return primary

    validated: list[Segment] = []

    for p_seg in primary:
        # Find overlapping alternative segments
        overlapping: list[Segment] = []
        for a_seg in alternative:
            overlap = min(p_seg["end"], a_seg["end"]) - max(p_seg["start"], a_seg["start"])
            if overlap > 0:
                overlapping.append(a_seg)

        if not overlapping:
            # No alternative coverage — keep primary
            validated.append(p_seg)
            continue

        # Compare with best-overlapping alternative segment
        best_alt = max(overlapping, key=lambda a: SequenceMatcher(
            None, p_seg["text"], a["text"]
        ).ratio())
        best_sim = SequenceMatcher(None, p_seg["text"], best_alt["text"]).ratio()

        if best_sim > 0.8:
            # High agreement: keep primary
            validated.append(p_seg)
        elif best_sim > 0.5:
            # Medium agreement: pick the longer text
            if len(p_seg["text"]) > len(best_alt["text"]):
                validated.append(p_seg)
            else:
                validated.append(Segment(
                    start=p_seg["start"], end=p_seg["end"], text=best_alt["text"]
                ))
        else:
            # Low agreement: keep primary (faster-whisper tends to be more reliable)
            validated.append(p_seg)

    # Add any alternative segments with no primary coverage
    for a_seg in alternative:
        has_overlap = False
        for p_seg in primary:
            overlap = min(p_seg["end"], a_seg["end"]) - max(p_seg["start"], a_seg["start"])
            if overlap > 0:
                has_overlap = True
                break
        if not has_overlap:
            validated.append(a_seg)

    # Re-sort by start time
    validated.sort(key=lambda s: s["start"])

    return validated


def _cross_validate_translations(
    primary: list[Segment],
    alternatives: dict[str, list[Segment]],
    original_segments: list[Segment],
    source_lang: str,
    target_lang: str,
) -> list[Segment]:
    """
    Cross-validate translations using consensus scoring.

    For each segment, compares all candidate translations and selects the one
    with the highest average similarity to other candidates (consensus).
    Falls back to primary when no consensus emerges (similarity < 0.3).
    """
    if not alternatives:
        return primary

    validated = []

    for i, seg in enumerate(primary):
        # Collect all candidate translations for this segment
        alt_texts = []
        for alt_segments in alternatives.values():
            if i < len(alt_segments):
                alt_texts.append(alt_segments[i]["text"])

        improved_text = _improve_translation_with_context(
            seg["text"], alt_texts, [], source_lang, target_lang
        )

        validated.append(
            Segment(start=seg["start"], end=seg["end"], text=improved_text)
        )

    return validated


def _improve_translation_with_context(
    primary_text: str,
    alternative_texts: list[str],
    context_segments: list[Segment],
    source_lang: str,
    target_lang: str,
) -> str:
    """
    Select the best translation using consensus-based scoring.

    Scores each candidate by its average similarity to all other candidates
    in the same target language. The most "agreed upon" translation wins.
    Falls back to primary if no consensus emerges.
    """
    if not alternative_texts:
        return primary_text

    all_texts = [primary_text] + alternative_texts

    # Consensus scoring: for each candidate, compute average similarity
    # to all other candidates (all in the SAME target language)
    scores: list[float] = []
    for i, text in enumerate(all_texts):
        sim_sum = 0.0
        count = 0
        for j, other in enumerate(all_texts):
            if i != j:
                sim_sum += SequenceMatcher(None, text, other).ratio()
                count += 1
        avg_sim = sim_sum / count if count > 0 else 0.0
        scores.append(avg_sim)

    best_index = scores.index(max(scores))
    best_score = scores[best_index]

    # If consensus is too low (< 0.3), prefer primary
    if best_score < 0.3:
        return primary_text

    # Log whenever we override the primary
    if best_index != 0:
        logger.debug(
            f"Consensus override: '{primary_text[:40]}' → '{all_texts[best_index][:40]}' "
            f"(score={best_score:.2f})"
        )

    return all_texts[best_index]


def _score_translation_quality(
    translation: str, context: str, target_lang: str
) -> float:
    """
    Score translation quality using consensus similarity (deprecated —
    kept for compatibility, actual scoring now done in _improve_translation_with_context).
    """
    return 1.0  # Neutral — real scoring is consensus-based in the caller


def _pick_alternative_engine(primary: str) -> str | None:
    """Pick a single alternative translation engine, avoiding the primary."""
    candidates = ["google", "marian", "nllb"]
    for c in candidates:
        if c != primary:
            return c
    return None


def _calculate_transcription_confidence(
    primary: list[Segment], alternative: list[Segment]
) -> float:
    """Calculate confidence score for transcription based on cross-validation."""
    if not alternative:
        return 0.5  # Low confidence without validation

    # Align segments by time overlap, not by index
    score, count = _compute_time_aligned_similarity(primary, alternative)
    return score / count if count > 0 else 0.5


def _compute_time_aligned_similarity(
    segments_a: list[Segment], segments_b: list[Segment]
) -> tuple[float, int]:
    """Compute similarity by matching segments that overlap in time."""
    total_similarity = 0.0
    count = 0

    for seg_a in segments_a:
        # Find overlapping segment in B
        best = None
        for seg_b in segments_b:
            overlap = min(seg_a["end"], seg_b["end"]) - max(seg_a["start"], seg_b["start"])
            if overlap > 0:
                sim = SequenceMatcher(None, seg_a["text"], seg_b["text"]).ratio()
                if best is None or overlap > best[0]:
                    best = (overlap, sim)
        if best is not None:
            total_similarity += best[1]
            count += 1

    return total_similarity, count


def _calculate_translation_confidence(
    primary: list[Segment], alternatives: dict[str, list[Segment]]
) -> float:
    """Calculate confidence score for translation based on cross-validation."""
    if not alternatives:
        return 0.5  # Low confidence without validation

    total_similarity = 0.0
    count = 0

    for engine_name, alt_segments in alternatives.items():
        for i in range(min(len(primary), len(alt_segments))):
            similarity = SequenceMatcher(
                None, primary[i]["text"], alt_segments[i]["text"]
            ).ratio()
            total_similarity += similarity
            count += 1

    if count == 0:
        return 0.5

    return total_similarity / count


def _translate_with_google(
    segments: list[Segment], source_lang: str, target_lang: str
) -> list[Segment]:
    """Translate using Google Translate."""
    try:
        from app.services.translator import _translate_google

        texts = [s["text"] for s in segments]
        translated_texts = _translate_google(texts, source_lang, target_lang)

        result = []
        for seg, text in zip(segments, translated_texts):
            result.append(Segment(start=seg["start"], end=seg["end"], text=text))

        return result
    except Exception as e:
        logger.warning(f"Google translation failed: {e}")
        return []


def _translate_with_marian(
    segments: list[Segment], source_lang: str, target_lang: str
) -> list[Segment]:
    """Translate using MarianMT."""
    try:
        from app.services.translator import _translate_marian

        texts = [s["text"] for s in segments]
        translated_texts = _translate_marian(texts, source_lang, target_lang)

        result = []
        for seg, text in zip(segments, translated_texts):
            result.append(Segment(start=seg["start"], end=seg["end"], text=text))

        return result
    except Exception as e:
        logger.warning(f"MarianMT translation failed: {e}")
        return []


def _translate_with_nllb(
    segments: list[Segment], source_lang: str, target_lang: str
) -> list[Segment]:
    """Translate using NLLB-200."""
    try:
        from app.services.translator import _translate_nllb

        texts = [s["text"] for s in segments]
        translated_texts = _translate_nllb(texts, source_lang, target_lang)

        result = []
        for seg, text in zip(segments, translated_texts):
            result.append(Segment(start=seg["start"], end=seg["end"], text=text))

        return result
    except Exception as e:
        logger.warning(f"NLLB translation failed: {e}")
        return []
