using System;
using System.Collections;
using System.Reflection;
using MCPForUnity.Runtime.Helpers;
using MCPForUnityTests.Editor.Helpers;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;
using UnityEngine.SceneManagement;

namespace MCPForUnityTests.EditMode.Tools
{
    public class NetcodePreflightTests
    {
        private PrefabTestSceneFixture fixture;
        private Scene scene;
        private Scene otherScene;
        private string folder;
        private readonly System.Collections.Generic.List<UnityEngine.Object> temporary = new();

        private static Type Ngo(string name) => Type.GetType("Unity.Netcode." + name + ", Unity.Netcode.Runtime", false);

        private static object Get(object target, string name) =>
            target.GetType().GetField(name)?.GetValue(target) ?? target.GetType().GetProperty(name)?.GetValue(target);

        private static void Set(object target, string name, object value) => target.GetType().GetField(name).SetValue(target, value);

        [SetUp]
        public void SetUp()
        {
            fixture = new PrefabTestSceneFixture();
            fixture.PrepareRunnerBootstrap();
            scene = fixture.Create("__McpNgo_", Guid.NewGuid().ToString("N"));
            folder = "Assets/__McpNgo_" + Guid.NewGuid().ToString("N");
        }

        [TearDown]
        public void TearDown()
        {
            foreach (var item in temporary)
                if (item != null)
                    UnityEngine.Object.DestroyImmediate(item);
            temporary.Clear();
            if (otherScene.IsValid() && otherScene.isLoaded)
                EditorSceneManager.ClosePreviewScene(otherScene);
            otherScene = default;
            fixture.Close();
            fixture.RestoreRunnerBootstrap();
            if (AssetDatabase.IsValidFolder(folder))
                AssetDatabase.DeleteAsset(folder);
        }

        private JObject Call(JObject p)
        {
            Type handler = typeof(MCPForUnity.Editor.Tools.ManagePackages).Assembly.GetType("MCPForUnity.Editor.Tools.Netcode.ManageNetcode");
            Assert.IsNotNull(handler, "Dedicated read-only Netcode preflight handler is required.");
            return JObject.FromObject(handler.GetMethod("HandleCommand").Invoke(null, new object[] { p }));
        }

        private JObject Request()
        {
#if UNITY_6000_4_OR_NEWER
            JValue handle = new JValue(scene.handle.GetRawData());
#else
            JValue handle = new JValue((int)scene.handle);
#endif
            return new JObject { ["action"] = "preflight", ["scene_handle"] = handle };
        }

        private Component Manager(bool transport = true)
        {
            Assert.IsNotNull(Ngo("NetworkManager"), "Install the explicitly selected NGO package for package-present acceptance.");
            var go = new GameObject("OwnedNgoManager");
            SceneManager.MoveGameObjectToScene(go, scene);
            var manager = go.AddComponent(Ngo("NetworkManager"));
            if (Get(manager, "NetworkConfig") == null)
                Set(manager, "NetworkConfig", Activator.CreateInstance(Ngo("NetworkConfig")));
            if (transport)
            {
                Type type = Ngo("Transports.UTP.UnityTransport");
                Assert.IsNotNull(type, "NGO UnityTransport is required for valid-transport fixtures.");
                Set(Get(manager, "NetworkConfig"), "NetworkTransport", go.AddComponent(type));
            }
            else
                Set(Get(manager, "NetworkConfig"), "NetworkTransport", null);
            return manager;
        }

        [Test]
        public void CapabilitiesReportsActualOptionalPackageWithoutStartingNetwork()
        {
            JObject result = Call(new JObject { ["action"] = "capabilities" });
            Assert.IsTrue((bool)result["success"], result.ToString());
            Assert.AreEqual(Ngo("NetworkManager") != null, (bool)result["data"]["package_available"]);
            Assert.AreEqual(true, (bool)result["data"]["read_only"]);
            if (Ngo("NetworkManager") != null)
            {
                var package = UnityEditor.PackageManager.PackageInfo.FindForAssembly(Ngo("NetworkManager").Assembly);
                Assert.AreEqual(package.version, (string)result["data"]["package_version"]);
            }
            if (Ngo("NetworkManager") == null)
            {
                JObject absent = Call(Request());
                Assert.IsFalse((bool)absent["success"]);
                StringAssert.Contains("netcode_package_unavailable", absent.ToString());
            }
        }

        [Test]
        public void EmptySceneReportsMissingManager()
        {
            Assert.IsNotNull(Ngo("NetworkManager"), "Package-present acceptance requires NGO.");
            JObject result = Call(Request());
            Assert.IsTrue((bool)result["success"], result.ToString());
            Assert.IsFalse((bool)result["data"]["ready"]);
            StringAssert.Contains("manager_missing", result["data"]["diagnostics"].ToString());
        }

        [Test]
        public void MissingTransportReportsInvalidConfigurationWithoutMutation()
        {
            Component manager = Manager(false);
            string before = EditorJsonUtility.ToJson(manager);
            JObject result = Call(Request());
            Assert.IsTrue((bool)result["success"], result.ToString());
            Assert.IsFalse((bool)result["data"]["ready"]);
            StringAssert.Contains("transport_missing", result["data"]["diagnostics"].ToString());
            Assert.AreEqual(before, EditorJsonUtility.ToJson(manager));
            Assert.AreEqual(false, Get(manager, "IsListening"));
        }

        [Test]
        public void ValidLocalConfigurationReportsReadyWithoutStartingSession()
        {
            Component manager = Manager();
            string before = EditorJsonUtility.ToJson(manager);
            JObject result = Call(Request());
            Assert.IsTrue((bool)result["success"], result.ToString());
            Assert.IsTrue((bool)result["data"]["ready"], result.ToString());
            Assert.AreEqual(before, EditorJsonUtility.ToJson(manager));
            Assert.AreEqual(false, Get(manager, "IsListening"));
        }

        [Test]
        public void CrossSceneTransportIsReportedAsInvalid()
        {
            Component manager = Manager();
            var other = new GameObject("OwnedForeignTransport");
            otherScene = EditorSceneManager.NewPreviewScene();
            Assert.IsTrue(otherScene.IsValid() && otherScene.isLoaded && EditorSceneManager.IsPreviewScene(otherScene));
            Assert.AreNotEqual(scene, otherScene, "The transport must be a real object in a different scene.");
            SceneManager.MoveGameObjectToScene(other, otherScene);
            Assert.AreEqual(otherScene, other.scene);
            temporary.Add(other);
            Set(Get(manager, "NetworkConfig"), "NetworkTransport", other.AddComponent(Ngo("Transports.UTP.UnityTransport")));
            JObject result = Call(Request());
            Assert.IsTrue((bool)result["success"], result.ToString());
            Assert.IsFalse((bool)result["data"]["ready"]);
            StringAssert.Contains("transport_scene_mismatch", result["data"]["diagnostics"].ToString());
        }

        private GameObject CreateNetworkPrefab(string name)
        {
            if (!AssetDatabase.IsValidFolder(folder))
                AssetDatabase.CreateFolder("Assets", folder.Substring("Assets/".Length));
            var source = new GameObject(name);
            source.AddComponent(Ngo("NetworkObject"));
            try
            {
                return PrefabUtility.SaveAsPrefabAsset(source, folder + "/" + name + ".prefab");
            }
            finally
            {
                UnityEngine.Object.DestroyImmediate(source);
            }
        }

        [TestCase("None")]
        [TestCase("Prefab")]
        [TestCase("Hash")]
        public void DuplicateSerializedPrefabRegistrationIsReportedAndPreserved(string mode)
        {
            Component manager = Manager();
            GameObject prefab = CreateNetworkPrefab("Owned");
            uint prefabHash;
            using (var serialized = new SerializedObject(prefab.GetComponent(Ngo("NetworkObject"))))
                prefabHash = (uint)serialized.FindProperty("GlobalObjectIdHash").longValue;
            Assert.AreNotEqual(0u, prefabHash);
            object entry = Activator.CreateInstance(Ngo("NetworkPrefab"));
            Set(entry, "Prefab", prefab);
            Set(entry, "Override", Enum.Parse(Ngo("NetworkPrefabOverride"), mode));
            Set(entry, "SourceHashToOverride", prefabHash);
            Set(entry, "SourcePrefabToOverride", prefab);
            Set(entry, "OverridingTargetPrefab", prefab);
            object config = Get(manager, "NetworkConfig");
            UnityEngine.Object owner = manager;
            string location;
            if (Get(config, "Prefabs") is object prefabs)
            {
                var list = ScriptableObject.CreateInstance(Ngo("NetworkPrefabsList"));
                temporary.Add(list);
                MethodInfo add = list.GetType().GetMethod("Add");
                add.Invoke(list, new[] { entry });
                add.Invoke(list, new[] { entry });
                IList lists = (IList)Get(prefabs, "NetworkPrefabsLists");
                lists.Clear();
                lists.Add(list);
                owner = list;
                location = "NetworkPrefabsLists[0]";
            }
            else
            {
                var entries = (IList)config.GetType().GetField("NetworkPrefabs", BindingFlags.Instance | BindingFlags.NonPublic).GetValue(config);
                entries.Add(entry);
                entries.Add(entry);
                location = "NetworkPrefabs";
            }
            string before = EditorJsonUtility.ToJson(owner);
            JObject result = Call(Request());
            Assert.IsTrue((bool)result["success"], result.ToString());
            Assert.IsFalse((bool)result["data"]["ready"]);
            StringAssert.Contains("duplicate_prefab", result["data"]["diagnostics"].ToString());
            Assert.AreEqual(2, (int)result["data"]["prefab_registration_count"]);
            var row = result["data"]["managers"][0]["prefabs"][1];
            Assert.AreEqual(prefabHash, (uint)row["source_hash"]);
            Assert.AreEqual(mode, (string)row["override"]);
            Assert.AreEqual(location + "[1]", (string)row["location"]);
            JObject limited = Request();
            limited["max_prefabs"] = 1;
            JObject incomplete = Call(limited);
            Assert.IsFalse((bool)incomplete["success"], incomplete.ToString());
            StringAssert.Contains("preflight_budget_exceeded", incomplete.ToString());
            Assert.AreEqual(before, EditorJsonUtility.ToJson(owner), "Raw duplicate registrations must not be cleaned up by inspection.");
        }

        private static FieldInfo RegisterInline(object config, object entry)
        {
            if (Get(config, "Prefabs") is object prefabs)
                ((IList)Get(prefabs, "NetworkPrefabsLists")).Clear();
            FieldInfo field = config.GetType().GetField("NetworkPrefabs", BindingFlags.Instance | BindingFlags.NonPublic)
                ?? config.GetType().GetField("OldPrefabList", BindingFlags.Instance | BindingFlags.NonPublic);
            Assert.IsNotNull(field);
            var entries = (IList)Activator.CreateInstance(field.FieldType);
            entries.Add(entry);
            field.SetValue(config, entries);
            return field;
        }

        [TestCase(true)]
        [TestCase(false)]
        public void InlinePrefabOverrideUsesThePackageSourceRuleWithoutNormalizingIt(bool hasDefault)
        {
            Component manager = Manager();
            object config = Get(manager, "NetworkConfig");
            GameObject target = CreateNetworkPrefab("Target");
            GameObject source = hasDefault ? CreateNetworkPrefab("Source") : null;
            object entry = Activator.CreateInstance(Ngo("NetworkPrefab"));
            Set(entry, "Override", Enum.Parse(Ngo("NetworkPrefabOverride"), "Prefab"));
            Set(entry, "Prefab", source);
            Set(entry, "OverridingTargetPrefab", target);
            FieldInfo field = RegisterInline(config, entry);
            string before = EditorJsonUtility.ToJson(manager);
            JObject result = Call(Request());
            Assert.IsTrue((bool)result["success"], result.ToString());
            bool ready = hasDefault && field.Name == "OldPrefabList";
            Assert.AreEqual(ready, (bool)result["data"]["ready"], result.ToString());
            Assert.AreEqual(before, EditorJsonUtility.ToJson(manager));
            Assert.IsNull(Get(entry, "SourcePrefabToOverride"));
            if (ready)
            {
                var row = result["data"]["managers"][0]["prefabs"][0];
                Assert.AreEqual(AssetDatabase.GetAssetPath(source), (string)row["source_path"]);
                using (var serialized = new SerializedObject(source.GetComponent(Ngo("NetworkObject"))))
                    Assert.AreEqual((uint)serialized.FindProperty("GlobalObjectIdHash").longValue, (uint)row["source_hash"]);
            }
            else
                StringAssert.Contains(field.Name + "[0] source", result["data"]["diagnostics"].ToString());
        }

        [Test]
        public void InlineOverrideWithMissingTargetIsReportedWithoutMigratingIt()
        {
            Component manager = Manager();
            object config = Get(manager, "NetworkConfig");
            object entry = Activator.CreateInstance(Ngo("NetworkPrefab"));
            Set(entry, "Override", Enum.Parse(Ngo("NetworkPrefabOverride"), "Hash"));
            Set(entry, "SourceHashToOverride", 123u);
            FieldInfo field = RegisterInline(config, entry);
            object entries = field.GetValue(config);
            string before = EditorJsonUtility.ToJson(manager);
            JObject result = Call(Request());
            Assert.IsTrue((bool)result["success"], result.ToString());
            Assert.IsFalse((bool)result["data"]["ready"], result.ToString());
            Assert.AreEqual(1, (int)result["data"]["prefab_registration_count"]);
            StringAssert.Contains("prefab_missing", result["data"]["diagnostics"].ToString());
            StringAssert.Contains(field.Name + "[0] target", result["data"]["diagnostics"].ToString());
            Assert.AreEqual(before, EditorJsonUtility.ToJson(manager));
            Assert.AreSame(entries, field.GetValue(config));
        }

        [Test]
        public void NativeBoundaryRefusesUnknownSettingsAndInvalidBudgets()
        {
            Assert.IsNotNull(Ngo("NetworkManager"), "Package-present acceptance requires NGO.");
            JObject settings = Request();
            settings["expected_settings"] = new JObject { ["ConnectionData"] = "must-not-be-read" };
            JObject unknown = Call(settings);
            Assert.IsFalse((bool)unknown["success"], unknown.ToString());
            StringAssert.Contains("Unsupported expected setting", unknown.ToString());
            JObject budget = Request();
            budget["max_prefabs"] = 0;
            JObject invalid = Call(budget);
            Assert.IsFalse((bool)invalid["success"], invalid.ToString());
            StringAssert.Contains("max_prefabs", invalid.ToString());
        }

        [Test]
        public void StaleSceneScopeFailsWithoutFallingBackToActiveScene()
        {
            Assert.IsNotNull(Ngo("NetworkManager"), "Package-present acceptance requires NGO.");
            JObject p = Request();
            p["scene_handle"] = long.MaxValue;
            JObject result = Call(p);
            Assert.IsFalse((bool)result["success"], result.ToString());
            StringAssert.Contains("scene_handle", result.ToString());
        }

        [Test]
        public void ExpectedSettingsMismatchReportsExactFieldAndPreservesConfiguration()
        {
            Component manager = Manager();
            Set(Get(manager, "NetworkConfig"), "ProtocolVersion", (ushort)7);
            string before = EditorJsonUtility.ToJson(manager);
            JObject p = Request();
            p["expected_settings"] = new JObject { ["ProtocolVersion"] = 8, ["EnableSceneManagement"] = false };
            JObject result = Call(p);
            Assert.IsTrue((bool)result["success"], result.ToString());
            Assert.IsFalse((bool)result["data"]["ready"]);
            StringAssert.Contains("settings_mismatch", result["data"]["diagnostics"].ToString());
            StringAssert.Contains("ProtocolVersion", result["data"]["diagnostics"].ToString());
            Assert.AreEqual(before, EditorJsonUtility.ToJson(manager));
        }
    }
}
