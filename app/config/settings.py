"""Application configuration loaded from environment variables."""

import os
from pathlib import Path
from dataclasses import dataclass, field

from dotenv import load_dotenv

# Load .env file from project root BEFORE reading os.getenv
load_dotenv()


@dataclass
class Settings:
    # --- Paths ---
    BASE_DIR: Path = field(
        default_factory=lambda: Path(__file__).resolve().parent.parent.parent
    )
    INPUT_DIR: Path = field(
        default_factory=lambda: Path(
            os.getenv(
                "INPUT_DIR",
                str(Path(__file__).resolve().parent.parent.parent / "input"),
            )
        )
    )
    OUTPUT_DIR: Path = field(
        default_factory=lambda: Path(
            os.getenv(
                "OUTPUT_DIR",
                str(Path(__file__).resolve().parent.parent.parent / "output"),
            )
        )
    )
    TEMP_DIR: Path = field(
        default_factory=lambda: Path(
            os.getenv(
                "TEMP_DIR", str(Path(__file__).resolve().parent.parent.parent / "temp")
            )
        )
    )
    MODELS_DIR: Path = field(
        default_factory=lambda: Path(
            os.getenv(
                "MODELS_DIR",
                str(Path(__file__).resolve().parent.parent.parent / "models"),
            )
        )
    )

    # --- Whisper / ASR ---
    WHISPER_MODEL: str = os.getenv(
        "WHISPER_MODEL", "medium"
    )  # tiny, base, small, medium, large-v3, large-v3-turbo
    WHISPER_DEVICE: str = os.getenv("WHISPER_DEVICE", "auto")  # auto, cpu, cuda
    WHISPER_COMPUTE_TYPE: str = os.getenv(
        "WHISPER_COMPUTE_TYPE", "auto"
    )  # auto, float16, int8, int8_float16

    # --- Whisper decoding parameters (GPU-tuned) ---
    WHISPER_BEAM_SIZE: int = int(os.getenv("WHISPER_BEAM_SIZE", "5"))  # 5=fast, 10=accurate
    WHISPER_BEST_OF: int = int(os.getenv("WHISPER_BEST_OF", "5"))  # candidates to sample
    WHISPER_TEMPERATURE: str = os.getenv("WHISPER_TEMPERATURE", "0.0,0.2,0.4,0.6,0.8,1.0")
    WHISPER_REPETITION_PENALTY: float = float(os.getenv("WHISPER_REPETITION_PENALTY", "1.1"))
    WHISPER_NO_SPEECH_THRESHOLD: float = float(os.getenv("WHISPER_NO_SPEECH_THRESHOLD", "0.6"))
    WHISPER_WORD_TIMESTAMPS: bool = (
        os.getenv("WHISPER_WORD_TIMESTAMPS", "false").lower() == "true"
    )
    WHISPER_VAD_MIN_SILENCE_MS: int = int(os.getenv("WHISPER_VAD_MIN_SILENCE_MS", "500"))

    # --- Translation ---
    TRANSLATION_ENGINE: str = os.getenv(
        "TRANSLATION_ENGINE", "marian"
    )  # marian, nllb, google
    TRANSLATION_DEVICE: str = os.getenv(
        "TRANSLATION_DEVICE", "auto"
    )  # auto, cuda, cpu
    # Marian models are loaded on-demand per language pair

    # --- Cross-validation ---
    USE_CROSS_VALIDATION: bool = (
        os.getenv("USE_CROSS_VALIDATION", "false").lower() == "true"
    )

    # --- Vocal separation (Demucs) ---
    USE_VOCAL_SEPARATION: bool = (
        os.getenv("USE_VOCAL_SEPARATION", "false").lower() == "true"
    )

    # --- Bilingual subtitles ---
    DUAL_SUBTITLES: bool = (
        os.getenv("DUAL_SUBTITLES", "false").lower() == "true"
    )
    DEMUCS_MODEL: str = os.getenv("DEMUCS_MODEL", "htdemucs")  # htdemucs, htdemucs_ft, htdemucs_6s

    # --- Video ---
    FFMPEG_PATH: str = os.getenv("FFMPEG_PATH", "ffmpeg")
    FFPROBE_PATH: str = os.getenv("FFPROBE_PATH", "ffprobe")

    # --- Subtitle style ---
    SUBTITLE_FONT_SIZE: int = int(os.getenv("SUBTITLE_FONT_SIZE", "24"))
    SUBTITLE_FONT_COLOR: str = os.getenv("SUBTITLE_FONT_COLOR", "white")
    SUBTITLE_OUTLINE_COLOR: str = os.getenv("SUBTITLE_OUTLINE_COLOR", "black")
    SUBTITLE_OUTLINE: int = int(os.getenv("SUBTITLE_OUTLINE", "2"))

    # --- Processing ---
    AUDIO_SAMPLE_RATE: int = 16000
    MAX_SEGMENT_LENGTH: int = int(
        os.getenv("MAX_SEGMENT_LENGTH", "30")
    )  # seconds per chunk for long audio

    # --- Database ---
    DATABASE_PATH: str = os.getenv("DATABASE_PATH", "./v2v.db")

    # --- Subscription ---
    SUBSCRIPTION_CHECK_INTERVAL: int = int(os.getenv("SUBSCRIPTION_CHECK_INTERVAL", "3600"))

    # --- Server ---
    HOST: str = os.getenv("HOST", "0.0.0.0")
    PORT: int = int(os.getenv("PORT", "8000"))

    def __post_init__(self):
        for d in [self.INPUT_DIR, self.OUTPUT_DIR, self.TEMP_DIR, self.MODELS_DIR]:
            d.mkdir(parents=True, exist_ok=True)


settings = Settings()
