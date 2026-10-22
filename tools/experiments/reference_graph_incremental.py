"""Compare scoped incremental analysis with a selected trusted Git control."""

import argparse
from dataclasses import asdict
import gc
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import time
import tracemalloc
from types import ModuleType

parser = argparse.ArgumentParser()
parser.add_argument("repo", type=Path)
parser.add_argument("output", type=Path)
parser.add_argument(
    "--analysis-ref", help="Read the analyzer from this local Git revision for a control run"
)
args = parser.parse_args()
sys.path.insert(0, str(args.repo / "Server/src"))
from services.reference_graph.analysis import AnalysisIndex, Catalog, Facts, parse_csharp

if args.analysis_ref:
    source = subprocess.run(
        ["git", "show", args.analysis_ref + ":Server/src/services/reference_graph/analysis.py"],
        cwd=args.repo,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    ).stdout
    control_name = "services.reference_graph.measured_control"
    control = ModuleType(control_name)
    sys.modules[control_name] = control
    control_namespace = vars(control)
    exec(compile(source, "<selected-git-analyzer>", "exec"), control_namespace)
    AnalysisIndex, Catalog, Facts, parse_csharp = (
        control_namespace["AnalysisIndex"],
        control_namespace["Catalog"],
        control_namespace["Facts"],
        control_namespace["parse_csharp"],
    )


def measured(operation):
    gc.collect()
    tracemalloc.start()
    started = time.perf_counter()
    value = operation()
    seconds = time.perf_counter() - started
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    return value, {"seconds": seconds, "peak_python_bytes": peak}


def branch_source(depth):
    source = "using UnityEngine; class Loader { static void W0(string x){ Resources.Load(x); } "
    for level in range(1, depth + 1):
        source += f'static void W{level}(string x){{ W{level - 1}(x+"a"); W{level - 1}(x+"b"); }} '
    return source + "}"


def digest(result):
    return hashlib.sha256(
        json.dumps(asdict(result), sort_keys=True, default=sorted).encode()
    ).hexdigest()


output = {"branching": [], "incremental": []}
for depth in (8, 13):
    facts = parse_csharp("loader", "Assets/Loader.cs", branch_source(depth))
    index, sample = measured(lambda facts=facts: AnalysisIndex([facts], Catalog([])))
    result, analysis_sample = measured(lambda index=index: index.analyze("loader"))
    output["branching"].append(
        {"depth": depth, "build": sample, "analysis": analysis_sample, "digest": digest(result)}
    )

for count in (10000, 50000):
    facts = [
        parse_csharp(
            f"owner{n}", f"Assets/T{n}.cs", f'class T{n} {{ public const string Key = "{n}"; }}'
        )
        for n in range(count)
    ]
    facts.append(
        parse_csharp(
            "caller",
            "Assets/Caller.cs",
            "using UnityEngine; class Caller { void Run() { Resources.Load(T0.Key); } }",
        )
    )
    documents = [asdict(item) for item in facts]
    index = AnalysisIndex(facts, Catalog([]))
    samples = []
    for iteration in range(3):
        changed = parse_csharp(
            "owner0", "Assets/T0.cs", f'class T0 {{ public const string Key = "new{iteration}"; }}'
        )
        documents[0] = asdict(changed)
        if hasattr(index, "update"):
            affected, sample = measured(
                lambda index=index, changed=changed: index.update([changed])
            )
        else:
            index, sample = measured(
                lambda documents=documents: AnalysisIndex(
                    [Facts.from_dict(row) for row in documents], Catalog([])
                )
            )
            affected = index.affected({"owner0"})
        sample["affected"] = len(affected)
        sample["digest"] = digest(index.analyze("caller"))
        samples.append(sample)
    output["incremental"].append({"documents": count, "samples": samples})
args.output.write_text(json.dumps(output, indent=2), encoding="utf-8")
print(json.dumps(output, indent=2))
