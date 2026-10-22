class AppError(Exception):
    """Base class for all domain errors."""


class ExternalServiceError(AppError):
    """External API failed after all retries or returned a non-retryable error."""

    def __init__(self, service: str, message: str, status_code: int | None = None) -> None:
        super().__init__(f"{service}: {message}")
        self.service = service
        self.status_code = status_code
