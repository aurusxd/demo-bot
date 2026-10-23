import pytest
from pydantic import ValidationError

from app.config import Settings


def make_settings(monkeypatch: pytest.MonkeyPatch, **env: str) -> Settings:
    env = {
        "DEEPSEEK_API_KEY": "sk-test-key",
        "POLZA_API_KEY": "pk-test-key",
        "POLZA_STT_MODEL": "openai/whisper-1",
        "POLZA_IMAGE_MODEL": "google/gemini-3.1-flash-lite-image",
        "ADMIN_CHAT_ID": "777",
        **env,
    }
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    return Settings(_env_file=None)  # pyright: ignore[reportCallIssue]


def test_bot_token_is_required(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("BOT_TOKEN", raising=False)
    with pytest.raises(ValidationError):
        make_settings(monkeypatch)


def test_deepseek_key_is_required(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.setenv("BOT_TOKEN", "123:abc")
    with pytest.raises(ValidationError):
        Settings(_env_file=None)  # pyright: ignore[reportCallIssue]


@pytest.mark.parametrize(
    "missing", ["POLZA_API_KEY", "POLZA_STT_MODEL", "POLZA_IMAGE_MODEL", "ADMIN_CHAT_ID"]
)
def test_service_settings_are_required(monkeypatch: pytest.MonkeyPatch, missing: str) -> None:
    monkeypatch.setenv("BOT_TOKEN", "123:abc")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-deep")
    monkeypatch.setenv("POLZA_API_KEY", "pk")
    monkeypatch.setenv("POLZA_STT_MODEL", "openai/whisper-1")
    monkeypatch.setenv("POLZA_IMAGE_MODEL", "google/gemini-3.1-flash-lite-image")
    monkeypatch.setenv("ADMIN_CHAT_ID", "777")
    monkeypatch.delenv(missing)
    with pytest.raises(ValidationError):
        Settings(_env_file=None)  # pyright: ignore[reportCallIssue]


def test_defaults_and_admin_ids_parsing(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = make_settings(monkeypatch, BOT_TOKEN="123:abc", ADMIN_IDS="1, 2,3")

    assert settings.admin_ids == [1, 2, 3]
    assert settings.tryon_daily_limit == 5
    assert settings.database_url.startswith("sqlite+aiosqlite")
    assert settings.polza_base_url == "https://polza.ai/api/v1"
    assert settings.polza_stt_provider is None


def test_empty_values_fall_back_to_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = make_settings(monkeypatch, BOT_TOKEN="123:abc", CRM_WEBHOOK_URL="", LOG_LEVEL="")

    assert settings.crm_webhook_url is None
    assert settings.log_level == "INFO"


def test_invalid_number_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(ValidationError):
        make_settings(monkeypatch, BOT_TOKEN="123:abc", TRYON_DAILY_LIMIT="many")


def test_secret_values_are_collected(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = make_settings(
        monkeypatch, BOT_TOKEN="123:abc", DEEPSEEK_API_KEY="sk-deep", POLZA_API_KEY="pk-polza"
    )

    assert settings.secret_values() == ["123:abc", "sk-deep", "pk-polza"]


def test_crm_webhook_url_is_masked_as_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    url = "https://crm.example/rest/1/hook-key/"
    settings = make_settings(monkeypatch, BOT_TOKEN="123:abc", CRM_WEBHOOK_URL=url)

    assert settings.secret_values()[-1] == url
    assert "hook-key" not in repr(settings)
    assert "sk-deep" not in repr(settings)


def test_validation_error_does_not_echo_secrets(monkeypatch: pytest.MonkeyPatch) -> None:
    token = "123456:" + "T" * 35
    monkeypatch.delenv("POLZA_IMAGE_MODEL", raising=False)
    with pytest.raises(ValidationError) as exc_info:
        make_settings(monkeypatch, BOT_TOKEN=token, POLZA_IMAGE_MODEL="")

    error = str(exc_info.value)
    assert "polza_image_model" in error
    for secret in (token[:10], "sk-test-key", "pk-test-key"):
        assert secret not in error
