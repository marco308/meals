from pydantic import BaseModel, Field, model_validator

from app.services.units import UnitNotAllowedError, normalize_quantity


class IngredientLineIn(BaseModel):
    """An ingredient reference in a write payload. AI-ergonomic: ingredients
    are referenced by name (find-or-create), never by id. Quantities are
    normalised to canonical form here, so a bad unit fails fast with a
    conversion hint in the 422 body."""

    name: str = Field(min_length=1, max_length=200)
    quantity: float | None = Field(default=None, gt=0)
    unit: str | None = Field(default=None, max_length=50)
    raw: str | None = Field(default=None, max_length=500)

    @model_validator(mode="after")
    def _normalise(self) -> "IngredientLineIn":
        self.name = " ".join(self.name.lower().split())
        if not self.name:
            raise ValueError("ingredient name must not be blank")
        if self.quantity is not None:
            if self.unit is None:
                raise ValueError(
                    f"ingredient '{self.name}': unit is required when quantity is given "
                    "(use g/kg/ml/l or a natural unit like 'tin', 'clove', 'item')"
                )
            try:
                self.quantity, self.unit = normalize_quantity(self.quantity, self.unit)
            except UnitNotAllowedError as exc:
                raise ValueError(f"ingredient '{self.name}': {exc}") from exc
        elif self.unit is not None:
            raise ValueError(f"ingredient '{self.name}': quantity is required when unit is given")
        return self
