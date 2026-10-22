from pathlib import Path

import pytest
from pydantic import ValidationError

from app.db.models import FurnitureType, PriceClass
from app.schemas.estimate import EstimateResult, PricingConfig
from app.schemas.qualification import LeadSlotsPatch
from app.services.estimate import EstimateService, load_pricing

PRICING = PricingConfig(
    base_per_meter={
        FurnitureType.KITCHEN: 45000,
        FurnitureType.WARDROBE: 30000,
        FurnitureType.WALK_IN: 35000,
    },
    class_coefficients={
        PriceClass.ECONOMY: 0.8,
        PriceClass.STANDARD: 1.0,
        PriceClass.PREMIUM: 1.4,
    },
    spread=0.15,
    round_to=1000,
)


def test_repo_pricing_file_is_valid() -> None:
    pricing = load_pricing(Path("config/pricing.yaml"))

    assert set(pricing.base_per_meter) == set(FurnitureType)
    assert set(pricing.class_coefficients) == set(PriceClass)


@pytest.mark.parametrize(
    ("furniture_type", "length_m", "price_class", "expected"),
    [
        # 3 m x 45 000 = 135 000 -> ±15 % = 114 750 / 155 250 -> rounded to 1 000
        (FurnitureType.KITCHEN, 3.0, None, (115000, 155000)),
        (FurnitureType.KITCHEN, 3.0, PriceClass.STANDARD, (115000, 155000)),
        # 2 m x 30 000 x 0.8 = 48 000 -> 40 800 / 55 200
        (FurnitureType.WARDROBE, 2.0, PriceClass.ECONOMY, (41000, 55000)),
        # 2.5 m x 35 000 x 1.4 = 122 500 -> 104 125 / 140 875
        (FurnitureType.WALK_IN, 2.5, PriceClass.PREMIUM, (104000, 141000)),
    ],
)
def test_estimate_range(
    furniture_type: FurnitureType,
    length_m: float,
    price_class: PriceClass | None,
    expected: tuple[int, int],
) -> None:
    result = EstimateService(PRICING).calculate(furniture_type, length_m, price_class)

    assert result == EstimateResult(*expected, currency="₽")


@pytest.mark.parametrize(
    ("furniture_type", "length_m"), [(None, 3.0), (FurnitureType.KITCHEN, None)]
)
def test_no_estimate_without_type_or_length(
    furniture_type: FurnitureType | None, length_m: float | None
) -> None:
    assert EstimateService(PRICING).calculate(furniture_type, length_m, None) is None


def test_tiny_estimate_is_never_zero() -> None:
    pricing = PRICING.model_copy(update={"round_to": 100000})

    result = EstimateService(pricing).calculate(FurnitureType.WARDROBE, 0.5, PriceClass.ECONOMY)

    assert result is not None and result.min_price == 100000


@pytest.mark.parametrize(
    "raw",
    [
        {"base_per_meter": {"kitchen": 1}, "class_coefficients": {}, "spread": 0.1},
        {**PRICING.model_dump(mode="json"), "spread": 1.5},
        {
            **PRICING.model_dump(mode="json"),
            "class_coefficients": {"economy": 0, "standard": 1, "premium": 1},
        },
        {**PRICING.model_dump(mode="json"), "unknown_key": 1},
    ],
)
def test_broken_pricing_is_rejected(raw: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        PricingConfig.model_validate(raw)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("3,5", 3.5), (5, 5.0), ("0.2", None), (100, None), ("три", None), (True, None), (None, None)],
)
def test_length_slot_is_validated_by_code(raw: object, expected: float | None) -> None:
    assert LeadSlotsPatch.model_validate({"length_m": raw}).length_m == expected


@pytest.mark.parametrize(
    ("raw", "expected"), [("Premium", PriceClass.PREMIUM), ("luxury", None), (None, None)]
)
def test_price_class_slot_is_lenient(raw: object, expected: PriceClass | None) -> None:
    assert LeadSlotsPatch.model_validate({"price_class": raw}).price_class == expected
