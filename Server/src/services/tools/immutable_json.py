"""Owned immutable JSON trees for measured concurrent response fan-out."""

from collections.abc import Callable
from types import MappingProxyType
from typing import cast


def _freeze(value: object, seen: set[int]) -> object:
    kind = type(value)
    if kind in (dict, list):
        identity = id(value)
        if identity in seen:
            raise TypeError("Aliased or cyclic response")
        seen.add(identity)
        if kind is list:
            return tuple(_freeze(child, seen) for child in cast(list[object], value))
        items = cast(dict[str, object], value)
        if any(type(key) is not str for key in items):
            raise TypeError("Non-JSON key")
        return MappingProxyType({key: _freeze(child, seen) for key, child in items.items()})
    if value is None or kind in (str, int, bool, float):
        return value
    raise TypeError("Non-JSON response")


def _thaw(value: object) -> object:
    if type(value) is tuple:
        return [_thaw(child) for child in cast(tuple[object, ...], value)]
    if type(value) is MappingProxyType:
        return {
            key: _thaw(child) for key, child in cast(MappingProxyType[str, object], value).items()
        }
    return value


def prepare_json_copy(value: object) -> Callable[[], object] | None:
    """Share only immutable scalars; every returned mutable container is detached.

    Models, subclasses, aliases and cycles keep the established deepcopy path.
    No encoding occurs, so arbitrary integers, Unicode and float bits survive.
    """
    try:
        frozen = _freeze(value, set())
    except (TypeError, RecursionError):
        return None
    return lambda: _thaw(frozen)
