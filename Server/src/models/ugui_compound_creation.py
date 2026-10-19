"""Closed, bounded inputs for newly owned compound uGUI controls."""

import json
import math
import struct
from typing import Annotated, Literal

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    StrictInt,
    TypeAdapter,
    field_validator,
    model_validator,
)


def finite_float(value: float) -> float:
    try:
        narrowed = struct.unpack("!f", struct.pack("!f", value))[0]
    except (OverflowError, struct.error) as exc:
        raise ValueError("invalid_compound_number") from exc
    if not math.isfinite(narrowed):
        raise ValueError("invalid_compound_number")
    return narrowed


def bounded_text(value: str) -> str:
    if len(value.encode("utf-8", errors="strict")) > 1024:
        raise ValueError("compound_text_limit")
    return value


Number = Annotated[float, Field(strict=True, allow_inf_nan=False), AfterValidator(finite_float)]
Pair = Annotated[list[Number], Field(min_length=2, max_length=2)]
Text = Annotated[str, AfterValidator(bounded_text)]


class Closed(BaseModel):
    model_config = ConfigDict(
        strict=True, extra="forbid", frozen=True, hide_input_in_errors=True, validate_default=True
    )


class Common(Closed):
    sizeDelta: Pair
    anchoredPosition: Pair = [0.0, 0.0]
    eventSystem: Literal["preserve", "create_legacy_if_missing"] = "preserve"

    @field_validator("sizeDelta")
    @classmethod
    def size_bounds(cls, value: list[float]) -> list[float]:
        if any(not 1 <= item <= 8192 for item in value):
            raise ValueError("invalid_compound_size")
        return value


class Scroll(Common):
    sizeDelta: Pair = [200.0, 200.0]
    spacing: Annotated[Number, Field(ge=0, le=128)] = 0.0


class Toggle(Common):
    sizeDelta: Pair = [160.0, 24.0]
    label: Text = "Toggle"
    isOn: bool = False


class Slider(Common):
    sizeDelta: Pair = [160.0, 24.0]
    minValue: Number = 0.0
    maxValue: Number = 1.0
    value: Number = 0.0
    wholeNumbers: bool = False
    direction: Literal["LeftToRight", "RightToLeft", "BottomToTop", "TopToBottom"] = "LeftToRight"

    @model_validator(mode="after")
    def range_bounds(self) -> "Slider":
        if self.minValue >= self.maxValue or not math.isfinite(
            finite_float(self.maxValue - self.minValue)
        ):
            raise ValueError("invalid_compound_range")
        if not self.minValue <= self.value <= self.maxValue:
            raise ValueError("compound_value_outside_range")
        if self.wholeNumbers and any(
            item != math.trunc(item) for item in (self.minValue, self.maxValue, self.value)
        ):
            raise ValueError("compound_requires_integral_values")
        return self


class Input(Common):
    sizeDelta: Pair = [160.0, 36.0]
    text: Text = ""
    placeholder: Text = "Enter text..."
    characterLimit: Annotated[int, Field(strict=True, ge=0, le=1024)] = 256

    @model_validator(mode="after")
    def initial_limit(self) -> "Input":
        if self.characterLimit and len(self.text.encode("utf-16-le")) // 2 > self.characterLimit:
            raise ValueError("compound_character_limit")
        return self


class RequestBase(Closed):
    action: Literal["create_control"] = "create_control"
    parent: str | StrictInt
    name: str
    include_inactive: bool = False

    @field_validator("parent")
    @classmethod
    def parent_bounds(cls, value: str | int) -> str | int:
        if isinstance(value, int):
            if not -(2**31) <= value < 2**31 or value == 0:
                raise ValueError("invalid_compound_parent")
        elif not value.strip() or len(value.encode("utf-8", errors="strict")) > 1024:
            raise ValueError("invalid_compound_parent")
        return value

    @field_validator("name")
    @classmethod
    def name_bounds(cls, value: str) -> str:
        if (
            not value.strip()
            or any(ord(c) < 32 or c in "/\\" for c in value)
            or len(value.encode("utf-8", errors="strict")) > 256
        ):
            raise ValueError("invalid_compound_name")
        return value


class ScrollRequest(RequestBase):
    element_type: Literal["scroll_view"]
    properties: Scroll


class ToggleRequest(RequestBase):
    element_type: Literal["toggle"]
    properties: Toggle


class SliderRequest(RequestBase):
    element_type: Literal["slider"]
    properties: Slider


class InputRequest(RequestBase):
    element_type: Literal["tmp_input"]
    properties: Input


CONTROL = TypeAdapter(
    Annotated[
        ScrollRequest | ToggleRequest | SliderRequest | InputRequest,
        Field(discriminator="element_type"),
    ]
)


def reject_constant(_: str) -> None:
    raise ValueError("invalid_compound_number")


def validate_compound_creation(
    parent: str | int | None,
    name: str | None,
    kind: str | None,
    properties: dict[str, JsonValue] | str | None,
    include_inactive: bool,
) -> dict[str, JsonValue]:
    if isinstance(properties, str):
        if len(properties.encode("utf-8", errors="strict")) > 16384:
            raise ValueError("compound_request_limit")
        parsed = json.loads(properties, parse_constant=reject_constant)
        if not isinstance(parsed, dict):
            raise ValueError("compound_properties_requires_object")
        properties = parsed
    if properties is None:
        properties = {}
    raw = {
        "action": "create_control",
        "element_type": kind,
        "parent": parent,
        "name": name,
        "properties": properties,
        "include_inactive": include_inactive,
    }
    result = CONTROL.validate_python(raw).model_dump(mode="json")
    encoded = json.dumps(result, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode(
        "utf-8", errors="strict"
    )
    if len(encoded) > 16384:
        raise ValueError("compound_request_limit")
    return result
