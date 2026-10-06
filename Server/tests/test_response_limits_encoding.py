"""Bounded whole-document encoding preserves streaming admission decisions."""
import json
import sys

from pydantic import AnyUrl, BaseModel
import pytest

from models import response_limits as limits


class ResultModel(BaseModel):
    name: str
    url: AnyUrl


class ShortString(str):
    def __len__(self):
        return 1


class ShortList(list):
    def __len__(self):
        return 1


class IntSubclass(int):
    pass


class EqualityTrap(type):
    def __eq__(cls, other):
        raise AssertionError("serialization must not compare custom metaclasses")


class CustomList(list, metaclass=EqualityTrap):
    pass


VALUES = [
    None, True, False, 0, -1, 1 << 13999, -(1 << 13999),
    0.0, -0.0, 5e-324, -1.7976931348623157e308,
    "", "경로 🎮", "\"\\\b\f\n\r\t\x00\x1f", "\ud800",
    [], (), {}, [True, False, None, {}, ()],
    {"": "", "\n": "\u2028", "data": [1.2, -3, "🧪"]},
    float("nan"), float("inf"), float("-inf"), {1: "invalid key"},
    ResultModel(name="테스트", url=AnyUrl("https://example.test/resource")),
    AnyUrl("https://example.test/resource"),
    {"nested": [ShortString("value"), IntSubclass(17)]},
    ShortList(["a", "b"]), CustomList([1, 2]),
]


@pytest.mark.parametrize("value", VALUES, ids=lambda value: type(value).__name__)
def test_charge_and_admission_match_streaming(value, monkeypatch):
    with monkeypatch.context() as reference:
        reference.setattr(limits, "_MAX_FAST_JSON_BYTES", 0)
        expected = limits.response_size(value)
    assert limits.response_size(value) == expected


@pytest.mark.parametrize("value", ["plain", "한글 🎮", "\x00" * 30,
                                  [True, None, -0.0], {"a": 100, "b": "c"}])
def test_exact_byte_boundary_and_conservative_bound_fallback(value):
    size = len(json.dumps(value, ensure_ascii=False, allow_nan=False).encode("utf-8"))
    assert limits.response_size(value, max_bytes=size) is not None
    assert limits.response_size(value, max_bytes=size - 1) is None


def test_tight_retained_budget_keeps_exact_charge():
    value = {"rows": [{"index": index, "label": "test"} for index in range(100)]}
    charge = limits.response_size(value)
    assert charge is not None
    assert limits.response_size(value, max_retained=charge) == charge
    assert limits.response_size(value, max_retained=charge - 1) is None


def test_large_document_and_custom_types_do_not_allocate_whole_json(monkeypatch):
    def forbid_whole_document(*args, **kwargs):
        raise AssertionError("unbounded or custom values must retain streaming encoding")

    monkeypatch.setattr(json.JSONEncoder, "encode", forbid_whole_document)
    for value in ["x" * (2 * 1024 * 1024),
                  {"key": ShortString("x" * 1000)},
                  ShortList(["a"] * 1000),
                  CustomList([1, 2]),
                  {"key": IntSubclass(10)},
                  ResultModel(name="example", url=AnyUrl("https://example.test"))]:
        assert limits.response_size(value) is not None


def test_graph_limits_reject_before_whole_document_allocation(monkeypatch):
    assert limits.response_size([0, 1], max_nodes=3) is not None

    def forbid_whole_document(*args, **kwargs):
        raise AssertionError("unsupported graphs must be rejected before encoding")

    monkeypatch.setattr(json.JSONEncoder, "encode", forbid_whole_document)
    cyclic = []
    cyclic.append(cyclic)
    assert limits.response_size(cyclic) is None
    assert limits.response_size({"a": [0]}, max_depth=1) is None
    assert limits.response_size([0, 1], max_nodes=2) is None


def test_runtime_integer_conversion_limit_is_preserved(monkeypatch):
    old_limit = sys.get_int_max_str_digits()
    try:
        sys.set_int_max_str_digits(640)
        value = {"large": 10 ** 700}
        assert limits.response_size(value) is None
        with monkeypatch.context() as reference:
            reference.setattr(limits, "_MAX_FAST_JSON_BYTES", 0)
            assert limits.response_size(value) is None
    finally:
        sys.set_int_max_str_digits(old_limit)
