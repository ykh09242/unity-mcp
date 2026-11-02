# Renderer routing and RenderGraph diagnostics

`manage_render_pipeline(action="pipeline_get_diagnostics")` is experimental and read-only.
It reports the active pipeline and observed URP package version, selected renderer asset,
optional camera route, graph/compatibility setting when its API is available, and renderer
feature enabled state. The legacy `manage_graphics` route supports the same action.

Use `renderer_index` to choose an entry in the active URP asset's renderer list. Omit it
or pass `-1` to use `m_DefaultRendererIndex`; `index` still means a **feature** index.
MCP and native commands also accept `rendererIndex`. An explicit `target` selects a renderer data
asset by asset path, unique exact name in the active pipeline renderer list, or instance ID. Diagnostics additionally accepts
a Camera or camera GameObject name/ID and observes its renderer override. Do not combine
`target` and `renderer_index`. Invalid explicit selections return an error instead of
silently inspecting or modifying the default renderer.

Examples:

```text
unity-mcp graphics feature-list --renderer-index 1
unity-mcp graphics feature-toggle --renderer-index 1 --index 0 --inactive
unity-mcp graphics feature-list --target Assets/Rendering/SecondaryRenderer.asset
unity-mcp graphics pipeline-diagnostics --target MainCamera
```

The response identifies the resolved renderer asset and its pipeline index when applicable.
Camera diagnostics distinguish its configured override from the effective default route
when the override is missing. No camera components, renderer instances, passes, or features
are created to obtain diagnostics; no `Create`, `AddRenderPasses`, rendering or saving is
invoked. Unknown package versions, unavailable settings, and unobservable passes remain
unknown. A discovered `RecordRenderGraph` override is implementation evidence only: it
does not establish that the feature is enqueued, uncullable, correct, or executed. Passes
stored outside direct feature fields may remain unobserved. Use a rendered-frame capture
or the Render Graph Viewer for actual execution evidence.

Unity's [URP 17 / Unity 6 upgrade guide](https://docs.unity3d.com/6000.0/Documentation/Manual/urp/upgrade-guide-unity-6.html)
describes RenderGraph and Compatibility Mode. The installed Unity 6000.0.69f1 package
sources (URP 17.0.4) and the version-specific
[`RenderGraphSettings`](https://github.com/Unity-Technologies/Graphics/blob/6000.0/staging/Packages/com.unity.render-pipelines.universal/Runtime/Settings/RenderGraphSettings.cs)
expose `enableRenderCompatibilityMode`. The corresponding
[`ScriptableRenderPass`](https://github.com/Unity-Technologies/Graphics/blob/6000.0/staging/Packages/com.unity.render-pipelines.universal/Runtime/Passes/ScriptableRenderPass.cs)
base implementation warns that a pass without a `RecordRenderGraph` implementation can
have no effect in RenderGraph mode. These are version-specific observations, not a claim
that unversioned Graphics master represents every installed stable URP package.
