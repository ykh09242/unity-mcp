"""Actual product server with an owned-only target and synthetic QA credential provider."""

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys

from play_scenario_mcp_sample import validate_ready

# Same public synthetic fixture value as TransportEditorIntegrationTests; never a user credential.
TOKEN = "owned-editor-qa-synthetic-token-only"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--ready", type=Path, required=True)
    parser.add_argument("--project", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    ready = json.loads(args.ready.read_text(encoding="utf-8"))
    identifier = validate_ready(ready, args.project)
    from models.models import UnityInstanceInfo
    from transport.legacy.port_discovery import PortDiscovery
    import transport.legacy.unity_connection as legacy
    import main as product

    instance = UnityInstanceInfo(
        id=identifier,
        name="project",
        path=str(args.project / "Assets"),
        hash=identifier.split("@")[1],
        port=ready["port"],
        status="running",
        last_heartbeat=datetime.now(timezone.utc),
        unity_version="6000.0.69f1",
    )
    # Replace only external discovery and credential lookup, never handlers or wire responses.
    PortDiscovery.discover_all_unity_instances = staticmethod(lambda: [instance])
    PortDiscovery.discover_unity_instance = staticmethod(
        lambda requested: instance if requested == identifier else None
    )
    legacy.read_status_file = lambda *_args, **_kwargs: {
        "reloading": False,
        "reason": "owned_sample",
    }
    legacy.read_stdio_token = lambda _generation: TOKEN
    (args.output / "server-process.json").write_text(
        json.dumps({"pid": os.getpid(), "instance": identifier, "fixture_bootstrap": True}),
        encoding="utf-8",
    )
    sys.argv = ["owned-sample-mcp", "--transport", "stdio", "--default-instance", identifier]
    product.main()


if __name__ == "__main__":
    main()
