using System;
using System.Collections;
using System.Collections.Generic;
using System.IO;
using System.Reflection;
using System.Threading;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Editor.Tools;
using MCPForUnityTests.Editor.Helpers;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;
using UnityEngine.SceneManagement;

namespace MCPForUnity.Tests.EditMode.Tools
{
    [TestFixture]
    public class ManageSceneValidationTests
    {
        private readonly PrefabTestSceneFixture fixture = new PrefabTestSceneFixture();
        private Scene scene;
        private string assetFolder;
        private static Type ValidationType => typeof(ManageScene).Assembly.GetType("MCPForUnity.Editor.Helpers.SceneValidation", true);

        private static FieldInfo ValidationField(string name) => ValidationType.GetField(name, BindingFlags.NonPublic | BindingFlags.Static);

        [OneTimeSetUp]
        public void Bootstrap() => fixture.PrepareRunnerBootstrap();

        [OneTimeTearDown]
        public void Restore() => fixture.RestoreRunnerBootstrap();

        [SetUp]
        public void SetUp()
        {
            scene = fixture.Create("McpSceneValidation_", Guid.NewGuid().ToString("N"));
        }

        [TearDown]
        public void TearDown()
        {
            ValidationField("AfterObjectForTests").SetValue(null, null);
            ValidationField("AfterRepairForTests").SetValue(null, null);
            ValidationField("BeforeRepairCommitForTests").SetValue(null, null);
            fixture.Close();
            if (assetFolder != null)
            {
                AssetDatabase.DeleteAsset(assetFolder);
                assetFolder = null;
            }
        }

        private static JObject Validate(JObject options = null)
        {
            var parameters = options ?? new JObject();
            parameters["action"] = "validate";
            object result = ManageScene.HandleCommand(parameters);
            return result as JObject ?? JObject.FromObject(result);
        }

        [Test]
        public void ExactSceneValidationAndSaveDoNotSwitchActiveScene()
        {
            assetFolder = "Assets/__McpExactScene_" + Guid.NewGuid().ToString("N");
            AssetDatabase.CreateFolder("Assets", assetFolder.Substring(7));
            Assert.IsTrue(EditorSceneManager.SaveScene(scene, assetFolder + "/Original.unity"));
            Scene other = EditorSceneManager.NewScene(NewSceneSetup.EmptyScene, NewSceneMode.Additive);
            try
            {
                SceneManager.SetActiveScene(other);
                new GameObject("OnlyInOtherScene");
                SceneManager.SetActiveScene(scene);
                JObject validation = Validate(new JObject { ["scene_handle"] = SceneValidation.SceneHandle(other) });
                Assert.IsTrue((bool)validation["success"], validation.ToString());
                Assert.IsTrue(JToken.DeepEquals(SceneValidation.SceneHandle(other), validation["data"]["scene_handle"]));
                Assert.AreEqual(1, (int)validation["data"]["objectsScanned"]);
                JObject saved = JObject.FromObject(
                    ManageScene.HandleCommand(
                        new JObject
                        {
                            ["action"] = "save",
                            ["scene_handle"] = SceneValidation.SceneHandle(other),
                            ["path"] = assetFolder + "/Other.unity",
                            ["verify"] = true,
                        }
                    )
                );
                Assert.IsTrue((bool)saved["success"], saved.ToString());
                Assert.AreEqual(scene, SceneManager.GetActiveScene());
                Assert.IsFalse((bool)saved["data"]["scene_state"]["active"]);
                Assert.AreEqual("passed", (string)saved["data"]["verification"]["status"]);
            }
            finally
            {
                EditorSceneManager.CloseScene(other, true);
            }
        }

        private GameObject CreateMissingScriptFixture(out string path)
        {
            assetFolder = "Assets/__McpSceneValidation_" + Guid.NewGuid().ToString("N");
            AssetDatabase.CreateFolder("Assets", assetFolder.Substring(7));
            var source = new GameObject("MissingScriptRoot");
            var component = source.AddComponent<MCPForUnityTests.Editor.Tools.SafeEditRequiredComponent>();
            string guid = AssetDatabase.AssetPathToGUID(AssetDatabase.GetAssetPath(MonoScript.FromMonoBehaviour(component)));
            Assert.IsNotEmpty(guid);
            path = assetFolder + "/Missing.prefab";
            PrefabUtility.SaveAsPrefabAsset(source, path);
            UnityEngine.Object.DestroyImmediate(source);
            string yaml = File.ReadAllText(path);
            StringAssert.Contains("guid: " + guid, yaml);
            File.WriteAllText(path, yaml.Replace("guid: " + guid, "guid: " + Guid.NewGuid().ToString("N")));
            AssetDatabase.ImportAsset(path, ImportAssetOptions.ForceUpdate);
            var instance = (GameObject)PrefabUtility.InstantiatePrefab(AssetDatabase.LoadAssetAtPath<GameObject>(path), scene);
            PrefabUtility.UnpackPrefabInstance(instance, PrefabUnpackMode.Completely, InteractionMode.AutomatedAction);
            Assert.AreEqual(1, GameObjectUtility.GetMonoBehavioursWithMissingScriptCount(instance));
            return instance;
        }

        [Test]
        public void AssetComponents_ImportedMissingScriptRetainsValidComponentsAndSlots()
        {
            CreateMissingScriptFixture(out string path);
            byte[] before = File.ReadAllBytes(path);
            var prefab = AssetDatabase.LoadAssetAtPath<GameObject>(path);
            Assert.AreEqual(1, GameObjectUtility.GetMonoBehavioursWithMissingScriptCount(prefab));
            int dirtyCount = EditorUtility.GetDirtyCount(prefab);
            var response = JObject.FromObject(ManageAsset.HandleCommand(new JObject { ["action"] = "get_components", ["path"] = path }));
            Assert.IsTrue(response.Value<bool>("success"), response.ToString());
            var slots = (JArray)response["data"];
            Assert.AreEqual(3, slots.Count);
            Assert.AreEqual("UnityEngine.Transform", slots[0].Value<string>("typeName"));
            Assert.IsFalse(slots[0].Value<bool>("missingScript"));
            Assert.AreEqual(0, slots[0].Value<int>("slotIndex"));
            Assert.AreEqual("UnityEngine.BoxCollider", slots[1].Value<string>("typeName"));
            Assert.IsFalse(slots[1].Value<bool>("missingScript"));
            Assert.IsTrue(slots[2].Value<bool>("missingScript"));
            Assert.AreEqual(2, slots[2].Value<int>("slotIndex"));
            Assert.AreEqual(JTokenType.Null, slots[2]["typeName"].Type);
            Assert.AreEqual(JTokenType.Null, slots[2]["instanceID"].Type);
            StringAssert.Contains("1 missing script slot(s)", response.Value<string>("message"));
            Assert.AreEqual(dirtyCount, EditorUtility.GetDirtyCount(prefab));
            CollectionAssert.AreEqual(before, File.ReadAllBytes(path));
        }

        [Test]
        public void AssetComponents_HealthyPrefabKeepsExistingComponentFields()
        {
            assetFolder = "Assets/__McpSceneValidation_" + Guid.NewGuid().ToString("N");
            AssetDatabase.CreateFolder("Assets", assetFolder.Substring(7));
            string path = assetFolder + "/Healthy.prefab";
            var source = new GameObject("Healthy");
            try
            {
                source.AddComponent<MCPForUnityTests.Editor.Tools.SafeEditRequiredComponent>();
                PrefabUtility.SaveAsPrefabAsset(source, path);
            }
            finally
            {
                UnityEngine.Object.DestroyImmediate(source);
            }
            byte[] before = File.ReadAllBytes(path);
            var response = JObject.FromObject(ManageAsset.HandleCommand(new JObject { ["action"] = "get_components", ["path"] = path }));
            Assert.IsTrue(response.Value<bool>("success"), response.ToString());
            var slots = (JArray)response["data"];
            Assert.AreEqual(3, slots.Count);
            foreach (var slot in slots)
            {
                Assert.IsNotEmpty(slot.Value<string>("typeName"));
                Assert.AreNotEqual(0, slot.Value<int>("instanceID"));
                Assert.IsFalse(slot.Value<bool>("missingScript"));
            }
            CollectionAssert.AreEqual(before, File.ReadAllBytes(path));
        }

        [TestCase(null)]
        [TestCase("")]
        [TestCase("Assets/__McpMissingComponentInspection.prefab")]
        public void AssetComponents_MissingPathReturnsUsefulError(string path)
        {
            var response = JObject.FromObject(ManageAsset.HandleCommand(new JObject { ["action"] = "get_components", ["path"] = path }));
            Assert.IsFalse(response.Value<bool>("success"), response.ToString());
            StringAssert.Contains(string.IsNullOrEmpty(path) ? "'path' is required" : "Asset not found", response.Value<string>("error"));
        }

        [Test]
        public void AssetComponents_NonGameObjectReturnsUsefulError()
        {
            assetFolder = "Assets/__McpSceneValidation_" + Guid.NewGuid().ToString("N");
            AssetDatabase.CreateFolder("Assets", assetFolder.Substring(7));
            string path = assetFolder + "/Text.txt";
            File.WriteAllText(path, "component inspection fixture");
            AssetDatabase.ImportAsset(path, ImportAssetOptions.ForceSynchronousImport);
            var response = JObject.FromObject(ManageAsset.HandleCommand(new JObject { ["action"] = "get_components", ["path"] = path }));
            Assert.IsFalse(response.Value<bool>("success"), response.ToString());
            StringAssert.Contains("not a GameObject", response.Value<string>("error"));
            Assert.AreEqual("component inspection fixture", File.ReadAllText(path));
        }

        [Test]
        public void Validate_RealMissingScript_RepairsOnlyAfterCompleteObservation()
        {
            GameObject broken = CreateMissingScriptFixture(out string path);
            new GameObject("SecondRoot");
            byte[] original = File.ReadAllBytes(path);
            var partial = Validate(
                new JObject
                {
                    ["maxObjects"] = 1,
                    ["maxMilliseconds"] = 1000,
                    ["autoRepair"] = true,
                }
            );
            Assert.AreEqual("validation_repair_requires_complete", partial.Value<string>("code"));
            Assert.AreEqual(1, GameObjectUtility.GetMonoBehavioursWithMissingScriptCount(broken));
            CollectionAssert.AreEqual(original, File.ReadAllBytes(path));
            var complete = Validate(
                new JObject
                {
                    ["maxObjects"] = 10,
                    ["maxMilliseconds"] = 1000,
                    ["autoRepair"] = true,
                }
            );
            Assert.IsTrue(complete.Value<bool>("success"), complete.ToString());
            Assert.IsTrue(complete["data"].Value<bool>("scanComplete"));
            Assert.AreEqual(1, complete["data"].Value<int>("repaired"));
            Assert.IsFalse(complete["data"].Value<bool>("repairUndoable"));
            Assert.AreEqual("complete_plan", complete["data"].Value<string>("repairCommitPolicy"));
            Assert.AreEqual(0, GameObjectUtility.GetMonoBehavioursWithMissingScriptCount(broken));
            Assert.AreEqual(0, Validate(new JObject { ["maxMilliseconds"] = 1000 })["data"].Value<int>("totalIssues"));
            CollectionAssert.AreEqual(original, File.ReadAllBytes(path));
        }

        [Test]
        public void Validate_CancellationDuringCommit_CompletesAllPlannedMissingScripts()
        {
            GameObject broken = CreateMissingScriptFixture(out string path);
            var second = UnityEngine.Object.Instantiate(broken);
            Assert.AreEqual(1, GameObjectUtility.GetMonoBehavioursWithMissingScriptCount(second));
            byte[] original = File.ReadAllBytes(path);
            using var cancellation = new CancellationTokenSource();
            ValidationField("AfterRepairForTests").SetValue(null, new Action<int>(_ => cancellation.Cancel()));
            object raw = ManageScene
                .HandleCommand(
                    new JObject
                    {
                        ["action"] = "validate",
                        ["maxMilliseconds"] = 1000,
                        ["autoRepair"] = true,
                    },
                    cancellation.Token
                )
                .GetAwaiter()
                .GetResult();
            var response = raw as JObject ?? JObject.FromObject(raw);
            Assert.IsTrue(cancellation.IsCancellationRequested);
            Assert.IsTrue(response.Value<bool>("success"), response.ToString());
            Assert.AreEqual(2, response["data"].Value<int>("repaired"));
            Assert.AreEqual(0, GameObjectUtility.GetMonoBehavioursWithMissingScriptCount(broken));
            Assert.AreEqual(0, GameObjectUtility.GetMonoBehavioursWithMissingScriptCount(second));
            CollectionAssert.AreEqual(original, File.ReadAllBytes(path));
            Assert.AreEqual(0, Validate(new JObject { ["maxMilliseconds"] = 1000 })["data"].Value<int>("totalIssues"));
        }

        [Test]
        public void Validate_CancellationImmediatelyBeforeCommit_DoesNotRemoveMissingScripts()
        {
            GameObject broken = CreateMissingScriptFixture(out string path);
            var second = UnityEngine.Object.Instantiate(broken);
            byte[] original = File.ReadAllBytes(path);
            using var cancellation = new CancellationTokenSource();
            ValidationField("BeforeRepairCommitForTests").SetValue(null, new Action(() => cancellation.Cancel()));
            Assert.Throws<OperationCanceledException>(() =>
                ManageScene.HandleCommand(
                    new JObject
                    {
                        ["action"] = "validate",
                        ["maxMilliseconds"] = 1000,
                        ["autoRepair"] = true,
                    },
                    cancellation.Token
                )
            );
            Assert.AreEqual(1, GameObjectUtility.GetMonoBehavioursWithMissingScriptCount(broken));
            Assert.AreEqual(1, GameObjectUtility.GetMonoBehavioursWithMissingScriptCount(second));
            CollectionAssert.AreEqual(original, File.ReadAllBytes(path));
        }

        [Test]
        public void Validate_UnexpectedCommitFailure_ReportsActualPartialState()
        {
            GameObject first = CreateMissingScriptFixture(out string path);
            var second = UnityEngine.Object.Instantiate(first);
            byte[] original = File.ReadAllBytes(path);
            ValidationField("AfterRepairForTests")
                .SetValue(null, new Action<int>(_ => throw new InvalidOperationException("Injected native-boundary failure")));
            var response = Validate(new JObject { ["maxMilliseconds"] = 1000, ["autoRepair"] = true });
            Assert.AreEqual("scene_validation_repair_failed", response.Value<string>("code"));
            Assert.AreEqual(1, response["data"].Value<int>("repaired"));
            Assert.AreEqual(1, response["data"].Value<int>("remainingMissingScripts"));
            Assert.AreEqual("partial", response["data"].Value<string>("repairState"));
            Assert.IsFalse(response["data"].Value<bool>("undoable"));
            Assert.AreEqual(
                1,
                GameObjectUtility.GetMonoBehavioursWithMissingScriptCount(first) + GameObjectUtility.GetMonoBehavioursWithMissingScriptCount(second)
            );
            CollectionAssert.AreEqual(original, File.ReadAllBytes(path));
        }

        [TestCase(3000, false)]
        [TestCase(600, true)]
        public void Validate_LargeOrDeepHierarchy_ResumesWithoutDuplicates(int count, bool deep)
        {
            var root = new GameObject("ValidationRoot");
            Transform parent = root.transform;
            for (int i = 1; i < count; i++)
            {
                var child = new GameObject("Node" + i);
                child.transform.SetParent(parent, false);
                if (deep)
                    parent = child.transform;
            }
            string cursor = null;
            int visited = 0;
            int calls = 0;
            var ids = new HashSet<int>();
            ValidationField("AfterObjectForTests").SetValue(null, new Action<int>(id => Assert.IsTrue(ids.Add(id), "An object must not be scanned twice.")));
            do
            {
                var response = Validate(
                    new JObject
                    {
                        ["maxObjects"] = 37,
                        ["maxMilliseconds"] = 1000,
                        ["continuation"] = cursor,
                    }
                );
                Assert.IsTrue(response.Value<bool>("success"), response.ToString());
                var data = response["data"];
                Assert.LessOrEqual(data.Value<int>("objectsScannedThisCall"), 37);
                visited += data.Value<int>("objectsScannedThisCall");
                Assert.AreEqual(visited, data.Value<int>("objectsScanned"));
                cursor = data.Value<string>("continuation");
                Assert.AreEqual(cursor == null, data.Value<bool>("scanComplete"));
                Assert.Less(++calls, count * 3, "Continuation must eventually finish, including deep-stack unwinding.");
            } while (cursor != null);
            Assert.AreEqual(count, visited);
            Assert.AreEqual(count, ids.Count);
            Assert.Greater(calls, 1);
        }

        [Test]
        public void Validate_StaleContinuationAndChangedOptions_RequireRestart()
        {
            new GameObject("First");
            new GameObject("Second");
            var first = Validate(new JObject { ["maxObjects"] = 1 });
            string cursor = first["data"].Value<string>("continuation");
            Assert.IsNotNull(cursor);
            var changed = Validate(new JObject { ["maxObjects"] = 2, ["continuation"] = cursor });
            Assert.IsFalse(changed.Value<bool>("success"));
            Assert.AreEqual("stale_scene_validation", changed.Value<string>("code"));
            new GameObject("AddedAfterObservation");
            var stale = Validate(new JObject { ["maxObjects"] = 1, ["continuation"] = cursor });
            Assert.AreEqual("stale_scene_validation", stale.Value<string>("code"));
        }

        [TestCase(0)]
        [TestCase(10001)]
        public void Validate_InvalidBudget_IsRejected(int limit)
        {
            var response = Validate(new JObject { ["maxObjects"] = limit });
            Assert.IsFalse(response.Value<bool>("success"));
            Assert.AreEqual("invalid_scene_validation", response.Value<string>("code"));
        }

        [Test]
        public void Validate_IncompleteRepair_RejectsWithoutMutation()
        {
            new GameObject("First");
            new GameObject("Second");
            bool dirty = scene.isDirty;
            var response = Validate(new JObject { ["maxObjects"] = 1, ["autoRepair"] = true });
            Assert.IsFalse(response.Value<bool>("success"));
            Assert.AreEqual("validation_repair_requires_complete", response.Value<string>("code"));
            Assert.AreEqual(dirty, scene.isDirty);
            Assert.AreEqual(2, scene.rootCount);
        }

        [Test]
        public void Validate_PreCanceled_DoesNotMutate()
        {
            new GameObject("First");
            using var cancellation = new CancellationTokenSource();
            cancellation.Cancel();
            Assert.Throws<OperationCanceledException>(() =>
                ManageScene.HandleCommand(new JObject { ["action"] = "validate", ["autoRepair"] = true }, cancellation.Token)
            );
            Assert.AreEqual(1, scene.rootCount);
        }

        [Test]
        public void Validate_CancellationDuringResumption_KeepsCursorReplayable()
        {
            new GameObject("First");
            new GameObject("Second");
            new GameObject("Third");
            var first = Validate(new JObject { ["maxObjects"] = 1, ["maxMilliseconds"] = 1000 });
            string cursor = first["data"].Value<string>("continuation");
            bool dirty = scene.isDirty;
            using var cancellation = new CancellationTokenSource();
            ValidationField("AfterObjectForTests").SetValue(null, new Action<int>(_ => cancellation.Cancel()));
            Assert.Throws<OperationCanceledException>(() =>
                ManageScene.HandleCommand(
                    new JObject
                    {
                        ["action"] = "validate",
                        ["maxObjects"] = 1,
                        ["maxMilliseconds"] = 1000,
                        ["continuation"] = cursor,
                    },
                    cancellation.Token
                )
            );
            ValidationField("AfterObjectForTests").SetValue(null, null);
            var resumed = Validate(
                new JObject
                {
                    ["maxObjects"] = 1,
                    ["maxMilliseconds"] = 1000,
                    ["continuation"] = cursor,
                }
            );
            var replay = Validate(
                new JObject
                {
                    ["maxObjects"] = 1,
                    ["maxMilliseconds"] = 1000,
                    ["continuation"] = cursor,
                }
            );
            Assert.IsTrue(resumed.Value<bool>("success"), resumed.ToString());
            Assert.AreEqual(2, resumed["data"].Value<int>("objectsScanned"));
            Assert.AreEqual(2, replay["data"].Value<int>("objectsScanned"));
            Assert.AreEqual(dirty, scene.isDirty);
            Assert.AreEqual(3, scene.rootCount);
        }

        [Test]
        public void Validate_TinyTimeSlices_AdvanceAndEventuallyFinish()
        {
            new GameObject("First");
            new GameObject("Second");
            FieldInfo clock = ValidationField("Milliseconds");
            object original = clock.GetValue(null);
            double tick = 0;
            clock.SetValue(null, new Func<double>(() => ++tick));
            try
            {
                string cursor = null;
                int calls = 0;
                JObject result;
                do
                {
                    result = Validate(new JObject { ["maxMilliseconds"] = 1, ["continuation"] = cursor });
                    Assert.IsTrue(result.Value<bool>("success"), result.ToString());
                    cursor = result["data"].Value<string>("continuation");
                    Assert.Less(++calls, 12);
                } while (cursor != null);
                Assert.AreEqual(2, result["data"].Value<int>("objectsScanned"));
                Assert.IsTrue(result["data"].Value<bool>("scanComplete"));
            }
            finally
            {
                clock.SetValue(null, original);
            }
        }

        [Test]
        public void Validate_ExpiredContinuation_RequiresRestart()
        {
            new GameObject("First");
            new GameObject("Second");
            string cursor = Validate(new JObject { ["maxObjects"] = 1 })["data"].Value<string>("continuation");
            var states = (IDictionary)ValidationField("States").GetValue(null);
            object state = states[cursor];
            state.GetType().GetField("Expires", BindingFlags.Instance | BindingFlags.NonPublic).SetValue(state, 0.0);
            var response = Validate(new JObject { ["maxObjects"] = 1, ["continuation"] = cursor });
            Assert.AreEqual("stale_scene_validation", response.Value<string>("code"));
        }

        [Test]
        public void Validate_CleanScene_ReturnsNoIssues()
        {
            var p = new JObject { ["action"] = "validate" };
            var result = ManageScene.HandleCommand(p);
            var r = result as JObject ?? JObject.FromObject(result);
            Assert.IsTrue(r.Value<bool>("success"), r.ToString());
            var data = r["data"];
            Assert.IsNotNull(data);
            Assert.AreEqual(0, data.Value<int>("totalIssues"));
        }

        [Test]
        public void Validate_WithAutoRepair_CleanScene_RepairsNothing()
        {
            var p = new JObject { ["action"] = "validate", ["autoRepair"] = true };
            var result = ManageScene.HandleCommand(p);
            var r = result as JObject ?? JObject.FromObject(result);
            Assert.IsTrue(r.Value<bool>("success"), r.ToString());
            Assert.AreEqual(0, r["data"].Value<int>("repaired"));
        }
    }
}
