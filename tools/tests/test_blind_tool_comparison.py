"""Exercise role, state, order and answer evidence in matched blind trials."""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools.experiments import blind_tool_selection as selection
from tools.experiments.blind_tool_answers import final_answer
from tools.experiments.blind_tool_comparison import compare_directories
from tools.experiments.blind_tool_trace import (
    GAME_VIEW,
    GRAPH,
    RAYCAST,
    Call,
    actor_binding,
    ClientTrace,
    decision,
    evidence_grade,
    role_status,
    timing_metrics,
    score_case,
)


def test_matched_cli_runs_adjacent_pairs_and_preserves_missing_and_unverified_arms(
    tmp_path, monkeypatch, capsys
):
    project, output = tmp_path / "Game", tmp_path / "results"
    project.mkdir()
    for name in ("baseline", "candidate"):
        (tmp_path / (name + ".py")).write_text("# " + name)
    probe = tmp_path / "probe.py"
    probe.write_text('print(\'{"instance":"Game@1","scene":"A","width":800,"height":600}\')')

    def fake_cli(args, case_dir):
        return [sys.executable, "-c", 'import json; print(json.dumps({"type":"turn.completed"}))']

    monkeypatch.setattr(selection, "codex_command", fake_cli)
    assert (
        selection.main(
            [
                "matched",
                "--project",
                str(project),
                "--output",
                str(output),
                "--instance",
                "Game@1",
                "--baseline-relay",
                str(tmp_path / "baseline.py"),
                "--candidate-relay",
                str(tmp_path / "candidate.py"),
                "--state-probe",
                str(probe),
                "--model",
                "fixture",
                "--effort",
                "medium",
                "--codex",
                sys.executable,
                "--task",
                "game-view",
                "--language",
                "ko",
                "--language",
                "ja",
                "--role",
                "main",
                "--repeat",
                "2",
            ]
        )
        == 0
    )
    report = json.loads(capsys.readouterr().out)
    assert report["eligible_pairs"] == report["planned_pairs"] == 4
    assert report["eligible_order_counts"] == {"AB": 2, "BA": 2}
    assert report["eligible_summary"]["candidate"]["final_answers"] == {"missing": 4}
    schedule = json.loads((output / "schedule.json").read_text())
    assert all(
        a[1] == b[1] and a[0] != b[0] for a, b in zip(schedule[::2], schedule[1::2], strict=True)
    )
    assert schedule[0][0] != schedule[2][0]
    case_dir = output / "candidate" / "game-view-ko-main-01"
    meta = case_dir / "case.json"
    metadata = json.loads(meta.read_text())
    metadata["editor_start_state"]["width"] = 801
    metadata["approval_policy"] = "auto-review"
    meta.write_text(json.dumps(metadata))
    (case_dir / "events.jsonl").write_text(
        json.dumps(
            {
                "type": "item.completed",
                "item": {
                    "type": "collab_tool_call",
                    "tool": "spawn",
                    "status": "completed",
                    "receiver_thread_ids": ["01800000-0000-4000-8100-000000000001"],
                },
            }
        )
    )
    (output / "candidate" / "game-view-ja-main-02" / "case.json").unlink()
    report = compare_directories(output / "baseline", output / "candidate")
    assert report["eligible_pairs"] == 2
    excluded = [p for p in report["pairs"] if not p["eligible"]]
    assert any(
        {"different:editor_start_state", "different:approval_policy", "unconfirmed_role"}.issubset(
            p["reasons"]
        )
        and p["candidate"]["role_status"] == "mixed"
        for p in excluded
    )
    assert any(p["candidate"] is None and p["baseline"] for p in excluded)
    missing_arm = compare_directories(output / "baseline", tmp_path / "absent")
    assert missing_arm["eligible_pairs"] == 0 and missing_arm["planned_pairs"] == 4


@pytest.mark.parametrize(
    ("task", "wire", "answer", "result"),
    [
        (
            "game-view",
            [{"render_size": [913, 529], "preset_label": "ワイド"}],
            {"source": "editor", "width": 913, "height": 529, "preset": "ワイド"},
            "correct",
        ),
        (
            "game-view",
            [{"render_size": [913, 529]}],
            {"source": "editor", "width": 529, "height": 913, "preset": None},
            "incorrect",
        ),
        (
            "graph",
            [{"nodes": 24, "edges": 17, "node_types": ["加算", "قيمة"]}],
            {
                "source": "editor",
                "data_state": "saved",
                "nodes": 17,
                "edges": 24,
                "node_types": ["加算", "قيمة"],
            },
            "incorrect",
        ),
        (
            "graph",
            [{"nodes": 24, "edges": 17, "node_types": ["加算", "قيمة"]}],
            {
                "source": "editor",
                "data_state": "open",
                "nodes": 24,
                "edges": 17,
                "node_types": ["加算", "قيمة"],
            },
            "incorrect",
        ),
        (
            "graph-open",
            [{"nodes": 24, "edges": 17, "node_types": ["A", "B"]}],
            {
                "source": "editor",
                "data_state": "saved",
                "nodes": None,
                "edges": None,
                "node_types": None,
            },
            "needs_review",
        ),
        (
            "graph-open",
            [{"nodes": 24, "edges": 17, "node_types": ["A", "B"]}],
            {
                "source": "editor",
                "data_state": "open",
                "nodes": 24,
                "edges": 17,
                "node_types": ["A", "B"],
            },
            "incorrect",
        ),
        (
            "raycast",
            [{"render_size": [913, 529]}, {"status": "unknown", "point": [456.5, 264.5]}],
            {"source": "editor", "center": [456.5, 264.5], "hit_status": None, "first_hit": None},
            "limited",
        ),
        (
            "raycast",
            [{"render_size": [913, 529]}, {"status": "unknown", "point": [456.5, 264.5]}],
            {
                "source": "editor",
                "center": [456.5, 264.5],
                "hit_status": "hit",
                "first_hit": "Guess",
            },
            "incorrect",
        ),
        (
            "editor",
            [{"fresh": True, "playing": False, "compiling": False, "scene_name": "A"}],
            {"source": "editor", "playing": 0, "compiling": False, "scene": "A"},
            "incorrect",
        ),
        (
            "context",
            [{"project_fields": True}],
            {
                "source": "editor",
                "project_root": "p",
                "unity_version": "v",
                "render_pipeline": "URP",
            },
            "needs_review",
        ),
    ],
)
def test_reported_values_are_checked_against_task_specific_facts(
    tmp_path, task, wire, answer, result
):
    (tmp_path / "answer.txt").write_text(json.dumps(answer))
    assert final_answer(tmp_path, task, wire, [])["status"] == result


def test_prior_prose_requires_review_bound_to_actual_answer_and_evidence(tmp_path):
    path = tmp_path / "answer.txt"
    path.write_text("노드 24개, 연결 17개를 관찰했습니다.", encoding="utf-8")
    graph = [{"nodes": 24, "edges": 17, "node_types": ["A", "B"]}]
    scored = final_answer(tmp_path, "graph", graph, [])
    assert scored["status"] == "needs_review"
    (tmp_path / "answer-review.json").write_text(
        json.dumps(
            {
                "review_digest": scored["review_digest"],
                "status": "incorrect",
                "reason": "The answer omits both requested types.",
            }
        )
    )
    assert final_answer(tmp_path, "graph", graph, [])["status"] == "incorrect"
    assert final_answer(tmp_path, "graph", [], [])["status"] == "needs_review"
    path.write_text("24 17 A B")
    assert final_answer(tmp_path, "graph", graph, [])["status"] == "needs_review"


def test_unsaved_window_cannot_be_observed_through_serialized_graph_route(tmp_path):
    (tmp_path / "case.json").write_text(
        json.dumps(
            {
                "task": "graph-open",
                "role": "main",
                "language": "ar",
                "repetition": 1,
                "graph_asset": "Assets/Fixture.shadergraph",
            }
        )
    )
    rows = [
        {"event": "request", "sequence": 1, "channel": "unity", "route": GRAPH},
        {
            "event": "response",
            "sequence": 1,
            "outcome": "returned",
            "observation": {"nodes": 24, "edges": 17, "node_types": ["A", "B"]},
        },
    ]
    (tmp_path / "mcp-1.jsonl").write_text("\n".join(map(json.dumps, rows)))
    row = score_case(tmp_path)
    assert row["first_suitable_business_call"] is None
    assert row["wire_evidence"] == "partial" and row["graph_data_state"] == "saved"
    assert row["unsaved_graph_observed"] is False


def test_prerequisite_and_repeats_reference_successful_prior_evidence(tmp_path):
    (tmp_path / "case.json").write_text(
        json.dumps(
            {
                "task": "raycast",
                "role": "main",
                "language": "ar",
                "repetition": 1,
                "graph_asset": "",
            }
        )
    )
    calls = [
        (RAYCAST, "error", {}),
        (GAME_VIEW, "error", {}),
        (RAYCAST, "error", {}),
        (GAME_VIEW, "returned", {"render_size": [1920, 1080]}),
        (RAYCAST, "returned", {"status": "unknown", "point": [960, 540]}),
    ]
    rows = []
    for number, (route, outcome, evidence) in enumerate(calls, 1):
        rows.extend(
            [
                {"event": "request", "sequence": number, "channel": "unity", "route": route},
                {
                    "event": "response",
                    "sequence": number,
                    "outcome": outcome,
                    "observation": evidence,
                },
            ]
        )
    (tmp_path / "mcp-1.jsonl").write_text("\n".join(map(json.dumps, rows)))
    report = score_case(tmp_path)
    assert report["raycast_before_size"] == 2
    assert report["repeats_after_complete_evidence"] == 0
    page = {"nodes": 24, "edges": 17, "node_types": ["A", "B"]}
    sequence = [
        Call(
            GRAPH,
            "unity",
            i,
            "returned",
            page,
            page=n,
            timeline="a",
            request_at=2 * i + 1,
            response_at=2 * i + 2,
        )
        for i, n in enumerate([1, 2, 2])
    ]
    assert timing_metrics("graph", sequence)[0] == 1


@pytest.mark.parametrize("channel", ["editor", "file"])
def test_answer_origin_is_independent_of_graph_transport(tmp_path, channel):
    facts = {"nodes": 2, "edges": 1, "node_types": ["A", "B"]}
    wire, saved = ([facts], []) if channel == "editor" else ([], [facts])
    answer = {**facts, "data_state": "saved", "source": "asset_file", "channel": channel}
    path = tmp_path / "answer.txt"
    path.write_text(json.dumps(answer))
    assert final_answer(tmp_path, "graph-saved", wire, saved, protocol="v2")["status"] == "correct"
    path.write_text(json.dumps({**answer, "source": "editor_live"}))
    assert (
        final_answer(tmp_path, "graph-saved", wire, saved, protocol="v2")["status"] == "incorrect"
    )
    del answer["channel"]
    for label in ("editor", "file"):
        path.write_text(json.dumps({**answer, "source": label}))
        assert (
            final_answer(tmp_path, "graph-saved", wire, saved, protocol="v1")["status"]
            == "needs_review"
        )
        assert (
            final_answer(tmp_path, "graph-saved", wire, saved, protocol="v2")["status"]
            == "needs_review"
        )


def test_latest_view_and_center_receipts_replace_previous_claims(tmp_path):
    size = {"render_size": [640, 480], "preset_label": "A", "preset_reported": True}
    hit = {
        "point": [320, 240],
        "status": "observed",
        "hits_total": 1,
        "first_hit": "UI09Cover",
        "policy_safe": True,
        "ordered_hits": True,
        "native_query_attempted": True,
    }
    unknown = {"point": [320, 240], "status": "unknown"}
    answer = {
        "source": "editor_live",
        "channel": "editor",
        "center": [320, 240],
        "hit_status": "hit",
        "first_hit": "UI09Cover",
    }
    path = tmp_path / "answer.txt"
    for order, wire_grade, hit_grade, null_grade in [
        ([hit, unknown], "partial", "incorrect", "limited"),
        ([unknown, hit], "complete", "correct", "incorrect"),
    ]:
        records = [
            Call(GAME_VIEW, "unity", 1, "returned", size),
            *[Call(RAYCAST, "unity", i, "returned", r) for i, r in enumerate(order, 2)],
        ]
        assert evidence_grade("raycast", records) == wire_grade
        path.write_text(json.dumps(answer))
        assert (
            final_answer(tmp_path, "raycast", [size, *order], [], protocol="v2")["status"]
            == hit_grade
        )
        path.write_text(json.dumps({**answer, "hit_status": None, "first_hit": None}))
        assert (
            final_answer(tmp_path, "raycast", [size, *order], [], protocol="v2")["status"]
            == null_grade
        )
    newer = {"render_size": [800, 600]}
    path.write_text(
        json.dumps(
            {
                "source": "editor_live",
                "channel": "editor",
                "width": 800,
                "height": 600,
                "preset": None,
            }
        )
    )
    assert (
        final_answer(tmp_path, "game-view", [size, newer], [], protocol="v2")["status"] == "correct"
    )
    path.write_text(
        json.dumps({**answer, "center": [400, 300], "hit_status": None, "first_hit": None})
    )
    assert (
        final_answer(tmp_path, "raycast", [size, hit, newer], [], protocol="v2")["status"]
        == "limited"
    )


def test_overlapping_requests_do_not_observe_future_responses():
    size = {"render_size": [640, 480]}
    first = Call(
        GAME_VIEW, "unity", 1, "returned", size, timeline="connection", request_at=1, response_at=3
    )
    second = Call(
        "get_game_view_size",
        "unity",
        2,
        "returned",
        size,
        timeline="connection",
        request_at=2,
        response_at=4,
    )
    ray = Call(RAYCAST, "unity", 3, timeline="connection", request_at=2, response_at=5)
    assert timing_metrics("game-view", [first, second]) == (0, 0)
    assert timing_metrics("raycast", [first, ray]) == (0, 1)
    second.request_at = 4
    assert timing_metrics("game-view", [first, second]) == (1, 0)
    second.timeline = "host"
    assert timing_metrics("game-view", [first, second]) == (None, None)


def test_conflicting_actor_metadata_and_actionless_routes():
    first, second = "01800000-0000-4000-8100-000000000001", "01800000-0000-4000-8100-000000000002"
    actor, status = actor_binding(
        {"_meta": {"threadId": first, "x-codex-turn-metadata": {"thread_id": second}}}
    )
    assert not actor and status == "conflict"
    child, _ = actor_binding({"_meta": {"threadId": second}})
    wire = Call(GAME_VIEW, "unity", 1, actor=actor, actor_binding=status)
    trace = ClientTrace(child_actors={child})
    assert role_status("subagent", trace, [[wire]]) == (
        "mixed",
        "conflicting-request-actor-metadata",
    )
    wire.actor_binding = "missing"
    assert role_status("subagent", trace, [[wire]])[0] == "inferred"
    wire.actor, wire.actor_binding = child, "bound"
    assert role_status("subagent", trace, [[wire]])[0] == "confirmed"
    asset = "Assets/Shader.shadergraph"
    assert decision("get_game_view_size", {}, asset, endpoint=True) == "unity"
    assert (
        decision("inspect_shader_graph", {"path": asset, "page_number": 2}, asset, endpoint=True)
        == "unity"
    )
    assert (
        decision("inspect_shader_graph", {"path": "Assets/Other.shadergraph"}, asset, endpoint=True)
        == "blocked"
    )
