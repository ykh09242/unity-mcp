"""Verify predeclared cross-language coverage, role exclusions and conditional metrics."""

from collections import Counter
import json
from pathlib import Path
import sys
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools.experiments import blind_tool_preflight, blind_tool_selection as selection
from tools.experiments.blind_tool_comparison import study_stratum
from tools.experiments.blind_tool_diagnostics import search_receipt
from tools.experiments.blind_tool_trace import decision


def test_named_study_preserves_planned_cells_when_a_host_cannot_measure_roles(
    tmp_path, monkeypatch, capsys
):
    project = tmp_path / "Game"
    project.mkdir()
    probe = tmp_path / "probe.py"
    probe.touch()
    output = tmp_path / "study"

    def skip(arms, directory, roles, run, score):
        reasons = {
            role: {"supported": False, "attempts": [{"reasons": ["unsupported_host"]}]}
            for role in roles
        }
        (directory / "preflight.json").write_text(json.dumps(reasons))
        return reasons

    monkeypatch.setattr(blind_tool_preflight, "run_pilots", skip)
    assert (
        selection.main(
            [
                "matched",
                "--study",
                "consumer-discovery",
                "--project",
                str(project),
                "--instance",
                "Game@123",
                "--graph-asset",
                "Assets/A.shadergraph",
                "--baseline-relay",
                str(probe),
                "--candidate-relay",
                str(probe),
                "--output",
                str(output),
                "--model",
                "fixture",
                "--effort",
                "medium",
                "--state-probe",
                str(probe),
                "--codex",
                sys.executable,
            ]
        )
        == 1
    )
    report = json.loads(capsys.readouterr().out)
    plan = json.loads((output / "study.json").read_text())
    assert plan["planned_pairs"] == report["planned_pairs"] == 36
    assert plan["scheduled_turns"] == 72 and plan["answer_protocol"] == "v3"
    assert report["eligible_pairs"] == 0
    assert all("preflight:unsupported_host" in pair["reasons"] for pair in report["pairs"])
    assert {
        k: v["planned_pairs"] for k, v in report["coverage"]["language"].items()
    } == dict.fromkeys(selection.LANGUAGES, 6)
    schedule = json.loads((output / "schedule.json").read_text())
    assert Counter((cell[2], arm) for arm, cell in schedule[::2]) == {
        (role, arm): 9 for role in selection.ROLES for arm in ("baseline", "candidate")
    }


def test_scores_split_no_search_unrecorded_and_naturally_ranked():
    def pair(task, capture, top3, correct):
        case = {
            "role_status": "confirmed",
            "wire_evidence": "complete",
            "calls_before_first_success": 0,
            "selection_diagnosis": {"search_capture": capture, "first_search_top3_suitable": top3},
            "final_answer": {"status": correct},
        }
        return {"task": task, "eligible": True, "baseline": case, "candidate": case}

    result = study_stratum(
        [
            pair("game-view", "no_search", None, "correct"),
            pair("graph-saved", "ranked", True, "correct"),
            pair("graph-saved", "attempt_without_rank", None, "limited"),
            pair("graph-open", "ranked", False, "limited"),
        ]
    )
    arm = result["arms"]["candidate"]
    assert arm["suitable_in_first_search_top3"] == {"numerator": 1, "denominator": 1}
    assert arm["first_success_without_search"] == {"numerator": 1, "denominator": 1}
    assert arm["first_success_on_first_business_call"]["denominator"] == 3
    assert arm["answer_status"] == {"correct": 2, "limited": 2}


def test_study_prompts_exclude_tool_oracle_and_implementation_hints():
    args = SimpleNamespace(
        study=selection.STUDY,
        graph_asset="Assets/X.shadergraph",
        answer_format="json",
        answer_protocol="v3",
        model="fixture",
        effort="medium",
    )
    for language in selection.LANGUAGES:
        for task in selection.STUDY_TASKS:
            direct = selection.delivery_prompt(args, task, language, "main").split("\n\n", 1)[1]
            child = selection.delivery_prompt(args, task, language, "subagent")
            assert "<request>\n" + direct + "\n</request>" in child
            assert all(
                name not in direct
                for name in (
                    "search_unity_tools",
                    "get_game_view_size",
                    "manage_tools",
                    "inspect_shader_graph",
                    "mcpforunity://",
                    "1280",
                    "Sprite_Dissolve",
                )
            )
    assert decision("manage_tools.activate", {}, "", endpoint=True) == "blocked"
    assert (
        search_receipt({"routes": [], "no_match": {"reason": "explicitly_unavailable"}})[
            "no_match_reason"
        ]
        == "explicitly_unavailable"
    )
