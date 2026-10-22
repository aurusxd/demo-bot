from pathlib import Path

from alembic import command
from alembic.config import Config

ROOT = Path(__file__).resolve().parent.parent


def test_migrations_apply_on_clean_db_and_match_models(tmp_path: Path) -> None:
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("sqlalchemy.url", f"sqlite+aiosqlite:///{(tmp_path / 'app.db').as_posix()}")

    command.upgrade(cfg, "head")
    command.check(cfg)  # raises if models differ from migrations
    command.downgrade(cfg, "base")
