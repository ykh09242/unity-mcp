using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using MCPForUnity.Editor.Tools;
using MCPForUnityTests.Editor.Helpers;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;
using UnityEngine.SceneManagement;
using Object = UnityEngine.Object;

namespace MCPForUnityTests.EditMode.Tools
{
    [TestFixture]
    public sealed class ReferenceDiagnosticsTests
    {
        private readonly PrefabTestSceneFixture sceneFixture = new PrefabTestSceneFixture();
        private string root;
        private Scene original;
        private readonly List<Scene> ownedScenes = new List<Scene>();
        private SerializationMode serialization;

        [OneTimeSetUp]
        public void OneTimeSetUp() => sceneFixture.PrepareRunnerBootstrap();

        [OneTimeTearDown]
        public void OneTimeTearDown() => sceneFixture.RestoreRunnerBootstrap();

        [SetUp]
        public void SetUp()
        {
            serialization = EditorSettings.serializationMode;
            EditorSettings.serializationMode = SerializationMode.ForceText;
            string name = "__McpReferenceDiagnostics_" + Guid.NewGuid().ToString("N");
            root = "Assets/" + name;
            AssetDatabase.CreateFolder("Assets", name);
            original = sceneFixture.Create("McpReferencesBase_", Guid.NewGuid().ToString("N"));
            Assert.IsTrue(
                EditorSceneManager.SaveScene(original, root + "/" + original.name + ".unity"),
                "The helper-owned base scene must save without changing its ownership name."
            );
        }

        [TearDown]
        public void TearDown()
        {
            try
            {
                foreach (Scene scene in ownedScenes)
                    if (scene.IsValid() && scene.isLoaded)
                        EditorSceneManager.CloseScene(scene, true);
                ownedScenes.Clear();
                if (original.IsValid() && original.isLoaded)
                    SceneManager.SetActiveScene(original);
                sceneFixture.Close();
            }
            finally
            {
                try
                {
                    if (!string.IsNullOrEmpty(root))
                    {
                        StringAssert.StartsWith("Assets/__McpReferenceDiagnostics_", root);
                        Assert.IsTrue(AssetDatabase.DeleteAsset(root), "The random fixture-owned asset folder must be removed.");
                    }
                }
                finally
                {
                    EditorSettings.serializationMode = serialization;
                }
            }
        }

        [Test]
        public void LargeAssetReferenceRequestHashesCompleteSourceWithoutSmallProjectClamps()
        {
            string path = root + "/large.bytes";
            using (var stream = new FileStream(path, FileMode.CreateNew, FileAccess.Write, FileShare.None))
                stream.SetLength(17 * 1024 * 1024);
            AssetDatabase.ImportAsset(path, ImportAssetOptions.ForceSynchronousImport);
            JObject response = JObject.FromObject(
                ManageAsset.HandleCommand(
                    new JObject
                    {
                        ["action"] = "get_dependencies",
                        ["path"] = path,
                        ["scanRoot"] = root,
                        ["maxAssets"] = 250000,
                        ["maxWork"] = 4000000,
                    }
                )
            );
            JToken data = Success(response);
            Assert.That((bool)data["scanComplete"], Is.True);
            Assert.That((int)data["maxAssets"], Is.EqualTo(250000));
            Assert.That(new FileInfo(path).Length, Is.EqualTo(17 * 1024 * 1024));
        }

        private Scene OwnedScene()
        {
            // The helper-owned saved base makes additive creation safe without touching caller scenes.
            Scene scene = EditorSceneManager.NewScene(NewSceneSetup.EmptyScene, NewSceneMode.Additive);
            ownedScenes.Add(scene);
            if (SceneManager.GetActiveScene() != scene)
                Assert.IsTrue(SceneManager.SetActiveScene(scene), "The owned additive scene must become active before creating fixture objects.");
            Assert.AreEqual(scene.handle, SceneManager.GetActiveScene().handle, "Fixture objects must be created in the owned additive scene.");
            return scene;
        }

        private ReferenceDiagnosticFixture Make(string name, Object target = null)
        {
            var value = ScriptableObject.CreateInstance<ReferenceDiagnosticFixture>();
            value.reference = target;
            AssetDatabase.CreateAsset(value, root + "/" + name + ".asset");
            return value;
        }

        private JObject Asset(string action, Object target, bool recursive = false, int size = 100, int page = 1, string revision = null, int work = 10000)
        {
            var request = new JObject
            {
                ["action"] = action,
                ["path"] = AssetDatabase.GetAssetPath(target),
                ["scanRoot"] = root,
                ["recursive"] = recursive,
                ["maxAssets"] = 32,
                ["maxWork"] = work,
                ["pageSize"] = size,
                ["pageNumber"] = page,
            };
            if (action == "preview_move")
                request["destination"] = root + "/Moved.asset";
            if (revision != null)
                request["referenceRevision"] = revision;
            return JObject.FromObject(ManageAsset.HandleCommand(request));
        }

        private JObject Diagnose(string scope, string path = null, int size = 100, int page = 1, int work = 100000, string revision = null)
        {
            var request = new JObject
            {
                ["action"] = "validate_references",
                ["referenceScope"] = scope,
                ["maxObjects"] = 1024,
                ["maxWork"] = work,
                ["pageSize"] = size,
                ["pageNumber"] = page,
            };
            if (path != null)
                request["path"] = path;
            if (revision != null)
                request["referenceRevision"] = revision;
            return JObject.FromObject(ManageScene.HandleCommand(request));
        }

        private static JToken Success(JObject response)
        {
            Assert.IsTrue(response.Value<bool>("success"), response.ToString());
            return response["data"];
        }

        [Test]
        public void DependencyChainCyclePagesAndUnloadedSceneAreObservedWithoutOpening()
        {
            // Given: A -> B -> C -> A, plus a saved scene referencing C.
            var c = Make("C");
            var b = Make("B", c);
            var a = Make("A", b);
            c.reference = a;
            EditorUtility.SetDirty(c);
            AssetDatabase.SaveAssets();
            Scene scene = OwnedScene();
            var go = new GameObject("ReferenceOwner");
            SceneManager.MoveGameObjectToScene(go, scene);
            var renderer = go.AddComponent<MeshRenderer>();
            var material = new Material(Shader.Find("Hidden/InternalErrorShader"));
            string materialPath = root + "/Target.mat";
            AssetDatabase.CreateAsset(material, materialPath);
            renderer.sharedMaterial = material;
            string scenePath = root + "/Unloaded.unity";
            Assert.IsTrue(EditorSceneManager.SaveScene(scene, scenePath));
            EditorSceneManager.CloseScene(scene, true);
            int count = SceneManager.sceneCount;
            // When: pages traverse the cycle and reverse scanning includes unloaded scenes.
            var first = Success(Asset("get_dependencies", a, true, 1));
            var second = Success(Asset("get_dependencies", a, true, 1, 2, (string)first["referenceRevision"]));
            var reverse = Success(Asset("get_reverse_references", material));
            // Then: each reachable asset appears once, edges carry provenance and scene stays unloaded.
            var all = Success(Asset("get_dependencies", a, true));
            CollectionAssert.IsSubsetOf(
                new[] { AssetDatabase.GetAssetPath(b), AssetDatabase.GetAssetPath(c) },
                all["entries"].Select(row => (string)row["path"]).ToArray()
            );
            Assert.AreNotEqual((string)first["entries"][0]["path"], (string)second["entries"][0]["path"]);
            Assert.IsTrue(all["entries"].All(row => row["provenance"] != null));
            Assert.IsTrue(
                reverse["entries"].Any(row => (string)row["path"] == scenePath),
                "Unloaded scene must appear as a direct AssetDatabase referrer: " + reverse
            );
            Assert.AreEqual(count, SceneManager.sceneCount);
            Assert.IsFalse(SceneManager.GetSceneByPath(scenePath).isLoaded);
        }

        [Test]
        public void PreviewRecheckDetectsSourceAndReferrerDriftAndPreservesGuid()
        {
            // Given: an observed move/delete preview with a dependent asset.
            var target = Make("Target");
            var owner = Make("Owner", target);
            AssetDatabase.SaveAssets();
            string path = AssetDatabase.GetAssetPath(target);
            string guid = AssetDatabase.AssetPathToGUID(path);
            var preview = Success(Asset("preview_move", target));
            Assert.AreEqual("guarded_move_delete", (string)preview["mutationApplication"]);
            Assert.IsTrue((bool)Success(Asset("preview_move", target, revision: (string)preview["referenceRevision"]))["revisionMatches"]);
            // When: a referencing source changes after the preview.
            owner.reference = null;
            EditorUtility.SetDirty(owner);
            AssetDatabase.SaveAssets();
            var drift = Asset("preview_move", target, revision: (string)preview["referenceRevision"]);
            // Then: explicit recheck refuses drift and no asset identity/path is mutated.
            Assert.IsFalse((bool)drift["success"], drift.ToString());
            Assert.AreEqual(guid, AssetDatabase.AssetPathToGUID(path));
            Assert.IsEmpty(AssetDatabase.AssetPathToGUID(root + "/Moved.asset"));
            var deletion = Success(Asset("preview_delete", target));
            Assert.AreEqual(0, deletion.Value<int>("totalEntries"));
            target.reference = owner;
            EditorUtility.SetDirty(target);
            AssetDatabase.SaveAssets();
            Assert.IsFalse((bool)Asset("preview_delete", target, revision: (string)deletion["referenceRevision"])["success"]);
        }

        private JObject Apply(string action, Object target, JToken preview, string destination = null, int work = 10000)
        {
            var request = new JObject
            {
                ["action"] = action,
                ["path"] = AssetDatabase.GetAssetPath(target),
                ["scanRoot"] = root,
                ["recursive"] = false,
                ["maxAssets"] = 32,
                ["maxWork"] = work,
                ["referenceRevision"] = preview["referenceRevision"],
            };
            if (destination != null)
                request["destination"] = destination;
            return JObject.FromObject(ManageAsset.HandleCommand(request));
        }

        [Test]
        public void GuardedMoveRejectsDriftOrDestinationMismatchAndValidMovePreservesGuid()
        {
            // Given: a preview bound to source, destination and referrer state.
            var target = Make("Target");
            var owner = Make("Owner", target);
            AssetDatabase.SaveAssets();
            string source = AssetDatabase.GetAssetPath(target);
            string guid = AssetDatabase.AssetPathToGUID(source);
            var initial = Success(Asset("preview_move", target));
            // When: application changes destination or the observed referrer graph drifts.
            Assert.IsFalse((bool)Apply("move", target, initial, root + "/Wrong.asset")["success"]);
            owner.reference = null;
            EditorUtility.SetDirty(owner);
            AssetDatabase.SaveAssets();
            Assert.IsFalse((bool)Apply("move", target, initial, root + "/Moved.asset")["success"]);
            Assert.AreEqual(guid, AssetDatabase.AssetPathToGUID(source));
            var fresh = Success(Asset("preview_move", target));
            var moved = Apply("move", target, fresh, root + "/Moved.asset");
            // Then: only the exact fresh preview permits the existing move and GUID stays stable.
            Assert.IsTrue((bool)moved["success"], moved.ToString());
            Assert.AreEqual(guid, AssetDatabase.AssetPathToGUID(root + "/Moved.asset"));
            Assert.IsEmpty(AssetDatabase.AssetPathToGUID(source));
        }

        [Test]
        public void GuardedDeleteRejectsIncompleteOrWrongOperationPreviewThenDeletesFromValidPreview()
        {
            // Given: a source and a direct dependent, with bounded and complete preview variants.
            var target = Make("Target");
            Make("Owner", target);
            AssetDatabase.SaveAssets();
            string path = AssetDatabase.GetAssetPath(target);
            string guid = AssetDatabase.AssetPathToGUID(path);
            var limited = Success(Asset("preview_delete", target, work: 1));
            Assert.IsFalse((bool)Apply("delete", target, limited, work: 1)["success"]);
            var wrong = Success(Asset("preview_move", target));
            Assert.IsFalse((bool)Apply("delete", target, wrong)["success"]);
            Assert.AreEqual(guid, AssetDatabase.AssetPathToGUID(path));
            var valid = Success(Asset("preview_delete", target));
            Assert.GreaterOrEqual(valid.Value<int>("totalEntries"), 1);
            // When: applying the exact complete delete preview.
            var deleted = Apply("delete", target, valid);
            // Then: existing deletion proceeds once with no replacement or automatic repair.
            Assert.IsTrue((bool)deleted["success"], deleted.ToString());
            Assert.IsEmpty(
                AssetDatabase.AssetPathToGUID(path, AssetPathToGUIDOptions.OnlyExistingAssets),
                "Deleted source must have no existing imported asset GUID; recently-deleted GUID cache entries are not existence evidence."
            );
            Assert.IsNull(AssetDatabase.LoadMainAssetAtPath(path), "Deleted source must not resolve to a main asset.");
            Assert.IsFalse(File.Exists(Path.Combine(Application.dataPath, path.Substring("Assets/".Length))), "The source asset file must be deleted.");
            Assert.IsFalse(
                File.Exists(Path.Combine(Application.dataPath, path.Substring("Assets/".Length)) + ".meta"),
                "The source metadata file must be deleted."
            );
        }

        [Test]
        public void DependencyBudgetsAndMissingTargetsDoNotClaimCompleteResults()
        {
            // Given: a two-step dependency graph.
            var c = Make("C");
            var b = Make("B", c);
            var a = Make("A", b);
            AssetDatabase.SaveAssets();
            // When: one unit of work cannot inspect the graph.
            var limited = Success(Asset("get_dependencies", a, true, work: 1));
            // Then: the partial nature is explicit and missing inputs are refused.
            Assert.IsFalse((bool)limited["scanComplete"]);
            Assert.LessOrEqual(limited.Value<int>("workUsed"), 1);
            var missing = JObject.FromObject(
                ManageAsset.HandleCommand(
                    new JObject
                    {
                        ["action"] = "get_dependencies",
                        ["path"] = root + "/Absent.asset",
                        ["scanRoot"] = root,
                    }
                )
            );
            Assert.IsFalse((bool)missing["success"]);
        }

        [Test]
        public void ScriptableObjectBrokenReferencesPagePastTwoHundredAndKeepOrdinaryNull()
        {
            // Given: 205 serialized broken PPtrs and an intentional null in one asset.
            var target = Make("Deleted");
            var owner = Make("Many");
            owner.references = Enumerable.Repeat<Object>(target, 205).Concat(new Object[] { null }).ToList();
            EditorUtility.SetDirty(owner);
            AssetDatabase.SaveAssets();
            string ownerPath = AssetDatabase.GetAssetPath(owner);
            Assert.IsTrue(AssetDatabase.DeleteAsset(AssetDatabase.GetAssetPath(target)));
            AssetDatabase.ImportAsset(ownerPath, ImportAssetOptions.ForceUpdate);
            owner = AssetDatabase.LoadAssetAtPath<ReferenceDiagnosticFixture>(ownerPath);
            bool dirty = EditorUtility.IsDirty(owner);
            // When: all three pages are read and a deliberately tiny property budget is used.
            var p1 = Success(Diagnose("scriptable_object", ownerPath));
            var p2 = Success(Diagnose("scriptable_object", ownerPath, page: 2, revision: (string)p1["referenceRevision"]));
            var p3 = Success(Diagnose("scriptable_object", ownerPath, page: 3, revision: (string)p1["referenceRevision"]));
            var limited = Success(Diagnose("scriptable_object", ownerPath, work: 1));
            // Then: no legacy 200-cap truncation, exact property provenance, and no dirtying/repair.
            Assert.AreEqual(205, p1.Value<int>("totalIssues"));
            Assert.AreEqual(100, p1["issues"].Count());
            Assert.AreEqual(100, p2["issues"].Count());
            Assert.AreEqual(5, p3["issues"].Count());
            var rows = p1["issues"].Concat(p2["issues"]).Concat(p3["issues"]).ToArray();
            Assert.AreEqual(205, rows.Select(row => (string)row["propertyPath"]).Distinct().Count());
            Assert.IsTrue(rows.All(row => ((string)row["propertyPath"]).StartsWith("references.Array.data[", StringComparison.Ordinal)));
            Assert.IsTrue(rows.All(row => row["targetGuid"].Type == JTokenType.Null && row["targetFileId"].Type == JTokenType.Null && row["evidence"] != null));
            Assert.IsFalse((bool)limited["scanComplete"]);
            Assert.AreEqual(dirty, EditorUtility.IsDirty(owner));
        }

        [Test]
        public void InactiveAdditiveSceneAndPrefabReferencesHaveOwningComponentLocations()
        {
            // Given: an inactive object in an owned additive scene and a prefab using a deleted material.
            Scene scene = OwnedScene();
            var go = new GameObject("InactiveOwner");
            SceneManager.MoveGameObjectToScene(go, scene);
            var renderer = go.AddComponent<MeshRenderer>();
            var material = new Material(Shader.Find("Hidden/InternalErrorShader"));
            string materialPath = root + "/Deleted.mat";
            AssetDatabase.CreateAsset(material, materialPath);
            renderer.sharedMaterials = new Material[] { material, null };
            go.SetActive(false);
            string prefabPath = root + "/InactiveOwner.prefab";
            Assert.IsNotNull(PrefabUtility.SaveAsPrefabAsset(go, prefabPath), "The inactive reference owner must persist as a prefab with the same root name.");
            Assert.IsTrue(EditorSceneManager.SaveScene(scene, root + "/Additive.unity"));
            Assert.IsTrue(AssetDatabase.DeleteAsset(materialPath));
            AssetDatabase.ImportAsset(prefabPath, ImportAssetOptions.ForceUpdate);
            var persistedPrefab = AssetDatabase.LoadAssetAtPath<GameObject>(prefabPath);
            Assert.IsNotNull(persistedPrefab, "The selected prefab must resolve before diagnostics.");
            Assert.AreEqual("InactiveOwner", persistedPrefab.name, "The prefab's persisted root must retain the fixture's exact hierarchy name.");
            Assert.IsFalse(persistedPrefab.activeSelf, "Inactive prefab coverage requires a persisted inactive root.");
            bool prefabDirty = EditorUtility.IsDirty(persistedPrefab);
            SceneManager.SetActiveScene(original);
            bool dirty = scene.isDirty;
            // When: explicit loaded-scene and prefab scopes are scanned.
            var scenes = Success(Diagnose("loaded_scenes"));
            var prefab = Success(Diagnose("prefab", prefabPath));
            // Then: owning scene/hierarchy/component/property identify the issue, including inactive objects.
            Assert.IsTrue(
                scenes["issues"]
                    .Any(row =>
                        (string)row["gameObjectPath"] == "InactiveOwner"
                        && (string)row["componentType"] == typeof(MeshRenderer).FullName
                        && ((string)row["propertyPath"]).StartsWith("m_Materials.Array.data[0]", StringComparison.Ordinal)
                    ),
                "Loaded scenes must identify the inactive additive MeshRenderer's first broken material slot: " + scenes
            );
            Assert.IsTrue(
                prefab["issues"]
                    .Any(row =>
                        (string)row["gameObjectPath"] == "InactiveOwner"
                        && (string)row["componentType"] == typeof(MeshRenderer).FullName
                        && (string)row["propertyPath"] == "m_Materials.Array.data[0]"
                    ),
                "Selected prefab must identify the persisted inactive root, MeshRenderer and exact broken material property: " + prefab
            );
            Assert.AreEqual(
                1,
                prefab.Value<int>("totalIssues"),
                "Only the broken first material slot is an issue; the intentional null second slot remains ordinary null."
            );
            Assert.IsFalse(go.activeSelf, "Scene diagnostic coverage must retain the inactive source object.");
            Assert.AreEqual(prefabDirty, EditorUtility.IsDirty(persistedPrefab), "Prefab diagnostic reads must preserve dirtiness.");
            Assert.AreEqual(dirty, scene.isDirty);
            Assert.AreEqual(original.handle, SceneManager.GetActiveScene().handle);
        }
    }
}
