from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict, Field, PositiveInt, model_validator

from app.db.models import FurnitureType, PriceClass


class PricingConfig(BaseModel):
    """config/pricing.yaml: validated at startup, the bot does not start with a broken price."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    currency: str = "₽"
    base_per_meter: dict[FurnitureType, PositiveInt]
    class_coefficients: dict[PriceClass, float]
    default_class: PriceClass = PriceClass.STANDARD
    spread: float = Field(ge=0, lt=1)
    round_to: PositiveInt = 1000

    @model_validator(mode="after")
    def _complete(self) -> "PricingConfig":
        missing_types = set(FurnitureType) - set(self.base_per_meter)
        missing_classes = set(PriceClass) - set(self.class_coefficients)
        if missing_types or missing_classes:
            raise ValueError(f"pricing incomplete: {missing_types | missing_classes}")
        if any(k <= 0 for k in self.class_coefficients.values()):
            raise ValueError("class coefficients must be positive")
        return self


@dataclass(frozen=True, slots=True)
class EstimateResult:
    min_price: int
    max_price: int
    currency: str
