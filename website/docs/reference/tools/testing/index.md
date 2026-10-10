---
title: "testing tools"
sidebar_label: "testing"
description: "MCP for Unity tools in the testing group."
---

# `testing` tools

Test runner, async test jobs & bounded Play Mode input simulation

- **[`get_test_job`](./get_test_job.md)** — Polls an async Unity test job by job_id.
- **[`manage_input`](./manage_input.md)** — Simulate bounded input in Unity Play Mode. status reports capabilities and held controls. ui_click dispatches uGUI pointer events to a scene instance ID or exact root hierarchy path (independent of raw input backend; does not test raycas…
- **[`manage_play_scenario`](./manage_play_scenario.md)** — Save/get/list/delete repeatable Play Mode scenarios and run/status/cancel Unity-owned jobs. save takes a whole scenario definition; get/delete/run take its name. status/cancel require a 32-character lowercase hexadecimal job_id. run retu…
- **[`run_tests`](./run_tests.md)** — Starts a Unity test run asynchronously and returns a job_id immediately.
