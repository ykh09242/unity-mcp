"""ASCII sizing avoids copies while preserving independent bounds and hooks."""
import json
import sys

import pytest

from models import response_limits as limits


def counted_utf8_encodes(call):
    """Observe C-level string encodes during one isolated synchronous operation."""
    encodes = []
    previous = sys.getprofile()

    def profile(_frame, event, function):
        if (event == "c_call" and getattr(function, "__name__", None) == "encode"
                and isinstance(getattr(function, "__self__", None), str)):
            encodes.append(function.__self__)

    try:
        sys.setprofile(profile)
        result = call()
    finally:
        sys.setprofile(previous)
    return result, encodes


@pytest.mark.parametrize("length", [32, 65_536])
def test_exact_ascii_raw_text_avoids_utf8_recount(length):
    # Given an ASCII document spanning one or multiple recount chunks.
    raw = '"' + 'A' * length + '"'
    # When it passes through the real bounded scanner.
    result, encodes = counted_utf8_encodes(lambda: limits.bounded_json_text(
        raw, max_bytes=len(raw), max_depth=64, max_nodes=100_000))
    # Then the document survives with no intermediate UTF-8 byte copies.
    assert result == raw
    assert encodes == []


@pytest.mark.parametrize("length", [32, 128 * 1024])
def test_admitted_ascii_document_avoids_full_utf8_copy(length):
    # Given a graph conservatively admitted for whole-document JSON encoding.
    value = {"data": 'A' * length}
    # When independent graph and encoded-size bounds inspect it.
    charge, encodes = counted_utf8_encodes(lambda: limits.response_size(value))
    # Then the graph is charged without a complete UTF-8 copy.
    assert charge is not None
    assert encodes == []


@pytest.mark.parametrize("raw", ['"경로🎮"', '"\ud800"'])
def test_raw_unicode_keeps_strict_utf8_byte_limits(raw):
    # Given non-ASCII text whose character count cannot prove its byte count.
    try:
        byte_count = len(raw.encode("utf-8"))
    except UnicodeError:
        byte_count = len(raw)
    # When the scanner uses the exact and one-byte-short limits.
    exact = limits.bounded_json_text(raw, max_bytes=byte_count, max_depth=64, max_nodes=100)
    short = limits.bounded_json_text(raw, max_bytes=byte_count - 1, max_depth=64, max_nodes=100)
    # Then strict Unicode encoding, including surrogate rejection, is preserved.
    assert exact == (raw if '\ud800' not in raw else None)
    assert short is None


def test_ascii_raw_subclass_keeps_slice_and_encode_hooks():
    # Given a subclass whose slice and encoding are externally observable.
    hooks = []

    class HookText(str):
        def __getitem__(self, key):
            result = str.__getitem__(self, key)
            if isinstance(key, slice):
                hooks.append("slice")
                return HookText(result)
            return result

        def encode(self, *args, **kwargs):
            hooks.append("encode")
            return str.encode(self, *args, **kwargs)

        def isascii(self):
            raise AssertionError("subclass ASCII hooks must not be called")

    raw = HookText('{"x":"ASCII"}')
    # When the bounded scanner examines the subclass.
    result = limits.bounded_json_text(raw, max_bytes=len(raw), max_depth=64, max_nodes=100)
    # Then its prior recount hooks remain in order.
    assert result == raw
    assert hooks == ["slice", "encode"]


def test_encoded_text_subclass_keeps_strict_encode_hook(monkeypatch):
    # Given a customized encoder that returns a string subclass.
    hooks = []
    normal_encode = json.JSONEncoder.encode

    class EncodedText(str):
        def isascii(self):
            raise AssertionError("non-exact encoder output must retain encoding")

        def encode(self, *args, **kwargs):
            hooks.append("encode")
            return str.encode(self, *args, **kwargs)

    monkeypatch.setattr(json.JSONEncoder, "encode",
                        lambda self, value: EncodedText(normal_encode(self, value)))
    # When the admitted whole-document sizing path executes.
    charge = limits.response_size({"data": "ASCII"})
    # Then custom encoding remains observable exactly once.
    assert charge is not None
    assert hooks == ["encode"]


@pytest.mark.parametrize("raw, kwargs", [
    ('"ASCII"', {"max_bytes": 6}),
    ('[[1]]', {"max_depth": 1}),
    ('[1,2]', {"max_nodes": 2}),
])
def test_ascii_copy_elision_preserves_scanner_rejections(raw, kwargs):
    # Given a document exceeding an independent scanner limit.
    bounds = {"max_bytes": 100, "max_depth": 64, "max_nodes": 100}
    bounds.update(kwargs)
    # When the bounded scanner examines it.
    result = limits.bounded_json_text(raw, **bounds)
    # Then no ASCII shortcut bypasses the limit.
    assert result is None
