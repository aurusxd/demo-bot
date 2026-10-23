import asyncio
import io
from dataclasses import dataclass

from loguru import logger
from PIL import Image, ImageOps

from app.clients.base import FileDownloader, SpeechClient
from app.core.errors import EmptyTranscriptionError, InvalidImageError, MediaTooLargeError

MAX_VOICE_DURATION_S = 60
MAX_FILE_SIZE_BYTES = 10 * 1024 * 1024
ALLOWED_IMAGE_FORMATS = frozenset({"JPEG", "PNG", "WEBP"})
DEFAULT_VOICE_MIME = "audio/ogg"
TRYON_MAX_SIDE_PX = 1536
JPEG_QUALITY = 90


@dataclass(frozen=True, slots=True)
class VoiceInput:
    file_id: str
    duration_s: int
    file_size: int | None = None
    mime_type: str | None = None


@dataclass(frozen=True, slots=True)
class PhotoInput:
    file_id: str
    file_size: int | None = None


class MediaService:
    """Validates incoming voice/photo and turns voice into text. Nothing is written to disk."""

    def __init__(self, downloader: FileDownloader, speech: SpeechClient) -> None:
        self._downloader = downloader
        self._speech = speech

    async def transcribe_voice(self, voice: VoiceInput) -> str:
        if voice.duration_s > MAX_VOICE_DURATION_S:
            logger.info("voice rejected duration_s={}", voice.duration_s)
            raise MediaTooLargeError(f"voice duration {voice.duration_s}s")
        audio = await self._download(voice.file_id, voice.file_size)
        text = (await self._speech.transcribe(audio, voice.mime_type or DEFAULT_VOICE_MIME)).strip()
        if not text:
            logger.info("voice transcription empty duration_s={}", voice.duration_s)
            raise EmptyTranscriptionError("empty transcription")
        logger.info("voice transcribed duration_s={} text_len={}", voice.duration_s, len(text))
        return text

    async def validate_photo(self, photo: PhotoInput) -> None:
        """Download and check the photo; the bytes are dropped, only file_id is kept."""
        data = await self._download(photo.file_id, photo.file_size)
        image_format = await asyncio.to_thread(_verify_image, data)
        logger.info("photo validated format={} bytes={}", image_format, len(data))

    async def prepare_tryon_photo(self, photo: PhotoInput) -> bytes:
        """Download, check and shrink the room photo for the try-on: a JPEG in memory."""
        data = await self._download(photo.file_id, photo.file_size)
        await asyncio.to_thread(_verify_image, data)
        prepared = await asyncio.to_thread(fit_jpeg, data)
        logger.info("tryon photo prepared bytes_in={} bytes_out={}", len(data), len(prepared))
        return prepared

    async def _download(self, file_id: str, declared_size: int | None) -> bytes:
        if declared_size is not None and declared_size > MAX_FILE_SIZE_BYTES:
            logger.info("file rejected declared_bytes={}", declared_size)
            raise MediaTooLargeError(f"file size {declared_size}")
        data = await self._downloader.download(file_id)
        if len(data) > MAX_FILE_SIZE_BYTES:
            logger.info("file rejected bytes={}", len(data))
            raise MediaTooLargeError(f"file size {len(data)}")
        return data


def _verify_image(data: bytes) -> str:
    """Blocking (Pillow): run in a worker thread. Returns the detected format."""
    try:
        with Image.open(io.BytesIO(data)) as image:
            image_format = image.format
            image.verify()
    except (OSError, SyntaxError, ValueError, Image.DecompressionBombError) as exc:
        logger.info("photo rejected error={}", type(exc).__name__)
        raise InvalidImageError(type(exc).__name__) from exc
    if image_format not in ALLOWED_IMAGE_FORMATS:
        logger.info("photo rejected format={}", image_format)
        raise InvalidImageError(f"format {image_format}")
    return image_format


def fit_jpeg(data: bytes, max_side: int = TRYON_MAX_SIDE_PX) -> bytes:
    """Blocking (Pillow): run in a worker thread. Shrinks to `max_side`, re-encodes as JPEG.

    The image must be verified first. EXIF orientation is applied, metadata is dropped.
    """
    with Image.open(io.BytesIO(data)) as source:
        image = ImageOps.exif_transpose(source).convert("RGB")
    image.thumbnail((max_side, max_side))
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=JPEG_QUALITY)
    return buffer.getvalue()
