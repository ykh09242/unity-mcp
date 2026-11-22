"""Verify that the native actor relay receives the sealed wording without rank hints."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools.experiments.blind_tool_selection import (
    create_parser,
    delivery_prompt,
    select_prompt_fixture,
)


def test_external_holdout_delivers_the_original_unicode_without_oracle_metadata(tmp_path):
    # Given an external locale and a task mapped to an existing read-only native oracle.
    prompt = "Missä valikossa on Kaarna?"
    fixture = tmp_path / "external.json"
    fixture.write_text(
        json.dumps(
            {
                "schema": 1,
                "tasks": {
                    "test-menu": {
                        "expected": ["oracle_route"],
                        "wrong_effect": ["mutate"],
                        "actor_task": "menu-declaration",
                    }
                },
                "queries": {"fi": {"test-menu": prompt}},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    parser = create_parser()
    args = parser.parse_args(
        [
            "run",
            "--project",
            str(tmp_path),
            "--output",
            str(tmp_path / "output"),
            "--model",
            "test-model",
            "--effort",
            "low",
            "--task",
            "menu-declaration",
            "--language",
            "fi",
            "--role",
            "main",
            "--endpoint",
            "http://127.0.0.1:8080/mcp",
            "--prompt-set",
            "holdout",
            "--holdout-prompts",
            str(fixture),
        ]
    )

    # When isolated harness prompt preparation selects the external fixture.
    select_prompt_fixture(parser, args)
    delivery = delivery_prompt(args, "menu-declaration", "fi", "main")

    # Then only the regular role framing and the exact original request reach the actor.
    assert delivery.split("\n\n", 1)[1] == prompt
