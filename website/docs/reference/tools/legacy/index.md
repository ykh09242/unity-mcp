---
title: "legacy tools"
sidebar_label: "legacy"
description: "MCP for Unity tools in the legacy group."
---

# `legacy` tools

Compatibility: full combined schemas for existing clients; prefer focused tools

- **[`manage_asset`](./manage_asset.md)** — Performs asset operations (import, create, modify, delete, etc.) in Unity.
- **[`manage_camera`](./manage_camera.md)** — Manage cameras (Unity Camera + Cinemachine).
- **[`manage_gameobject`](./manage_gameobject.md)** — Performs CRUD operations on GameObjects.
- **[`manage_graphics`](./manage_graphics.md)** — Manage rendering graphics: volumes, post-processing, light baking, rendering stats, pipeline settings, and URP renderer features.
- **[`manage_physics`](./manage_physics.md)** — Manage physics settings, collision matrix, materials, joints, queries, and validation.
- **[`manage_prefabs`](./manage_prefabs.md)** — Manages Unity Prefab assets. get_hierarchy returns bounded sibling-index depth-first pages: page_size defaults to 50 (1-500), max_depth defaults to 64 (0-128, root depth 0), and cursor continues the identical revision/options.
- **[`manage_scene`](./manage_scene.md)** — Performs CRUD operations on Unity scenes.
- **[`manage_ui`](./manage_ui.md)** — Manages Unity UI Toolkit elements (UXML, USS, UIDocument and Unity 6.5+ PanelRenderer components).
