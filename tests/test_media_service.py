import pytest

from app.core.errors import (
    EmptyTranscriptionError,
    ExternalServiceError,
    InvalidImageError,
    MediaTooLargeError,
)
from app.services.media import (
    MAX_FILE_SIZE_BYTES,
    MAX_VOICE_DURATION_S,
    MediaService,
    PhotoInput,
    VoiceInput,
)
from tests.fakes import FakeDownloader, FakeSpeechClient, image_bytes

VOICE = b"OggS-voice"


async def test_voice_is_downloaded_and_transcribed() -> None:
    speech = FakeSpeechClient("  Нужна кухня 3 метра  ")
    service = MediaService(FakeDownloader({"v1": VOICE}), speech)

    text = await service.transcribe_voice(VoiceInput("v1", duration_s=5, mime_type="audio/ogg"))

    assert text == "Нужна кухня 3 метра"
    assert speech.calls == [(VOICE, "audio/ogg")]


async def test_voice_without_mime_defaults_to_ogg() -> None:
    speech = FakeSpeechClient("да")
    service = MediaService(FakeDownloader({"v1": VOICE}), speech)

    await service.transcribe_voice(VoiceInput("v1", duration_s=1))

    assert speech.calls[0][1] == "audio/ogg"


async def test_too_long_voice_is_rejected_before_download() -> None:
    downloader = FakeDownloader({"v1": VOICE})
    service = MediaService(downloader, FakeSpeechClient())

    with pytest.raises(MediaTooLargeError):
        await service.transcribe_voice(VoiceInput("v1", duration_s=MAX_VOICE_DURATION_S + 1))

    assert downloader.calls == []


async def test_empty_transcription_raises() -> None:
    service = MediaService(FakeDownloader({"v1": VOICE}), FakeSpeechClient("   "))

    with pytest.raises(EmptyTranscriptionError):
        await service.transcribe_voice(VoiceInput("v1", duration_s=3))


async def test_speech_api_error_propagates() -> None:
    speech = FakeSpeechClient(ExternalServiceError("polza", "HTTP 502", 502))
    service = MediaService(FakeDownloader({"v1": VOICE}), speech)

    with pytest.raises(ExternalServiceError):
        await service.transcribe_voice(VoiceInput("v1", duration_s=3))


@pytest.mark.parametrize("image_format", ["JPEG", "PNG", "WEBP"])
async def test_valid_photo_passes(image_format: str) -> None:
    service = MediaService(FakeDownloader({"p1": image_bytes(image_format)}), FakeSpeechClient())

    await service.validate_photo(PhotoInput("p1", file_size=100))


@pytest.mark.parametrize("data", [b"not an image", image_bytes("GIF"), image_bytes()[:40]])
async def test_invalid_photo_is_rejected(data: bytes) -> None:
    service = MediaService(FakeDownloader({"p1": data}), FakeSpeechClient())

    with pytest.raises(InvalidImageError):
        await service.validate_photo(PhotoInput("p1"))


async def test_too_large_photo_is_rejected_before_download() -> None:
    downloader = FakeDownloader({"p1": image_bytes()})
    service = MediaService(downloader, FakeSpeechClient())

    with pytest.raises(MediaTooLargeError):
        await service.validate_photo(PhotoInput("p1", file_size=MAX_FILE_SIZE_BYTES + 1))

    assert downloader.calls == []


async def test_download_error_propagates() -> None:
    service = MediaService(FakeDownloader(), FakeSpeechClient())

    with pytest.raises(ExternalServiceError):
        await service.validate_photo(PhotoInput("missing"))
