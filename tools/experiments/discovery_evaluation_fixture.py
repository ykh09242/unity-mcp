"""Load a versioned discovery evaluation corpus without interpreting its languages."""

import json
import re
from collections import Counter
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path

FIXTURE = Path(__file__).with_name("universal_discovery_queries.json")
HINT_ARGUMENTS = frozenset({"catalog_query", "intent", "observation_source", "required_values"})


@dataclass(frozen=True, slots=True)
class Request:
    language: str
    task: str
    query: str
    expected: frozenset[str]
    wrong_effect: frozenset[str]
    actor_task: str | None


@dataclass(frozen=True, slots=True)
class EvaluationSuite:
    sha256: str
    requests: tuple[Request, ...]


def load_suite(path: Path) -> EvaluationSuite:
    payload = path.read_bytes()
    document = json.loads(payload)
    if document["schema"] != 1:
        raise ValueError(f"{path}: unsupported evaluation fixture schema")
    tasks = document["tasks"]
    requests = []
    for language, queries in document["queries"].items():
        if not re.fullmatch(r"[a-z]{2,3}(?:-[A-Za-z0-9]{2,8})*", language):
            raise ValueError(f"{path}: invalid language tag {language!r}")
        if set(queries) != set(tasks):
            raise ValueError(f"{path}: {language} does not cover all tasks")
        for task, query in queries.items():
            expected = frozenset(tasks[task]["expected"])
            wrong_effect = frozenset(tasks[task]["wrong_effect"])
            if not query.strip() or len(query) > 512 or not expected or expected & wrong_effect:
                raise ValueError(f"{path}: invalid request or oracle for {language}/{task}")
            requests.append(
                Request(
                    language, task, query, expected, wrong_effect, tasks[task].get("actor_task")
                )
            )
    if not requests:
        raise ValueError(f"{path}: empty evaluation fixture")
    return EvaluationSuite(sha256(payload).hexdigest(), tuple(requests))


def load_assisted_hints(path: Path, suite: EvaluationSuite):
    """Join the caller's plan to frozen requests without substituting their query text."""
    payload = path.read_bytes()
    document = json.loads(payload)
    if document["schema"] != 1 or suite.sha256 not in document["suites"]:
        raise ValueError(f"{path}: unsupported hint plan or unknown fixture")
    by_language = document["suites"][suite.sha256]
    if Counter({language: len(cases) for language, cases in by_language.items()}) != Counter(
        request.language for request in suite.requests
    ):
        raise ValueError(f"{path}: assisted plan does not cover every fixture request")
    arguments = {}
    for request in suite.requests:
        hints = by_language[request.language].get(request.task)
        if not isinstance(hints, dict) or not hints.keys() <= HINT_ARGUMENTS:
            raise ValueError(f"{path}: invalid hints for {request.language}/{request.task}")
        arguments[request.query] = hints
    return sha256(payload).hexdigest(), arguments
