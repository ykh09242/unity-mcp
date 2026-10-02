using System;
using System.Collections.Generic;
using System.Linq;
using System.Text.RegularExpressions;
using MCPForUnity.Editor.Tools.Graphics;
using MCPForUnity.Runtime.Helpers;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;
using UnityEngine.Rendering;
using UnityEngine.SceneManagement;
using UnityEngine.TestTools;

namespace MCPForUnityTests.Editor.Tools
{
    // Authored Edit-mode controls; compilation does not execute probes or baking.
    [Parallelizable(ParallelScope.None)]
    public class LightingProbeIntegrityTests
    {
        private Scene originalScene;
        private Scene ownedScene;
        private UnityEngine.Object[] originalSelection;
        private UnityEngine.Object originalActiveSelection;
        private LightingSettings originalOwnedSceneSettings;
        private bool captured;
        private readonly List<GameObject> ownedObjects = new();
        private readonly List<LightingSettings> ownedSettings = new();

        [SetUp]
        public void SetUp()
        {
            captured = false;
            ownedScene = default;
            originalOwnedSceneSettings = null;
            ownedObjects.Clear();
            ownedSettings.Clear();
            if (PrefabStageUtility.GetCurrentPrefabStage() != null)
                Assert.Ignore("An unowned Prefab Stage is open.");
            originalScene = SceneManager.GetActiveScene();
            originalSelection = Selection.objects;
            originalActiveSelection = Selection.activeObject;
            captured = true;
            ownedScene = EditorSceneManager.NewScene(NewSceneSetup.EmptyScene, NewSceneMode.Additive);
            Assert.IsTrue(SceneManager.SetActiveScene(ownedScene));
            Lightmapping.TryGetLightingSettings(out originalOwnedSceneSettings);
        }

        [TearDown]
        public void TearDown()
        {
            if (!captured) return;
            try
            {
                if (ownedScene.IsValid() && ownedScene.isLoaded)
                {
                    Assert.IsTrue(SceneManager.SetActiveScene(ownedScene));
                    foreach (GameObject go in ownedObjects)
                    {
                        if (go == null) continue;
                        Assert.AreEqual(ownedScene, go.scene);
                        foreach (Component component in go.GetComponents<Component>())
                            if (component != null) Undo.ClearUndo(component);
                        Undo.ClearUndo(go);
                        UnityEngine.Object.DestroyImmediate(go);
                    }
                    foreach (LightingSettings settings in ownedSettings)
                    {
                        if (settings == null) continue;
                        // Only a previously absent, nonpersistent result of our invalid request is owned.
                        Assert.IsFalse(AssetDatabase.Contains(settings));
                        Lightmapping.TryGetLightingSettings(out var assigned);
                        if (assigned == settings) Lightmapping.lightingSettings = originalOwnedSceneSettings;
                        Undo.ClearUndo(settings);
                        UnityEngine.Object.DestroyImmediate(settings);
                    }
                    Assert.AreEqual(0, ownedScene.rootCount, "Unexpected objects retained for diagnosis.");
                    Assert.IsTrue(EditorSceneManager.CloseScene(ownedScene, true));
                }
            }
            finally
            {
                if (originalScene.IsValid() && originalScene.isLoaded) SceneManager.SetActiveScene(originalScene);
                Selection.objects = originalSelection;
                Selection.activeObject = originalActiveSelection;
                captured = false;
            }
        }

        [TestCase("unknown")]
        [TestCase("lightmapper")]
        [TestCase("compression")]
        public void AllInvalidSettingsPreserveAssignedIdentityAndInventory(string kind)
        {
            JObject settings = kind switch
            {
                "unknown" => new JObject { ["unknown"] = 1 },
                "lightmapper" => new JObject { ["lightmapper"] = "bad" },
                _ => new JObject { ["mixedBakeMode"] = "bad", ["lightmapCompression"] = "bad" }
            };
            Lightmapping.TryGetLightingSettings(out var before);
            if (before != null)
                Assert.Ignore("The owned empty scene already has LightingSettings; allocation regression requires none.");
            LightingSettings[] inventory = Resources.FindObjectsOfTypeAll<LightingSettings>();
            JObject response;
            try
            {
                response = Send("bake_set_settings", new JObject { ["settings"] = settings });
            }
            finally
            {
                // Capture a regressed allocation before any assertion, for exact teardown cleanup.
                if (Lightmapping.TryGetLightingSettings(out var after) && after != null
                    && !inventory.Contains(after) && !AssetDatabase.Contains(after))
                    ownedSettings.Add(after);
            }
            Assert.IsFalse(response.Value<bool>("success"), response.ToString());
            Lightmapping.TryGetLightingSettings(out var current);
            Assert.AreSame(before, current);
            CollectionAssert.AreEquivalent(inventory, Resources.FindObjectsOfTypeAll<LightingSettings>());
        }

        [TestCase(false)]
        [TestCase(true)]
        public void MalformedOrEmptySettingsRetainIdentity(bool array)
        {
            Lightmapping.TryGetLightingSettings(out var before);
            var response = Send("bake_set_settings", new JObject
            {
                ["settings"] = array ? (JToken)new JArray() : new JObject()
            });
            Assert.IsFalse(response.Value<bool>("success"), response.ToString());
            Lightmapping.TryGetLightingSettings(out var after);
            Assert.AreSame(before, after);
        }

        [Test]
        public void DefaultLightProbeGridIsCenteredInLocalSpace()
        {
            var response = Send("bake_create_light_probe_group", new JObject { ["name"] = UniqueName() });
            LightProbeGroup group = ReturnedObject(response).GetComponent<LightProbeGroup>();
            Assert.AreEqual(18, group.probePositions.Length);
            Assert.AreEqual(new Vector3(-2, -1, -2), group.probePositions[0]);
            Assert.AreEqual(new Vector3(2, 1, 2), group.probePositions[17]);
        }

        [TestCase(0f)]
        [TestCase(2f)]
        public void LightProbeGridPreservesZeroSpacingAndNumericForms(float spacing)
        {
            var response = Send("bake_create_light_probe_group", new JObject
            {
                ["name"] = UniqueName(), ["position"] = new JArray(0, -1, "2", 99),
                ["grid_size"] = new JArray(2, 1, 2), ["spacing"] = spacing
            });
            GameObject go = ReturnedObject(response);
            Assert.AreEqual(new Vector3(0, -1, 2), go.transform.position);
            Vector3[] positions = go.GetComponent<LightProbeGroup>().probePositions;
            Assert.AreEqual(4, positions.Length);
            Assert.AreEqual(new Vector3(-spacing / 2, 0, -spacing / 2), positions[0]);
            Assert.AreEqual(new Vector3(spacing / 2, 0, spacing / 2), positions[3]);
        }

        [Test]
        public void InvalidGridRejectsWithoutAllocatingObject()
        {
            string name = UniqueName();
            LogAssert.Expect(LogType.Error, new Regex("\\[ManageGraphics\\] Action 'bake_create_light_probe_group' failed:"));
            var response = Send("bake_create_light_probe_group", new JObject
            {
                ["name"] = name, ["grid_size"] = new JArray(2, "bad", 3)
            });
            Assert.IsFalse(response.Value<bool>("success"), response.ToString());
            Assert.IsFalse(ownedScene.GetRootGameObjects().Any(go => go.name == name));
        }

        [Test]
        public void ReflectionProbeCreationPreservesFalseAndSize()
        {
            var response = Send("bake_create_reflection_probe", new JObject
            {
                ["name"] = UniqueName(), ["position"] = new JArray(0, -1, 2),
                ["size"] = new JArray(4, 5, 6), ["resolution"] = 256,
                ["mode"] = "cUsToM", ["hdr"] = false, ["box_projection"] = false
            });
            GameObject go = ReturnedObject(response);
            ReflectionProbe probe = go.GetComponent<ReflectionProbe>();
            Assert.AreEqual(ReflectionProbeMode.Custom, probe.mode);
            Assert.AreEqual(new Vector3(4, 5, 6), probe.size);
            Assert.AreEqual(new Vector3(0, -1, 2), go.transform.position);
            Assert.AreEqual(256, probe.resolution);
            Assert.IsFalse(probe.hdr);
            Assert.IsFalse(probe.boxProjection);
        }

        [Test]
        public void InvalidReflectionModeRejectsWithoutAllocatingObject()
        {
            string name = UniqueName();
            var response = Send("bake_create_reflection_probe", new JObject { ["name"] = name, ["mode"] = "bad" });
            Assert.IsFalse(response.Value<bool>("success"), response.ToString());
            Assert.IsFalse(ownedScene.GetRootGameObjects().Any(go => go.name == name));
        }

        [TestCase("empty")]
        [TestCase("short")]
        [TestCase("number")]
        public void RejectedPositionsPreserveAllCoordinatesAndDirtyState(string kind)
        {
            LightProbeGroup group = OwnedGroup();
            Vector3[] before = group.probePositions;
            int beforeDirty = EditorUtility.GetDirtyCount(group);
            JArray positions = kind == "empty" ? new JArray() : new JArray
            {
                new JArray(9, 9, 9), kind == "short" ? new JArray(1, 2) : new JArray(1, 2, "bad")
            };
            if (kind == "number")
                LogAssert.Expect(LogType.Error, new Regex("\\[ManageGraphics\\] Action 'bake_set_probe_positions' failed:"));
            var response = Send("bake_set_probe_positions", new JObject
            {
                ["target"] = group.gameObject.GetInstanceIDCompat().ToString(), ["positions"] = positions
            });
            Assert.IsFalse(response.Value<bool>("success"), response.ToString());
            CollectionAssert.AreEqual(before, group.probePositions);
            Assert.AreEqual(beforeDirty, EditorUtility.GetDirtyCount(group));
        }

        [Test]
        public void ValidPositionsUseExactOwnedIdAndPreserveNumericForms()
        {
            LightProbeGroup group = OwnedGroup();
            var response = Send("bake_set_probe_positions", new JObject
            {
                ["target"] = group.gameObject.GetInstanceIDCompat().ToString(),
                ["positions"] = new JArray { new JArray(0, -1, "2", 99) }
            });
            Assert.IsTrue(response.Value<bool>("success"), response.ToString());
            CollectionAssert.AreEqual(new[] { new Vector3(0, -1, 2) }, group.probePositions);
        }

        private JObject Send(string action, JObject fields)
        {
            fields["action"] = action;
            string requestedName = fields.Value<string>("name");
            try { return JObject.FromObject(ManageGraphics.HandleCommand(fields)); }
            finally
            {
                // Capture only this uniquely named request result before assertions.
                foreach (GameObject go in ownedScene.GetRootGameObjects())
                    if (requestedName != null && go.name == requestedName && !ownedObjects.Contains(go))
                        ownedObjects.Add(go);
            }
        }

        private GameObject ReturnedObject(JObject response)
        {
            Assert.IsTrue(response.Value<bool>("success"), response.ToString());
            int id = response["data"].Value<int>("instanceID");
            GameObject go = ownedScene.GetRootGameObjects().Single(root => root.GetInstanceIDCompat() == id);
            Assert.AreEqual(ownedScene, go.scene);
            return go;
        }

        private LightProbeGroup OwnedGroup()
        {
            var go = new GameObject(UniqueName());
            ownedObjects.Add(go);
            Assert.AreEqual(ownedScene, go.scene);
            LightProbeGroup group = go.AddComponent<LightProbeGroup>();
            group.probePositions = new[] { new Vector3(1, 2, 3), new Vector3(-1, 0, 1) };
            return group;
        }

        private static string UniqueName() => "__McpLightingProbeIntegrity_" + Guid.NewGuid().ToString("N");
    }
}
