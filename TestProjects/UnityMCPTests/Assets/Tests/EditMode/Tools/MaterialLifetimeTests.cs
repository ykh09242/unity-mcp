using System;
using System.Collections.Generic;
using System.Linq;
using System.Reflection;
using MCPForUnity.Editor.Tools;
using MCPForUnity.Runtime.Helpers;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEditor;
using UnityEngine;

namespace MCPForUnityTests.Editor.Tools
{
    public class MaterialLifetimeTests
    {
        private GameObject target;
        private Renderer renderer;
        private Material source;
        private string folder;
        private readonly HashSet<Material> generated = new HashSet<Material>();

        [SetUp]
        public void SetUp()
        {
            Undo.ClearAll();
            string nonce = Guid.NewGuid().ToString("N");
            folder = "Assets/McpMaterialLifetime_" + nonce;
            AssetDatabase.CreateFolder("Assets", "McpMaterialLifetime_" + nonce);
            source = new Material(Shader.Find("Standard")) { name = "McpMaterialLifetime_" + nonce };
            AssetDatabase.CreateAsset(source, folder + "/Source.mat");
            AssetDatabase.ImportAsset(folder + "/Source.mat", ImportAssetOptions.ForceSynchronousImport);
            source = AssetDatabase.LoadAssetAtPath<Material>(folder + "/Source.mat");
            target = GameObject.CreatePrimitive(PrimitiveType.Cube);
            renderer = target.GetComponent<Renderer>();
            renderer.sharedMaterials = new[] { source, source, source };
        }

        [TearDown]
        public void TearDown()
        {
            Undo.ClearAll();
            if (target != null)
                UnityEngine.Object.DestroyImmediate(target);
            foreach (Material material in generated)
                if (material != null && !AssetDatabase.Contains(material))
                    UnityEngine.Object.DestroyImmediate(material);
            generated.Clear();
            if (!string.IsNullOrEmpty(folder))
                AssetDatabase.DeleteAsset(folder);
        }

        [Test]
        public void InstanceColor_ClonesOnlySelectedSlotAndReusesIt()
        {
            Command("set_renderer_color", new JObject { ["color"] = new JArray(1, 0, 0, 1) });
            var clone = renderer.sharedMaterials[1];
            Assert.AreNotEqual(source, clone);
            Assert.AreEqual(source, renderer.sharedMaterials[0]);
            Assert.AreEqual(source, renderer.sharedMaterials[2]);
            for (int index = 0; index < 20; index++)
            {
                Command("set_renderer_color", new JObject { ["color"] = new JArray(0, 1, 0, 1) });
                Assert.AreEqual(clone, renderer.sharedMaterials[1]);
            }
            Assert.AreEqual(1, generated.Count);
            AssertNativeInstanceCount(1);
            Assert.AreEqual(Color.white, source.color);
        }

        [Test]
        public void InstanceAssignment_ReusesOneOwnedMaterial()
        {
            Material clone = null;
            for (int index = 0; index < 20; index++)
            {
                Command("assign_material_to_renderer", new JObject { ["materialPath"] = folder + "/Source.mat" });
                if (index == 0)
                    clone = renderer.sharedMaterials[1];
                else
                    Assert.AreEqual(clone, renderer.sharedMaterials[1]);
            }
            Assert.AreEqual(1, generated.Count);
            AssertNativeInstanceCount(1);
            Assert.AreNotEqual(source, clone);
        }

        [Test]
        public void InstanceRenderState_ReusesMaterialAndKeepsUndoRedo()
        {
            Command("set_render_state", new JObject { ["renderState"] = new JObject { ["render_queue"] = 2501 } });
            Material clone = renderer.sharedMaterials[1];
            Undo.FlushUndoRecordObjects();
            Command("set_render_state", new JObject { ["renderState"] = new JObject { ["render_queue"] = 2502 } });
            Undo.FlushUndoRecordObjects();
            Assert.AreEqual(clone, renderer.sharedMaterials[1]);
            Undo.PerformUndo();
            Assert.AreEqual(clone, renderer.sharedMaterials[1]);
            Assert.AreEqual(2501, clone.renderQueue);
            Undo.PerformRedo();
            Assert.AreEqual(clone, renderer.sharedMaterials[1]);
            Assert.AreEqual(2502, clone.renderQueue);
            Assert.AreEqual(source, renderer.sharedMaterials[0]);
            Assert.AreEqual(1, generated.Count);
            AssertNativeInstanceCount(1);
        }

        [Test]
        public void AnOwnedMaterialSharedElsewhere_IsClonedBeforeEditing()
        {
            Command("set_render_state", new JObject { ["renderState"] = new JObject { ["render_queue"] = 2501 } });
            Material clone = renderer.sharedMaterials[1];
            var other = GameObject.CreatePrimitive(PrimitiveType.Cube);
            try
            {
                other.GetComponent<Renderer>().sharedMaterial = clone;
                Command("set_render_state", new JObject { ["renderState"] = new JObject { ["render_queue"] = 2502 } });
                Assert.AreNotEqual(clone, renderer.sharedMaterials[1]);
                Assert.AreEqual(2501, other.GetComponent<Renderer>().sharedMaterial.renderQueue);
                Assert.AreEqual(2502, renderer.sharedMaterials[1].renderQueue);
            }
            finally
            {
                UnityEngine.Object.DestroyImmediate(other);
            }
        }

        [Test]
        public void OwnedMaterialRegistry_ReleasesDestroyedOwners()
        {
            Command("set_render_state", new JObject { ["renderState"] = new JObject { ["render_queue"] = 2501 } });
            Type type = typeof(ManageMaterial).Assembly.GetType("MCPForUnity.Editor.Helpers.MaterialLifetimeStore");
            Assert.IsNotNull(type);
            var store = (ScriptableObject)
                type.GetProperty("instance", BindingFlags.Public | BindingFlags.Static | BindingFlags.FlattenHierarchy).GetValue(null);
            type.GetMethod("Prune", BindingFlags.Instance | BindingFlags.NonPublic).Invoke(store, null);
            Assert.AreEqual(1, type.GetProperty("OwnedCount", BindingFlags.Instance | BindingFlags.NonPublic).GetValue(store));
            UnityEngine.Object.DestroyImmediate(target);
            target = null;
            type.GetMethod("Prune", BindingFlags.Instance | BindingFlags.NonPublic).Invoke(store, null);
            Assert.AreEqual(0, type.GetProperty("OwnedCount", BindingFlags.Instance | BindingFlags.NonPublic).GetValue(store));
        }

        [UnityEngine.TestTools.UnityTest]
        public System.Collections.IEnumerator RedoAfterEmptyPruneRestartsOwnershipCleanup()
        {
            Command("set_render_state", new JObject { ["renderState"] = new JObject { ["render_queue"] = 2501 } });
            yield return null;
            Undo.FlushUndoRecordObjects();
            Type type = typeof(ManageMaterial).Assembly.GetType("MCPForUnity.Editor.Helpers.MaterialLifetimeStore", true);
            object store = type.GetProperty("instance", BindingFlags.Public | BindingFlags.Static | BindingFlags.FlattenHierarchy).GetValue(null);
            var flags = BindingFlags.Instance | BindingFlags.NonPublic;
            var prune = type.GetMethod("Prune", flags);
            var scheduled = type.GetField("scheduled", flags);
            Undo.PerformUndo();
            yield return null;
            Assert.AreEqual(source, renderer.sharedMaterials[1]);
            prune.Invoke(store, null);
            Assert.IsFalse((bool)scheduled.GetValue(store), "An empty registry must stop polling.");
            Undo.PerformRedo();
            yield return null;
            Material restored = renderer.sharedMaterials[1];
            generated.Add(restored);
            Assert.AreEqual(2501, restored.renderQueue);
            Assert.AreEqual(1, type.GetProperty("OwnedCount", flags).GetValue(store));
            Assert.IsTrue((bool)scheduled.GetValue(store), "Redo must restart cleanup after restoring ownership entries.");
            Command("set_render_state", new JObject { ["renderState"] = new JObject { ["render_queue"] = 2502 } });
            Assert.AreEqual(restored, renderer.sharedMaterials[1], "The restored ownership must keep subsequent edits on the same material.");
            AssertNativeInstanceCount(1);
            UnityEngine.Object.DestroyImmediate(target);
            target = null;
            Undo.ClearAll();
            prune.Invoke(store, null);
            Assert.AreEqual(0, type.GetProperty("OwnedCount", flags).GetValue(store));
            Assert.IsFalse((bool)scheduled.GetValue(store));
        }

        private void AssertNativeInstanceCount(int expected)
        {
            Assert.AreEqual(
                expected,
                UnityEngine
                    .Resources.FindObjectsOfTypeAll<Material>()
                    .Count(material => !EditorUtility.IsPersistent(material) && material.name.StartsWith(source.name, StringComparison.Ordinal)),
                "Count all native instances, including allocations no longer referenced by a renderer."
            );
        }

        private void Command(string action, JObject extra)
        {
            var request = new JObject
            {
                ["action"] = action,
                ["target"] = target.GetInstanceIDCompat().ToString(),
                ["mode"] = "instance",
                ["slot"] = 1,
            };
            request.Merge(extra);
            try
            {
                var result = JObject.FromObject(ManageMaterial.HandleCommand(request));
                Assert.IsTrue(result.Value<bool>("success"), result.ToString());
            }
            finally
            {
                foreach (Material material in renderer.sharedMaterials)
                    if (material != null && material != source)
                        generated.Add(material);
            }
        }
    }
}
