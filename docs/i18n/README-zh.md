<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="../images/logo-header-dark.png">
    <img alt="MCP for Unity" src="../images/logo-header-light.png" width="400">
  </picture>
</p>

<div align="center">

[English](../../README.md) <img src="../images/connector.svg" alt="↔" height="14"> [简体中文](README-zh.md) &nbsp;&nbsp;&nbsp;|&nbsp;&nbsp;&nbsp; [文档](https://ykh09242.github.io/unity-mcp/)

## Unity MCP (ykh09242)

由 [ykh09242](https://github.com/ykh09242) 维护的 Git-only fork，源自 [CoplayDev/unity-mcp](https://github.com/CoplayDev/unity-mcp)。保留原始 MIT 版权、作者和论文引用。此 fork 不是 Coplay/Aura 服务，也未声明 Asset Store、OpenUPM 或 PyPI 发布。

</div>

<p align="center">MCP for Unity 通过 <a href="https://modelcontextprotocol.io/introduction">Model Context Protocol</a> 把 Claude、Cursor、VS Code、本地大模型等 AI 助手接入 Unity 编辑器，让它们直接帮你管理资源、搭场景、写脚本、跑测试，把开发流程里的重复活儿都包了。</p>

<p align="center">
  <img alt="MCP for Unity building a scene" src="../images/building_scene.gif">
</p>

---

<details>
<summary><strong>上游发布历史</strong></summary>

以下版本属于 CoplayDev 上游，不是此 fork 的发布版本。

* **[v10.0.0](https://github.com/CoplayDev/unity-mcp/releases/tag/v10.0.0)**（2026-06-30）
* **[v9.7.3](https://github.com/CoplayDev/unity-mcp/releases/tag/v9.7.3)**（2026-06-15）
* **[v9.7.1](https://github.com/CoplayDev/unity-mcp/releases/tag/v9.7.1)**（2026-05-24）
* **[v9.7.0](https://github.com/CoplayDev/unity-mcp/releases/tag/v9.7.0)**（2026-05-22）
* **[v9.6.8](https://github.com/CoplayDev/unity-mcp/releases/tag/v9.6.8)**（2026-04-27）

完整上游历史见 [发布说明](../../website/docs/releases.md)。

</details>

---

## 它能做什么

通过 MCP 客户端操作 Unity 编辑器：查看场景、创建 GameObject、管理资源、编辑脚本、运行测试、分析性能和构建。工具是否可用取决于编辑器开关、依赖和明确授权；当前工具清单以生成的参考文档为准。此 fork 改进了继承的工具实现，不代表这些工具类别全部由 fork 新增。

**[查看完整工具目录 →](../../website/docs/reference/tools/index.md)**

---

## 快速开始

**从上游迁移：** 添加此 fork 前，请先在 Package Manager 中移除原来的 `com.coplaydev.unity-mcp`。两者保留相同的程序集名称和资源 GUID，不能同时安装。安装后重新配置 MCP 客户端，以使用 fork 固定的服务器来源。

**环境要求：** Unity **2021.3 LTS → 6.x** · Python **3.10+**（用 [`uv`](https://docs.astral.sh/uv/) 管理）。兼容**任意 MCP 客户端**——Claude Desktop 与 Claude Code、Cursor、VS Code、Windsurf、Cline、Gemini CLI 等等。

1. **安装** —— 在 Unity 里打开 Package Manager，从 git URL 添加：
   `https://github.com/ykh09242/unity-mcp.git?path=/MCPForUnity#ykh09242-v1.1.3`
2. **连接** —— 打开 `Window > Unity MCP (ykh09242)`。使用本地 HTTP 时，先启动服务器，再配置客户端并连接 Unity bridge。
3. **验证** —— 读取 `mcpforunity://instances`，选择目标编辑器，先确认项目和场景，再尝试一个小改动。

本地 HTTP 每次启动都会生成新 token；重启服务器后需重新配置并连接 HTTP MCP 客户端。现代无会话调用通过工具参数 `unity_instance` 和资源元数据 `_meta.unity_instance` 选择目标；远程托管必须显式指定目标。脚本、代码执行、菜单、包、构建和批处理等高影响操作需要编辑器中的明确授权。

当前英文操作指南：[安装与连接](../../website/docs/getting-started/install.md)、[迁移到 fork](../../website/docs/getting-started/migrate.md)、[安全与授权](../../website/docs/guides/security.md)、[故障排查](../../website/docs/guides/troubleshooting.md)。完整运行验证的限制见 [fork 1.1.3 发布说明](https://github.com/ykh09242/unity-mcp/releases/tag/ykh09242-v1.1.3)。

此 fork 从稳定版本 `1.0.0` 开始独立管理版本，不沿用上游的 `10.3.x`。`ykh09242-v1.1.3` 是当前稳定发布标签，前缀用于区分上游历史标签；如需不可变安装，请使用完整 commit SHA。`#beta` 仅用于跟踪移动开发分支，分支名称不决定发布是否为测试版。UPM 包名为 `com.ykh09242.unity-mcp`。服务器发行名称为 `ykh09242-unity-mcp-server`，从固定完整 commit 的 GitHub 源码压缩包安装，无需 Git checkout；可执行命令仍为 `mcp-for-unity` 和 `unity-mcp`。下方 stdio 示例中的 `<mcpServerSource>` 必须替换为已安装的 `MCPForUnity/package.json` 中同名字段的固定源码 URL。详见 [服务器指南](../../Server/README.md)。

<details>
<summary><strong>手动配置</strong></summary>

如果自动配置不生效，把下面的内容加到你的 MCP 客户端配置文件里：

**本地 HTTP（使用支持该配置格式的客户端；包内 Claude Desktop 配置器使用 stdio）：**

先启动本地 HTTP 服务器，再把 `<current launch token>` 替换为私有 token 文件中的当前值。每次重启服务器后重新配置客户端。不要把 token 或包含 token 的配置提交到 Git。
```json
{
  "mcpServers": {
    "unityMCP": {
      "url": "http://localhost:8080/mcp",
      "headers": { "X-Unity-MCP-Token": "<current launch token>" }
    }
  }
}
```

**VS Code：**
```json
{
  "servers": {
    "unityMCP": {
      "type": "http",
      "url": "http://localhost:8080/mcp",
      "headers": { "X-Unity-MCP-Token": "<current launch token>" }
    }
  }
}
```

<details>
<summary>Stdio 配置（uvx）</summary>

**macOS/Linux：**
```json
{
  "mcpServers": {
    "unityMCP": {
      "command": "uvx",
      "args": ["--from", "<mcpServerSource>", "mcp-for-unity", "--transport", "stdio"]
    }
  }
}
```

**Windows：**
```json
{
  "mcpServers": {
    "unityMCP": {
      "command": "C:/Users/YOUR_USERNAME/AppData/Local/Microsoft/WinGet/Links/uvx.exe",
      "args": ["--from", "<mcpServerSource>", "mcp-for-unity", "--transport", "stdio"]
    }
  }
}
```
</details>
</details>

---

<details>
<summary><strong>多个 Unity 实例</strong></summary>

MCP for Unity 支持同时开多个 Unity 编辑器实例。想把操作定向到某个实例：

1. 让大模型读一下 `unity_instances` 资源
2. 用 `set_active_instance` 传入 `Name@hash`（比如 `MyProject@abc123`）
3. 之后所有工具调用都会走这个实例
</details>

<details>
<summary><strong>Roslyn 脚本验证（进阶）</strong></summary>

想用能查出未定义命名空间、类型和方法的 **Strict** 验证：

1. 装 [NuGetForUnity](https://github.com/GlitchEnzo/NuGetForUnity)
2. `Window > NuGet Package Manager` → 安装 `Microsoft.CodeAnalysis` v5.0
3. 再装 `SQLitePCLRaw.core` 和 `SQLitePCLRaw.bundle_e_sqlite3` v3.0.2
4. 在 `Player Settings > Scripting Define Symbols` 里加上 `USE_ROSLYN`
5. 重启 Unity

  <details>
  <summary>手动安装 DLL（NuGetForUnity 用不了时）</summary>

  1. 从 [NuGet](https://www.nuget.org/packages/Microsoft.CodeAnalysis.CSharp/) 下载 `Microsoft.CodeAnalysis.CSharp.dll` 及其依赖
  2. 把 DLL 放进 `Assets/Plugins/`
  3. 确认 .NET 兼容性设置正确
  4. 在 Scripting Define Symbols 里加上 `USE_ROSLYN`
  5. 重启 Unity
  </details>
</details>

<details>
<summary><strong>故障排除</strong></summary>

* **Unity Bridge 连不上：** 看一下 `Window > Unity MCP (ykh09242)` 的状态，重启 Unity
* **服务器起不来：** 确认 `uv --version` 能跑，并看看终端报错
* **客户端连不上：** 确认 HTTP 服务在运行，且 URL 和你的配置一致

**详细配置指南：**
* [uv/Python 安装](../../website/docs/guides/uv-setup.md)
* [客户端配置](../../website/docs/guides/client-configurators.md)
* [常见问题](../../website/docs/guides/troubleshooting.md)

还是搞不定？[提个 fork Issue](https://github.com/ykh09242/unity-mcp/issues)。
</details>

<details>
<summary><strong>参与贡献</strong></summary>

开发环境配置见 [开发者指南](../development/README-DEV-zh.md)，自定义工具见 [工具指南](../../website/docs/guides/custom-tools.md)。

1. Fork → 开 issue → 建分支（`feature/your-idea`）→ 改 → 提 PR
</details>

<details>
<summary><strong>遥测与隐私</strong></summary>

此 fork 默认没有遥测目标，不会向 Coplay 发送遥测。只有明确设置有效端点并启用遥测后才会运行。`DISABLE_TELEMETRY=true` 等已有关闭开关继续生效。详见 [遥测文档](../../website/docs/architecture/telemetry.md)。
</details>

---

**许可证：** MIT —— 见 [LICENSE](../../LICENSE) | **需要帮助？** [fork Issues](https://github.com/ykh09242/unity-mcp/issues)

---

<details>
<summary><strong>论文引用</strong></summary>
如果 MCP for Unity 对你的研究有帮助，请引用 Wu 和 Barnett 的原始论文。

```bibtex
@inproceedings{10.1145/3757376.3771417,
author = {Wu, Shutong and Barnett, Justin P.},
title = {MCP-Unity: Protocol-Driven Framework for Interactive 3D Authoring},
year = {2025},
isbn = {9798400721366},
publisher = {Association for Computing Machinery},
address = {New York, NY, USA},
url = {https://doi.org/10.1145/3757376.3771417},
doi = {10.1145/3757376.3771417},
series = {SA Technical Communications '25}
}
```
</details>

## 免责声明

本项目是一个免费开源的 Unity 编辑器工具，与 Unity Technologies 无关。
