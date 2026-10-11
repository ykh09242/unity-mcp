"""Portable profile decisions and JUnit, separate from original native run artifacts."""

from html import escape
import json
from pathlib import Path
from xml.etree import ElementTree

from cli.utils.play_scenario_analysis_io import write_new
from cli.utils.play_scenario_html import STYLE
from cli.utils.play_scenario_regression import RegressionResult
from cli.utils.play_scenario_reports import _xml_text


def write_regression_reports(directory: Path, result: RegressionResult) -> None:
    encoded = result.model_dump_json(indent=2).encode("utf-8")
    if len(encoded) > 8 * 1024 * 1024:
        raise ValueError("Regression decisions exceed the 8 MiB evidence bound")
    write_new(directory / "regression.json", encoded)
    suite = ElementTree.Element(
        "testsuite", name=_xml_text(result.name), tests=str(len(result.gates))
    )
    failures = sum(gate.status == "failed" for gate in result.gates)
    errors = sum(gate.status == "unavailable" for gate in result.gates)
    suite.set("failures", str(failures))
    suite.set("errors", str(errors))
    rows = []
    for gate in result.gates:
        detail = json.dumps(gate.detail, ensure_ascii=True, indent=2, allow_nan=False)
        case = ElementTree.SubElement(
            suite, "testcase", name=_xml_text(gate.name), classname="regression"
        )
        if gate.status != "passed":
            node = ElementTree.SubElement(
                case, "failure" if gate.status == "failed" else "error", type=gate.status
            )
            node.text = _xml_text(detail[:65536])
        suffix = "\n[Display truncated; consult regression.json]" if len(detail) > 65536 else ""
        rows.append(
            f"<article><h2>{escape(gate.name)}: {gate.status}</h2><pre>{escape(detail[:65536] + suffix)}</pre></article>"
        )
    write_new(
        directory / "junit.xml", ElementTree.tostring(suite, encoding="utf-8", xml_declaration=True)
    )
    html = (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        '<meta http-equiv="Content-Security-Policy" content="default-src \'none\'; '
        "style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'\">"
        f"<title>{escape(result.name)} | Regression</title><style>{STYLE}</style></head>"
        f"<body><header><h1>{escape(result.name)}</h1><p>Regression exit code: {result.exit_code}</p>"
        "<p>Each selected gate is independent. Missing evidence is unavailable. Original execution outcomes remain unchanged.</p>"
        f"</header><main>{''.join(rows)}</main></body></html>"
    )
    write_new(directory / "report.html", html.encode("utf-8"))
