"""Read a bounded explicit policy with exact decimal limits and no implied pass defaults."""

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from hashlib import sha256
import json
from pathlib import Path
from typing import Annotated, Final, Literal, assert_never

from pydantic import BaseModel, ConfigDict, Field, JsonValue, field_validator

from cli.utils.play_scenario_soak import (
    SoakContractError,
    _reject_constant,
    _unique_object,
    read_soak_bytes,
)

POLICY_LIMIT: Final = 8192
MAX_LIMIT: Final = 2**63 - 1
MetricName = Literal["managed_bytes", "allocated_bytes", "object_count"]


class MetricBudget(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True, extra="forbid", allow_inf_nan=False)
    max_net_change_increase: Annotated[int, Field(ge=0, le=MAX_LIMIT)]
    max_slope_increase_per_iteration: Decimal
    min_paired_samples: Annotated[int, Field(ge=2, le=1000)]
    min_consecutive_samples: Annotated[int, Field(ge=2, le=1000)]

    @field_validator("max_slope_increase_per_iteration", mode="before")
    @classmethod
    def exact_decimal(cls, value: JsonValue | Decimal) -> Decimal:
        """Reject floats/bools and bound decimal expansion before constructing a Fraction."""
        match value:
            case bool() | float() | str() | list() | dict() | None:
                raise SoakContractError("Slope limit must be an exact JSON number")
            case int():
                parsed = Decimal(value)
            case Decimal():
                parsed = value
            case unreachable:
                assert_never(unreachable)
        if (
            not parsed.is_finite()
            or not 0 <= parsed <= MAX_LIMIT
            or not -18 <= parsed.as_tuple().exponent <= 18
            or len(parsed.as_tuple().digits) > 37
        ):
            raise SoakContractError("Slope limit exceeds its numeric or 18-place precision bound")
        return parsed


class SoakBudgetPolicy(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")
    schema_version: Annotated[int, Field(ge=1, le=1)]
    metrics: Annotated[dict[MetricName, MetricBudget], Field(min_length=1, max_length=3)]


@dataclass(frozen=True, slots=True)
class SavedBudgetPolicy:
    policy: SoakBudgetPolicy
    digest: str


def _decimal(token: str) -> Decimal:
    if len(token) > 64:
        raise SoakContractError("Policy numeric token exceeds its bound")
    try:
        return Decimal(token)
    except InvalidOperation as exc:
        raise SoakContractError(
            "Policy decimal token has an invalid or oversized exponent"
        ) from exc


def _require_shallow(encoded: bytes) -> None:
    depth = 0
    quoted = escaped = False
    for value in encoded:
        if quoted:
            if escaped:
                escaped = False
            elif value == 92:
                escaped = True
            elif value == 34:
                quoted = False
        elif value == 34:
            quoted = True
        elif value in (91, 123):
            depth += 1
            if depth > 8:
                raise SoakContractError("Budget policy nesting exceeds depth 8")
        elif value in (93, 125):
            depth -= 1


def read_budget_policy(path: Path) -> SavedBudgetPolicy:
    """Reject ambiguous/nonfinite/deep/linked policy input before any gate decision."""
    encoded = read_soak_bytes(path, POLICY_LIMIT)
    _require_shallow(encoded)
    parsed = json.loads(
        encoded.decode("utf-8"),
        object_pairs_hook=_unique_object,
        parse_float=_decimal,
        parse_constant=_reject_constant,
    )
    return SavedBudgetPolicy(SoakBudgetPolicy.model_validate(parsed), sha256(encoded).hexdigest())
