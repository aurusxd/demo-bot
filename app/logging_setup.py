import inspect
import logging
import re
import sys
from collections.abc import Iterable
from pathlib import Path
from typing import TYPE_CHECKING

from loguru import logger

if TYPE_CHECKING:
    from loguru import Record

LOG_FILE = Path("logs/app.log")
LOG_ROTATION = "10 MB"
LOG_RETENTION = "14 days"
LOG_COMPRESSION = "zip"
MASK = "***"
MIN_SECRET_LEN = 8

STDOUT_FORMAT = (
    "<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> | <level>{level: <8}</level> | "
    "<cyan>{name}:{function}:{line}</cyan> | {message} | {extra}"
)

# Third-party loggers that duplicate our own logs (updates, HTTP calls, SQL).
QUIET_LOGGERS = ("aiogram.event", "httpx", "httpcore", "sqlalchemy.engine", "aiosqlite")

# No leading \b: the token is glued to "/bot" in Telegram API URLs.
_BOT_TOKEN_RE = re.compile(r"(?<!\d)\d{6,12}:[A-Za-z0-9_-]{30,}")
_BEARER_RE = re.compile(r"(?i)\bbearer\s+\S+")
_PHONE_RE = re.compile(r"(?:\+\d{1,3}|\b[78])[\s\-(]*\d{3}[\s\-)]*\d{3}[\s\-]*\d{2}[\s\-]*\d{2}\b")


def mask_phone(phone: str) -> str:
    """'+7 (912) 345-12-34' -> '+7***1234'."""
    digits = re.sub(r"\D", "", phone)
    if len(digits) < 5:
        return MASK
    if len(digits) == 11 and digits[0] == "8":
        digits = "7" + digits[1:]
    return f"+{digits[0]}{MASK}{digits[-4:]}"


class Sanitizer:
    """Removes secrets and phone numbers from log text."""

    def __init__(self, secrets: Iterable[str]) -> None:
        unique = {s for s in secrets if len(s) >= MIN_SECRET_LEN}
        self._secrets = sorted(unique, key=len, reverse=True)

    def __call__(self, text: str) -> str:
        for secret in self._secrets:
            text = text.replace(secret, MASK)
        text = _BOT_TOKEN_RE.sub(MASK, text)
        text = _BEARER_RE.sub(f"Bearer {MASK}", text)
        return _PHONE_RE.sub(lambda m: mask_phone(m.group()), text)


class _InterceptHandler(logging.Handler):
    """Routes stdlib logging (aiogram, httpx, sqlalchemy, alembic) into loguru."""

    def emit(self, record: logging.LogRecord) -> None:
        try:
            level: str | int = logger.level(record.levelname).name
        except ValueError:
            level = record.levelno

        frame, depth = inspect.currentframe(), 0
        while frame is not None:
            filename = frame.f_code.co_filename
            is_internal = filename == logging.__file__ or (
                "importlib" in filename and "_bootstrap" in filename
            )
            if depth > 0 and not is_internal:
                break
            frame = frame.f_back
            depth += 1

        logger.opt(depth=depth, exception=record.exc_info).log(level, record.getMessage())


def _reset(sanitize: Sanitizer) -> None:
    def patch(record: "Record") -> None:
        record["message"] = sanitize(record["message"])
        for key, value in record["extra"].items():
            if isinstance(value, str):
                record["extra"][key] = sanitize(value)

    logger.remove()
    logger.configure(patcher=patch)


def setup_startup_logging() -> None:
    """Safe logging until settings are loaded (secrets unknown yet).

    loguru's default sink has diagnose=True: a startup traceback (bad .env) would dump local
    variables, i.e. the raw env with every token and key. Tokens are masked by pattern only.
    """
    _reset(Sanitizer(()))
    logger.add(sys.stderr, format=STDOUT_FORMAT, diagnose=False, backtrace=False)


def setup_logging(level: str, secrets: Iterable[str], log_file: Path | None = LOG_FILE) -> None:
    _reset(Sanitizer(secrets))
    # diagnose=False: never dump local variable values (may contain secrets/PII) in tracebacks.
    logger.add(sys.stdout, level=level, format=STDOUT_FORMAT, diagnose=False, backtrace=False)
    if log_file is not None:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        logger.add(
            log_file,
            level=level,
            serialize=True,
            rotation=LOG_ROTATION,
            retention=LOG_RETENTION,
            compression=LOG_COMPRESSION,
            enqueue=True,
            diagnose=False,
            backtrace=False,
            encoding="utf-8",
        )

    logging.basicConfig(handlers=[_InterceptHandler()], level=0, force=True)
    for name in QUIET_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)

    logger.info("logging configured level={} file={}", level, log_file)
