"""Partition calibration routes by their declared operation families and equivalent surfaces."""

from itertools import chain
import unicodedata

from services.registry.action_catalog import operation_owner
from services.registry.task_search import _EQUIVALENT_READS
from services.registry.tool_registry import FIXED_OBSERVATION_ACTIONS

from tools.experiments.discovery_evaluation_fixture import Request

# Standalone Editor observations do not all have a forwarding tool in the registry.
# These are benchmark boundaries. The production catalog and ranker do not import them.
EDITOR_PROJECT_READS = frozenset({"mcpforunity://project/layers", "mcpforunity://project/tags"})


def binding_name(key):
    return ".".join(value for value in key[1:] if value)


def belongs_to(route: str, family: str) -> bool:
    if "://" in route:
        return family == "manage_editor" and (
            route.startswith("mcpforunity://editor/") or route in EDITOR_PROJECT_READS
        )
    name, _, selector = route.partition(".")
    name, selector = FIXED_OBSERVATION_ACTIONS.get(name, (name, selector))
    return operation_owner(name, selector) == family


def query_identity(text):
    return " ".join(unicodedata.normalize("NFKC", text).casefold().split())


def excluded_bindings(family, documents, requests):
    # A frozen positive oracle may accept a cross-owner alternative, even on another
    # profile. Take the transitive closure so none of its views can teach the fold.
    links = {r.expected for r in requests}
    links.update(frozenset(map(binding_name, read.bindings())) for read in _EQUIVALENT_READS)
    known = set(map(binding_name, documents))
    known.update(chain.from_iterable(links))
    known.update(chain.from_iterable(r.wrong_effect for r in requests))
    held = {name for name in known if belongs_to(name, family)}
    if not held:
        raise ValueError(f"Unknown calibration family: {family}")
    while True:
        expanded = held.union(*(group for group in links if group & held))
        if expanded == held:
            return frozenset(held)
        held = expanded


def partition(documents, requests: tuple[Request, ...], family=None):
    held = excluded_bindings(family, documents, requests) if family is not None else frozenset()
    retained = {key: texts for key, texts in documents.items() if binding_name(key) not in held}
    bindings = set(map(binding_name, retained))
    excluded_queries = {
        query_identity(r.query) for r in requests if held.intersection(r.expected | r.wrong_effect)
    }
    eligible = tuple(
        r
        for r in requests
        if bindings & r.expected and query_identity(r.query) not in excluded_queries
    )
    if not retained or not eligible or (family and len(retained) == len(documents)):
        raise ValueError(f"Empty or ineffective calibration split: {family}")
    return retained, eligible, held
