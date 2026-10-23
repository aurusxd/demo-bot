class AppError(Exception):
    """Base class for all domain errors."""


class ExternalServiceError(AppError):
    """External API failed after all retries or returned a non-retryable error."""

    def __init__(self, service: str, message: str, status_code: int | None = None) -> None:
        super().__init__(f"{service}: {message}")
        self.service = service
        self.status_code = status_code


class InvalidInputError(AppError):
    """User input failed validation (too long, empty, wrong type)."""


class MediaTooLargeError(InvalidInputError):
    """Voice is too long or a file exceeds the size limit."""


class InvalidImageError(InvalidInputError):
    """The photo cannot be opened as an allowed image format."""


class EmptyTranscriptionError(InvalidInputError):
    """Speech recognition returned no text (silence, noise)."""


class CatalogItemNotFoundError(AppError):
    """The catalog item is gone (removed from catalog.yaml) or the id is forged."""


class TryOnLimitError(AppError):
    """The user's daily try-on generations are used up."""


class TryOnFailedError(AppError):
    """Try-on generation failed (image API error, timeout, unreadable reference)."""
