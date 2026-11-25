"""Scope process environment changes made by server tests and SDK scenarios."""

from collections.abc import Iterator, Mapping
from contextlib import contextmanager
import os


def restore_environment(desired: Mapping[str, str]) -> None:
    # Reassigning an inherited empty value removes it from native Windows children.
    for key in os.environ.keys() - desired.keys():
        del os.environ[key]
    for key, value in desired.items():
        if os.environ.get(key) != value:
            os.environ[key] = value


@contextmanager
def scoped_environment(updates: Mapping[str, str] | None = None) -> Iterator[None]:
    previous = os.environ.copy()
    try:
        if updates:
            os.environ.update(updates)
        yield
    finally:
        restore_environment(previous)
