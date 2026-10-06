"""Large ASCII sizing preserves limits without allocating full JSON strings."""
import json
import sys
import tracemalloc

import pytest

from models import response_limits as limits


@pytest.mark.parametrize("unit", ['A', '"', '\\', 'quote"\\slash'])
def test_large_ascii_scalar_preserves_charge_without_full_json_buffer(unit):
    # Given a large printable ASCII value and its independently encoded size.
    value = unit * (1024 * 1024 // len(unit))
    encoded_bytes = len(json.dumps(value, ensure_ascii=False).encode("utf-8"))
    expected = 1024 + 128 + 2 * sys.getsizeof(value) + 4 * len(value) + encoded_bytes
    # When bounded sizing executes with the source already allocated.
    tracemalloc.start()
    try:
        actual = limits.response_size(value)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    # Then the original charge remains exact and no full JSON buffer is retained.
    assert actual == expected
    assert peak < 65_536


def test_large_ascii_envelope_preserves_punctuation_aliases_and_boundaries():
    # Given a preview envelope whose same leaf appears twice, with numeric metadata.
    text = 'A' * (256 * 1024)
    leaf = [text, True, False, None, 17, -0.0]
    value = {"data": (leaf, leaf)}
    encoded_bytes = len(json.dumps(value, ensure_ascii=False).encode("utf-8"))
    visits = [value, "data", value["data"], leaf, *leaf, leaf, *leaf]
    expected = (1024 + sum(128 + 2 * sys.getsizeof(item) for item in visits)
                + 4 * (len("data") + 2 * len(text)) + encoded_bytes)
    # When independent encoded, retained, depth and node bounds inspect the envelope.
    charge = limits.response_size(value, max_bytes=encoded_bytes, max_nodes=len(visits))
    # Then every alias and separator is counted, including all rejection boundaries.
    assert charge == expected
    assert limits.response_size(value, max_bytes=encoded_bytes - 1) is None
    assert limits.response_size(value, max_retained=expected) == expected
    assert limits.response_size(value, max_retained=expected - 1) is None
    assert limits.response_size(value, max_nodes=len(visits) - 1) is None
    assert limits.response_size(value, max_depth=2) is None


@pytest.mark.parametrize("length, direct", [(256 * 1024 - 1, False), (256 * 1024, True)])
def test_scalar_threshold_selects_bounded_allocation_path(length, direct):
    # Given printable ASCII leaves on the scalar threshold boundary.
    value = 'A' * length
    # When sizing chooses the appropriate bounded allocation path.
    tracemalloc.start()
    try:
        charge = limits.response_size(value)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    # Then only eligible large scalars avoid the full JSON working string.
    assert charge is not None
    assert (peak < 65_536) is direct


def test_large_ascii_keys_and_numeric_metadata_keep_exact_json_length():
    # Given printable ASCII key text, all printable characters and finite metadata.
    key = 'K' * (256 * 1024)
    value = {key: {"chars": ''.join(chr(i) for i in range(32, 127)),
                   "numbers": (1 << 13999, -(1 << 13999), 5e-324, 1.7976931348623157e308),
                   "empty": [[], {}, (), ""]}}
    encoded_bytes = len(json.dumps(value, ensure_ascii=False).encode("utf-8"))
    # When sizing checks the exact JSON limit.
    exact = limits.response_size(value, max_bytes=encoded_bytes)
    short = limits.response_size(value, max_bytes=encoded_bytes - 1)
    # Then large dict keys, escaping, metadata and punctuation share the old boundary.
    assert exact is not None
    assert short is None


@pytest.mark.parametrize("tail", ['\n', '\x00', '\x7f', '경로🎮', '\ud800'])
def test_large_scalar_fallback_keeps_unicode_escape_and_byte_boundaries(tail):
    # Given a large scalar outside the printable ASCII subset.
    value = 'A' * (256 * 1024) + tail
    try:
        encoded_bytes = len(json.dumps(value, ensure_ascii=False).encode("utf-8"))
    except UnicodeError:
        encoded_bytes = 0
    # When exact UTF-8 byte boundaries inspect the scalar.
    exact = limits.response_size(value, max_bytes=encoded_bytes)
    short = limits.response_size(value, max_bytes=encoded_bytes - 1)
    # Then escaping/strict Unicode behavior is preserved through fallback.
    assert (exact is not None) is (tail != '\ud800')
    assert short is None


def test_large_ascii_inside_custom_container_keeps_iterator_hooks():
    # Given a subclass with externally observable traversal/serialization hooks.
    hooks = []

    class HookList(list):
        def __iter__(self):
            hooks.append("iter")
            return list.__iter__(self)

    value = HookList(['A' * (256 * 1024)])
    # When bounded inspection and normal encoding execute.
    charge = limits.response_size(value)
    # Then subclass serialization is not replaced by the exact-builtin size pass.
    assert charge is not None
    assert hooks == ["iter", "iter"]


@pytest.mark.parametrize("number", [float("nan"), float("inf"), 1 << 14000])
def test_large_ascii_does_not_bypass_invalid_numeric_nodes(number):
    # Given a large printable leaf paired with an invalid number.
    value = ['A' * (256 * 1024), number]
    # When the existing graph inspection validates it.
    charge = limits.response_size(value)
    # Then exact byte sizing cannot admit an invalid numeric node.
    assert charge is None


def test_large_ascii_does_not_bypass_cycles():
    # Given a graph with a large leaf followed by a container cycle.
    value = ['A' * (256 * 1024)]
    value.append(value)
    # When bounded inspection visits the cycle.
    charge = limits.response_size(value)
    # Then the original depth/node traversal still rejects it.
    assert charge is None
