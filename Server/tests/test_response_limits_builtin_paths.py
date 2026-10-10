"""Exact built-in visits preserve charging and subclass side effects."""

import json
import sys

from pydantic import AnyUrl, BaseModel
import pytest

from models import response_limits as limits


@pytest.mark.parametrize("fast_limit", [0, 2 * 1024 * 1024])
def test_builtin_graph_avoids_model_and_url_type_probes(monkeypatch, fast_limit):
    value = {"data": [True, False, None, 17, -0.0, "\ud55c🧪", (1, 2)]}
    model_checks = []

    def counted_isinstance(item, classes):
        if classes is BaseModel or classes is AnyUrl:
            model_checks.append(classes)
        return isinstance(item, classes)

    monkeypatch.setattr(limits, "isinstance", counted_isinstance, raising=False)
    monkeypatch.setattr(limits, "_MAX_FAST_JSON_BYTES", fast_limit)
    assert limits.response_size(value) is not None
    assert model_checks == []


def test_duplicate_aliases_keep_per_visit_charges_and_node_boundaries():
    leaf = {"k": 1}
    value = [leaf, leaf]
    visits = [value, leaf, "k", 1, leaf, "k", 1]
    encoded_bytes = len(json.dumps(value, ensure_ascii=False).encode("utf-8"))
    expected = 1024 + sum(128 + 2 * sys.getsizeof(item) for item in visits) + 8 + encoded_bytes
    assert limits.response_size(value, max_nodes=7) == expected
    assert limits.response_size(value, max_nodes=6) is None
    assert limits.response_size(value, max_retained=expected) == expected
    assert limits.response_size(value, max_retained=expected - 1) is None


def test_subclasses_keep_sizeof_length_iterator_and_integer_hook_order():
    hooks = []

    class HookString(str):
        def __len__(self):
            hooks.append("string_len")
            return str.__len__(self)

        def __sizeof__(self):
            hooks.append("string_sizeof")
            return str.__sizeof__(self)

    class HookInt(int):
        def bit_length(self):
            hooks.append("int_bit_length")
            return int.bit_length(self)

        def __sizeof__(self):
            hooks.append("int_sizeof")
            return int.__sizeof__(self)

    class HookList(list):
        def __iter__(self):
            hooks.append("list_iter")
            return list.__iter__(self)

        def __sizeof__(self):
            hooks.append("list_sizeof")
            return list.__sizeof__(self)

    value = HookList([HookString("\ud55c🧪"), HookInt(1 << 13999)])
    assert limits.response_size(value) is not None
    assert hooks == [
        "list_sizeof",
        "list_iter",
        "string_sizeof",
        "string_len",
        "string_len",
        "int_sizeof",
        "int_bit_length",
        "list_iter",
    ]
