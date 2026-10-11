"""Truthful native environment and explicit policy fixtures for offline gates."""

import json
from .play_scenario_soak_compare_fixtures import _evidence, _change_reproduction


ENVIRONMENT = {
    "schema_version": 1,
    "operating_system": "Windows 11",
    "platform": "WindowsPlayer",
    "architecture": "x64",
    "scripting_backend": "mono",
    "build_mode": "development",
    "runtime_version": "4.0.30319.42000",
}


def _known_evidence(directory, values):
    result = _evidence(directory, values)
    _change_reproduction(result, environment=ENVIRONMENT)
    return result


def _policy(path, net=20, slope=10, paired=2, consecutive=2):
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "metrics": {
                    "managed_bytes": {
                        "max_net_change_increase": net,
                        "max_slope_increase_per_iteration": slope,
                        "min_paired_samples": paired,
                        "min_consecutive_samples": consecutive,
                    }
                },
            }
        )
    )
    return path
