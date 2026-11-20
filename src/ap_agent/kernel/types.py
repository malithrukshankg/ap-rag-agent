"""Shared field types and the strict base model."""

from decimal import Decimal
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

Currency = Annotated[str, StringConstraints(pattern=r"^[A-Z]{3}$")]
Money = Annotated[Decimal, Field(max_digits=15, decimal_places=2)]
Sha256Hex = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]


class Strict(BaseModel):
    """Base for every domain type: unknown fields are rejected, instances are immutable.

    `extra="forbid"` is what makes schema validation meaningful at TB1 and TB3 (§4).
    """

    model_config = ConfigDict(extra="forbid", frozen=True)
