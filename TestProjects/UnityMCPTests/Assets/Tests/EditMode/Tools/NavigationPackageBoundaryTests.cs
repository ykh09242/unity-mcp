using System;
using System.Linq;
using MCPForUnity.Runtime.Helpers;
using MCPForUnityTests.Editor.Helpers;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEngine;
using UnityEngine.SceneManagement;
#if MCP_FOR_UNITY_AI_MODULE
using UnityEditor;
using UnityEngine.AI;
#endif

namespace MCPForUnityTests.EditMode.Tools
{
    public class NavigationPackageBoundaryTests
    {
        private static JValue SceneHandle(Scene selected)
        {
#if UNITY_6000_4_OR_NEWER
            return new JValue(selected.handle.GetRawData());
#else
            return new JValue((int)selected.handle);
#endif
        }

        [Test]
        public void BuiltinAgentDiscoverySuppliesReusableConfigurationWithoutPackageDependency()
        {
#if MCP_FOR_UNITY_AI_MODULE
            var handler = typeof(MCPForUnity.Editor.Tools.ManagePackages)
                .Assembly.GetType("MCPForUnity.Editor.Tools.Navigation.ManageNavigation")
                .GetMethod("HandleCommand");
            int count = NavMesh.GetSettingsCount();
            NavMeshBuildSettings[] settings = Enumerable.Range(0, count).Select(NavMesh.GetSettingsByIndex).ToArray();
            var fixture = new PrefabTestSceneFixture();
            fixture.PrepareRunnerBootstrap();
            Scene scene = fixture.Create("__McpNavigationDiscovery_", Guid.NewGuid().ToString("N"));
            GameObject go = null;
            try
            {
                bool dirty = scene.isDirty;
                int roots = scene.rootCount;
                int undoGroup = Undo.GetCurrentGroup();
                JObject result = JObject.FromObject(handler.Invoke(null, new object[] { new JObject { ["action"] = "capabilities" } }));
                Assert.IsTrue((bool)result["success"], result.ToString());
                JObject data = (JObject)result["data"];
                JArray agents = (JArray)data["agent_types"];
                JArray areas = (JArray)data["areas"];
                Assert.IsNotNull(agents, "Built-in configured agents must be discoverable without AI Navigation.");
                Assert.AreEqual(Math.Min(count, 100), agents.Count);
                Assert.AreEqual(count, (int)data["agent_type_count"]);
                Assert.AreEqual(count > agents.Count, (bool)data["agent_types_truncated"]);
                Assert.IsNotEmpty(agents);
                Assert.IsNotEmpty(areas);
                Assert.LessOrEqual(areas.Count, 32);
                int agentId = (int)agents[0]["agent_type_id"];
                int area = (int)areas[0]["index"];
                Assert.AreEqual(NavMesh.GetSettingsNameFromID(agentId), (string)agents[0]["name"]);
                Assert.AreEqual(NavMesh.GetAreaFromName((string)areas[0]["name"]), area);
                string[] keys = data["supported_properties"]["agent"].Values<string>().ToArray();
                CollectionAssert.Contains(keys, "agentTypeID");
                CollectionAssert.Contains(keys, "areaMask");
                Assert.AreEqual(count, NavMesh.GetSettingsCount());
                CollectionAssert.AreEqual(settings, Enumerable.Range(0, count).Select(NavMesh.GetSettingsByIndex).ToArray());
                Assert.AreEqual(dirty, scene.isDirty);
                Assert.AreEqual(roots, scene.rootCount);
                Assert.AreEqual(undoGroup, Undo.GetCurrentGroup());

                go = new GameObject("OwnedDiscoveredAgent");
                go.SetActive(false);
                JObject request = new JObject
                {
                    ["action"] = "add",
                    ["kind"] = "agent",
                    ["scene_handle"] = SceneHandle(scene),
                    ["target"] = go.GetInstanceIDCompat(),
                };
                JObject added = JObject.FromObject(handler.Invoke(null, new object[] { request }));
                Assert.IsTrue((bool)added["success"], added.ToString());
                request["action"] = "configure";
                request["target"] = added["data"]["component_id"].DeepClone();
                request["properties"] = new JObject { ["agentTypeID"] = agentId, ["areaMask"] = 1 << area };
                JObject configured = JObject.FromObject(handler.Invoke(null, new object[] { request }));
                Assert.IsTrue((bool)configured["success"], configured.ToString());
                Assert.AreEqual(agentId, (int)configured["data"]["properties"]["agentTypeID"]);
                Assert.AreEqual(1 << area, (int)configured["data"]["properties"]["areaMask"]);
            }
            finally
            {
                if (go != null)
                    UnityEngine.Object.DestroyImmediate(go);
                fixture.Close();
                fixture.RestoreRunnerBootstrap();
            }
#else
            Assert.Ignore("Requires the built-in Unity AI module, not the optional AI Navigation package.");
#endif
        }

        [Test]
        public void BasePackageReportsDetectionAndRefusesMissingSurfaceDependency()
        {
            Type handler = typeof(MCPForUnity.Editor.Tools.ManagePackages).Assembly.GetType("MCPForUnity.Editor.Tools.Navigation.ManageNavigation");
            Assert.IsNotNull(handler, "Dedicated navigation handler must compile with and without AI Navigation installed.");
            JObject result = JObject.FromObject(handler.GetMethod("HandleCommand").Invoke(null, new object[] { new JObject { ["action"] = "capabilities" } }));
            Assert.IsTrue((bool)result["success"], result.ToString());
            bool present = AppDomain.CurrentDomain.GetAssemblies().Any(a => a.GetType("Unity.AI.Navigation.NavMeshSurface") != null);
            Assert.AreEqual(present, (bool)result["data"]["package_available"]);
#if MCP_FOR_UNITY_AI_MODULE
            JObject properties = (JObject)result["data"]["supported_properties"];
            Assert.IsNotNull(properties, "Built-in authoring keys must be discoverable without AI Navigation.");
            CollectionAssert.Contains(properties["agent"].Values<string>().ToArray(), "speed");
            CollectionAssert.Contains(properties["obstacle"].Values<string>().ToArray(), "carving");
            if (!present)
                Assert.IsEmpty((JArray)properties["surface"]);
#endif
            if (present)
                return;
            var fixture = new PrefabTestSceneFixture();
            fixture.PrepareRunnerBootstrap();
            Scene scene = fixture.Create("__McpNavigationAbsence_", Guid.NewGuid().ToString("N"));
            GameObject go = new GameObject("OwnedSurfaceRefusal");
            try
            {
                JObject refusal = JObject.FromObject(
                    handler
                        .GetMethod("HandleCommand")
                        .Invoke(
                            null,
                            new object[]
                            {
                                new JObject
                                {
                                    ["action"] = "add",
                                    ["kind"] = "surface",
                                    ["scene_handle"] = SceneHandle(scene),
                                    ["target"] = go.GetInstanceIDCompat(),
                                },
                            }
                        )
                );
                Assert.IsFalse((bool)refusal["success"]);
                StringAssert.Contains(
                    (bool?)result["data"]["module_available"] == false ? "ai_module_unavailable" : "ai_navigation_package_unavailable",
                    refusal.ToString()
                );
                Assert.AreEqual(1, go.GetComponents<Component>().Length, "No component or package may be installed implicitly.");
            }
            finally
            {
                UnityEngine.Object.DestroyImmediate(go);
                fixture.Close();
                fixture.RestoreRunnerBootstrap();
            }
        }
    }
}
