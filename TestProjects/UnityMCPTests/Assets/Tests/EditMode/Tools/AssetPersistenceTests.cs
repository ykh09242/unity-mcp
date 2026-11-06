using System;
using System.Collections;
using System.IO;
using MCPForUnity.Editor.Tools;
using MCPForUnity.Editor.Tools.Animation;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEditor;
using UnityEditor.Animations;
using UnityEngine;
using UnityEngine.TestTools;

namespace MCPForUnityTests.Editor.Tools
{
    public class AssetPersistenceTests
    {
        private sealed class ThrowingAssignmentFixture : ScriptableObject
        {
            public int first = 1;
            public int backing = 2;
            public bool failRestore;
            public int Second
            {
                get => backing;
                set
                {
                    backing = value;
                    if (value == 9 || (failRestore && value == 2))
                        throw new InvalidOperationException("injected setter failure");
                }
            }
        }

        private sealed class CoupledAssignmentFixture : ScriptableObject
        {
            public int a = 1;
            public int b = 2;
            public int failed = 3;
            public bool conflicting;
            public int A
            {
                get => a;
                set
                {
                    a = value;
                    b = value;
                }
            }
            public int B
            {
                get => b;
                set
                {
                    b = value;
                    if (conflicting)
                        a = value;
                }
            }
            public int Fail
            {
                get => failed;
                set
                {
                    failed = value;
                    if (value == 9)
                        throw new InvalidOperationException("injected setter failure");
                }
            }
        }

        private static bool ApplyDirectAssignments(ScriptableObject fixture, JObject properties)
        {
            var method = typeof(ManageAsset).GetMethod(
                "ApplyObjectPropertiesAtomically",
                System.Reflection.BindingFlags.NonPublic | System.Reflection.BindingFlags.Static
            );
            return (bool)method.Invoke(null, new object[] { fixture, properties });
        }

        [TestCase(false)]
        [TestCase(true)]
        public void DirectAssignmentsVerifyCoupledSettersAfterRollback(bool conflicting)
        {
            var fixture = ScriptableObject.CreateInstance<CoupledAssignmentFixture>();
            try
            {
                fixture.conflicting = conflicting;
                var error = Assert.Throws<System.Reflection.TargetInvocationException>(() =>
                    ApplyDirectAssignments(
                        fixture,
                        new JObject
                        {
                            ["A"] = 7,
                            ["B"] = 8,
                            ["Fail"] = 9,
                        }
                    )
                );
                Assert.AreEqual(2, fixture.b);
                Assert.AreEqual(3, fixture.failed);
                if (conflicting)
                {
                    Assert.AreEqual(2, fixture.a);
                    StringAssert.Contains("Restoration incomplete", error.InnerException.Message);
                    StringAssert.Contains("A", error.InnerException.Message);
                }
                else
                {
                    Assert.AreEqual(1, fixture.a);
                    StringAssert.Contains("restored", error.InnerException.Message);
                }
            }
            finally
            {
                UnityEngine.Object.DestroyImmediate(fixture);
            }
        }

        [Test]
        public void DirectAssignmentsHonorOrderAndCurrentValues()
        {
            var fixture = ScriptableObject.CreateInstance<ThrowingAssignmentFixture>();
            try
            {
                Assert.IsTrue(ApplyDirectAssignments(fixture, new JObject { ["Second"] = 5, ["backing"] = 2 }));
                Assert.AreEqual(2, fixture.backing);
                Assert.IsTrue(ApplyDirectAssignments(fixture, new JObject { ["first"] = 5, ["First"] = 1 }));
                Assert.AreEqual(1, fixture.first);
                Assert.IsFalse(ApplyDirectAssignments(fixture, new JObject { ["first"] = 1, ["backing"] = 2 }));
            }
            finally
            {
                UnityEngine.Object.DestroyImmediate(fixture);
            }
        }

        [TestCase(false)]
        [TestCase(true)]
        public void DirectAssignmentsRollBackThrowingSetterAndReportRestoration(bool failRestore)
        {
            var fixture = ScriptableObject.CreateInstance<ThrowingAssignmentFixture>();
            try
            {
                fixture.failRestore = failRestore;
                var error = Assert.Throws<System.Reflection.TargetInvocationException>(() =>
                    ApplyDirectAssignments(fixture, new JObject { ["first"] = 5, ["Second"] = 9 })
                );
                Assert.AreEqual(1, fixture.first);
                Assert.AreEqual(2, fixture.backing);
                StringAssert.Contains(failRestore ? "Restoration incomplete" : "restored", error.InnerException.Message);
            }
            finally
            {
                UnityEngine.Object.DestroyImmediate(fixture);
            }
        }

        [Test]
        public void DirectAssignmentsValidateAllMembersBeforeMutation()
        {
            var fixture = ScriptableObject.CreateInstance<ThrowingAssignmentFixture>();
            try
            {
                Assert.Throws<System.Reflection.TargetInvocationException>(() =>
                    ApplyDirectAssignments(fixture, new JObject { ["first"] = 5, ["missing"] = 9 })
                );
                Assert.AreEqual(1, fixture.first);
            }
            finally
            {
                UnityEngine.Object.DestroyImmediate(fixture);
            }
        }

        private string folder;
        private AnimationClip unrelated;
        private byte[] original;
        private string unrelatedPath;

        [SetUp]
        public void SetUp()
        {
            folder = "Assets/ScopedSave_" + Guid.NewGuid().ToString("N");
            Assert.IsNotEmpty(AssetDatabase.CreateFolder("Assets", Path.GetFileName(folder)));
            unrelatedPath = folder + "/Unrelated.anim";
            unrelated = new AnimationClip { name = "Unrelated", frameRate = 30 };
            AssetDatabase.CreateAsset(unrelated, unrelatedPath);
            original = File.ReadAllBytes(unrelatedPath);
        }

        [TearDown]
        public void TearDown()
        {
            if (!string.IsNullOrEmpty(folder))
                AssetDatabase.DeleteAsset(folder);
        }

        [UnityTest]
        public IEnumerator ProjectSettingsMutationDoesNotSaveUnrelatedAssets()
        {
            foreach (string kind in new[] { "tag", "layer" })
            {
                string name = "Scoped_" + Guid.NewGuid().ToString("N");
                string key = kind + "Name";
                string settings = Path.Combine(Path.GetDirectoryName(Application.dataPath), "ProjectSettings/TagManager.asset");
                unrelated.frameRate = 17;
                EditorUtility.SetDirty(unrelated);
                try
                {
                    JObject result = JObject.FromObject(ManageEditor.HandleCommand(new JObject { ["action"] = "add_" + kind, [key] = name }));
                    Assert.IsTrue(result.Value<bool>("success"), result.ToString());
                    var deadline = DateTime.UtcNow.AddSeconds(3);
                    while (!File.ReadAllText(settings).Contains(name) && DateTime.UtcNow < deadline)
                        yield return null;
                    StringAssert.Contains(name, File.ReadAllText(settings));
                    CollectionAssert.AreEqual(original, File.ReadAllBytes(unrelatedPath));
                    Assert.IsTrue(EditorUtility.IsDirty(unrelated));
                }
                finally
                {
                    JObject result = JObject.FromObject(ManageEditor.HandleCommand(new JObject { ["action"] = "remove_" + kind, [key] = name }));
                    Assert.IsTrue(result.Value<bool>("success"), result.ToString());
                }
                var cleanupDeadline = DateTime.UtcNow.AddSeconds(3);
                while (File.ReadAllText(settings).Contains(name) && DateTime.UtcNow < cleanupDeadline)
                    yield return null;
                StringAssert.DoesNotContain(name, File.ReadAllText(settings));
                CollectionAssert.AreEqual(original, File.ReadAllBytes(unrelatedPath));
                Assert.IsTrue(EditorUtility.IsDirty(unrelated));
            }
        }

        [Test]
        public void ProjectSettingsSaveFailureRestoresLiveStateAndAllowsRetry(
            [Values("add_tag", "remove_tag", "add_layer", "remove_layer")] string action,
            [Values("read", "serialize", "replace")] string failure,
            [Values(false, true)] bool initiallyDirty
        )
        {
            string settings = Path.Combine(Path.GetDirectoryName(Application.dataPath), "ProjectSettings/TagManager.asset");
            var target = AssetDatabase.LoadAllAssetsAtPath("ProjectSettings/TagManager.asset")[0];
            byte[] originalDisk = File.ReadAllBytes(settings);
            string originalLive = EditorJsonUtility.ToJson(target);
            bool originalDirty = EditorUtility.IsDirty(target);
            string kind = action.EndsWith("tag") ? "tag" : "layer";
            string name = "Rollback_" + Guid.NewGuid().ToString("N");
            string sentinel = "Dirty_" + Guid.NewGuid().ToString("N");
            string fieldName =
                failure == "read" ? "ReadTagManagerBytes"
                : failure == "serialize" ? "SerializeTagManager"
                : "ReplaceTagManagerFile";
            var field = typeof(ManageEditor).GetField(fieldName, System.Reflection.BindingFlags.Static | System.Reflection.BindingFlags.NonPublic);
            Assert.IsNotNull(field, "The persistence boundary must support deterministic failure injection.");
            object originalBoundary = field.GetValue(null);
            try
            {
                if (action.StartsWith("remove"))
                {
                    var added = JObject.FromObject(ManageEditor.HandleCommand(new JObject { ["action"] = "add_" + kind, [kind + "Name"] = name }));
                    Assert.IsTrue(added.Value<bool>("success"), added.ToString());
                }
                if (initiallyDirty)
                {
                    using var serialized = new SerializedObject(target);
                    using var tags = serialized.FindProperty("tags");
                    tags.InsertArrayElementAtIndex(tags.arraySize);
                    using var tag = tags.GetArrayElementAtIndex(tags.arraySize - 1);
                    tag.stringValue = sentinel;
                    serialized.ApplyModifiedPropertiesWithoutUndo();
                    EditorUtility.SetDirty(target);
                }
                else
                    EditorUtility.ClearDirty(target);

                byte[] beforeDisk = File.ReadAllBytes(settings);
                string beforeLive = EditorJsonUtility.ToJson(target);
                Delegate fault =
                    failure == "read" ? new Func<string, byte[]>(_ => throw new IOException("injected read failure"))
                    : failure == "serialize"
                        ? new Action<UnityEngine.Object[], string, bool>((_, __, ___) => throw new IOException("injected serialization failure"))
                    : new Action<string, string, string>((_, __, ___) => throw new IOException("injected replacement failure"));
                field.SetValue(null, fault);
                var request = new JObject { ["action"] = action, [kind + "Name"] = name };
                var failed = JObject.FromObject(ManageEditor.HandleCommand(request));
                Assert.IsFalse(failed.Value<bool>("success"), failed.ToString());
                StringAssert.Contains("injected", failed.Value<string>("error"));
                CollectionAssert.AreEqual(beforeDisk, File.ReadAllBytes(settings));
                Assert.AreEqual(beforeLive, EditorJsonUtility.ToJson(target), "Failed mutations must restore preexisting unsaved settings.");
                Assert.AreEqual(initiallyDirty, EditorUtility.IsDirty(target));
                CollectionAssert.AreEqual(original, File.ReadAllBytes(unrelatedPath));

                field.SetValue(null, originalBoundary);
                var retried = JObject.FromObject(ManageEditor.HandleCommand(request));
                Assert.IsTrue(retried.Value<bool>("success"), retried.ToString());
                Assert.AreEqual(action.StartsWith("add"), File.ReadAllText(settings).Contains(name));
                if (initiallyDirty)
                    StringAssert.Contains(sentinel, File.ReadAllText(settings), "Retry must retain preexisting dirty edits.");
                Assert.IsFalse(EditorUtility.IsDirty(target));
            }
            finally
            {
                field.SetValue(null, originalBoundary);
                File.WriteAllBytes(settings, originalDisk);
                EditorJsonUtility.FromJsonOverwrite(originalLive, target);
                if (originalDirty)
                    EditorUtility.SetDirty(target);
                else
                    EditorUtility.ClearDirty(target);
            }
        }

        [TestCase("clear")]
        [TestCase("missing")]
        [TestCase("set")]
        public void PrefabReferenceAssignmentPersistsAndPreservesUnrelatedDirtyAsset(string mode)
        {
            string meshPath = folder + "/Referenced.asset";
            string prefabPath = folder + "/Reference.prefab";
            var mesh = new Mesh { name = "Referenced" };
            AssetDatabase.CreateAsset(mesh, meshPath);
            var source = new GameObject("Reference", typeof(MeshFilter), typeof(Canvas));
            try
            {
                source.GetComponent<MeshFilter>().sharedMesh = mode == "set" ? null : mesh;
                source.GetComponent<Canvas>().sortingOrder = 7;
                PrefabUtility.SaveAsPrefabAsset(source, prefabPath, out bool saved);
                Assert.IsTrue(saved);
            }
            finally
            {
                UnityEngine.Object.DestroyImmediate(source);
            }
            byte[] before = File.ReadAllBytes(prefabPath);
            unrelated.frameRate = 17;
            EditorUtility.SetDirty(unrelated);
            if (mode == "missing")
                LogAssert.Expect(
                    LogType.Error,
                    new System.Text.RegularExpressions.Regex(".*Action 'modify' failed.*", System.Text.RegularExpressions.RegexOptions.Singleline)
                );
            JObject response = JObject.FromObject(
                ManageAsset.HandleCommand(
                    new JObject
                    {
                        ["action"] = "modify",
                        ["path"] = prefabPath,
                        ["properties"] = new JObject
                        {
                            ["MeshFilter"] = new JObject
                            {
                                ["sharedMesh"] = mode == "clear" ? JValue.CreateNull() : new JValue(mode == "set" ? meshPath : folder + "/Missing.asset"),
                            },
                            ["Canvas"] = new JObject { ["sortingOrder"] = 8 },
                        },
                    }
                )
            );
            Assert.AreEqual(mode != "missing", response.Value<bool>("success"), response.ToString());
            CollectionAssert.AreEqual(original, File.ReadAllBytes(unrelatedPath));
            Assert.IsTrue(EditorUtility.IsDirty(unrelated));
            if (mode != "missing")
                CollectionAssert.AreNotEqual(before, File.ReadAllBytes(prefabPath));
            else
                CollectionAssert.AreEqual(before, File.ReadAllBytes(prefabPath), "A failed non-null conversion must not clear the existing reference.");
            AssetDatabase.ImportAsset(prefabPath, ImportAssetOptions.ForceSynchronousImport | ImportAssetOptions.ForceUpdate);
            var reloaded = PrefabUtility.LoadPrefabContents(prefabPath);
            try
            {
                Assert.That(reloaded.GetComponent<MeshFilter>().sharedMesh, Is.EqualTo(mode == "clear" ? null : mesh));
                Assert.That(reloaded.GetComponent<Canvas>().sortingOrder, Is.EqualTo(mode == "missing" ? 7 : 8));
            }
            finally
            {
                PrefabUtility.UnloadPrefabContents(reloaded);
            }
            CollectionAssert.AreEqual(original, File.ReadAllBytes(unrelatedPath));
            Assert.IsTrue(EditorUtility.IsDirty(unrelated));
        }

        [TestCase("missing_member")]
        [TestCase("invalid_value")]
        [TestCase("missing_component")]
        [TestCase("throwing_setter")]
        [TestCase("restore_failure")]
        public void PrefabComponentRequestFailsAtomically(string failure)
        {
            string prefabPath = folder + "/Atomic.prefab";
            var source = new GameObject("Atomic", typeof(Canvas), typeof(PrefabAssignmentFixture));
            try
            {
                source.GetComponent<Canvas>().sortingOrder = 7;
                source.GetComponent<PrefabAssignmentFixture>().failRestore = failure == "restore_failure";
                PrefabUtility.SaveAsPrefabAsset(source, prefabPath, out bool saved);
                Assert.IsTrue(saved);
            }
            finally
            {
                UnityEngine.Object.DestroyImmediate(source);
            }

            byte[] before = File.ReadAllBytes(prefabPath);
            var second =
                failure == "missing_member" ? new JObject { ["Missing"] = 9 }
                : failure == "invalid_value" ? new JObject { ["backing"] = "invalid" }
                : new JObject { ["Value"] = 9 };
            string component = failure == "missing_component" ? "MissingComponentForAtomicTest" : nameof(PrefabAssignmentFixture);
            if (failure == "invalid_value")
                LogAssert.Expect(LogType.Error, new System.Text.RegularExpressions.Regex(@"\[PropertyConversion\] Property value conversion failed\."));
            LogAssert.Expect(
                LogType.Error,
                new System.Text.RegularExpressions.Regex(".*Action 'modify' failed.*", System.Text.RegularExpressions.RegexOptions.Singleline)
            );
            JObject response = JObject.FromObject(
                ManageAsset.HandleCommand(
                    new JObject
                    {
                        ["action"] = "modify",
                        ["path"] = prefabPath,
                        ["properties"] = new JObject
                        {
                            ["Canvas"] = new JObject { ["sortingOrder"] = 8 },
                            [component] = second,
                        },
                    }
                )
            );
            Assert.IsFalse(response.Value<bool>("success"), response.ToString());
            if (failure == "throwing_setter" || failure == "restore_failure")
                StringAssert.Contains(failure == "restore_failure" ? "Restoration incomplete" : "restored", (string)response["error"]);
            var current = AssetDatabase.LoadAssetAtPath<GameObject>(prefabPath);
            Assert.AreEqual(7, current.GetComponent<Canvas>().sortingOrder);
            Assert.AreEqual(2, current.GetComponent<PrefabAssignmentFixture>().backing);
            CollectionAssert.AreEqual(before, File.ReadAllBytes(prefabPath));
            AssetDatabase.ImportAsset(prefabPath, ImportAssetOptions.ForceSynchronousImport | ImportAssetOptions.ForceUpdate);
            var reloaded = PrefabUtility.LoadPrefabContents(prefabPath);
            try
            {
                Assert.AreEqual(7, reloaded.GetComponent<Canvas>().sortingOrder);
                Assert.AreEqual(2, reloaded.GetComponent<PrefabAssignmentFixture>().backing);
            }
            finally
            {
                PrefabUtility.UnloadPrefabContents(reloaded);
            }
        }

        [TestCase(true)]
        [TestCase(false)]
        public void PublicReferenceFieldAssignmentPersistsAndPreservesUnrelatedDirtyAsset(bool clear)
        {
            string path = folder + "/Reference.asset";
            var target = ScriptableObject.CreateInstance<MCPForUnityTests.EditMode.Tools.ReferenceDiagnosticFixture>();
            target.reference = unrelated;
            AssetDatabase.CreateAsset(target, path);
            byte[] before = File.ReadAllBytes(path);
            unrelated.frameRate = 17;
            EditorUtility.SetDirty(unrelated);
            JObject response = JObject.FromObject(
                ManageAsset.HandleCommand(
                    new JObject
                    {
                        ["action"] = "modify",
                        ["path"] = path,
                        ["properties"] = new JObject { ["reference"] = clear ? JValue.CreateNull() : new JValue(folder + "/Missing.asset") },
                    }
                )
            );
            Assert.AreEqual(clear, response.Value<bool>("success"), response.ToString());
            CollectionAssert.AreEqual(original, File.ReadAllBytes(unrelatedPath));
            Assert.IsTrue(EditorUtility.IsDirty(unrelated));
            if (clear)
                CollectionAssert.AreNotEqual(before, File.ReadAllBytes(path));
            else
                CollectionAssert.AreEqual(before, File.ReadAllBytes(path));
            UnityEngine.Resources.UnloadAsset(target);
            AssetDatabase.ImportAsset(path, ImportAssetOptions.ForceSynchronousImport | ImportAssetOptions.ForceUpdate);
            var reloaded = AssetDatabase.LoadAssetAtPath<MCPForUnityTests.EditMode.Tools.ReferenceDiagnosticFixture>(path);
            Assert.IsNotNull(reloaded);
            Assert.That(reloaded.reference, Is.EqualTo(clear ? null : unrelated));
            CollectionAssert.AreEqual(original, File.ReadAllBytes(unrelatedPath));
            Assert.IsTrue(EditorUtility.IsDirty(unrelated));
        }

        [TestCase("asset_create")]
        [TestCase("asset_modify")]
        [TestCase("controller_create")]
        [TestCase("controller_add_state")]
        [TestCase("controller_add_parameter")]
        [TestCase("controller_add_layer")]
        [TestCase("clip_create")]
        public void TargetMutationPreservesUnrelatedBytesAndDirtyState(string action)
        {
            string controllerPath = folder + "/Target.controller";
            string materialPath = folder + "/Target.mat";
            if (action.StartsWith("controller_add_", StringComparison.Ordinal))
                AnimatorController.CreateAnimatorControllerAtPath(controllerPath);
            if (action == "asset_modify")
                AssetDatabase.CreateAsset(new Material(Shader.Find("Standard")), materialPath);
            unrelated.frameRate = 17;
            EditorUtility.SetDirty(unrelated);
            Assert.IsTrue(EditorUtility.IsDirty(unrelated));
            JObject result;
            string targetPath;
            if (action.StartsWith("asset_", StringComparison.Ordinal))
            {
                targetPath = materialPath;
                result = JObject.FromObject(
                    ManageAsset.HandleCommand(
                        new JObject
                        {
                            ["action"] = action == "asset_create" ? "create" : "modify",
                            ["path"] = materialPath,
                            ["assetType"] = "material",
                            ["properties"] = new JObject { ["color"] = new JArray(0.1f, 0.2f, 0.3f, 1f) },
                        }
                    )
                );
            }
            else
            {
                targetPath = action == "clip_create" ? folder + "/Target.anim" : controllerPath;
                result = JObject.FromObject(
                    ManageAnimation.HandleCommand(
                        new JObject
                        {
                            ["action"] = action,
                            ["controllerPath"] = controllerPath,
                            ["clipPath"] = action == "clip_create" ? targetPath : null,
                            ["stateName"] = "OwnedState",
                            ["parameterName"] = "OwnedParameter",
                            ["layerName"] = "OwnedLayer",
                        }
                    )
                );
            }
            Assert.IsTrue(result.Value<bool>("success"), result.ToString());
            if (action == "asset_modify")
                StringAssert.Contains("modified successfully", (string)result["message"]);
            CollectionAssert.AreEqual(original, File.ReadAllBytes(unrelatedPath), "Unrelated on-disk bytes must not be saved.");
            Assert.IsTrue(EditorUtility.IsDirty(unrelated), "The user's pending changes must remain dirty.");
            Assert.IsTrue(File.Exists(targetPath), targetPath);
            Assert.IsFalse(EditorUtility.IsDirty(AssetDatabase.LoadMainAssetAtPath(targetPath)), "The requested target must be saved.");
            string serialized = File.ReadAllText(targetPath);
            if (action == "controller_add_state")
                StringAssert.Contains("OwnedState", serialized);
            if (action == "controller_add_parameter")
                StringAssert.Contains("OwnedParameter", serialized);
            if (action == "controller_add_layer")
                StringAssert.Contains("OwnedLayer", serialized);
        }
    }
}
