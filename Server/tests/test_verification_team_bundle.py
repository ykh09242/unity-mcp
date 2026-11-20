"""A shared bundle uses the shipping readers and carries only the observations they require."""

import json
from hashlib import sha256
from shutil import copyfile

import pytest
from click.testing import CliRunner
from tests.test_verification_selection_quality import MAP, ROUTE
from tests.test_verification_selection_quality import xml_cohort as xml_cohort
from tests.verification_artifact_fixtures import make_capture
from unity_mcp_evidence.commands import offline_main
from unity_mcp_evidence.team_bundle import pack, replay
from unity_mcp_evidence.team_projection import digest, encoded
from unity_mcp_evidence.verification_change import compare_runs, read_run


def private_project(paths, project):
    raw = json.loads(paths[0].read_text(encoding="utf-8"))
    raw["data"]["project_scope"] = str(project)
    paths[0].write_text(json.dumps(raw), encoding="utf-8")


def test_relocated_change_matches_original_classifications_and_drops_user_strings(tmp_path):
    project = tmp_path / "Users" / "private-developer" / "game"
    a = make_capture(project, 1, "failed")
    private_project(a, project)
    before = read_run(*a)
    b = make_capture(project, 2, base_manifest=before.manifest, base_source=before.source)
    private_project(b, project)
    original = compare_runs(before, read_run(*b))
    bundle = tmp_path / "team.json"
    receipt = pack("change", {"baseline": a, "candidate": b}, (), (), bundle)
    assert (
        receipt["sources"]["candidate"]["original_sha256"]["verification"]
        == sha256(b[0].read_bytes()).hexdigest()
    )
    for private in ("private-developer", str(project), "fixture.ready", "job-1", "doors"):
        assert private.encode() not in bundle.read_bytes()
    other = tmp_path / "second machine"
    other.mkdir()
    relocated = copyfile(bundle, other / "reproduction.json")
    for path in (*a, *b):
        path.unlink()
    result, code = replay(relocated, receipt["bundle_sha256"])
    assert code == 0 and result["state"] == original["state"]
    assert sorted(r["classification"] for r in result["assessment"]["checks"]) == sorted(
        r["classification"] for r in original["checks"]
    )
    with pytest.raises(ValueError, match="independently supplied"):
        replay(relocated, "f" * 64)


def test_withheld_and_absent_are_distinct_and_never_satisfy_evidence(tmp_path):
    paths = make_capture(tmp_path, 1)
    target = tmp_path / "team.json"
    receipt = pack("change", {"baseline": paths, "candidate": ()}, ("baseline",), (), target)
    result, code = replay(target, receipt["bundle_sha256"])
    assert code == 2 and result["assessment"] is None
    assert result["unavailable_roles"] == ["baseline", "candidate"]
    assert (
        result["sources"]["baseline"]["original_sha256"]["native"]
        == sha256(paths[1].read_bytes()).hexdigest()
    )
    assert result["sources"]["candidate"] == {"availability": "absent", "original_sha256": {}}
    body = json.loads(target.read_bytes())
    assert "projection" not in body["roles"]["baseline"]


def test_tampered_projection_and_repeated_input_cannot_become_valid(tmp_path):
    a = make_capture(tmp_path, 1)
    bundle = tmp_path / "team.json"
    with pytest.raises(ValueError, match="Reused"):
        pack("change", {"baseline": a, "candidate": a}, (), (), bundle)
    assert not bundle.exists()
    receipt = pack("change", {"baseline": a, "candidate": ()}, (), (), bundle)
    data = json.loads(bundle.read_bytes())
    data["roles"]["baseline"]["projection"]["inputs_confirmed"] = False
    bundle.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ValueError, match="independently supplied"):
        replay(bundle, receipt["bundle_sha256"])
    with pytest.raises(ValueError, match="projection differs"):
        replay(bundle, sha256(bundle.read_bytes()).hexdigest())


def test_selection_recomputed_by_offline_cli_keeps_miss_and_omission(xml_cohort, tmp_path):
    selected = xml_cohort(1, {MAP: "Passed"})
    full = xml_cohort(2, {MAP: "Passed", ROUTE: "Failed"}, 4)
    runner = CliRunner()
    dest = tmp_path / "selection.json"
    result = runner.invoke(
        offline_main,
        [
            "--format",
            "json",
            "team",
            "pack-selection",
            "--selected",
            str(selected),
            "--full",
            str(full),
            "--affected-test",
            ROUTE,
            "--bundle",
            str(dest),
        ],
    )
    assert result.exit_code == 0, result.output
    assert ROUTE.encode() not in dest.read_bytes() and b"Assets/Map.mat" not in dest.read_bytes()
    pin = json.loads(result.output)["bundle_sha256"]
    replayed = runner.invoke(
        offline_main, ["--format", "json", "team", "replay", str(dest), "--sha256", pin]
    )
    assert replayed.exit_code == 0, replayed.output
    measured = json.loads(replayed.output)["assessment"]
    assert len(measured["selection"]["missed_failures"]) == 1
    assert measured["selection"]["measured_seconds_saved"] == 3
    second = tmp_path / "partial.json"
    receipt = pack(
        "selection",
        {"selected": (selected,), "full": (full,), "fallback": (full,)},
        ("fallback",),
        (),
        second,
    )
    partial, code = replay(second, receipt["bundle_sha256"])
    assert code == 2 and partial["unavailable_roles"] == ["fallback"]


@pytest.mark.parametrize("corruption", ["empty", "foreign_job", "wrong_target"])
def test_pinned_incomplete_primary_scenario_cannot_pass(tmp_path, corruption):
    a = make_capture(tmp_path, 1)
    before = read_run(*a)
    b = make_capture(
        tmp_path, 2, "failed", base_manifest=before.manifest, base_source=before.source
    )
    path = tmp_path / "bundle.json"
    pack("change", {"baseline": a, "candidate": b}, (), (), path)
    bundle = json.loads(path.read_bytes())
    for slot in bundle["roles"].values():
        projection = slot["projection"]
        if corruption == "empty":
            projection["checks"] = []
        else:
            primary = next(check for check in projection["checks"] if check["kind"] == "scenario")
            if corruption == "foreign_job":
                primary["job_id"] = "f" * 64
            else:
                primary["target"] = "editor"
        slot["projection_sha256"] = digest(encoded(projection))
    path.write_bytes(encoded(bundle))
    with pytest.raises(ValueError, match="primary scenario"):
        replay(path, digest(path.read_bytes()))
