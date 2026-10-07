using System;
using System.Collections;
using System.Linq;
using System.Reflection;
using MCPForUnity.Editor.Tools.Prefabs;
using MCPForUnity.Runtime.Helpers;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;
using UnityEngine.SceneManagement;
using Object = UnityEngine.Object;

namespace MCPForUnity.Tests.EditMode.Tools
{
    public class ManagePrefabOverridesTests
    {
        private string Folder;
        private string PrefabPath => Folder + "/Source.prefab";
        private Scene previousScene;
        private Scene scene;
        private GameObject instance;
        private bool restoreEmptyScene;
        private bool restoreRunnerDefaultScene;

        [OneTimeSetUp]
        public void PrepareRunnerBootstrap()
        {
            Scene initial = SceneManager.GetActiveScene();
            if (
                SceneManager.sceneCount == 1
                && initial.IsValid()
                && initial.rootCount != 0
                && string.IsNullOrEmpty(initial.path)
                && !initial.isDirty
                && IsRunnerBootstrapScene(initial)
            )
            {
                // The pinned runner creates this scene after external editor initialization.
                // Normalize only its exact owned scene once, so per-case cleanup can restore
                // an empty scene without later mistaking a recreated default scene for user data.
                EditorSceneManager.NewScene(NewSceneSetup.EmptyScene, NewSceneMode.Single);
                restoreRunnerDefaultScene = true;
            }
        }

        [OneTimeTearDown]
        public void RestoreRunnerBootstrap()
        {
            if (!restoreRunnerDefaultScene)
                return;
            Scene current = SceneManager.GetActiveScene();
            Assert.IsTrue(
                SceneManager.sceneCount == 1 && current.IsValid() && string.IsNullOrEmpty(current.path) && !current.isDirty && current.rootCount == 0,
                "The fixture must finish with only its clean empty replacement scene before restoring the runner default."
            );
            EditorSceneManager.NewScene(NewSceneSetup.DefaultGameObjects, NewSceneMode.Single);
        }

        private static bool IsRunnerBootstrapScene(Scene candidate)
        {
            Type holderType = typeof(UnityEditor.TestTools.TestRunner.Api.TestRunnerApi).Assembly.GetType(
                "UnityEditor.TestTools.TestRunner.TestRun.TestJobDataHolder"
            );
            FieldInfo runsField = holderType?.GetField("TestRuns");
            if (runsField == null)
                return false;
            foreach (Object holder in Resources.FindObjectsOfTypeAll(holderType))
            {
                if (!(runsField.GetValue(holder) is IEnumerable runs))
                    continue;
                foreach (object run in runs)
                {
                    FieldInfo runningField = run.GetType().GetField("isRunning");
                    FieldInfo sceneField = run.GetType().GetField("InitTestScene");
                    if (runningField?.GetValue(run) is bool running && running && sceneField?.GetValue(run) is Scene bootstrap && bootstrap == candidate)
                        return true;
                }
            }
            return false;
        }

        [SetUp]
        public void SetUp()
        {
            scene = default;
            previousScene = default;
            Folder = null;
            restoreEmptyScene = false;
            if (PrefabStageUtility.GetCurrentPrefabStage() != null)
                Assert.Ignore("Run prefab override fixtures in the main stage; the current prefab stage is preserved.");
            previousScene = SceneManager.GetActiveScene();
            bool emptyPath = string.IsNullOrEmpty(previousScene.path);
            restoreEmptyScene = SceneManager.sceneCount == 1 && previousScene.IsValid() && emptyPath && !previousScene.isDirty && previousScene.rootCount == 0;
            if (!restoreEmptyScene)
            {
                for (int i = 0; i < SceneManager.sceneCount; i++)
                    if (string.IsNullOrEmpty(SceneManager.GetSceneAt(i).path))
                        Assert.Ignore(
                            $"Preserving existing scenes: sceneCount={SceneManager.sceneCount}, pathEmpty={(emptyPath ? 1 : 0)}, dirty={(previousScene.isDirty ? 1 : 0)}, rootCount={previousScene.rootCount}."
                        );
            }
            // Single replacement is restricted to the clean, empty initial scene. All
            // populated/saved scenes remain loaded; an unsaved populated scene is preserved.
            scene = EditorSceneManager.NewScene(NewSceneSetup.EmptyScene, restoreEmptyScene ? NewSceneMode.Single : NewSceneMode.Additive);
            string suffix = System.Guid.NewGuid().ToString("N");
            scene.name = "McpPrefabOverrides_" + suffix;
            Assert.IsTrue(scene.IsValid() && scene.isLoaded, "The owned editor fixture scene must be loaded.");
            // NewScene(Single) already selects its scene. SetActiveScene can return false
            // for that redundant request in Edit Mode; assert the actual active-scene state.
            if (SceneManager.GetActiveScene() != scene)
                Assert.IsTrue(SceneManager.SetActiveScene(scene), "The owned additive fixture scene must become active.");
            Assert.AreEqual(scene, SceneManager.GetActiveScene(), "Prefab commands must resolve against the owned fixture scene.");
            Folder = "Assets/__McpPrefabOverrides_" + suffix;
            Assert.IsFalse(AssetDatabase.IsValidFolder(Folder));
            AssetDatabase.CreateFolder("Assets", "__McpPrefabOverrides_" + suffix);
            var source = new GameObject("Source");
            var child = new GameObject("Child");
            child.transform.SetParent(source.transform);
            child.AddComponent<BoxCollider>();
            PrefabUtility.SaveAsPrefabAsset(source, PrefabPath);
            Object.DestroyImmediate(source);
            instance = (GameObject)PrefabUtility.InstantiatePrefab(AssetDatabase.LoadAssetAtPath<GameObject>(PrefabPath), scene);
            Undo.ClearAll();
        }

        [TearDown]
        public void TearDown()
        {
            if (scene.IsValid())
                Undo.ClearAll();
            if (previousScene.IsValid() && previousScene.isLoaded)
                SceneManager.SetActiveScene(previousScene);
            if (scene.IsValid() && scene.isLoaded)
            {
                StringAssert.StartsWith("McpPrefabOverrides_", scene.name);
                if (restoreEmptyScene)
                    EditorSceneManager.NewScene(NewSceneSetup.EmptyScene, NewSceneMode.Single);
                else
                    EditorSceneManager.CloseScene(scene, true);
            }
            if (!string.IsNullOrEmpty(Folder))
            {
                StringAssert.StartsWith("Assets/__McpPrefabOverrides_", Folder);
                AssetDatabase.DeleteAsset(Folder);
            }
        }

        private JObject Command(string action, JArray ids = null, string path = null)
        {
            var args = new JObject { ["action"] = action, ["target"] = instance.GetInstanceIDCompat() };
            if (ids != null)
                args["overrideIds"] = ids;
            if (path != null)
                args["prefabPath"] = path;
            return JObject.FromObject(ManagePrefabs.HandleCommand(args));
        }

        private BoxCollider OverrideCollider()
        {
            var collider = instance.GetComponentInChildren<BoxCollider>();
            collider.isTrigger = true;
            collider.size = new Vector3(2, 3, 4);
            PrefabUtility.RecordPrefabInstancePropertyModifications(collider);
            return collider;
        }

        private string PropertyId(string path)
        {
            JObject result = Command("list_overrides");
            Assert.IsTrue(result.Value<bool>("success"), result.ToString());
            return result["data"]["entries"].Single(e => e.Value<string>("propertyPath") == path).Value<string>("overrideId");
        }

        [TestCase("list_overrides")]
        [TestCase("LIST_OVERRIDES")]
        [TestCase("List_Overrides")]
        public void List_ReportsValuesAndGroupsWithoutChangingOverrides(string action)
        {
            BoxCollider collider = OverrideCollider();
            var result = Command(action);
            Assert.IsTrue(result.Value<bool>("success"), result.ToString());
            var entry = result["data"]["entries"].Single(e => e.Value<string>("propertyPath") == "m_IsTrigger");
            Assert.AreEqual(true, entry.Value<bool>("currentValue"));
            Assert.AreEqual(false, entry.Value<bool>("prefabValue"));
            Assert.AreEqual(collider.GetInstanceIDCompat(), entry["target"].Value<int>("instanceId"));
            Assert.IsNotEmpty((JArray)result["data"]["groups"]);
            Assert.IsTrue(collider.isTrigger);
        }

        [TestCase("revert_overrides")]
        [TestCase("REVERT_OVERRIDES")]
        [TestCase("Revert_Overrides")]
        public void Revert_OnlySelectedPropertyAndSupportsUndo(string action)
        {
            BoxCollider collider = OverrideCollider();
            var result = Command(action, new JArray(PropertyId("m_IsTrigger")));
            Assert.IsTrue(result.Value<bool>("success"), result.ToString());
            Assert.IsFalse(collider.isTrigger);
            Assert.AreEqual(new Vector3(2, 3, 4), collider.size);
            Assert.IsTrue(scene.isDirty);
            Undo.PerformUndo();
            Assert.IsTrue(collider.isTrigger);
        }

        [TestCase("apply_overrides")]
        [TestCase("APPLY_OVERRIDES")]
        [TestCase("Apply_Overrides")]
        public void Apply_OnlySelectedPropertyPersistsToAsset(string action)
        {
            OverrideCollider();
            var result = Command(action, new JArray(PropertyId("m_IsTrigger")), PrefabPath);
            Assert.IsTrue(result.Value<bool>("success"), result.ToString());
            var assetCollider = AssetDatabase.LoadAssetAtPath<GameObject>(PrefabPath).GetComponentInChildren<BoxCollider>();
            Assert.IsTrue(assetCollider.isTrigger);
            Assert.AreEqual(Vector3.one, assetCollider.size);
            Assert.AreEqual(new Vector3(2, 3, 4), instance.GetComponentInChildren<BoxCollider>().size);
        }

        [TestCase("LIST_OVERRIDES")]
        [TestCase("List_Overrides")]
        public void List_WithSelectionRemainsReadOnly(string action)
        {
            BoxCollider collider = OverrideCollider();
            var result = Command(action, new JArray(PropertyId("m_IsTrigger")));
            Assert.IsTrue(result.Value<bool>("success"), result.ToString());
            Assert.IsTrue(collider.isTrigger, "Listing must not revert a selected override.");
            Assert.IsNotEmpty((JArray)result["data"]["entries"]);
            Assert.IsFalse(AssetDatabase.LoadAssetAtPath<GameObject>(PrefabPath).GetComponentInChildren<BoxCollider>().isTrigger);
        }

        [TestCase("APPLY_OVERRIDES")]
        [TestCase("Apply_Overrides")]
        public void Apply_RequiresDestinationBeforeChangingOverrides(string action)
        {
            BoxCollider collider = OverrideCollider();
            var result = Command(action, new JArray(PropertyId("m_IsTrigger")));
            Assert.IsFalse(result.Value<bool>("success"), result.ToString());
            Assert.IsTrue(collider.isTrigger);
            Assert.AreEqual(new Vector3(2, 3, 4), collider.size);
            Assert.IsFalse(AssetDatabase.LoadAssetAtPath<GameObject>(PrefabPath).GetComponentInChildren<BoxCollider>().isTrigger);
        }

        [Test]
        public void InvalidSelection_FailsBeforeAnyPropertyChanges()
        {
            BoxCollider collider = OverrideCollider();
            var result = Command("revert_overrides", new JArray(PropertyId("m_IsTrigger"), "property:stale:id"));
            Assert.IsFalse(result.Value<bool>("success"));
            Assert.IsTrue(collider.isTrigger);
            Assert.AreEqual(new Vector3(2, 3, 4), collider.size);
        }

        [Test]
        public void Writes_RequireExplicitSelectionAndDestination()
        {
            OverrideCollider();
            Assert.IsFalse(Command("revert_overrides").Value<bool>("success"));
            Assert.IsFalse(Command("revert_overrides", new JArray()).Value<bool>("success"));
            Assert.IsFalse(Command("apply_overrides", new JArray(PropertyId("m_IsTrigger"))).Value<bool>("success"));
            Assert.IsFalse(Command("apply_overrides", new JArray(PropertyId("m_IsTrigger")), "Assets/../Outside.prefab").Value<bool>("success"));
            Assert.IsTrue(instance.GetComponentInChildren<BoxCollider>().isTrigger);
        }

        [Test]
        public void List_RejectsAmbiguousNamesAndSupportsObjectPagingFilter()
        {
            BoxCollider collider = OverrideCollider();
            var args = new JObject
            {
                ["action"] = "list_overrides",
                ["target"] = instance.GetInstanceIDCompat(),
                ["objectId"] = collider.GetInstanceIDCompat(),
                ["propertyFilter"] = "m_",
                ["pageSize"] = 1,
            };
            var result = JObject.FromObject(ManagePrefabs.HandleCommand(args));
            Assert.IsTrue(result.Value<bool>("success"), result.ToString());
            Assert.AreEqual(1, ((JArray)result["data"]["entries"]).Count);
            Assert.IsNotNull(result["data"]["nextOffset"].Value<int?>());
            var duplicate = new GameObject(instance.name);
            args["target"] = instance.name;
            result = JObject.FromObject(ManagePrefabs.HandleCommand(args));
            Assert.IsFalse(result.Value<bool>("success"));
            StringAssert.Contains("ambiguous", result.Value<string>("error"));
            Object.DestroyImmediate(duplicate);
        }

        [Test]
        public void StructuralOverrides_ListAndRevertAddedGameObject()
        {
            var added = new GameObject("Added");
            added.transform.SetParent(instance.transform);
            var result = Command("list_overrides");
            var entry = result["data"]["entries"].Single(e => e.Value<string>("kind") == "added_gameobject");
            result = Command("revert_overrides", new JArray(entry.Value<string>("overrideId")));
            Assert.IsTrue(result.Value<bool>("success"), result.ToString());
            Assert.IsNull(instance.transform.Find("Added"));
            Undo.PerformUndo();
            Assert.IsNotNull(instance.transform.Find("Added"));
        }

        [Test]
        public void StructuralOverrides_ListAndRevertRemovedComponent()
        {
            Object.DestroyImmediate(instance.GetComponentInChildren<BoxCollider>());
            var result = Command("list_overrides");
            var entry = result["data"]["entries"].Single(e => e.Value<string>("kind") == "removed_component");
            result = Command("revert_overrides", new JArray(entry.Value<string>("overrideId")));
            Assert.IsTrue(result.Value<bool>("success"), result.ToString());
            Assert.IsNotNull(instance.GetComponentInChildren<BoxCollider>());
        }

        [TestCase("apply_overrides")]
        [TestCase("APPLY_OVERRIDES")]
        [TestCase("Apply_Overrides")]
        public void StructuralOverrides_ApplyAddedObjectIncludesItsSubtreeOnly(string action)
        {
            var added = new GameObject("Added");
            added.transform.SetParent(instance.transform);
            var grandchild = new GameObject("Grandchild");
            grandchild.transform.SetParent(added.transform);
            OverrideCollider();
            var listed = Command("list_overrides");
            var entry = listed["data"]["entries"].Single(e => e.Value<string>("kind") == "added_gameobject");
            var result = Command(action, new JArray(entry.Value<string>("overrideId")), PrefabPath);
            Assert.IsTrue(result.Value<bool>("success"), result.ToString());
            var asset = AssetDatabase.LoadAssetAtPath<GameObject>(PrefabPath);
            Assert.IsNotNull(asset.transform.Find("Added/Grandchild"));
            Assert.IsFalse(asset.GetComponentInChildren<BoxCollider>().isTrigger);
            Assert.IsTrue(instance.GetComponentInChildren<BoxCollider>().isTrigger);
        }

        [TestCase(false)]
        [TestCase(true)]
        public void StructuralOverrides_AddedComponentUsesExplicitSelection(bool apply)
        {
            var component = instance.AddComponent<AudioSource>();
            var listed = Command("list_overrides");
            var entry = listed["data"]["entries"].Single(e => e.Value<string>("kind") == "added_component");
            var result = Command(apply ? "apply_overrides" : "revert_overrides", new JArray(entry.Value<string>("overrideId")), apply ? PrefabPath : null);
            Assert.IsTrue(result.Value<bool>("success"), result.ToString());
            Assert.AreEqual(apply, instance.GetComponent<AudioSource>() != null);
            Assert.AreEqual(apply, AssetDatabase.LoadAssetAtPath<GameObject>(PrefabPath).GetComponent<AudioSource>() != null);
        }

        [TestCase(false)]
        [TestCase(true)]
        public void StructuralOverrides_NonTransformDependencyFailsBeforeMutation(bool apply)
        {
            var source = instance.AddComponent<AudioSource>();
            var filter = instance.AddComponent<AudioLowPassFilter>();
            var listed = Command("list_overrides");
            var entry = listed["data"]
                ["entries"]
                .Single(e => e.Value<string>("kind") == "added_component" && e["target"].Value<int>("instanceId") == source.GetInstanceIDCompat());
            var result = Command(apply ? "apply_overrides" : "revert_overrides", new JArray(entry.Value<string>("overrideId")), apply ? PrefabPath : null);
            Assert.IsFalse(result.Value<bool>("success"), result.ToString());
            StringAssert.Contains("RequireComponent", result.Value<string>("error"));
            Assert.IsTrue(source != null);
            Assert.IsTrue(filter != null);
            Assert.IsNull(AssetDatabase.LoadAssetAtPath<GameObject>(PrefabPath).GetComponent<AudioSource>());
            Assert.IsNull(AssetDatabase.LoadAssetAtPath<GameObject>(PrefabPath).GetComponent<AudioLowPassFilter>());
        }

        [TestCase(false)]
        [TestCase(true)]
        public void StructuralOverrides_CoupledComponentFailsBeforeMutation(bool apply)
        {
            var particles = instance.AddComponent<ParticleSystem>();
            var renderer = instance.GetComponent<ParticleSystemRenderer>();
            var listed = Command("list_overrides");
            var entry = listed["data"]
                ["entries"]
                .Single(e => e.Value<string>("kind") == "added_component" && e["target"].Value<int>("instanceId") == particles.GetInstanceIDCompat());
            var result = Command(apply ? "apply_overrides" : "revert_overrides", new JArray(entry.Value<string>("overrideId")), apply ? PrefabPath : null);
            Assert.IsFalse(result.Value<bool>("success"), result.ToString());
            StringAssert.Contains("Coupled", result.Value<string>("error"));
            Assert.IsTrue(particles != null);
            Assert.IsTrue(renderer != null);
            Assert.IsNull(AssetDatabase.LoadAssetAtPath<GameObject>(PrefabPath).GetComponent<ParticleSystem>());
        }

#if UNITY_2022_2_OR_NEWER
        [Test]
        public void StructuralOverrides_ListAndRevertRemovedGameObject()
        {
            Object.DestroyImmediate(instance.transform.Find("Child").gameObject);
            var listed = Command("list_overrides");
            Assert.IsTrue(listed["data"].Value<bool>("removedGameObjectsSupported"));
            var entry = listed["data"]["entries"].Single(e => e.Value<string>("kind") == "removed_gameobject");
            var result = Command("revert_overrides", new JArray(entry.Value<string>("overrideId")));
            Assert.IsTrue(result.Value<bool>("success"), result.ToString());
            Assert.IsNotNull(instance.transform.Find("Child"));
        }
#endif

        [Test]
        public void NestedInstance_RequiresItsOwnDestinationAsset()
        {
            Object.DestroyImmediate(instance);
            var outer = new GameObject("Outer");
            var nested = (GameObject)PrefabUtility.InstantiatePrefab(AssetDatabase.LoadAssetAtPath<GameObject>(PrefabPath), scene);
            nested.transform.SetParent(outer.transform);
            string outerPath = Folder + "/Outer.prefab";
            PrefabUtility.SaveAsPrefabAsset(outer, outerPath);
            Object.DestroyImmediate(outer);
            outer = (GameObject)PrefabUtility.InstantiatePrefab(AssetDatabase.LoadAssetAtPath<GameObject>(outerPath), scene);
            instance = outer.transform.GetChild(0).gameObject;
            OverrideCollider();
            var listed = Command("list_overrides");
            Assert.AreEqual(PrefabPath, listed["data"].Value<string>("prefabPath"));
            var rejected = Command("apply_overrides", new JArray(PropertyId("m_IsTrigger")), outerPath);
            Assert.IsFalse(rejected.Value<bool>("success"));
            Assert.IsFalse(AssetDatabase.LoadAssetAtPath<GameObject>(PrefabPath).GetComponentInChildren<BoxCollider>().isTrigger);
        }
    }
}
