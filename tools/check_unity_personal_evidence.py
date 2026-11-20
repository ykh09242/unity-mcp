"""Qualify native QA against Unity Personal without changing licenses or storing account data."""

import argparse
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys

PERSONAL = "Unity Personal"
EDITOR_PRODUCTS = {
    PERSONAL,
    "Unity Pro",
    "Unity Enterprise",
    "Unity Industry",
    "Unity Plus",
    "Unity Education",
}
PRODUCT_LINE = re.compile(rb"^\s*Product:\s*(Unity [A-Za-z ]{1,60}?)\s*$")


def classify_inventory(response: dict) -> dict:
    data = response.get("data") or {}
    rows = data.get("items")
    if (
        response.get("success") is not True
        or not isinstance(rows, list)
        or data.get("partial") is not False
        or data.get("nextPage") is not None
        or type(data.get("total")) is not int
        or data["total"] != len(rows)
    ):
        raise ValueError("Active license inventory is incomplete")
    products = set()
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("product"), str):
            raise ValueError("Active license inventory has an unreadable product")
        product = row["product"].strip()
        products.add(
            product if product in EDITOR_PRODUCTS or product == "Asset Store" else "unrecognized"
        )
    return {
        "products": sorted(products),
        "personal_only_editor": products - {"Asset Store"} == {PERSONAL},
    }


def active_inventory() -> dict:
    binary = shutil.which("unity")
    if not binary:
        raise ValueError("Unity CLI is required for the active license preflight")
    command = [binary, "license", "--json", "--no-banner", "--non-interactive", "--no-log-proxy"]
    result = subprocess.run(command, capture_output=True, timeout=30, check=False)
    if result.returncode or len(result.stdout) > 256 * 1024:
        raise ValueError("The Unity CLI did not return a complete active license inventory")
    return classify_inventory(json.loads(result.stdout))


def qualify_editor_log(path: Path) -> dict:
    products = set()
    fingerprint = sha256()
    with path.open("rb") as stream:
        for line in stream:
            fingerprint.update(line)
            match = PRODUCT_LINE.fullmatch(line)
            if match:
                label = match.group(1).decode("ascii")
                products.add(label if label in EDITOR_PRODUCTS else "unrecognized")
    return {
        "editor_log": str(path.resolve()),
        "sha256": fingerprint.hexdigest(),
        "products": sorted(products),
        "personal": products == {PERSONAL},
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--active", action="store_true", help="Check the local Unity CLI license inventory"
    )
    parser.add_argument(
        "--editor-log",
        type=Path,
        action="append",
        default=[],
        help="Log from the actual native Editor or build",
    )
    parser.add_argument("--output", type=Path, help="New local JSON receipt")
    args = parser.parse_args()
    if not args.active and not args.editor_log:
        parser.error("Select --active or at least one --editor-log")
    receipt = {"schema_version": 1, "observed_utc": datetime.now(timezone.utc).isoformat()}
    try:
        if args.active:
            receipt["active"] = active_inventory()
        receipt["editors"] = [qualify_editor_log(path) for path in args.editor_log]
        eligible = receipt.get("active", {}).get("personal_only_editor", True)
        receipt["passed"] = eligible and all(row["personal"] for row in receipt["editors"])
    except (OSError, ValueError, subprocess.TimeoutExpired):
        receipt.update(
            passed=False, error="Requested license evidence is unavailable or incomplete"
        )
    encoded = json.dumps(receipt, indent=2)
    if args.output:
        with args.output.open("x", encoding="utf-8") as stream:
            stream.write(encoded + "\n")
    print(encoded)
    return 0 if receipt["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
