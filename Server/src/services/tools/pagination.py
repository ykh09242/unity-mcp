"""Early page validation mirrored by the authoritative native page bounds."""

from decimal import Decimal, InvalidOperation
from typing import Any

MAX_PAGE_SIZE = 1000
MAX_PREVIEW_PAGE_SIZE = 32
MAX_POSITION = 2_147_483_647


def page_integer(value: Any, default: int, minimum: int, maximum: int, name: str) -> int:
    if value is None:
        return default
    error = f"'{name}' must be an integer between {minimum} and {maximum}."
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise ValueError(error)
    if isinstance(value, int):
        number = value
    else:
        text = str(value)
        if len(text) > 64:
            raise ValueError(error)
        try:
            decimal = Decimal(text)
            if not decimal.is_finite() or decimal != decimal.to_integral_value():
                raise ValueError(error)
            if not minimum <= decimal <= maximum:
                raise ValueError(error)
            number = int(decimal)
        except InvalidOperation as exc:
            raise ValueError(error) from exc
    if not minimum <= number <= maximum:
        raise ValueError(error)
    return number


def validate_page(
    size: Any, position: Any, *, preview: bool = False, cursor: bool = False
) -> tuple[int, int]:
    maximum = MAX_PREVIEW_PAGE_SIZE if preview else MAX_PAGE_SIZE
    return (
        page_integer(size, min(50, maximum), 1, maximum, "page_size"),
        page_integer(
            position,
            0 if cursor else 1,
            0 if cursor else 1,
            MAX_POSITION,
            "cursor" if cursor else "page_number",
        ),
    )
