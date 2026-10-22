import pytest
from pydantic import ValidationError

from app.config import Settings


def make_settings(monkeypatch: pytest.MonkeyPatch, **env: str) -> Settings:
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    return Settings(_env_file=None)  # pyright: ignore[reportCallIssue]


def test_bot_token_is_required(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("BOT_TOKEN", raising=False)
    with pytest.raises(ValidationError):
        make_settings(monkeypatch)


def test_defaults_and_admin_ids_parsing(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = make_settings(monkeypatch, BOT_TOKEN="123:abc", ADMIN_IDS="1, 2,3")

    assert settings.admin_ids == [1, 2, 3]
    assert settings.tryon_daily_limit == 5
    assert settings.database_url.startswith("sqlite+aiosqlite")


def test_empty_values_fall_back_to_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = make_settings(monkeypatch, BOT_TOKEN="123:abc", ADMIN_CHAT_ID="", LOG_LEVEL="")

    assert settings.admin_chat_id is None
    assert settings.log_level == "INFO"


def test_invalid_number_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(ValidationError):
        make_settings(monkeypatch, BOT_TOKEN="123:abc", TRYON_DAILY_LIMIT="many")


def test_secret_values_are_collected(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = make_settings(monkeypatch, BOT_TOKEN="123:abc", DEEPSEEK_API_KEY="sk-deep")

    assert settings.secret_values() == ["123:abc", "sk-deep"]
    assert "sk-deep" not in repr(settings)
