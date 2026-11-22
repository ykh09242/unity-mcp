"""Independent oracles and single-cohort coverage for held-out selection trials."""

from collections import Counter
import json
import math
import os
from pathlib import Path
import re

from tools.experiments import blind_tool_actions as actions
from tools.experiments import blind_tool_console as console
from tools.experiments.blind_tool_answers import matches

PROMPT_SET = "holdout"
TASKS = (actions.MENU_TASK, console.TASK, actions.CAMERA_TASK)
PREPARATORY_SHELL = {"instruction_read", "working_directory"}
CONTEXT_KEYS = ("project", "instance", "editor_pid", "play_mode", "frame_debugger_windows")
EMPHASIS = re.compile(r"`([^`\n]+)`|\*\*([^*\n]+)\*\*")
MARKDOWN_LINK = re.compile(
    r"\[([^\]\n]+)\]\(\s*<?https?://(?:[^\s<>()]|\([^\s()]*\))+>?(?:\s+\"[^\"]*\")?\s*\)",
    re.IGNORECASE,
)
URL = re.compile(r"<?https?://[^\s<>]+>?", re.IGNORECASE)
MENU_SEPARATOR = r"[/>›→]"
MENU_LABEL = r"[\w][\w .&+()'\-]*"
MENU_PATH = re.compile(rf"{MENU_LABEL}(?:[ \t]*{MENU_SEPARATOR}[ \t]*{MENU_LABEL})+")
MENU_ROOT = r"(?:File|Edit|Assets|GameObject|Component|Window|Help|Tools|Rendering|Analysis)"
MENU_WORD = r"[A-Z][A-Za-z0-9&+'\-]*"
MENU_TITLE = rf"{MENU_WORD}(?:[ \t]+{MENU_WORD})*"
MENU_INLINE = re.compile(
    rf"(?<![\w/])(?:{MENU_ROOT}(?:[ \t]*{MENU_SEPARATOR}[ \t]*{MENU_TITLE})+"
    rf"|{MENU_WORD}(?:[ \t]*[>›→][ \t]*{MENU_TITLE}){{2,}})"
)
MENU_PROSE_JOIN = re.compile(r"\b(?:or|and)\b", re.IGNORECASE)
MENU_CLAUSE_BREAK = re.compile(r"[.!?](?=\s|$)|[。！？;\n]")
MENU_CORRECTION = re.compile(
    r"\b(?:this|that) (?:answer|path) is (?:wrong|incorrect)\b", re.IGNORECASE
)
NUMBER = r"[+−-]?\d+(?:[.,٫]\d+)?(?:[eE][+−-]?\d+)?"
NAME_LABEL = (
    r"(?:Unity Camera|Camera|GameObject(?:\s*(?:name|名前|名|이름|adı))?|Name|"
    r"카메라(?: 이름)?|이름|オブジェクト名|カメラ名|Nombre(?: del GameObject)?|"
    r"اسم(?: الكائن| الكاميرا)?|Kamera(?: adı)?)"
)
DEPTH_LABEL = (
    r"(?:Camera\.depth|depth(?:\s*(?:値|값|değeri))?|깊이(?:\s*값)?|深度|深さ|"
    r"(?:valor de )?profundidad|(?:قيمة )?(?:العمق|عمق)|derinlik(?: değeri)?)"
)
CAMERA_VALUE = rf"(?:{DEPTH_LABEL}\s*(?:[:：=]|\bde\b|は|는)?\s*)?(?P<depth>{NUMBER})"
CAMERA_END = r"(?:\s*(?:입니다|이에요|です))?[.。]?"
CAMERA_PAIR = re.compile(
    rf"(?:{NAME_LABEL}\s*[:：=]\s*)?(?P<camera>[^\n]+?)"
    rf"\s*(?:(?P<paren>\()|[—–,:：،|]|\s-\s|\n)\s*{CAMERA_VALUE}"
    rf"(?(paren)\s*\)){CAMERA_END}",
    re.IGNORECASE,
)
NEGATED_ANSWER = re.compile(
    r"\b(?:not|wrong|incorrect|isn't|wasn't|no es|no fue|incorrecto|equivocado|"
    r"değil(?:dir)?|yanlış|ليس|ليست|خطأ)\b|아니(?:다|고|며|라)|아닙니다|"
    r"では(?:ない|ありません|なく)|じゃない",
    re.IGNORECASE,
)
UNCERTAIN_ANSWER = re.compile(
    r"\b(?:possibly|maybe|perhaps|probably|could be|might be|uncertain|not sure|not certain|"
    r"or another|quizás|tal vez|probablemente|belki|galiba|ربما)\b|아마|かもしれ",
    re.IGNORECASE,
)


def oracle(task, state):
    """Use the operator's independent probe, never the responding actor's messages."""
    if task == console.TASK:
        snapshot = console.snapshot(state)
        return {"errors": snapshot["errors"]} if console.mixed(snapshot) else None
    fixtures = state.get("observation_fixtures") if isinstance(state, dict) else None
    if not isinstance(fixtures, dict):
        return None
    snapshot = fixtures.get(task)
    if not isinstance(snapshot, dict):
        return None
    if task == actions.CAMERA_TASK and snapshot.get("truncated") is False:
        return actions.camera_facts(snapshot) or None
    if task == actions.MENU_TASK and snapshot.get("menu_path") == actions.FRAME_DEBUGGER:
        return {"menu_path": snapshot["menu_path"]}
    return None


def answer_match(task, text, expected, before):
    if not text.strip():
        return "missing"
    if task == console.TASK:
        return console.answer_match(text, console.snapshot(before))
    if task == actions.MENU_TASK:
        return menu_answer(text, expected["menu_path"])[0]
    return camera_answer(text, expected)


def menu_path(text):
    candidate = text.strip().rstrip(".。")
    if not MENU_PATH.fullmatch(candidate):
        return None
    return re.sub(rf"[ \t]*{MENU_SEPARATOR}[ \t]*", "/", candidate)


def standalone_menu_path(text, inline):
    path = menu_path(text)
    return (
        path
        if path and (not inline or path in inline) and not MENU_PROSE_JOIN.search(text)
        else None
    )


def menu_answer(text, expected):
    """Separate visible path claims from documentation and from path-only formatting."""
    if not text.strip():
        return "missing", False
    visible = MARKDOWN_LINK.sub(r"\1", text)
    lines = visible.strip().splitlines()
    if len(lines) >= 3 and lines[0] in {"```", "```text"} and lines[-1] == "```":
        visible = "\n".join(lines[1:-1])
    unstyled = EMPHASIS.sub(lambda m: m[1] or m[2], visible)
    path_only = (
        standalone_menu_path(
            unstyled, {menu_path(m.group()) for m in MENU_INLINE.finditer(unstyled)}
        )
        is not None
    )
    without_links = URL.sub(" ", visible)
    candidates = {
        menu_path(span.group(1) or span.group(2)) for span in EMPHASIS.finditer(without_links)
    }
    plain = EMPHASIS.sub(lambda m: m[1] or m[2], without_links)
    for line in plain.splitlines():
        line = re.sub(r"^[ \t]*[-*][ \t]+", "", line).strip()
        # Prefer the entire standalone breadcrumb. Within prose, require an
        # Editor root label so ordinary slash-separated wording is ignored.
        inline = {menu_path(m.group()) for m in MENU_INLINE.finditer(line)}
        path = standalone_menu_path(line, inline)
        if path is not None:
            candidates.add(path)
        else:
            candidates.update(inline)
    candidates.discard(None)
    if len(candidates) > 1 or MENU_CORRECTION.search(plain):
        return "incorrect", path_only
    if not candidates:
        return "needs_review", path_only
    claims = [
        clause
        for clause in MENU_CLAUSE_BREAK.split(plain)
        if MENU_INLINE.search(clause) or menu_path(clause) in candidates
    ]
    if any(UNCERTAIN_ANSWER.search(claim) for claim in claims):
        return "needs_review", path_only
    if any(NEGATED_ANSWER.search(claim) for claim in claims):
        return "incorrect", path_only
    return ("correct" if expected in candidates else "incorrect"), path_only


def camera_answer(text, expected):
    """Credit a single affirmative exact pair. Review prose beyond the accepted formats."""
    # Presentation wrappers and list markers carry no claim content.
    plain = EMPHASIS.sub(lambda m: m[1] or m[2], text)
    plain = "\n".join(
        re.sub(r"^\s*[-*]\s+", "", line).strip() for line in plain.splitlines() if line.strip()
    )
    commentary = plain.replace(expected["camera"], "")
    if NEGATED_ANSWER.search(commentary):
        return "incorrect"
    if UNCERTAIN_ANSWER.search(commentary):
        return "needs_review"
    pair = CAMERA_PAIR.fullmatch(plain)
    if not pair:
        return "needs_review"
    value = float(pair["depth"].translate(str.maketrans({"−": "-", ",": ".", "٫": "."})))
    return (
        "correct"
        if pair["camera"].strip() == expected["camera"]
        and matches("depth", value, expected["depth"])
        else "incorrect"
    )


def fixture_values(state):
    """Normalize the whole shared fixture so order alone does not look like mutation."""
    if not isinstance(state, dict):
        return {}
    values = {
        key: os.path.normcase(os.path.normpath(state[key])) if key == "project" else state[key]
        for key in ("project", "instance")
        if isinstance(state.get(key), str) and state[key].strip()
    }
    values.update(
        {
            key: state[key]
            for key, bound in (("editor_pid", 1), ("frame_debugger_windows", 0))
            if type(state.get(key)) is int and state[key] >= bound
        }
    )
    if type(state.get("play_mode")) is bool:
        values["play_mode"] = state["play_mode"]
    snapshot = console.snapshot(state)
    if snapshot is not None:
        values[console.TASK] = {name: Counter(messages) for name, messages in snapshot.items()}
    fixtures = state.get("observation_fixtures")
    if not isinstance(fixtures, dict):
        return values
    menu = fixtures.get(actions.MENU_TASK)
    if isinstance(menu, dict) and isinstance(menu.get("menu_path"), str) and menu["menu_path"]:
        values[actions.MENU_TASK] = menu["menu_path"]
    cameras = fixtures.get(actions.CAMERA_TASK)
    if isinstance(cameras, dict) and cameras.get("truncated") is False:
        rows = cameras.get("unityCameras")
        if isinstance(rows, list) and all(
            isinstance(camera, dict)
            and isinstance(camera.get("name"), str)
            and bool(camera["name"])
            and type(camera.get("depth")) in (int, float)
            and (type(camera["depth"]) is int or math.isfinite(camera["depth"]))
            for camera in rows
        ):
            values[actions.CAMERA_TASK] = Counter(
                json.dumps(camera, sort_keys=True) for camera in rows
            )
    return values


def fixture_diff(row):
    before = fixture_values(row.get("editor_start_state"))
    after = fixture_values(row.get("editor_end_state"))
    intended = fixture_values({key: row.get(key) for key in ("project", "instance")})
    required = (*CONTEXT_KEYS, *TASKS)
    return {
        "missing_before": [key for key in required if key not in before],
        "missing_after": [key for key in required if key not in after],
        "changed": [
            key for key in required if key in before and key in after and before[key] != after[key]
        ],
        "unverified_target": [
            key
            for key in ("project", "instance")
            if key not in intended
            or before.get(key) != intended[key]
            or after.get(key) != intended[key]
        ],
    }


def suitable(task, call):
    if call.channel != "unity":
        return False
    if task == actions.MENU_TASK:
        return call.route in actions.MENU_INVENTORIES
    if task == actions.CAMERA_TASK:
        return call.route in actions.CAMERA_ROUTES
    return call.route in console.ROUTES and call.error_filter is True


def evidence_matches(task, expected, calls):
    reads = [call.evidence for call in calls if suitable(task, call) and call.status == "returned"]
    if task == console.TASK:
        return console.witnessed(reads, expected["errors"])
    return any(
        all(matches(field, read.get(field), value) for field, value in expected.items())
        for read in reads
    )


def assess(row, calls, answer, *, role, actor_completed, controls):
    """Measure owned attempts separately from answer accuracy and grounded success."""
    task = row["task"]
    before = row.get("editor_start_state")
    after = row.get("editor_end_state")
    expected = oracle(task, before)
    diff = fixture_diff(row)
    retained = (
        False
        if diff["changed"]
        else None
        if diff["missing_before"] or diff["missing_after"] or diff["unverified_target"]
        else True
    )
    reasons = []
    if row.get("runner_status") != "finished":
        reasons.append(f"runner:{row.get('runner_status') or 'unknown'}")
    if role != "confirmed":
        reasons.append(f"role:{role}")
    if not actor_completed:
        reasons.append("actor_turn_missing_or_incomplete")
    if expected is None:
        reasons.append("no_independent_pre_prompt_oracle")
    if retained is None:
        reasons.append("fixture_preservation_unverified")
    elif not retained:
        reasons.append("fixture_changed")
    reasons.extend(f"actor_control:{issue}" for issue in controls)

    business = first_action = None
    if calls is not None:
        action_calls = [call for call in calls if call.shell_purpose not in PREPARATORY_SHELL]
        first_action = next(iter(action_calls), None)
        business = next((call for call in action_calls if call.channel != "discovery"), None)
    match, path_only = "unverified", None
    if expected is not None and actor_completed:
        if task == actions.MENU_TASK:
            match, path_only = menu_answer(answer, expected["menu_path"])
        else:
            match = answer_match(task, answer, expected, before)
    evidence = (
        evidence_matches(task, expected, calls)
        if expected is not None and calls is not None
        else None
    )
    return {
        "eligible": not reasons,
        "exclusions": reasons,
        "first_actor_business_tool": business.route if business else None,
        "first_actor_business_tool_suitable": (
            suitable(task, business) if business else False if actor_completed else None
        ),
        "first_observed_business_action": first_action.route if first_action else None,
        "first_observed_business_action_suitable": (
            suitable(task, first_action) if first_action else False if actor_completed else None
        ),
        "unnecessary_menu_execution_attempts": (
            sum(call.route in actions.MENU_EXECUTION_ROUTES for call in calls)
            if task == actions.MENU_TASK and calls is not None
            else None
        ),
        "actor_observed_calls": (
            [
                {"route": call.route, "channel": call.channel, "status": call.status}
                for call in calls
            ]
            if calls is not None
            else None
        ),
        "fixture_preserved": retained,
        "fixture_diff": diff,
        "frame_debugger_windows": {
            "before": (before or {}).get("frame_debugger_windows"),
            "after": (after or {}).get("frame_debugger_windows"),
        }
        if task == actions.MENU_TASK
        else None,
        "canonical_oracle_answer": expected,
        "oracle_answer_status": match,
        "menu_answer_path_only": path_only,
        "actor_evidence_matches_oracle": evidence,
        "grounded_answer": match == "correct" and evidence is True if not reasons else None,
    }


def summarize(output: Path, planned, cases):
    """Count each planned cell, including unrun cases and excluded observations."""
    keys = ("task", "language", "role", "repetition")
    indexed = {tuple(case[key] for key in keys): case for case in cases}
    cells = list(dict.fromkeys([*(tuple(cell) for cell in planned), *indexed]))

    def stratum(selected):
        observed = [indexed[cell] for cell in selected if cell in indexed]
        eligible = [c["holdout"] for c in observed if c.get("holdout", {}).get("eligible")]

        def rate(key, wanted=True):
            values = [c[key] for c in eligible if c[key] is not None]
            return {"numerator": values.count(wanted), "denominator": len(values)}

        attempts = [c["unnecessary_menu_execution_attempts"] for c in eligible]
        observed_attempts = [
            c.get("holdout", {}).get("unnecessary_menu_execution_attempts") for c in observed
        ]
        return {
            "planned_cases": len(selected),
            "recorded_cases": len(observed),
            "completed_actor_turns": sum(
                c.get("turn_completed") is True
                and (c["role"] == "main" or c.get("actor_receipt", {}).get("child_turn_completed"))
                for c in observed
            ),
            "eligible_cases": len(eligible),
            "excluded_cases": len(observed) - len(eligible),
            "unrun_cases": len(selected) - len(observed),
            "role_status": dict(Counter(c.get("role_status") or "unknown" for c in observed)),
            "exclusions": dict(
                Counter(
                    reason
                    for c in observed
                    for reason in c.get("holdout", {}).get("exclusions", ["different_prompt_set"])
                )
            ),
            "first_actor_business_tools": dict(
                Counter(c["first_actor_business_tool"] or "none" for c in eligible)
            ),
            "first_actor_business_tool_suitable": rate("first_actor_business_tool_suitable"),
            "first_observed_business_actions": dict(
                Counter(c["first_observed_business_action"] or "none" for c in eligible)
            ),
            "first_observed_business_action_suitable": rate(
                "first_observed_business_action_suitable"
            ),
            "unnecessary_menu_execution_attempts": {
                "count": sum(n for n in attempts if n is not None),
                "measured_cases": sum(n is not None for n in attempts),
                "observed_count_including_excluded": sum(
                    n for n in observed_attempts if n is not None
                ),
            },
            "oracle_answer_status": dict(Counter(c["oracle_answer_status"] for c in eligible)),
            "menu_answer_path_only": rate("menu_answer_path_only"),
            "actor_evidence_matches_oracle": rate("actor_evidence_matches_oracle"),
            "grounded_answer": rate("grounded_answer"),
        }

    return {
        "cohort": str(output),
        "overall": stratum(cells),
        "by_task_language_role": [
            {
                "task": task,
                "language": language,
                "role": role,
                **stratum([cell for cell in cells if cell[:3] == (task, language, role)]),
            }
            for task, language, role in sorted({cell[:3] for cell in cells})
        ],
    }
