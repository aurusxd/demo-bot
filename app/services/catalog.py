from dataclasses import dataclass
from pathlib import Path

import yaml
from loguru import logger

from app.db.models import CatalogCategory, CatalogItem
from app.db.repositories import CatalogRepository, UnitOfWork
from app.schemas.catalog import CatalogConfig, CatalogEntry


def load_catalog(path: Path) -> CatalogConfig:
    """Blocking file reads: call at startup (via asyncio.to_thread).

    Every image must exist next to the yaml, so a typo fails the start, not a demo.
    """
    with path.open(encoding="utf-8") as file:
        raw: object = yaml.safe_load(file)
    catalog = CatalogConfig.model_validate(raw)
    missing = [entry.image for entry in catalog.items if not (path.parent / entry.image).is_file()]
    if missing:
        raise FileNotFoundError(f"catalog images not found in {path.parent}: {missing}")
    logger.info("catalog loaded path={} items={}", path, len(catalog.items))
    return catalog


@dataclass(frozen=True, slots=True)
class CatalogCard:
    item_id: int
    category: CatalogCategory
    index: int  # 0-based position in the category
    total: int
    title: str
    style: str | None
    file_id: str | None  # Telegram cache; None -> upload from image_path
    image_path: Path


class CatalogService:
    """Catalog seeded from yaml; cards are paged per category, photos cached by file_id."""

    def __init__(self, items: CatalogRepository, uow: UnitOfWork, images_dir: Path) -> None:
        self._items = items
        self._uow = uow
        self._images_dir = images_dir

    async def seed(self, catalog: CatalogConfig) -> None:
        """Idempotent: keyed by image path, keeps cached file_ids, drops items gone from yaml."""
        existing = {item.image_path: item for item in await self._items.list_all()}
        added = updated = 0
        for entry in catalog.items:
            item = existing.pop(entry.image, None)
            if item is None:
                await self._items.add(_new_item(entry))
                added += 1
            elif _apply_entry(item, entry):
                await self._items.save(item)
                updated += 1
        for stale in existing.values():
            await self._items.delete(stale)
        await self._uow.commit()
        logger.info(
            "catalog seeded items={} added={} updated={} removed={}",
            len(catalog.items),
            added,
            updated,
            len(existing),
        )

    async def get_card(self, category: CatalogCategory, index: int) -> CatalogCard | None:
        """Card at `index`, wrapping around both ends; None if the category is empty."""
        total = await self._items.count(category)
        if total == 0:
            logger.warning("catalog category empty category={}", category)
            return None
        index %= total  # callback data comes from the client: any int is safe here
        item = await self._items.get_at(category, index)
        if item is None:  # the catalog changed between the two queries
            return None
        logger.info(
            "catalog card category={} index={} total={} item_id={} cached={}",
            category,
            index,
            total,
            item.id,
            item.tg_file_id is not None,
        )
        return CatalogCard(
            item_id=item.id,
            category=category,
            index=index,
            total=total,
            title=item.title,
            style=item.style,
            file_id=item.tg_file_id,
            image_path=self._images_dir / item.image_path,
        )

    async def remember_file_id(self, item_id: int, file_id: str) -> None:
        """Cache the Telegram file_id after an upload: next time the photo is not re-sent."""
        item = await self._items.get(item_id)
        if item is None:
            return
        item.tg_file_id = file_id
        await self._items.save(item)
        await self._uow.commit()
        logger.info("catalog file_id cached item_id={}", item_id)


def _new_item(entry: CatalogEntry) -> CatalogItem:
    return CatalogItem(
        category=entry.category,
        title=entry.title,
        style=entry.style,
        image_path=entry.image,
        source_url=entry.source_url,
    )


def _apply_entry(item: CatalogItem, entry: CatalogEntry) -> bool:
    """Copy yaml fields onto an existing item; True if anything changed."""
    fields = {
        "category": entry.category,
        "title": entry.title,
        "style": entry.style,
        "source_url": entry.source_url,
    }
    changed = [name for name, value in fields.items() if getattr(item, name) != value]
    for name in changed:
        setattr(item, name, fields[name])
    return bool(changed)
