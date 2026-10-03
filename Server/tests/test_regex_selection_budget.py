"""Causal work and behavior checks for script regex selection."""
import importlib
import re
import asyncio
import threading
import time

import pytest

from services.tools import bounded_regex

selection = importlib.import_module("services.tools.script_apply_edits")


def test_closing_brace_selection_tokenizes_once(monkeypatch):
    text = "class C {\n" + "    void M() {\n    }\n" * 100 + "}\n"
    original = selection._iter_csharp_tokens
    tokens = 0

    def counted(source):
        nonlocal tokens
        for token in original(source):
            tokens += 1
            yield token

    monkeypatch.setattr(selection, "_iter_csharp_tokens", counted)
    match = selection._find_best_anchor_match(r"^\s*}\s*$", text, re.MULTILINE)
    assert "}" in match.group()
    assert tokens <= len(text), f"Retokenized prefixes: {tokens} tokens for {len(text)} characters"


@pytest.mark.parametrize("literal", [
    '"}"', '@"}"', '$"}}"', '$@"}}"', '"""}"""', '$$"""}}"""',
    "'}'", "/* } */", "// }\n",
])
def test_brace_selection_ignores_strings_comments_and_preserves_outermost(literal):
    text = "class C {\n void M() {\n  " + literal + ";\n }\n}\n/* } */"
    match = selection._find_best_anchor_match(r"}[^\n]*$", text, re.MULTILINE)
    assert match.start() == text.index("}\n/*")


@pytest.mark.parametrize("prefer_last,expected", [(True, 4), (False, 0)])
def test_regular_anchors_preserve_first_and_last(prefer_last, expected):
    match = selection._find_best_anchor_match("x", "x x x", 0, prefer_last)
    assert match.start() == expected


def test_plain_dollar_runs_have_linear_lexer_work():
    class CountedText(str):
        reads = 0

        def __getitem__(self, key):
            self.reads += 1
            return super().__getitem__(key)

    text = CountedText("$" * 10_000 + '\n$"{1}"\n}\n}\n')
    list(selection._iter_csharp_tokens(text))
    assert text.reads < len(text) * 6


def test_wide_matches_charge_scanning_and_tokenize_once():
    text = "class C {\n" + "a" * 200_000 + "}\n" + "b" * 200_000 + "}\n"
    budget = bounded_regex.WorkBudget()
    budget.remaining = len(text) * 4
    match = selection._find_best_anchor_match(r"[^}]*}\s*$", text, re.MULTILINE, budget=budget)
    assert match is not None
    assert budget.remaining < len(text) * 2


def test_near_limit_ten_thousand_braces_complete_or_fail_closed():
    text = "class C {\n" + (" " * 193 + "}\n") * 10_000
    started = time.monotonic()
    try:
        match = selection._find_best_anchor_match(r"^ *}$", text, re.MULTILINE)
        assert match.start() == text.rfind("\n", 0, len(text) - 2) + 1
    except TimeoutError:
        pass
    assert time.monotonic() - started < 2


def test_selection_deadline_covers_postregex_tokenization(monkeypatch):
    budget = bounded_regex.WorkBudget()
    original = bounded_regex.find_matches

    def find_and_expire(*args, **kwargs):
        matches = original(*args, **kwargs)
        budget.deadline = 0
        return matches

    monkeypatch.setattr(bounded_regex, "find_matches", find_and_expire)
    with pytest.raises(TimeoutError, match="total work"):
        selection._find_best_anchor_match(r"}$", "{}\n{}", re.MULTILINE, budget=budget)


def test_work_budget_is_shared_with_postregex_selection():
    text = "{}\n{}"
    budget = bounded_regex.WorkBudget()
    budget.remaining = len(text) + len(r"}$") + 2
    with pytest.raises(ValueError, match="character budget"):
        selection._find_best_anchor_match(r"}$", text, re.MULTILINE, budget=budget)


def test_lexer_checks_cooperative_cancellation(monkeypatch):
    text = "class C {" + " " * 100_000 + "}\n}\n"
    budget = bounded_regex.WorkBudget()
    original = selection._iter_csharp_tokens
    tokens = 0

    def cancel_during_lexing(source):
        nonlocal tokens
        for token in original(source):
            tokens += 1
            if tokens == 2048:
                budget.cancelled.set()
            yield token

    monkeypatch.setattr(selection, "_iter_csharp_tokens", cancel_during_lexing)
    with pytest.raises(TimeoutError, match="cancelled"):
        selection._find_best_anchor_match(r"}$", text, re.MULTILINE, budget=budget)
    assert tokens <= 3073


@pytest.mark.asyncio
async def test_span_backreference_expansion_is_bounded_before_allocating(monkeypatch):
    monkeypatch.setattr(bounded_regex, "MAX_TEXT_CHARS", 1024)
    with pytest.raises(selection._TextEditError, match="output size limit"):
        await selection._text_edit_spans("a" * 1000, [
            {"op": "regex_replace", "pattern": "(a+)", "replacement": "$1" * 100}
        ])


@pytest.mark.asyncio
async def test_span_operations_share_request_work_budget(monkeypatch):
    monkeypatch.setattr(bounded_regex, "MAX_WORK_CHARS", 100)
    with pytest.raises(selection._TextEditError, match="character budget"):
        await selection._text_edit_spans("abcdefghi", [
            {"op": "regex_replace", "pattern": "a", "replacement": "b"}
        ] * 20)


@pytest.mark.asyncio
async def test_span_conversion_preserves_backreferences_and_line_positions():
    source = "class C {\n name=abc;\n}\n"
    spans = await selection._text_edit_spans(source, [
        {"op": "regex_replace", "pattern": r"name=(\w+)", "replacement": "$1=value"}
    ])
    assert spans == [{"startLine": 2, "startCol": 2, "endLine": 2, "endCol": 10, "newText": "abc=value"}]
    assert selection._preview_text_spans(source, spans) == "class C {\n abc=value;\n}\n"
    assert selection._preview_text_spans(source, []) == source


@pytest.mark.asyncio
async def test_two_admitted_workers_leave_executor_available(monkeypatch):
    monkeypatch.setattr(selection, "_REGEX_WORKERS", threading.BoundedSemaphore(2))
    entered = [threading.Event(), threading.Event()]
    release = threading.Event()

    def blocked(index):
        entered[index].set()
        assert release.wait(1)
        return index

    tasks = [asyncio.create_task(selection._run_regex_work(
        bounded_regex.WorkBudget(), lambda index=index: blocked(index))) for index in range(2)]
    try:
        while not all(event.is_set() for event in entered):
            await asyncio.sleep(0.001)
        with pytest.raises(ValueError, match="busy"):
            await selection._run_regex_work(bounded_regex.WorkBudget(), lambda: 3)
        assert await asyncio.to_thread(lambda: "executor available") == "executor available"
    finally:
        release.set()
        assert await asyncio.gather(*tasks) == [0, 1]


@pytest.mark.asyncio
async def test_cancelled_worker_keeps_admission_until_it_releases(monkeypatch):
    monkeypatch.setattr(selection, "_REGEX_WORKERS", threading.BoundedSemaphore(1))
    entered = threading.Event()
    release = threading.Event()
    exited = threading.Event()
    budget = bounded_regex.WorkBudget()

    def blocked_work():
        entered.set()
        try:
            assert release.wait(1)
            budget.check()
        finally:
            exited.set()

    task = asyncio.create_task(selection._run_regex_work(budget, blocked_work))
    try:
        while not entered.is_set():
            await asyncio.sleep(0.001)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert budget.cancelled.is_set()
        with pytest.raises(ValueError, match="busy"):
            await selection._run_regex_work(bounded_regex.WorkBudget(), lambda: 1)
    finally:
        release.set()
        while not exited.is_set():
            await asyncio.sleep(0.001)
    for _ in range(100):
        await asyncio.sleep(0.001)
        if selection._REGEX_WORKERS.acquire(blocking=False):
            selection._REGEX_WORKERS.release()
            break
    assert await selection._run_regex_work(bounded_regex.WorkBudget(), lambda: 7) == 7
