"""Opted-in Git provenance obeys collection limits without losing prior evidence."""

import os
import threading

import pytest

from services.reference_graph.collection_protocol import (
    CollectionBudget,
    CollectionBudgetExceeded,
    CollectionLimits,
)
from services.reference_graph.external import ExternalSource
from services.reference_graph.store import guard_graph_work


def git_source(tmp_path, **limits):
    (tmp_path / ".git" / "refs" / "heads").mkdir(parents=True)
    budget = CollectionBudget(CollectionLimits(**limits))
    source = ExternalSource(
        {"root": str(tmp_path), "include": ["*.json"], "git_revision": True}, budget=budget
    )
    return source, budget


@pytest.mark.parametrize("metadata", ["HEAD", "refs/heads/main", "packed-refs"])
def test_git_metadata_declared_size_obeys_source_limit(tmp_path, metadata):
    source, budget = git_source(tmp_path, max_source_bytes=100)
    (tmp_path / ".git" / "HEAD").write_bytes(b"ref: refs/heads/main\n")
    (tmp_path / ".git" / metadata).write_bytes(b"a" * 101)
    with pytest.raises(CollectionBudgetExceeded, match="max_source_bytes=100"):
        source.repository_revision()
    assert budget.decoded_bytes == (0 if metadata == "HEAD" else 21)


def test_git_metadata_charges_repeated_head_validation_to_shared_budget(tmp_path):
    source, budget = git_source(tmp_path, max_bytes=70)
    (tmp_path / ".git" / "HEAD").write_bytes(b"1" * 40 + b"\n")
    with pytest.raises(CollectionBudgetExceeded, match="max_bytes=70"):
        source.repository_revision()
    assert budget.decoded_bytes == 41


@pytest.mark.parametrize("metadata", ["HEAD", "refs/heads/main", "packed-refs"])
def test_git_metadata_rejects_long_records_without_buffering_whole_file(
    tmp_path, monkeypatch, metadata
):
    source, budget = git_source(tmp_path)
    (tmp_path / ".git" / "HEAD").write_bytes(b"ref: refs/heads/main\n")
    (tmp_path / ".git" / metadata).write_bytes(b"1" * 200_000)
    opened = []
    original_open = os.open

    def record_open(*args, **kwargs):
        descriptor = original_open(*args, **kwargs)
        opened.append(descriptor)
        return descriptor

    monkeypatch.setattr(os, "open", record_open)
    assert source.repository_revision()["state"] == "unavailable"
    assert 0 < budget.decoded_bytes <= 65558
    for descriptor in opened:
        with pytest.raises(OSError):
            os.fstat(descriptor)


def test_many_packed_refs_obey_total_bytes(tmp_path):
    source, budget = git_source(tmp_path, max_bytes=200)
    (tmp_path / ".git" / "HEAD").write_bytes(b"ref: refs/heads/main\n")
    (tmp_path / ".git" / "packed-refs").write_bytes(
        b"1" * 40 + b" refs/heads/main\n" + (b"2" * 40 + b" refs/heads/other\n") * 10
    )
    with pytest.raises(CollectionBudgetExceeded, match="max_bytes=200"):
        source.repository_revision()
    assert budget.decoded_bytes == 21


def test_expired_git_metadata_budget_cannot_report_observed_head(tmp_path):
    source, budget = git_source(tmp_path, max_seconds=1)
    (tmp_path / ".git" / "HEAD").write_bytes(b"1" * 40 + b"\n")
    budget.started -= 2
    with pytest.raises(CollectionBudgetExceeded, match="max_seconds=1"):
        source.repository_revision()
    assert budget.decoded_bytes == 0


def test_git_metadata_cancellation_during_read_closes_descriptor(tmp_path, monkeypatch):
    source, budget = git_source(tmp_path)
    (tmp_path / ".git" / "HEAD").write_bytes(b"ref: refs/heads/main\n")
    (tmp_path / ".git" / "packed-refs").write_bytes(
        (b"2" * 40 + b" refs/heads/other\n") * 1000 + b"1" * 40 + b" refs/heads/main\n"
    )
    stopped = threading.Event()
    opened = []
    original_open, original_charge = os.open, budget.source

    def record_open(*args, **kwargs):
        descriptor = original_open(*args, **kwargs)
        opened.append(descriptor)
        return descriptor

    def charge(size, accumulated):
        original_charge(size, accumulated)
        if budget.decoded_bytes > 1000:
            stopped.set()

    def check():
        if stopped.is_set():
            raise InterruptedError("cancelled during metadata read")

    monkeypatch.setattr(os, "open", record_open)
    monkeypatch.setattr(budget, "source", charge)
    with guard_graph_work(check), pytest.raises(InterruptedError, match="cancelled"):
        source.repository_revision()
    assert 1000 < budget.decoded_bytes < 2000
    for descriptor in opened:
        with pytest.raises(OSError):
            os.fstat(descriptor)
