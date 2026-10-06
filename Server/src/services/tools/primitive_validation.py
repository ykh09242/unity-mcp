"""Validate the native PrimitiveType Enum.Parse syntax without rewriting tokens."""
import re
from typing import Any


_NAMES = {"sphere", "capsule", "cylinder", "cube", "plane", "quad"}
_INTEGER = re.compile(r"[+-]?[0-9]+\Z")
# String.Trim uses Char.IsWhiteSpace; Python strip also removes U+001C..U+001F.
_WHITESPACE = "\t\n\v\f\r \u0085\u00a0\u1680\u2000\u2001\u2002\u2003\u2004\u2005\u2006\u2007\u2008\u2009\u200a\u2028\u2029\u202f\u205f\u3000"


def primitive_type_error(value: Any, field: str = "primitive_type") -> str | None:
    """Allow optional empty values, named combinations and signed Int32 strings."""
    if value is None or value == "":
        return None
    if isinstance(value, str):
        token = value.strip(_WHITESPACE)
        if _INTEGER.fullmatch(token):
            # Avoid Python's digit limit while preserving native leading-zero syntax.
            digits = token.lstrip("+-").lstrip("0") or "0"
            if len(digits) <= 10:
                number = int(digits) * (-1 if token.startswith("-") else 1)
                if -2_147_483_648 <= number <= 2_147_483_647:
                    return None
        elif all(part.strip(_WHITESPACE).lower() in _NAMES for part in token.split(",")):
            return None
    return f"Invalid '{field}': expected a PrimitiveType name, named combination or signed Int32 string."
