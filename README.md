<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="docs/images/logo-header-dark.png">
    <img alt="MCP for Unity" src="docs/images/logo-header-light.png" width="400">
  </picture>
</p>

<div align="center">

[English](README.md) <img src="docs/images/connector.svg" alt="↔" height="14"> [简体中文](docs/i18n/README-zh.md) &nbsp;&nbsp;&nbsp;|&nbsp;&nbsp;&nbsp; [Documentation](https://ykh09242.github.io/unity-mcp/)

## Unity MCP (ykh09242)

Git-only fork maintained by [ykh09242](https://github.com/ykh09242), based on [CoplayDev/unity-mcp](https://github.com/CoplayDev/unity-mcp). Original MIT copyright, authorship, and research citation are retained. This fork is not an upstream release or a Coplay/Aura service.

Fork versions start at `1.0.0` independently of the upstream `10.3.x` baseline. See [fork releases](https://github.com/ykh09242/unity-mcp/releases) for the tested component versions and server source pin.

</div>

<p align="center"><b>Create your Unity apps with LLMs.</b> MCP for Unity bridges AI assistants — Claude, Codex, VS Code, local LLMs, and more — with your Unity Editor via <a href="https://modelcontextprotocol.io/introduction">Model Context Protocol</a>. Give your LLM the tools to manage assets, control scenes, edit scripts, run tests, and automate your game dev workflows.</p>

<p align="center">
  <img alt="MCP for Unity building a scene" src="docs/images/building_scene.gif">
</p>

---

<!-- recent-updates:start -->
<details>
<summary><strong>Upstream Release History</strong></summary>

These releases belong to CoplayDev/unity-mcp, not this fork.

* **[v10.3.0](https://github.com/CoplayDev/unity-mcp/releases/tag/v10.3.0)** (2026-10-04)
* **[v10.2.0](https://github.com/CoplayDev/unity-mcp/releases/tag/v10.2.0)** (2026-09-01)
* **[v10.1.2](https://github.com/CoplayDev/unity-mcp/releases/tag/v10.1.2)** (2026-08-02)
* **[v10.1.0](https://github.com/CoplayDev/unity-mcp/releases/tag/v10.1.0)** (2026-07-13)
* **[v10.0.2](https://github.com/CoplayDev/unity-mcp/releases/tag/v10.0.2)** (2026-07-13)

Full upstream history: [Release Notes](website/docs/releases.md).

</details>
<!-- recent-updates:end -->

---

## What it does

Connect an MCP assistant to the Unity Editor to inspect scenes, create objects, manage assets, edit scripts, run tests, profile, and build. Tool availability depends on Editor toggles, installed dependencies, protocol mode, and explicit consent. The fork improves the inherited tool surface; see the [stable 1.1.3 release notes](https://github.com/ykh09242/unity-mcp/releases/tag/ykh09242-v1.1.3) for additions, fixes, and verification limits.

**[Browse the full tool catalog →](website/docs/reference/tools/index.md)**

---

## Quickstart

**Switching from upstream:** Remove the existing `com.coplaydev.unity-mcp` package in Package Manager before adding this fork. Do not install both together: they retain the same assembly names and asset GUIDs. After installing the fork, reconfigure your MCP clients to use its pinned server.

**Requirements:** Unity **2021.3 LTS → 6.x** · Python **3.11+** (via [`uv`](https://docs.astral.sh/uv/)). Works with **any MCP client** — Claude Desktop & Code, Cursor, VS Code, Windsurf, Cline, Gemini CLI, and more.

1. **Install** — Unity → Package Manager → Add from git URL:
   `https://github.com/ykh09242/unity-mcp.git?path=/MCPForUnity#ykh09242-v1.1.3`
2. **Connect** — Open `Window > Unity MCP (ykh09242)`. For local HTTP, start the server before configuring clients, then connect the Unity bridge. Configure your selected client and reconnect it.
3. **Verify** — Read `mcpforunity://instances`, choose the Editor, and inspect its project/scene before making [a small first change](website/docs/getting-started/first-prompt.md).

**Start here:** [Install And Connect](website/docs/getting-started/install.md) · [Migrate From Upstream](website/docs/getting-started/migrate.md) · [Manual Client Configuration](website/docs/getting-started/clients.md) · [Troubleshooting](website/docs/guides/troubleshooting.md).

Local HTTP creates a fresh token each launch: reconfigure HTTP MCP clients after restarting the server. Modern sessionless requests use `unity_instance` for tools and `_meta.unity_instance` for resources when selection is ambiguous; hosted requests always need a target. [Security And Consent](website/docs/guides/security.md) explains high-impact tool permissions, credential handling, and safe retries.

`ykh09242-v1.1.3` is the fork's stable release tag. The prefix distinguishes fork releases from inherited upstream tags. Use a full commit SHA for immutable installation, or `#beta` only to follow the moving development branch. The branch name does not make a tagged release a beta. The package name is `com.ykh09242.unity-mcp`; its `MCPForUnity/package.json` records the commit-addressed server archive in `mcpServerSource`. The Python distribution is `ykh09242-unity-mcp-server`, installed from that GitHub archive without a Git checkout, while the executable names remain `mcp-for-unity` and `unity-mcp`. See [server setup](Server/README.md). No fork Asset Store, OpenUPM, PyPI, or hosted MCP service distribution is advertised.

---

## Community

- [Issues](https://github.com/ykh09242/unity-mcp/issues) — bugs, feature requests, and questions about this fork
- Security: see [SECURITY.md](SECURITY.md) for private reporting

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). Branch off `beta`. The full dev setup and testing guidance live in the [Contributing](website/docs/contributing/dev-setup.md) docs.

## Advanced

- **Multiple Unity instances** — [Multi-Instance Routing](website/docs/guides/multi-instance.md)
- **Tool groups (vfx / animation / ui / testing / etc.)** — [Tool Groups](website/docs/guides/tool-groups.md)
- **Upstream v10 asset generation and upgrade notes** — [v10 Migration](website/docs/migrations/v10.md)
- **Roslyn script validation** — [Roslyn Validation](website/docs/guides/roslyn.md)
- **Remote-hosted server with auth** — [Remote Server Auth](website/docs/guides/remote-server-auth.md)
- **Terminal workflows** — [CLI](website/docs/guides/cli.md)

## Citation

If MCP for Unity helped your research, please cite the original work by Wu and Barnett.

```bibtex
@inproceedings{wu2025mcpunity,
  author    = {Wu, Shutong and Barnett, Justin P.},
  title     = {{MCP-Unity}: {Protocol-Driven} Framework for Interactive {3D} Authoring},
  year      = {2025},
  isbn      = {9798400721366},
  publisher = {Association for Computing Machinery},
  address   = {New York, NY, USA},
  url       = {https://doi.org/10.1145/3757376.3771417},
  doi       = {10.1145/3757376.3771417},
  series    = {SA Technical Communications '25}
}
```

## Disclaimer

This project is a free and open-source tool for the Unity Editor, and is not affiliated with Unity Technologies.

---

**License:** MIT — see [LICENSE](LICENSE).
