using System;
using System.IO;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Editor.Tools.Prefabs;
using MCPForUnityTests.Editor.Tools.Fixtures;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEditor;
using UnityEngine;
using Object = UnityEngine.Object;

namespace MCPForUnityTests.Editor.Tools
{
    public class PrefabContentsAuthoringTests
    {
        private string folder;
        private string path;

        [SetUp]
        public void SetUp()
        {
            folder = "Assets/PrefabAuthoring_" + Guid.NewGuid().ToString("N");
            AssetDatabase.CreateFolder("Assets", Path.GetFileName(folder));
            path = folder + "/Panel.prefab";
            var root = new GameObject("Panel", typeof(RectTransform));
            try
            {
                ((RectTransform)root.transform).anchoredPosition = new Vector2(12, 34);
                PrefabUtility.SaveAsPrefabAsset(root, path);
            }
            finally
            {
                Object.DestroyImmediate(root);
            }
        }

        [TearDown]
        public void TearDown()
        {
            PrefabPreviewLifecycleProbe.DriftOnEdit = false;
            AssetDatabase.DeleteAsset(folder);
        }

        private JObject Modify(string property, JToken value, bool preview = false) =>
            JObject.FromObject(
                ManagePrefabs.HandleCommand(
                    new JObject
                    {
                        ["action"] = "modify_contents",
                        ["prefabPath"] = path,
                        ["previewOnly"] = preview,
                        ["componentProperties"] = new JObject { ["RectTransform"] = new JObject { [property] = value } },
                    }
                )
            );

        [TestCase("m_AnchoredPosition")]
        [TestCase("m_AnchorMin")]
        public void Vector2Object_SavesBothComponents(string property)
        {
            var result = Modify(property, new JObject { ["x"] = 0.25, ["y"] = 0.75 });
            Assert.IsTrue((bool)result["success"], result.ToString());
            using var serialized = new SerializedObject(AssetDatabase.LoadAssetAtPath<GameObject>(path).transform);
            Assert.AreEqual(new Vector2(0.25f, 0.75f), serialized.FindProperty(property).vector2Value);
        }

        [TestCase("{\"x\":1}")]
        [TestCase("{\"x\":1,\"y\":true}")]
        [TestCase("{\"x\":1,\"y\":null}")]
        [TestCase("{\"x\":1,\"y\":1e100}")]
        public void InvalidVector2_DoesNotSave(string json)
        {
            byte[] before = File.ReadAllBytes(path);
            var result = Modify("m_AnchoredPosition", JToken.Parse(json));
            Assert.IsFalse((bool)result["success"], result.ToString());
            CollectionAssert.AreEqual(before, File.ReadAllBytes(path));
        }

        [Test]
        public void Vector2ChildWrite_RemainsSupported()
        {
            var root = new GameObject("VectorChild", typeof(RectTransform));
            try
            {
                Assert.IsTrue(ComponentOps.SetProperty(root.transform, "m_AnchoredPosition.x", new JValue(42), out string error), error);
                Assert.AreEqual(42, ((RectTransform)root.transform).anchoredPosition.x);
            }
            finally
            {
                Object.DestroyImmediate(root);
            }
        }

        [Test]
        public void Preview_ShowsValuesWithoutSaving()
        {
            byte[] before = File.ReadAllBytes(path);
            var result = Modify("m_AnchoredPosition.x", new JValue(42), true);
            Assert.IsTrue((bool)result["success"], result.ToString());
            CollectionAssert.AreEqual(before, File.ReadAllBytes(path), "Preview must not save.");
            Assert.IsFalse((bool)result["data"]["saved"]);
            StringAssert.Contains("m_AnchoredPosition", result["data"]["preview"].ToString());
            StringAssert.Contains("42", result["data"]["preview"].ToString());
        }

        [TestCase(false)]
        [TestCase(true)]
        public void Preview_ObservesIndirectEditsAndNormalizesInternalReferences(bool drift)
        {
            GameObject contents = PrefabUtility.LoadPrefabContents(path);
            try
            {
                var child = new GameObject("Target", typeof(RectTransform));
                child.transform.SetParent(contents.transform, false);
                var probe = contents.AddComponent<PrefabPreviewLifecycleProbe>();
                probe.Reference = child.transform;
                PrefabUtility.SaveAsPrefabAsset(contents, path);
            }
            finally
            {
                PrefabUtility.UnloadPrefabContents(contents);
            }
            byte[] before = File.ReadAllBytes(path);
            PrefabPreviewLifecycleProbe.DriftOnEdit = drift;
            var result = drift
                ? JObject.FromObject(
                    ManagePrefabs.HandleCommand(
                        new JObject
                        {
                            ["action"] = "modify_contents",
                            ["prefabPath"] = path,
                            ["previewOnly"] = true,
                            ["componentProperties"] = new JObject { [nameof(PrefabPreviewLifecycleProbe)] = new JObject { ["DrivenReference"] = null } },
                        }
                    )
                )
                : Modify("m_AnchoredPosition.x", new JValue(42), true);
            Assert.IsTrue((bool)result["success"], result.ToString());
            CollectionAssert.AreEqual(before, File.ReadAllBytes(path));
            var preview = result["data"]["preview"];
            Assert.IsTrue((bool)preview["complete"], preview.ToString());
            if (drift)
            {
                StringAssert.Contains(":Reference", preview.ToString(), "The requested reference setter must execute.");
                StringAssert.Contains("888", preview.ToString(), "A reference setter's additional layout changes must also be reported.");
            }
            else
                foreach (JToken change in preview["changes"])
                    StringAssert.DoesNotContain(":Reference", (string)change["path"], "Load-local instance IDs must not appear as reference edits.");
        }

        [Test]
        public void Preview_LargeArrayReportsTruncationWithoutSaving()
        {
            GameObject contents = PrefabUtility.LoadPrefabContents(path);
            try
            {
                contents.AddComponent<PrefabPreviewLifecycleProbe>();
                PrefabUtility.SaveAsPrefabAsset(contents, path);
            }
            finally
            {
                PrefabUtility.UnloadPrefabContents(contents);
            }
            byte[] before = File.ReadAllBytes(path);
            var values = new JArray();
            for (int i = 0; i < 250; i++)
                values.Add(i);
            var result = JObject.FromObject(
                ManagePrefabs.HandleCommand(
                    new JObject
                    {
                        ["action"] = "modify_contents",
                        ["prefabPath"] = path,
                        ["previewOnly"] = true,
                        ["componentProperties"] = new JObject { [nameof(PrefabPreviewLifecycleProbe)] = new JObject { ["Values"] = values } },
                    }
                )
            );
            Assert.IsTrue((bool)result["success"], result.ToString());
            CollectionAssert.AreEqual(before, File.ReadAllBytes(path));
            var preview = result["data"]["preview"];
            Assert.AreEqual(200, ((JArray)preview["changes"]).Count);
            Assert.IsTrue((bool)preview["truncated"]);
            Assert.IsFalse((bool)preview["complete"]);
            Assert.Greater((int)preview["change_count"], 200);
        }

        [Test]
        public void PreviewFlag_RejectsOtherNativeActions()
        {
            var result = JObject.FromObject(ManagePrefabs.HandleCommand(new JObject { ["action"] = "save_prefab_stage", ["previewOnly"] = true }));
            Assert.IsFalse((bool)result["success"]);
            StringAssert.Contains("preview_only", (string)result["error"]);
        }
    }
}
