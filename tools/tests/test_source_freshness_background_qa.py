"""The GUI witness must reject batch-mode, focused, stale, or incomplete evidence."""

import importlib.util
from pathlib import Path

import pytest


def validator():
    path = Path(__file__).parents[1] / "source_freshness_background_qa.py"
    assert path.is_file(), "The background witness validator is missing"
    spec = importlib.util.spec_from_file_location("freshness_qa", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.validate_witness


def evidence():
    return {
        "before": {"value": 1, "focused": False, "batch_mode": False, "pid": 123},
        "after": {
            "value": 2,
            "focused": False,
            "batch_mode": False,
            "pid": 123,
            "source_freshness": {"status": "observed_current", "source_revision_confirmed": False},
        },
        "observed_old_value_after_write": 1,
        "focus_samples": [456, 456],
    }


def test_real_unfocused_changed_code_is_required():
    assert validator()(evidence(), 123) == []


@pytest.mark.parametrize(
    ("stage", "field", "value"),
    [
        ("before", "focused", True),
        ("after", "batch_mode", True),
        ("after", "value", 1),
        ("after", "pid", 124),
    ],
)
def test_invalid_native_evidence_cannot_pass(stage, field, value):
    sample = evidence()
    sample[stage][field] = value
    assert validator()(sample, 123)


def test_os_focus_and_missing_coverage_cannot_be_ignored():
    sample = evidence()
    sample["focus_samples"] = [123]
    assert validator()(sample, 123)
    sample = evidence()
    sample["after"]["source_freshness"]["status"] = "incomplete"
    assert validator()(sample, 123)
