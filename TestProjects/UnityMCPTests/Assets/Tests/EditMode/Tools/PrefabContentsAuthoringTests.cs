using System;
using System.IO;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Editor.Tools.Prefabs;
using MCPForUnityTests.Editor.Helpers;
using MCPForUnityTests.Editor.Tools.Fixtures;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEditor;
using UnityEngine;
using Object = UnityEngine.Object;

namespace MCPForUnityTests.Editor.Tools
{
    [Parallelizable(ParallelScope.None)]
    public class PrefabContentsAuthoringTests
    {
        private readonly PrefabTestSceneFixture sceneFixture = new PrefabTestSceneFixture();
        private string folder;
        private string path;

        [SetUp]
        public void SetUp()
        {
            sceneFixture.PrepareRunnerBootstrap();
            sceneFixture.Create("McpPrefabAuthoring_", Guid.NewGuid().ToString("N"));
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
            try
            {
                PrefabPreviewLifecycleProbe.DriftOnEdit = false;
                if (!string.IsNullOrEmpty(folder) && AssetDatabase.IsValidFolder(folder))
                    AssetDatabase.DeleteAsset(folder);
            }
            finally
            {
                try
                {
                    sceneFixture.Close();
                }
                finally
                {
                    sceneFixture.RestoreRunnerBootstrap();
                }
            }
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

        [Test]
        public void SavedContentsReportActualRequestedField()
        {
            JObject response = Modify("anchoredPosition", new JArray(21, 43));
            Assert.IsTrue((bool)response["success"], response.ToString());
            Assert.IsTrue((bool)response["data"]["saved"]);
            Assert.AreEqual("passed", (string)response["data"]["verification"]["status"]);
            JToken value = response["data"]["verification"]["observed_values"][0]["observed_values"][0]["value"];
            Assert.AreEqual(21, (float)value[0]);
            Assert.AreEqual(43, (float)value[1]);
        }

        [TestCase("Collider")]
        [TestCase("UnityEngine.Collider")]
        public void SavedContentsReportBaseTypeRequestedField(string selector)
        {
            var contents = PrefabUtility.LoadPrefabContents(path);
            try
            {
                contents.AddComponent<BoxCollider>();
                PrefabUtility.SaveAsPrefabAsset(contents, path);
            }
            finally
            {
                PrefabUtility.UnloadPrefabContents(contents);
            }
            JObject response = JObject.FromObject(
                ManagePrefabs.HandleCommand(
                    new JObject
                    {
                        ["action"] = "modify_contents",
                        ["prefabPath"] = path,
                        ["componentProperties"] = new JObject { [selector] = new JObject { ["isTrigger"] = true } },
                    }
                )
            );
            Assert.IsTrue((bool)response["success"], response.ToString());
            JToken verification = response["data"]["verification"];
            Assert.AreEqual("passed", (string)verification["status"], verification.ToString());
            JToken retained = verification["observed_values"][1]["observed_values"][0];
            Assert.AreEqual("isTrigger", (string)retained["requested_property"]);
            Assert.IsTrue((bool)retained["matches_requested"]);
        }

        [Test]
        public void SavedVerificationBoundsComponentsAndRequestedFields()
        {
            var contents = PrefabUtility.LoadPrefabContents(path);
            try
            {
                for (int index = 0; index < 20; index++)
                    contents.AddComponent<BoxCollider>();
                PrefabUtility.SaveAsPrefabAsset(contents, path);
            }
            finally
            {
                PrefabUtility.UnloadPrefabContents(contents);
            }
            var fields = new JObject();
            for (int index = 0; index < 80; index++)
                fields["unknown" + index] = index;
            var requested = new JObject { ["componentProperties"] = new JObject { ["RectTransform"] = fields } };
            var method = typeof(ManagePrefabs).GetMethod("SavedVerification", System.Reflection.BindingFlags.Static | System.Reflection.BindingFlags.NonPublic);
            var result = (JObject)method.Invoke(null, new object[] { path, Array.Empty<int>(), Array.Empty<string>(), requested });
            Assert.IsFalse((bool)result["complete"], result.ToString());
            Assert.AreNotEqual("passed", (string)result["status"]);
            Assert.LessOrEqual(((JArray)result["observed_values"]).Count, 16);
            Assert.IsTrue(result["further_fixes_required"].Type == JTokenType.Null || (bool)result["further_fixes_required"]);
        }

        [Test]
        public void SavedContentsReportsRequestedObjectFieldsAfterRootNameNormalization()
        {
            JObject response = JObject.FromObject(
                ManagePrefabs.HandleCommand(
                    new JObject
                    {
                        ["action"] = "modify_contents",
                        ["prefabPath"] = path,
                        ["name"] = "RequestedRootName",
                        ["tag"] = "EditorOnly",
                        ["layer"] = "Ignore Raycast",
                    }
                )
            );
            Assert.IsTrue((bool)response["success"], response.ToString());
            JToken verification = response["data"]["verification"];
            Assert.AreEqual("issues_found", (string)verification["status"], verification.ToString());
            Assert.IsTrue((bool)verification["further_fixes_required"]);
            var values = (JArray)verification["gameobject_values"];
            Assert.AreEqual(3, values.Count);
            Assert.IsFalse((bool)values[0]["matches_requested"]);
            Assert.IsTrue((bool)values[1]["matches_requested"]);
            Assert.IsTrue((bool)values[2]["matches_requested"]);
        }

        private void CreateAmbiguousHierarchy()
        {
            var contents = PrefabUtility.LoadPrefabContents(path);
            try
            {
                foreach (string branch in new[] { "Branch", "Branch", "Unique" })
                {
                    var parent = new GameObject(branch);
                    parent.transform.SetParent(contents.transform, false);
                    new GameObject("Leaf").transform.SetParent(parent.transform, false);
                }
                PrefabUtility.SaveAsPrefabAsset(contents, path);
            }
            finally
            {
                PrefabUtility.UnloadPrefabContents(contents);
            }
        }

        [TestCase("Leaf", false)]
        [TestCase("Branch/Leaf", false)]
        [TestCase("Panel/Branch/Leaf", false)]
        [TestCase("Branch", true)]
        [TestCase("Panel/Branch", true)]
        public void AmbiguousMutationSelectorsAreRefusedWithoutSaving(string selector, bool parent)
        {
            CreateAmbiguousHierarchy();
            byte[] before = File.ReadAllBytes(path);
            int scenes = UnityEditor.SceneManagement.EditorSceneManager.previewSceneCount;
            var request = new JObject { ["action"] = "modify_contents", ["prefabPath"] = path };
            if (parent)
                request["createChild"] = new JObject { ["name"] = "Added", ["parent"] = selector };
            else
            {
                request["target"] = selector;
                request["name"] = "Changed";
            }
            var result = JObject.FromObject(ManagePrefabs.HandleCommand(request));
            Assert.IsFalse(result.Value<bool>("success"), result.ToString());
            StringAssert.Contains("ambiguous", result.ToString().ToLowerInvariant());
            CollectionAssert.AreEqual(before, File.ReadAllBytes(path));
            Assert.AreEqual(scenes, UnityEditor.SceneManagement.EditorSceneManager.previewSceneCount, "Isolated contents must be unloaded after refusal.");
        }

        [TestCase("parent")]
        [TestCase("deleteChild")]
        public void AmbiguousReparentAndDeleteSelectorsAreRefusedWithoutSaving(string operation)
        {
            CreateAmbiguousHierarchy();
            byte[] before = File.ReadAllBytes(path);
            var request = new JObject { ["action"] = "modify_contents", ["prefabPath"] = path };
            if (operation == "parent")
            {
                request["target"] = "Unique/Leaf";
                request["parent"] = "Branch";
            }
            else
                request["deleteChild"] = "Branch/Leaf";
            var result = JObject.FromObject(ManagePrefabs.HandleCommand(request));
            Assert.IsFalse(result.Value<bool>("success"), result.ToString());
            StringAssert.Contains("ambiguous", result.ToString().ToLowerInvariant());
            CollectionAssert.AreEqual(before, File.ReadAllBytes(path));
        }

        [TestCase("Unique/Leaf")]
        [TestCase("Panel/Unique/Leaf")]
        public void UniqueMutationPathsStillResolve(string selector)
        {
            CreateAmbiguousHierarchy();
            var result = JObject.FromObject(
                ManagePrefabs.HandleCommand(
                    new JObject
                    {
                        ["action"] = "modify_contents",
                        ["prefabPath"] = path,
                        ["target"] = selector,
                        ["name"] = "Changed",
                    }
                )
            );
            Assert.IsTrue(result.Value<bool>("success"), result.ToString());
            Assert.IsNotNull(AssetDatabase.LoadAssetAtPath<GameObject>(path).transform.Find("Unique/Changed"));
        }

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

        [Test]
        public void ComponentPreview_SkipsUnrelatedLargeSerializedArrays()
        {
            var contents = PrefabUtility.LoadPrefabContents(path);
            try
            {
                var unrelated = new GameObject("Unrelated");
                unrelated.transform.SetParent(contents.transform, false);
                unrelated.AddComponent<PrefabPreviewLifecycleProbe>().Values = new int[21000];
                PrefabUtility.SaveAsPrefabAsset(contents, path);
            }
            finally
            {
                PrefabUtility.UnloadPrefabContents(contents);
            }
            byte[] before = File.ReadAllBytes(path);
            int scenes = UnityEditor.SceneManagement.EditorSceneManager.previewSceneCount;
            var request = new JObject
            {
                ["action"] = "modify_contents",
                ["prefabPath"] = path,
                ["previewOnly"] = true,
                ["componentProperties"] = new JObject { ["RectTransform"] = new JObject { ["m_AnchoredPosition.x"] = 42 } },
            };
            var full = JObject.FromObject(ManagePrefabs.HandleCommand(request));
            Assert.IsFalse(full.Value<bool>("success"), full.ToString());
            Assert.AreEqual("prefab_preview_limit_exceeded", full.Value<string>("code"));
            Assert.AreEqual("properties", (string)full["data"]["limit_kind"]);
            Assert.AreEqual(20000, (int)full["data"]["limit"]);
            Assert.AreEqual(20001, (int)full["data"]["observed"]);
            Assert.AreEqual("components", (string)full["data"]["follow_up"]["next_call"]["arguments"]["preview_scope"]);

            request["previewScope"] = "components";
            var scoped = JObject.FromObject(ManagePrefabs.HandleCommand(request));
            Assert.IsTrue(scoped.Value<bool>("success"), scoped.ToString());
            var preview = scoped["data"]["preview"];
            Assert.AreEqual("components", (string)preview["scope"]);
            Assert.IsTrue((bool)preview["complete"]);
            Assert.IsFalse((bool)preview["whole_prefab_complete"]);
            Assert.AreEqual(1, (int)preview["observed"]["before"]["objects"]);
            Assert.Less((int)preview["observed"]["after"]["properties"], 100);
            StringAssert.Contains("m_AnchoredPosition", preview["changes"].ToString());
            CollectionAssert.AreEqual(before, File.ReadAllBytes(path));
            Assert.AreEqual(scenes, UnityEditor.SceneManagement.EditorSceneManager.previewSceneCount);
        }

        [TestCase("name", "Renamed")]
        [TestCase("setActive", false)]
        [TestCase("position", "[1,2,3]")]
        [TestCase("parent", "Unrelated")]
        public void ComponentPreview_RejectsBroaderMutationBeforeEditing(string field, object value)
        {
            byte[] before = File.ReadAllBytes(path);
            var request = new JObject
            {
                ["action"] = "modify_contents",
                ["prefabPath"] = path,
                ["previewOnly"] = true,
                ["previewScope"] = "components",
                ["componentProperties"] = new JObject { ["RectTransform"] = new JObject { ["m_AnchoredPosition.x"] = 42 } },
                [field] = JToken.FromObject(value),
            };
            var result = JObject.FromObject(ManagePrefabs.HandleCommand(request));
            Assert.IsFalse(result.Value<bool>("success"), result.ToString());
            StringAssert.Contains("preview_scope", (string)result["error"]);
            CollectionAssert.AreEqual(before, File.ReadAllBytes(path));
        }

        [TestCase(false)]
        [TestCase(true)]
        public void ComponentPreview_NormalizesReferencesAndDeclaresPartialCoverage(bool drift)
        {
            var contents = PrefabUtility.LoadPrefabContents(path);
            try
            {
                var child = new GameObject("Target");
                child.transform.SetParent(contents.transform, false);
                contents.AddComponent<PrefabPreviewLifecycleProbe>().Reference = child.transform;
                PrefabUtility.SaveAsPrefabAsset(contents, path);
            }
            finally
            {
                PrefabUtility.UnloadPrefabContents(contents);
            }
            byte[] before = File.ReadAllBytes(path);
            int scenes = UnityEditor.SceneManagement.EditorSceneManager.previewSceneCount;
            PrefabPreviewLifecycleProbe.DriftOnEdit = drift;
            var result = JObject.FromObject(
                ManagePrefabs.HandleCommand(
                    new JObject
                    {
                        ["action"] = "modify_contents",
                        ["prefab_path"] = path,
                        ["preview_only"] = true,
                        ["preview_scope"] = "components",
                        ["component_properties"] = new JObject
                        {
                            [nameof(PrefabPreviewLifecycleProbe)] = drift
                                ? new JObject { ["DrivenReference"] = null }
                                : new JObject { ["Values"] = new JArray(1, 2) },
                        },
                    }
                )
            );
            Assert.IsTrue(result.Value<bool>("success"), result.ToString());
            var preview = result["data"]["preview"];
            Assert.IsTrue((bool)preview["complete"], preview.ToString());
            Assert.IsFalse((bool)preview["whole_prefab_complete"]);
            Assert.AreEqual(1, (int)preview["observed"]["before"]["objects"]);
            if (drift)
                StringAssert.Contains(":Reference", preview["changes"].ToString());
            else
                StringAssert.DoesNotContain(":Reference", preview["changes"].ToString());
            CollectionAssert.AreEqual(before, File.ReadAllBytes(path));
            Assert.AreEqual(scenes, UnityEditor.SceneManagement.EditorSceneManager.previewSceneCount);
        }

        [Test]
        public void ComponentPreview_LimitDoesNotRepeatTheSameRequest()
        {
            var contents = PrefabUtility.LoadPrefabContents(path);
            try
            {
                contents.AddComponent<PrefabPreviewLifecycleProbe>().Values = new int[21000];
                PrefabUtility.SaveAsPrefabAsset(contents, path);
            }
            finally
            {
                PrefabUtility.UnloadPrefabContents(contents);
            }
            byte[] before = File.ReadAllBytes(path);
            var result = JObject.FromObject(
                ManagePrefabs.HandleCommand(
                    new JObject
                    {
                        ["action"] = "modify_contents",
                        ["prefabPath"] = path,
                        ["previewOnly"] = true,
                        ["previewScope"] = "components",
                        ["componentProperties"] = new JObject { [nameof(PrefabPreviewLifecycleProbe)] = new JObject { ["Values"] = new JArray(1) } },
                    }
                )
            );
            Assert.IsFalse(result.Value<bool>("success"), result.ToString());
            Assert.AreEqual("prefab_preview_limit_exceeded", (string)result["code"]);
            Assert.AreEqual("components", (string)result["data"]["scope"]);
            Assert.IsNull(result["data"]["follow_up"]["next_call"]);
            CollectionAssert.AreEqual(before, File.ReadAllBytes(path));
        }

        [TestCase("BoxCollider")]
        [TestCase("MissingPreviewComponent")]
        public void ComponentPreview_RejectsAmbiguousOrMissingComponents(string type)
        {
            var contents = PrefabUtility.LoadPrefabContents(path);
            try
            {
                contents.AddComponent<BoxCollider>();
                contents.AddComponent<BoxCollider>();
                PrefabUtility.SaveAsPrefabAsset(contents, path);
            }
            finally
            {
                PrefabUtility.UnloadPrefabContents(contents);
            }
            byte[] before = File.ReadAllBytes(path);
            int scenes = UnityEditor.SceneManagement.EditorSceneManager.previewSceneCount;
            var result = JObject.FromObject(
                ManagePrefabs.HandleCommand(
                    new JObject
                    {
                        ["action"] = "modify_contents",
                        ["prefabPath"] = path,
                        ["previewOnly"] = true,
                        ["previewScope"] = "components",
                        ["componentProperties"] = new JObject { [type] = new JObject { ["m_Enabled"] = false } },
                    }
                )
            );
            Assert.IsFalse(result.Value<bool>("success"), result.ToString());
            CollectionAssert.AreEqual(before, File.ReadAllBytes(path));
            Assert.AreEqual(scenes, UnityEditor.SceneManagement.EditorSceneManager.previewSceneCount);
        }

        [TestCase(false, "components")]
        [TestCase(true, "unknown")]
        public void PreviewScope_RejectsUnsupportedInvocation(bool previewOnly, string scope)
        {
            var result = JObject.FromObject(
                ManagePrefabs.HandleCommand(
                    new JObject
                    {
                        ["action"] = "modify_contents",
                        ["prefabPath"] = path,
                        ["previewOnly"] = previewOnly,
                        ["previewScope"] = scope,
                        ["componentProperties"] = new JObject { ["RectTransform"] = new JObject { ["m_AnchoredPosition.x"] = 42 } },
                    }
                )
            );
            Assert.IsFalse(result.Value<bool>("success"), result.ToString());
            StringAssert.Contains("preview_scope", (string)result["error"]);
        }
    }
}
