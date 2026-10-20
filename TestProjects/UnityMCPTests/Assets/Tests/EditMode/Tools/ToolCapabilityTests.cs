using System;
using System.Collections.Generic;
using System.Linq;
using System.Reflection;
using System.Threading;
using System.Threading.Tasks;
using MCPForUnity.Editor.Constants;
using MCPForUnity.Editor.Services;
using MCPForUnity.Editor.Tools;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEditor;

namespace MCPForUnityTests.EditMode.Tools
{
    [Parallelizable(ParallelScope.None)]
    public class ToolCapabilityTests
    {
        [TestCase("scene_inspect", "get_active", "valid")]
        [TestCase("scene_inspect", "create", "rejected")]
        [TestCase("scene_lifecycle", "load", "valid")]
        [TestCase("scene_lifecycle", "screenshot", "rejected")]
        [TestCase("scene_lifecycle", "modify_build_settings", "rejected")]
        [TestCase("gameobject_create", "create", "valid")]
        [TestCase("gameobject_create", "delete", "rejected")]
        [TestCase("gameobject_placement", "placement", "valid")]
        public void BatchPreview_UsesFocusedActionGuardWithoutExecuting(string tool, string action, string status)
        {
            CommandRegistry.Initialize();
            var result = CommandRegistry.ValidateBatchAction(tool, new JObject { ["action"] = action });
            Assert.That(result.Status, Is.EqualTo(status));
        }

        [Test]
        public void BatchPreview_RejectsNonStringActions()
        {
            CommandRegistry.Initialize();
            foreach (JToken action in new JToken[] { JValue.CreateNull(), new JValue(1), new JObject(), new JArray("get_active") })
            {
                var result = CommandRegistry.ValidateBatchAction("scene_inspect", new JObject { ["action"] = action });
                Assert.That(result.Status, Is.EqualTo("rejected"));
            }
        }

        [Test]
        public void Discovery_SeparatesFeatureAndCompatibilityEntries()
        {
            var discovery = new ToolDiscoveryService();
            var query = discovery.GetToolMetadata("query_physics");
            Assert.IsNotNull(query, "The physics query capability must be independently discoverable.");
            Assert.That(query.Group, Is.EqualTo("physics"));
            Assert.That(query.AutoRegister, Is.False, "The server owns the focused input schema.");
            Assert.That(query.EnabledByDefault, Is.False);
            Assert.That(discovery.GetToolMetadata("manage_physics").Group, Is.EqualTo("legacy"));
            Assert.That(discovery.GetToolMetadata("scene_inspect").EnabledByDefault, Is.True);
        }

        [TestCase("query_physics", "apply_force")]
        [TestCase("capture_camera", "create_camera")]
        [TestCase("manage_render_fog", "bake_start")]
        [TestCase("prefab_inspect", "modify_contents")]
        [TestCase("scene_inspect", "create")]
        [TestCase("asset_inspect", "delete")]
        [TestCase("ui_inspect", "delete")]
        [TestCase("gameobject_create", "delete")]
        public async Task NativeDispatch_RejectsActionsOutsideTheFeature(string tool, string action)
        {
            var result = JObject.FromObject(await CommandRegistry.InvokeCommandAsync(tool, new JObject { ["action"] = action }));
            Assert.That((bool?)result["success"], Is.False, result.ToString());
            Assert.That((string)result["error"], Is.EqualTo("action_outside_capability"), result.ToString());
        }

        [Test]
        public async Task NativeDispatch_RunsReadOnlyPhysicsQuery()
        {
            var result = JObject.FromObject(
                await CommandRegistry.InvokeCommandAsync(
                    "query_physics",
                    new JObject
                    {
                        ["action"] = "raycast",
                        ["origin"] = new JArray(10000, 10000, 10000),
                        ["direction"] = new JArray(0, 1, 0),
                        ["max_distance"] = 1,
                    }
                )
            );
            Assert.That((bool?)result["success"], Is.True, result.ToString());
        }

        [TestCase("prefab_inspect", "get_hierarchy")]
        [TestCase("manage_rendering_stats", "stats_get")]
        public void NativeDispatch_PreservesCooperativeCancellation(string tool, string action)
        {
            var metadata = new ToolDiscoveryService().GetToolMetadata(tool);
            Assert.IsNotNull(metadata);
            var type = typeof(McpForUnityToolAttribute).Assembly.GetType(metadata.Namespace + "." + metadata.ClassName);
            var method = type.GetMethod("HandleCommand", new[] { typeof(JObject), typeof(CancellationToken) });
            Assert.IsNotNull(method, "The capability must expose a cooperative handler.");
            using var cancellation = new CancellationTokenSource();
            cancellation.Cancel();
            var failure = Assert.Throws<TargetInvocationException>(() =>
                method.Invoke(
                    null,
                    new object[]
                    {
                        new JObject { ["action"] = action },
                        cancellation.Token,
                    }
                )
            );
            Assert.That(failure.InnerException, Is.InstanceOf<OperationCanceledException>());
        }

        [TestCase(false, false)]
        [TestCase(true, false)]
        [TestCase(true, true)]
        public void Discovery_PreservesPreviousChoicesAndUsesFocusedFreshDefaults(bool hasLegacyChoice, bool legacyEnabled)
        {
            var names = typeof(McpForUnityToolAttribute)
                .Assembly.GetTypes()
                .Where(type => type.Namespace == "MCPForUnity.Editor.Tools.Capabilities")
                .Select(type => type.GetCustomAttribute<McpForUnityToolAttribute>()?.Name)
                .Where(name => name != null)
                .Concat(
                    new[]
                    {
                        "manage_physics",
                        "manage_graphics",
                        "manage_camera",
                        "manage_gameobject",
                        "manage_prefabs",
                        "manage_scene",
                        "manage_asset",
                        "manage_ui",
                    }
                )
                .ToArray();
            var saved = new Dictionary<string, (bool exists, bool value)>();
            foreach (string name in names)
            {
                string key = EditorPrefKeys.ToolEnabledPrefix + name;
                saved[key] = (EditorPrefs.HasKey(key), EditorPrefs.GetBool(key, false));
            }
            try
            {
                foreach (string key in saved.Keys)
                    EditorPrefs.DeleteKey(key);
                if (hasLegacyChoice)
                    EditorPrefs.SetBool(EditorPrefKeys.ToolEnabledPrefix + "manage_scene", legacyEnabled);
                var discovery = new ToolDiscoveryService();
                Assert.That(discovery.IsToolEnabled("scene_inspect"), Is.EqualTo(!hasLegacyChoice));
                Assert.That(discovery.IsToolEnabled("scene_lifecycle"), Is.EqualTo(!hasLegacyChoice));
                Assert.That(discovery.IsToolEnabled("scene_reference_diagnostics"), Is.False);
                Assert.That(discovery.IsToolEnabled("manage_scene"), Is.EqualTo(hasLegacyChoice && legacyEnabled));
                Assert.That(discovery.GetToolMetadata("scene_inspect").DisplayName, Is.EqualTo("Inspect Scenes & Hierarchies"));
                discovery.InvalidateCache();
                Assert.That(discovery.IsToolEnabled("scene_inspect"), Is.EqualTo(!hasLegacyChoice), "Rediscovery must preserve the initialized choice.");
            }
            finally
            {
                foreach (var entry in saved)
                {
                    if (entry.Value.exists)
                        EditorPrefs.SetBool(entry.Key, entry.Value.value);
                    else
                        EditorPrefs.DeleteKey(entry.Key);
                }
            }
        }
    }
}
