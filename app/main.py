"""
FastAPI application entry point.

Start with:
    uvicorn app.main:app --reload
"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import router
from app.config.settings import settings
from app.utils.logger import logger

app = FastAPI(
    title="V2V — Video Subtitle Translator",
    version="0.1.0",
    description="Automatically transcribe, translate, and burn subtitles into videos.",
)

# Allow all origins for MVP
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router)


@app.on_event("startup")
async def startup():
    logger.info(f"V2V starting — model: {settings.WHISPER_MODEL}, device: {settings.WHISPER_DEVICE}")
    logger.info(f"Input dir: {settings.INPUT_DIR}")
    logger.info(f"Output dir: {settings.OUTPUT_DIR}")
