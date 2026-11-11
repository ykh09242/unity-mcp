---
title: "testing tools"
sidebar_label: "testing"
description: "MCP for Unity tools in the testing group."
---

# `testing` tools

Test runner, async test jobs & bounded Play Mode input simulation

- **[`cancel_test_job`](./cancel_test_job.md)** — Request cancellation of an owned native test job.
- **[`export_test_job`](./export_test_job.md)** — Export completed Unity test evidence as JSON or JUnit.
- **[`get_test_job`](./get_test_job.md)** — Polls an async Unity test job by job_id.
- **[`get_tests`](./get_tests.md)** — Lists Unity tests without running them.
- **[`manage_input`](./manage_input.md)** — Simulate bounded input in Unity Play Mode. status reports capabilities and held controls. ui_click dispatches uGUI pointer events to a scene instance ID or exact root hierarchy path (independent of raw input backend; does not test raycas…
- **[`manage_play_scenario`](./manage_play_scenario.md)** — Save/get/list/delete scenarios and suites; reports history and run/status/cancel Unity-owned jobs.
- **[`manage_scenario_recording`](./manage_scenario_recording.md)** — Record observed uGUI Button pointer clicks and scene changes during unpaused Editor Play Mode.
- **[`manage_script_quality`](./manage_script_quality.md)** — Observe optional pinned Unity/C# diagnostics without writing source. capabilities reports explicit Editor setup/consent. analyze and capture_baseline require options.paths (1..256 Assets/Packages .cs files) XOR options.change_set_id; inc…
- **[`manage_simulation_profiles`](./manage_simulation_profiles.md)** — Capture named Time/Physics3D/Physics2D/Audio/Input configuration profiles, inspect per-domain applicability, diff/validate against current settings, explicitly apply, and conditionally restore confirmed changes. capabilities lists exact…
- **[`manage_verification`](./manage_verification.md)** — Run project verification with source-linked stage reports. profiles reads defaults and explicit project overrides without writing. set_profile requires profile_id, profile and expected_hash from profiles (missing for a new file). prepare…
- **[`run_tests`](./run_tests.md)** — Starts a Unity test run asynchronously and returns a job_id immediately.
- **[`view_test_results`](./view_test_results.md)** — Read one completed Unity test-result page (up to 100 failed/skipped details).
