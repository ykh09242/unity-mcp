#if MCP_FOR_UNITY_AI_MODULE
using System;
using System.Collections;
using System.Linq;
using MCPForUnityTests.Editor.Helpers;
using MCPForUnity.Runtime.Helpers;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEditor;
using UnityEngine;
using UnityEngine.AI;
using UnityEngine.SceneManagement;
using UnityEngine.TestTools;

namespace MCPForUnityTests.EditMode.Tools
{
    public class NavigationAuthoringIntegrityTests
    {
        private readonly PrefabTestSceneFixture fixture = new PrefabTestSceneFixture();
        private Scene scene;
        private GameObject root;
        private Component surface;
        private Type surfaceType;
        private Type linkType;
        private NavMeshData bakedData;
        private string sceneAssetRoot;
        private string sceneAssetRootGuid;

        [SetUp]
        public void SetUp()
        {
            sceneAssetRoot = null;
            sceneAssetRootGuid = null;
            fixture.PrepareRunnerBootstrap();
            scene = fixture.Create("__McpNavigation_", Guid.NewGuid().ToString("N"));
            surfaceType = AppDomain.CurrentDomain.GetAssemblies().Select(a => a.GetType("Unity.AI.Navigation.NavMeshSurface")).FirstOrDefault(t => t != null);
            linkType = AppDomain.CurrentDomain.GetAssemblies().Select(a => a.GetType("Unity.AI.Navigation.NavMeshLink")).FirstOrDefault(t => t != null);
            if (surfaceType == null || linkType == null)
                Assert.Ignore("Requires optional com.unity.ai.navigation; base-package absence is tested by capabilities.");
            root = new GameObject("OwnedNavigation");
            surface = root.AddComponent(surfaceType);
            Set(surface, "collectObjects", Enum.Parse(surfaceType.GetProperty("collectObjects").PropertyType, "Children"));
            Set(surface, "useGeometry", NavMeshCollectGeometry.PhysicsColliders);
            Floor("Left", -5);
            Floor("Right", 5);
        }

        [TearDown]
        public void TearDown()
        {
            if (surface != null)
            {
                bakedData = surfaceType.GetProperty("navMeshData").GetValue(surface) as NavMeshData;
                surfaceType.GetMethod("RemoveData").Invoke(surface, null);
            }
            if (root != null)
                UnityEngine.Object.DestroyImmediate(root);
            if (bakedData != null && !AssetDatabase.Contains(bakedData))
                UnityEngine.Object.DestroyImmediate(bakedData);
            fixture.Close();
            if (!string.IsNullOrEmpty(sceneAssetRootGuid))
            {
                StringAssert.StartsWith("Assets/__McpNavigationScenes_", sceneAssetRoot);
                Assert.AreEqual(sceneAssetRootGuid, AssetDatabase.AssetPathToGUID(sceneAssetRoot));
                Assert.IsTrue(AssetDatabase.DeleteAsset(sceneAssetRoot));
            }
            fixture.RestoreRunnerBootstrap();
        }

        [UnityTest]
        public IEnumerator DisconnectedPathThenExplicitLinkAndBakeCompletes()
        {
            yield return Bake();
            JObject disconnected = Query();
            Assert.IsTrue((bool)disconnected["success"], disconnected.ToString());
            Assert.AreNotEqual("PathComplete", (string)disconnected["data"]["path_status"]);
            JObject surfaceRead = Send("read", "surface", surface.GetInstanceIDCompat());
            Assert.IsTrue((bool)surfaceRead["data"]["has_baked_data"]);
            Assert.IsFalse((bool)surfaceRead["data"]["data_agent_type_available"]);
            Assert.AreEqual(JTokenType.Null, surfaceRead["data"]["data_agent_type_id"].Type);
            Assert.IsFalse((bool)disconnected["data"]["surfaces"][0]["baked_agent_type_available"]);
            GameObject bridge = new GameObject("OwnedBridge");
            bridge.transform.SetParent(root.transform);
            Component link = bridge.AddComponent(linkType);
            JObject configured = Send(
                "configure",
                "link",
                link.GetInstanceIDCompat(),
                new JObject
                {
                    ["startPoint"] = new JArray(-3, 0, 0),
                    ["endPoint"] = new JArray(3, 0, 0),
                    ["width"] = 2,
                    ["bidirectional"] = true,
                    ["agentTypeID"] = NavMesh.GetSettingsByIndex(0).agentTypeID,
                }
            );
            Assert.IsTrue((bool)configured["success"], configured.ToString());
            yield return Bake();
            JObject connected = Query();
            Assert.AreEqual("PathComplete", (string)connected["data"]["path_status"], connected.ToString());
            Assert.IsTrue(JToken.DeepEquals(SceneHandle(scene), connected["data"]["scene_handle"]));
        }

        [UnityTest]
        public IEnumerator AgentAreaAndForeignSceneAreExplicitlyDiagnosed()
        {
            yield return Bake();
            JObject badType = Query(new JObject { ["agent_type_id"] = int.MaxValue });
            StringAssert.Contains("agent_type", badType.ToString());
            Assert.IsFalse((bool)badType["success"]);
            JObject area = Query(new JObject { ["area_mask"] = 0 });
            Assert.AreEqual("PathInvalid", (string)area["data"]["path_status"]);
            StringAssert.Contains("area_mask_excludes_all", area.ToString());
            JObject wrongScene = Send("read", "surface", surface.GetInstanceIDCompat(), null, new JValue(-1L));
            Assert.IsFalse((bool)wrongScene["success"]);
            StringAssert.Contains("scene", wrongScene.ToString());
        }

        [UnityTest]
        public IEnumerator QueuedCancellationAndChangedSurfaceDoNotBake()
        {
            JObject request = Send("bake", "surface", surface.GetInstanceIDCompat());
            string id = (string)request["data"]["job_id"];
            JObject cancelled = Send("cancel_bake", extra: new JObject { ["job_id"] = id });
            Assert.AreEqual("cancelled", (string)cancelled["data"]["state"], cancelled.ToString());
            yield return null;
            Assert.IsNull(surfaceType.GetProperty("navMeshData").GetValue(surface));
            request = Send("bake", "surface", surface.GetInstanceIDCompat());
            id = (string)request["data"]["job_id"];
            Set(surface, "defaultArea", 2);
            JObject status = null;
            for (int i = 0; i < 120; i++)
            {
                yield return null;
                status = Send("bake_status", extra: new JObject { ["job_id"] = id });
                if ((string)status["data"]?["state"] != "queued")
                    break;
            }
            Assert.AreEqual("failed", (string)status?["data"]?["state"], status?.ToString());
            StringAssert.Contains("configuration changed", status.ToString());
            Assert.IsNull(surfaceType.GetProperty("navMeshData").GetValue(surface));
        }

        [Test]
        public void ExplicitAgentCreationConfigurationAndPagedReadPreserveScope()
        {
            GameObject child = new GameObject("OwnedAgent");
            child.transform.SetParent(root.transform);
            child.SetActive(false);
            JObject added = Send("add", "agent", child.GetInstanceIDCompat());
            Assert.IsTrue((bool)added["success"], added.ToString());
            int id = (int)added["data"]["component_id"];
            JObject configured = Send(
                "configure",
                "agent",
                id,
                new JObject
                {
                    ["speed"] = 7,
                    ["areaMask"] = 0,
                    ["autoBraking"] = false,
                }
            );
            Assert.IsTrue((bool)configured["success"], configured.ToString());
            JObject read = Send("read", "agent", id);
            Assert.AreEqual(7, (float)read["data"]["properties"]["speed"]);
            Assert.IsFalse((bool)read["data"]["properties"]["autoBraking"]);
            JObject list = Send("list", "agent", extra: new JObject { ["offset"] = 0, ["limit"] = 1 });
            Assert.AreEqual(1, (int)list["data"]["total"]);
            Assert.AreEqual(id, (int)list["data"]["items"][0]["component_id"]);
        }

        [UnityTest]
        public IEnumerator ForeignSceneSurfaceCausesScopeRefusal()
        {
            // Additive authoring requires a saved owned anchor in the runner's initial single-scene mode.
            string folder = "__McpNavigationScenes_" + Guid.NewGuid().ToString("N");
            sceneAssetRoot = "Assets/" + folder;
            Assert.IsFalse(AssetDatabase.IsValidFolder(sceneAssetRoot));
            sceneAssetRootGuid = AssetDatabase.CreateFolder("Assets", folder);
            Assert.IsNotEmpty(sceneAssetRootGuid);
            Assert.IsTrue(UnityEditor.SceneManagement.EditorSceneManager.SaveScene(scene, sceneAssetRoot + "/" + scene.name + ".unity"));
            yield return Bake();
            var foreignFixture = new PrefabTestSceneFixture();
            Scene foreign = foreignFixture.Create("__McpNavigationForeign_", Guid.NewGuid().ToString("N"));
            GameObject other = null;
            NavMeshData data = null;
            try
            {
                other = new GameObject("OwnedForeignSurface");
                SceneManager.MoveGameObjectToScene(other, foreign);
                Component second = other.AddComponent(surfaceType);
                Set(second, "collectObjects", Enum.Parse(surfaceType.GetProperty("collectObjects").PropertyType, "Children"));
                Set(second, "useGeometry", NavMeshCollectGeometry.PhysicsColliders);
                GameObject floor = GameObject.CreatePrimitive(PrimitiveType.Cube);
                floor.transform.SetParent(other.transform);
                floor.transform.position = new Vector3(100, -0.5f, 0);
                floor.transform.localScale = new Vector3(8, 1, 8);
                surfaceType.GetMethod("BuildNavMesh").Invoke(second, null);
                data = surfaceType.GetProperty("navMeshData").GetValue(second) as NavMeshData;
                Assert.IsNotNull(data);
                // An edited authoring ID cannot prove the type of already-baked data.
                Set(second, "agentTypeID", int.MaxValue);
                JObject refused = Query();
                Assert.IsFalse((bool)refused["success"], refused.ToString());
                StringAssert.Contains("foreign_scene_navmesh", refused.ToString());
                Assert.IsTrue(JToken.DeepEquals(SceneHandle(scene), refused["data"]["scene_handle"]));
            }
            finally
            {
                if (other != null)
                    UnityEngine.Object.DestroyImmediate(other);
                if (data != null && !AssetDatabase.Contains(data))
                    UnityEngine.Object.DestroyImmediate(data);
                foreignFixture.Close();
            }
        }

        [Test]
        public void ConfigureUsesUndoAndRejectsWholeInvalidPatch()
        {
            NavMeshObstacle obstacle = root.AddComponent<NavMeshObstacle>();
            float original = obstacle.radius;
            Scene originalScene = SceneManager.GetActiveScene();
            JObject invalid = Send("configure", "obstacle", obstacle.GetInstanceIDCompat(), new JObject { ["radius"] = 3, ["arbitrary"] = 4 });
            Assert.IsFalse((bool)invalid["success"]);
            Assert.AreEqual(original, obstacle.radius);
            JObject result = Send("configure", "obstacle", obstacle.GetInstanceIDCompat(), new JObject { ["radius"] = 3 });
            Assert.IsTrue((bool)result["success"], result.ToString());
            Assert.AreEqual(3, obstacle.radius);
            Assert.AreEqual(originalScene, SceneManager.GetActiveScene());
            Undo.PerformUndo();
            Assert.AreEqual(original, obstacle.radius);
        }

        [Test]
        public void SurfaceBakeRefusesUnscopedAllCollection()
        {
            Set(surface, "collectObjects", Enum.Parse(surfaceType.GetProperty("collectObjects").PropertyType, "All"));
            JObject result = Send("bake", "surface", surface.GetInstanceIDCompat());
            Assert.IsFalse((bool)result["success"]);
            StringAssert.Contains("Children", result.ToString());
        }

        [Test]
        public void CapabilitiesReportsOptionalPackageVersionAndNativeLimitations()
        {
            JObject result = Send("capabilities");
            Assert.IsTrue((bool)result["success"], result.ToString());
            Assert.IsTrue((bool)result["data"]["package_available"]);
            Assert.IsNotEmpty((string)result["data"]["package_version"]);
            Assert.IsFalse((bool)result["data"]["native_bake_interruptible"]);
        }

        private IEnumerator Bake()
        {
            JObject request = Send("bake", "surface", surface.GetInstanceIDCompat());
            Assert.IsTrue((bool)request["success"], request.ToString());
            Assert.AreEqual("queued", (string)request["data"]["state"]);
            string id = (string)request["data"]["job_id"];
            JObject status = null;
            int editorUpdates = 0;
            void ObserveUpdate() => editorUpdates++;
            EditorApplication.update += ObserveUpdate;
            try
            {
                for (int i = 0; i < 120; i++)
                {
                    yield return null;
                    status = Send("bake_status", extra: new JObject { ["job_id"] = id });
                    if ((string)status["data"]?["state"] != "queued")
                        break;
                }
                Assert.AreEqual("completed", (string)status?["data"]?["state"], "Observed editor updates: " + editorUpdates + ". " + status);
            }
            finally
            {
                EditorApplication.update -= ObserveUpdate;
            }
        }

        private JObject Query(JObject extra = null)
        {
            JObject p = new JObject
            {
                ["start"] = new JArray(-5, 0, 0),
                ["end"] = new JArray(5, 0, 0),
                ["agent_type_id"] = NavMesh.GetSettingsByIndex(0).agentTypeID,
                ["area_mask"] = NavMesh.AllAreas,
                ["max_distance"] = 2,
            };
            if (extra != null)
                p.Merge(extra, new JsonMergeSettings { MergeArrayHandling = MergeArrayHandling.Replace });
            return Send("calculate_path", extra: p);
        }

        private JObject Send(string action, string kind = null, int? target = null, JObject properties = null, JToken sceneHandle = null, JObject extra = null)
        {
            Type handler = typeof(MCPForUnity.Editor.Tools.ManagePackages).Assembly.GetType("MCPForUnity.Editor.Tools.Navigation.ManageNavigation");
            Assert.IsNotNull(handler, "SPEC13 requires its dedicated public native navigation handler.");
            JObject p = new JObject { ["action"] = action, ["scene_handle"] = sceneHandle ?? SceneHandle(scene) };
            if (kind != null)
                p["kind"] = kind;
            if (target.HasValue)
                p["target"] = target.Value;
            if (properties != null)
                p["properties"] = properties;
            if (extra != null)
                p.Merge(extra);
            return JObject.FromObject(handler.GetMethod("HandleCommand").Invoke(null, new object[] { p }));
        }

        private static JValue SceneHandle(Scene selected)
        {
#if UNITY_6000_4_OR_NEWER
            return new JValue(selected.handle.GetRawData());
#else
            return new JValue((int)selected.handle);
#endif
        }

        private static void Set(Component component, string property, object value) => component.GetType().GetProperty(property).SetValue(component, value);

        private void Floor(string name, float x)
        {
            GameObject floor = GameObject.CreatePrimitive(PrimitiveType.Cube);
            floor.name = name;
            floor.transform.SetParent(root.transform);
            floor.transform.position = new Vector3(x, -0.5f, 0);
            floor.transform.localScale = new Vector3(6, 1, 8);
        }
    }
}
#endif
