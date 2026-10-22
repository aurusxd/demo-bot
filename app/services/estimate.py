from pathlib import Path

import yaml
from loguru import logger

from app.db.models import FurnitureType, PriceClass
from app.schemas.estimate import EstimateResult, PricingConfig


def load_pricing(path: Path) -> PricingConfig:
    """Blocking file read: call at startup (via asyncio.to_thread)."""
    with path.open(encoding="utf-8") as file:
        raw: object = yaml.safe_load(file)
    pricing = PricingConfig.model_validate(raw)
    logger.info("pricing loaded path={} types={}", path, len(pricing.base_per_meter))
    return pricing


class EstimateService:
    """Deterministic price range: length x base price x class coefficient, then ±spread.

    No LLM here: the LLM only extracts numbers, the price is always computed by code.
    """

    def __init__(self, pricing: PricingConfig) -> None:
        self._pricing = pricing

    @property
    def currency(self) -> str:
        return self._pricing.currency

    def calculate(
        self,
        furniture_type: FurnitureType | None,
        length_m: float | None,
        price_class: PriceClass | None,
    ) -> EstimateResult | None:
        if furniture_type is None or length_m is None:
            logger.info(
                "estimate skipped has_type={} has_length={}",
                furniture_type is not None,
                length_m is not None,
            )
            return None
        pricing = self._pricing
        used_class = price_class or pricing.default_class
        center = (
            length_m
            * pricing.base_per_meter[furniture_type]
            * pricing.class_coefficients[used_class]
        )
        result = EstimateResult(
            min_price=self._round(center * (1 - pricing.spread)),
            max_price=self._round(center * (1 + pricing.spread)),
            currency=pricing.currency,
        )
        logger.info(
            "estimate done type={} length_m={} class={} min={} max={}",
            furniture_type,
            length_m,
            used_class,
            result.min_price,
            result.max_price,
        )
        return result

    def _round(self, value: float) -> int:
        step = self._pricing.round_to
        return max(step, round(value / step) * step)
