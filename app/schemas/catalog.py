from pathlib import PurePosixPath

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.db.models import CatalogCategory

IMAGE_SUFFIXES = frozenset({".jpg", ".jpeg", ".png", ".webp"})
TITLE_MAX_LEN = 255
STYLE_MAX_LEN = 128


class CatalogEntry(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    category: CatalogCategory
    title: str = Field(min_length=1, max_length=TITLE_MAX_LEN)
    style: str | None = Field(default=None, max_length=STYLE_MAX_LEN)
    image: str  # relative to catalog.yaml, POSIX separators
    source_url: str | None = None

    @field_validator("image")
    @classmethod
    def _relative_image(cls, value: str) -> str:
        path = PurePosixPath(value)
        if path.is_absolute() or ".." in path.parts or "\\" in value:
            raise ValueError("image must be a relative path inside the catalog dir")
        if path.suffix.lower() not in IMAGE_SUFFIXES:
            raise ValueError(f"image must be one of {sorted(IMAGE_SUFFIXES)}")
        return value


class CatalogConfig(BaseModel):
    """assets/catalog/catalog.yaml: validated at startup, a broken one stops the bot."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    items: list[CatalogEntry] = Field(min_length=1)

    @model_validator(mode="after")
    def _unique_images(self) -> "CatalogConfig":
        images = [entry.image for entry in self.items]
        if len(images) != len(set(images)):
            raise ValueError("duplicate catalog image paths")
        return self
