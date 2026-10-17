using System;
using System.Collections.Generic;
using System.Linq;
using MCPForUnity.Editor.Helpers;
using Newtonsoft.Json.Linq;
using UnityEngine;
using UnityEngine.SceneManagement;

namespace MCPForUnity.Editor.Tools
{
    /// <summary>Read-only capture with stable semantic identities and explicit evidence limits.</summary>
    [McpForUnityTool("capture_ui_snapshot", Group = "ui")]
    public static class CaptureUISnapshot
    {
        public static object HandleCommand(JObject parameters)
        {
            try
            {
                var p = new ToolParams(parameters ?? throw new ArgumentException("Parameters are required."));
                string framework = p.Get("framework");
                int limit = p.GetInt("max_nodes") ?? 200;
                if (limit < 1 || limit > 1000)
                    throw new ArgumentException("max_nodes must be within 1..1000.");
                var identities = parameters.Property("identity_map") == null ? null : UISnapshotIdentityMap.Parse(parameters["identity_map"], limit);
                var root = ManageUGUI.Resolve(p.GetRaw("target"), true);
                string identity = Identity(root);
                JArray nodes;
                switch (framework)
                {
                    case "ugui":
                        nodes = UguiSnapshotCapture.Capture(root, limit, identities);
                        break;
                    case "uitoolkit":
                        nodes = UitoolkitSnapshotCapture.Capture(root, limit, identities);
                        break;
                    default:
                        throw new ArgumentException("framework must be ugui or uitoolkit.");
                }
                identities?.EnsureAllVisited();
                if (Screen.width < 1 || Screen.height < 1 || Screen.width > 16384 || Screen.height > 16384)
                    throw new ArgumentException("Current Screen resolution is unavailable or outside the snapshot bound.");
                var scenes = new List<string>();
                for (int i = 0; i < SceneManager.sceneCount; i++)
                {
                    var scene = SceneManager.GetSceneAt(i);
                    if (scene.isLoaded)
                        scenes.Add(SceneIdentity(scene));
                }
                if (!scenes.Contains(SceneIdentity(root.scene)))
                    scenes.Add(SceneIdentity(root.scene));
                if (scenes.Count > 32 || scenes.Distinct(StringComparer.Ordinal).Count() != scenes.Count)
                    throw new ArgumentException("Loaded scenes exceed the bound or have ambiguous identities.");
                var snapshot = new JObject
                {
                    ["schema_version"] = identities == null ? 2 : 3,
                    ["framework"] = framework,
                    ["mode"] = Application.isPlaying ? "play" : "edit",
                    ["scenes"] = new JArray(scenes.OrderBy(value => value, StringComparer.Ordinal)),
                    ["resolution"] = new JObject { ["width"] = Screen.width, ["height"] = Screen.height },
                    ["target"] = identity,
                    ["evidence"] = new JObject
                    {
                        ["geometry_space"] = framework == "ugui" ? "world_aabb" : "panel_points",
                        ["visibility_method"] = "render_eligibility",
                        ["interaction_method"] = "component_state",
                        ["occlusion_status"] = framework == "ugui" ? "renderer_cull_only" : "unavailable",
                        ["layout_status"] = "current",
                        ["control_state_method"] = "typed_allowlist_v1",
                        ["input_privacy"] = "recognized_input_ownership_v1",
                    },
                    ["complete"] = true,
                    ["max_nodes"] = limit,
                    ["nodes"] = nodes,
                    ["limitations"] = new JArray(
                        "Visibility is current render eligibility, not pixel visibility or actual occlusion.",
                        "Interactable state does not prove raycast reachability or event delivery.",
                        "Geometry uses current layout; Screen resolution is observed without resizing or forcing layout.",
                        "Unsaved scene identities use the scene name; saved scene paths are preferred for durable comparisons.",
                        "Recognized input contents and owned renderers are omitted before text access; redacted content changes cannot be compared. Unrelated script-copied secrets are not identified.",
                        "Selection indices are current positions, not persistent item identities. Native input inventories may allocate before managed processing bounds are checked.",
                        framework == "ugui"
                            ? "uGUI bounds are world XY axis-aligned bounds, not camera-projected screen rectangles; renderer cull is partial evidence only. Use manage_ugui diagnose_projection for explicit current camera evidence."
                            : "UI Toolkit bounds use panel coordinates; ancestor display, visibility and opacity affect render eligibility, while clipping and overlapping elements remain untested."
                    ),
                };
                if (identities != null)
                {
                    snapshot["evidence"]["identity_method"] = "explicit_path_map_v1";
                    ((JArray)snapshot["limitations"]).Add(
                        "Explicit IDs are caller-maintained current-path associations, stable only under the same effective parent identity; stale logical associations cannot be detected."
                    );
                }
                if (
                    snapshot
                        .Descendants()
                        .OfType<JValue>()
                        .Any(value => value.Type == JTokenType.Float && (double.IsNaN(value.Value<double>()) || double.IsInfinity(value.Value<double>())))
                )
                    throw new ArgumentException("Snapshot contains unavailable non-finite state or geometry.");
                if (System.Text.Encoding.UTF8.GetByteCount(snapshot.ToString()) > 2 * 1024 * 1024)
                    throw new ArgumentException("Snapshot exceeds the 2 MiB artifact bound.");
                return new SuccessResponse("Captured complete semantic UI snapshot.", snapshot);
            }
            catch (Exception exception)
            {
                return new ErrorResponse(exception.Message);
            }
        }

        internal static string SceneIdentity(Scene scene) => string.IsNullOrEmpty(scene.path) ? "unsaved:" + scene.name : scene.path;

        internal static string Identity(GameObject go)
        {
            var names = new Stack<string>();
            for (Transform current = go.transform; current != null; current = current.parent)
            {
                if (names.Count >= 128 || current.name.Length > 1024)
                    throw new ArgumentException("Hierarchy identity exceeds the depth/name bound.");
                var parent = current.parent;
                if (parent == null)
                {
                    var roots = go.scene.GetRootGameObjects();
                    if (roots.Length > 1000 || roots.Count(item => item.name == current.name) != 1)
                        throw new ArgumentException("Hierarchy root identity is ambiguous or exceeds the sibling bound.");
                }
                else
                {
                    if (parent.childCount > 1000)
                        throw new ArgumentException("Hierarchy sibling count exceeds the identity bound.");
                    int matches = 0;
                    for (int i = 0; i < parent.childCount; i++)
                        if (parent.GetChild(i).name == current.name)
                            matches++;
                    if (matches != 1)
                        throw new ArgumentException("Hierarchy contains ambiguous sibling names.");
                }
                names.Push(Uri.EscapeDataString(current.name));
            }
            string value = SceneIdentity(go.scene) + "::" + string.Join("/", names);
            if (value.Length > 4096)
                throw new ArgumentException("Hierarchy identity exceeds 4096 characters.");
            return value;
        }

        internal static JArray Rect(Rect value)
        {
            float[] values = { value.x, value.y, value.width, value.height };
            if (values.Any(item => float.IsNaN(item) || float.IsInfinity(item)))
                throw new ArgumentException("Current UI geometry is unavailable or non-finite.");
            return new JArray(values);
        }
    }
}
