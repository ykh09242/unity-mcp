"""Native JSON decoding with the existing strict UTF-8/stdlib compatibility path."""

import json
import sys
from typing import Literal, Protocol, TypeAlias

from pydantic_core import from_json

JSONInput: TypeAlias = str | bytes | bytearray
JSONValue: TypeAlias = str | int | float | bool | None | list["JSONValue"] | dict[str, "JSONValue"]


class NativeJSONDecoder(Protocol):
    """Complete-input native JSON call used by the compatibility decoder."""

    def __call__(
        self,
        data: JSONInput,
        *,
        allow_inf_nan: Literal[True],
        allow_partial: Literal[False],
        cache_strings: Literal["keys"],
    ) -> JSONValue: ...


def _decode_stdlib(data: JSONInput, fallback_text: str | None) -> JSONValue:
    text = (
        fallback_text
        if fallback_text is not None
        else (data if isinstance(data, str) else data.decode("utf-8"))
    )
    return json.loads(text)


def decode_json(
    data: JSONInput,
    *,
    fallback_text: str | None = None,
    native_decoder: NativeJSONDecoder | None = None,
) -> JSONValue:
    """Parse complete JSON; preserve configured digit limits and fallback errors.

    Callers own preparse limits. Hub passes its already bounded text so fallback
    never repeats UTF-8 decoding or invokes a custom decode hook a second time.
    """
    if sys.get_int_max_str_digits() != sys.int_info.default_max_str_digits:
        return _decode_stdlib(data, fallback_text)
    try:
        decoder = native_decoder if native_decoder is not None else from_json
        return decoder(data, allow_inf_nan=True, allow_partial=False, cache_strings="keys")
    except ValueError:
        # Native JSON rejects accepted lone surrogates; malformed input must
        # retain the stdlib's exceptions and protocol-level classification.
        return _decode_stdlib(data, fallback_text)
