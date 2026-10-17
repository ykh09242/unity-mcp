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
- **[`manage_scenario_recording`](./manage_scenario_recording.md)** — Record observed uGUI Button pointer clicks and scene changes during unpaused Editor Play Mode into a bounded scenario draft. start returns recording_id immediately; status polls explicitly. stop saves a NEW scenario with draft=true and r…
- **[`run_tests`](./run_tests.md)** — Starts a Unity test run asynchronously and returns a job_id immediately.
