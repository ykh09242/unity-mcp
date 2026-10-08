using System;
using System.Collections.Generic;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Editor.Tools.Prefabs;
using MCPForUnityTests.Editor.Helpers;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEditor;
using UnityEngine;
using UnityEngine.SceneManagement;
using Object = UnityEngine.Object;

namespace MCPForUnityTests.Editor.Tools
{
    public class ManagePrefabsMaterialIntegrityTests
    {
        private string assetRoot;
        private string folderGuid;
        private Scene ownedScene;
        private Object[] originalSelection;
        private Object originalActiveSelection;
        private readonly List<Object> runtimeObjects = new List<Object>();
        private readonly PrefabTestSceneFixture testScene = new PrefabTestSceneFixture();
        private bool capturedState;

        [OneTimeSetUp]
        public void PrepareRunnerBootstrap() => testScene.PrepareRunnerBootstrap();

        [OneTimeTearDown]
        public void RestoreRunnerBootstrap() => testScene.RestoreRunnerBootstrap();

        [SetUp]
        public void SetUp()
        {
            ownedScene = default;
            assetRoot = null;
            folderGuid = null;
            originalSelection = null;
            originalActiveSelection = null;
            capturedState = false;
            runtimeObjects.Clear();
            originalSelection = Selection.objects;
            originalActiveSelection = Selection.activeObject;
            capturedState = true;
            string suffix = Guid.NewGuid().ToString("N");
            ownedScene = testScene.Create("McpPrefabMaterialIntegrity_", suffix);
            assetRoot = "Assets/__McpPrefabMaterialIntegrity_" + suffix;
            Assert.IsFalse(AssetDatabase.IsValidFolder(assetRoot));
            folderGuid = AssetDatabase.CreateFolder("Assets", "__McpPrefabMaterialIntegrity_" + suffix);
            Assert.IsNotEmpty(folderGuid);
        }

        [TearDown]
        public void TearDown()
        {
            if (!capturedState)
                return;
            try
            {
                foreach (Object item in runtimeObjects)
                    if (item != null && !EditorUtility.IsPersistent(item))
                        Object.DestroyImmediate(item);
                runtimeObjects.Clear();
                if (!string.IsNullOrEmpty(folderGuid))
                {
                    Assert.IsTrue(assetRoot.StartsWith("Assets/__McpPrefabMaterialIntegrity_", StringComparison.Ordinal));
                    Assert.AreEqual(32, assetRoot.Substring("Assets/__McpPrefabMaterialIntegrity_".Length).Length);
                    Assert.AreEqual(folderGuid, AssetDatabase.AssetPathToGUID(assetRoot));
                    Assert.IsTrue(AssetDatabase.DeleteAsset(assetRoot));
                }
            }
            finally
            {
                try
                {
                    testScene.Close();
                }
                finally
                {
                    Selection.objects = originalSelection ?? Array.Empty<Object>();
                    Selection.activeObject = originalActiveSelection;
                    capturedState = false;
                }
            }
        }

        [Test]
        public void SameNameSiblingsSaveDistinctMaterialReferencesAndColors()
        {
            GameObject root = Root();
            Renderer first = Child(root, "Shared", Material(Color.red));
            Renderer second = Child(root, "Shared", Material(Color.blue));

            Create(root, "One");

            // Imports may recreate managed wrappers; compare the native Unity object identity.
            Assert.IsTrue(first.sharedMaterials[0] != second.sharedMaterials[0]);
            AssertColor(first.sharedMaterials[0], Color.red);
            AssertColor(second.sharedMaterials[0], Color.blue);
            Renderer[] saved = Saved("One").GetComponentsInChildren<Renderer>(true);
            Assert.AreEqual(2, saved.Length);
            Assert.IsTrue(saved[0].sharedMaterials[0] != saved[1].sharedMaterials[0]);
            AssertColor(saved[0].sharedMaterials[0], Color.red);
            AssertColor(saved[1].sharedMaterials[0], Color.blue);
        }

        [Test]
        public void ExistingCollidingMaterialAndItsOtherUserRemainUnchanged()
        {
            Material existing = Persist(Material(Color.green), "Shared_mat.mat");
            string originalGuid = AssetDatabase.AssetPathToGUID(AssetDatabase.GetAssetPath(existing));
            GameObject otherRoot = Root();
            Renderer otherUser = Child(otherRoot, "ExternalUser", existing);
            GameObject root = Root();
            Renderer source = Child(root, "Shared", Material(Color.red));

            Create(root, "One");

            Assert.IsTrue(existing == otherUser.sharedMaterials[0]);
            Assert.IsTrue(existing != source.sharedMaterials[0]);
            AssertColor(existing, Color.green);
            AssertColor(source.sharedMaterials[0], Color.red);
            Assert.AreEqual(originalGuid, AssetDatabase.AssetPathToGUID(AssetDatabase.GetAssetPath(existing)));
        }

        [Test]
        public void SavingAnotherPrefabWithSameRendererNamePreservesFirstPrefabMaterial()
        {
            GameObject firstRoot = Root();
            Renderer first = Child(firstRoot, "Shared", Material(Color.red));
            Create(firstRoot, "One");
            Material firstAsset = first.sharedMaterials[0];
            GameObject secondRoot = Root();
            Renderer second = Child(secondRoot, "Shared", Material(Color.blue));

            Create(secondRoot, "Two");

            Assert.IsTrue(firstAsset != second.sharedMaterials[0]);
            AssertColor(firstAsset, Color.red);
            AssertColor(Saved("One").GetComponentInChildren<Renderer>().sharedMaterials[0], Color.red);
            AssertColor(Saved("Two").GetComponentInChildren<Renderer>().sharedMaterials[0], Color.blue);
        }

        [Test]
        public void CollidingFolderIsPreservedAndMaterialUsesAnotherPath()
        {
            EnsureMaterialsFolder();
            string collision = assetRoot + "/Materials/Shared_mat.mat";
            AssetDatabase.CreateFolder(assetRoot + "/Materials", "Shared_mat.mat");
            string guid = AssetDatabase.AssetPathToGUID(collision);
            GameObject root = Root();
            Renderer source = Child(root, "Shared", Material(Color.red));

            Create(root, "One");

            Assert.IsTrue(AssetDatabase.IsValidFolder(collision));
            Assert.AreEqual(guid, AssetDatabase.AssetPathToGUID(collision));
            Assert.AreNotEqual(collision, AssetDatabase.GetAssetPath(source.sharedMaterials[0]));
            AssertColor(source.sharedMaterials[0], Color.red);
        }

        [Test]
        public void PersistentSlotOverridesSurviveNeighborRuntimeMaterialPersistence()
        {
            Material existing = Persist(Material(Color.green), "Existing.mat");
            GameObject root = Root();
            Renderer source = Child(root, "Shared", Material(Color.red), existing);
            var block = new MaterialPropertyBlock();
            block.SetColor("_Color", Color.blue);
            block.SetFloat("_SyntheticFloat", 2.5f);
            source.SetPropertyBlock(block, 1);

            Create(root, "One");

            Assert.IsTrue(existing == source.sharedMaterials[1]);
            Assert.AreEqual(Color.blue, Read(source, 1).GetColor("_Color"));
            Assert.AreEqual(2.5f, Read(source, 1).GetFloat("_SyntheticFloat"));
            Assert.IsTrue(existing == Saved("One").GetComponentInChildren<Renderer>().sharedMaterials[1]);
        }

        [TestCase(0f)]
        [TestCase(2.5f)]
        public void FloatOnlyOverrideDoesNotCreateAMaterialOrClearTheBlock(float value)
        {
            GameObject root = Root();
            Renderer source = Child(root, "Shared", (Material)null);
            var block = new MaterialPropertyBlock();
            block.SetFloat("_SyntheticFloat", value);
            source.SetPropertyBlock(block, 0);

            JObject response = Create(root, "One");

            Assert.AreEqual(0, (int)response["data"]["materialsPersisted"]);
            Assert.IsNull(source.sharedMaterials[0]);
            Assert.IsFalse(Read(source, 0).isEmpty);
            Assert.AreEqual(value, Read(source, 0).GetFloat("_SyntheticFloat"));
            Assert.IsFalse(AssetDatabase.IsValidFolder(assetRoot + "/Materials"));
            Assert.IsNull(Saved("One").GetComponentInChildren<Renderer>().sharedMaterials[0]);
        }

        [Test]
        public void TextureOnlyOverrideDoesNotCreateAMaterialOrClearTheBlock()
        {
            GameObject root = Root();
            Renderer source = Child(root, "Shared", (Material)null);
            var texture = new Texture2D(1, 1);
            runtimeObjects.Add(texture);
            var block = new MaterialPropertyBlock();
            block.SetTexture("_SyntheticTexture", texture);
            source.SetPropertyBlock(block, 0);

            JObject response = Create(root, "One");

            Assert.AreEqual(0, (int)response["data"]["materialsPersisted"]);
            Assert.IsNull(source.sharedMaterials[0]);
            Assert.IsTrue(texture == Read(source, 0).GetTexture("_SyntheticTexture"));
        }

        [TestCase(false)]
        [TestCase(true)]
        public void IndexedColorIsBakedWhileMixedSourceOverridesRemain(bool runtimeMaterial)
        {
            GameObject root = Root();
            Renderer source = Child(root, "Shared", runtimeMaterial ? Material(Color.red) : null);
            string colorProperty = ColorProperty(source.sharedMaterials[0]);
            var block = new MaterialPropertyBlock();
            block.SetColor(colorProperty, Color.blue);
            block.SetFloat("_SyntheticFloat", 2.5f);
            source.SetPropertyBlock(block, 0);

            Create(root, "One");

            AssertColor(source.sharedMaterials[0], Color.blue, colorProperty);
            AssertColor(Saved("One").GetComponentInChildren<Renderer>().sharedMaterials[0], Color.blue, colorProperty);
            Assert.AreEqual(Color.blue, Read(source, 0).GetColor(colorProperty));
            Assert.AreEqual(2.5f, Read(source, 0).GetFloat("_SyntheticFloat"));
        }

        [TestCase(false)]
        [TestCase(true)]
        public void RendererLevelColorIsBakedWhenIndexedBlockIsAbsent(bool runtimeMaterial)
        {
            GameObject root = Root();
            Renderer source = Child(root, "Shared", runtimeMaterial ? Material(Color.red) : null);
            string colorProperty = ColorProperty(source.sharedMaterials[0]);
            var block = new MaterialPropertyBlock();
            block.SetColor(colorProperty, Color.blue);
            source.SetPropertyBlock(block);

            Create(root, "One");

            AssertColor(source.sharedMaterials[0], Color.blue, colorProperty);
            AssertColor(Saved("One").GetComponentInChildren<Renderer>().sharedMaterials[0], Color.blue, colorProperty);
            var actual = new MaterialPropertyBlock();
            source.GetPropertyBlock(actual);
            Assert.AreEqual(Color.blue, actual.GetColor(colorProperty));
        }

        [Test]
        public void IndexedColorOverridesRendererLevelColorWithoutClearingEither()
        {
            GameObject root = Root();
            Renderer source = Child(root, "Shared", Material(Color.red));
            var global = new MaterialPropertyBlock();
            global.SetColor("_Color", Color.green);
            source.SetPropertyBlock(global);
            var indexed = new MaterialPropertyBlock();
            indexed.SetColor("_Color", Color.blue);
            source.SetPropertyBlock(indexed, 0);

            Create(root, "One");

            AssertColor(Saved("One").GetComponentInChildren<Renderer>().sharedMaterials[0], Color.blue);
            Assert.AreEqual(Color.blue, Read(source, 0).GetColor("_Color"));
            source.GetPropertyBlock(global);
            Assert.AreEqual(Color.green, global.GetColor("_Color"));
        }

        [Test]
        public void IndexedNonColorBlockSuppressesRendererLevelColor()
        {
            GameObject root = Root();
            Renderer source = Child(root, "Shared", (Material)null);
            string colorProperty = ColorProperty(null);
            var global = new MaterialPropertyBlock();
            global.SetColor(colorProperty, Color.green);
            source.SetPropertyBlock(global);
            var indexed = new MaterialPropertyBlock();
            indexed.SetFloat("_SyntheticFloat", 0f);
            source.SetPropertyBlock(indexed, 0);

            JObject response = Create(root, "One");

            Assert.AreEqual(0, (int)response["data"]["materialsPersisted"]);
            Assert.IsNull(source.sharedMaterials[0]);
            Assert.IsFalse(Read(source, 0).isEmpty);
            source.GetPropertyBlock(global);
            Assert.AreEqual(Color.green, global.GetColor(colorProperty));
        }

        [Test]
        public void PersistentMaterialAndEmptySlotsRemainUnchanged()
        {
            Material existing = Persist(Material(Color.green), "Existing.mat");
            GameObject root = Root();
            Renderer source = Child(root, "Shared", existing, null);
            var block = new MaterialPropertyBlock();
            block.SetColor("_Color", Color.blue);
            source.SetPropertyBlock(block, 0);
            string guid = AssetDatabase.AssetPathToGUID(AssetDatabase.GetAssetPath(existing));

            JObject response = Create(root, "One");

            Assert.AreEqual(0, (int)response["data"]["materialsPersisted"]);
            Assert.IsTrue(existing == source.sharedMaterials[0]);
            Assert.IsNull(source.sharedMaterials[1]);
            Assert.AreEqual(Color.blue, Read(source, 0).GetColor("_Color"));
            Assert.AreEqual(guid, AssetDatabase.AssetPathToGUID(AssetDatabase.GetAssetPath(existing)));
        }

        [Test]
        public void SharedRuntimeMaterialSupportsIndependentSlotColors()
        {
            GameObject root = Root();
            Material original = Material(Color.red);
            Renderer source = Child(root, "Shared", original, original);
            var first = new MaterialPropertyBlock();
            first.SetColor("_Color", Color.blue);
            source.SetPropertyBlock(first, 0);
            var second = new MaterialPropertyBlock();
            second.SetColor("_Color", Color.green);
            source.SetPropertyBlock(second, 1);

            Create(root, "One");

            Assert.IsTrue(source.sharedMaterials[0] != source.sharedMaterials[1]);
            AssertColor(source.sharedMaterials[0], Color.blue);
            AssertColor(source.sharedMaterials[1], Color.green);
            AssertColor(original, Color.red);
        }

        [Test]
        public void AbortedPreparationRestoresSlotsAndRollsBackOnlyEmptyOwnedFolders()
        {
            GameObject root = Root();
            Material original = Material(Color.red);
            Renderer source = Child(root, "Shared", original);
            var block = new MaterialPropertyBlock();
            block.SetColor("_Color", Color.blue);
            block.SetFloat("_SyntheticFloat", 2.5f);
            source.SetPropertyBlock(block, 0);
            string path = assetRoot + "/Prepared/Materials/Owned.mat";
            Material owned;
            using (var folders = new AssetFolderScope())
            {
                folders.EnsureParentDirectory(path);
                using var transaction = new ManagePrefabs.RuntimeMaterialTransaction();
                owned = transaction.CreateMaterial(original.shader, path);
                transaction.Assign(source, 0, original, owned);
            }
            Assert.IsTrue(source.sharedMaterials[0] == original);
            Assert.IsTrue(owned == null);
            Assert.IsNull(AssetDatabase.LoadAssetAtPath<Material>(path));
            Assert.IsFalse(AssetDatabase.IsValidFolder(assetRoot + "/Prepared"));
            Assert.AreEqual(Color.blue, Read(source, 0).GetColor("_Color"));
            Assert.AreEqual(2.5f, Read(source, 0).GetFloat("_SyntheticFloat"));
        }

        [Test]
        public void CommittedPreparationKeepsOwnedAssetAndAssignment()
        {
            GameObject root = Root();
            Material original = Material(Color.red);
            Renderer source = Child(root, "Shared", original);
            EnsureMaterialsFolder();
            string path = assetRoot + "/Materials/Owned.mat";
            Material owned;
            using (var transaction = new ManagePrefabs.RuntimeMaterialTransaction())
            {
                owned = transaction.CreateMaterial(original.shader, path);
                transaction.Assign(source, 0, original, owned);
                transaction.Commit();
            }
            Assert.IsTrue(source.sharedMaterials[0] == owned);
            Assert.IsTrue(EditorUtility.IsPersistent(owned));
            Assert.IsTrue(AssetDatabase.LoadAssetAtPath<Material>(path) == owned);
        }

        [Test]
        public void RollbackPreservesExternalSlotChangesAndPersistentNeighbors()
        {
            GameObject root = Root();
            Material original = Material(Color.red);
            Material persistent = Persist(Material(Color.green), "Existing.mat");
            Renderer source = Child(root, "Shared", original, persistent);
            Material external = Material(Color.blue);
            string path = assetRoot + "/Materials/Owned.mat";
            using (var transaction = new ManagePrefabs.RuntimeMaterialTransaction())
            {
                Material owned = transaction.CreateMaterial(original.shader, path);
                transaction.Assign(source, 0, original, owned);
                source.sharedMaterials = new[] { external, persistent };
            }
            Assert.IsTrue(source.sharedMaterials[0] == external);
            Assert.IsTrue(source.sharedMaterials[1] == persistent);
            Assert.IsTrue(EditorUtility.IsPersistent(persistent));
            Assert.IsNull(AssetDatabase.LoadAssetAtPath<Material>(path));
        }

        [Test]
        public void PreparationDoesNotOverwriteSlotChangedDuringAssetCreation()
        {
            GameObject root = Root();
            Material original = Material(Color.red);
            Renderer source = Child(root, "Shared", original);
            Material external = Material(Color.blue);
            EnsureMaterialsFolder();
            string path = assetRoot + "/Materials/Owned.mat";
            using (var transaction = new ManagePrefabs.RuntimeMaterialTransaction())
            {
                Material owned = transaction.CreateMaterial(original.shader, path);
                source.sharedMaterials = new[] { external };
                Assert.Throws<InvalidOperationException>(() => transaction.Assign(source, 0, original, owned));
            }
            Assert.IsTrue(source.sharedMaterials[0] == external);
            Assert.IsNull(AssetDatabase.LoadAssetAtPath<Material>(path));
        }

        [Test]
        public void RollbackPreservesReplacementAtPreviouslyOwnedAssetPath()
        {
            Material original = Material(Color.red);
            EnsureMaterialsFolder();
            string path = assetRoot + "/Materials/Owned.mat";
            Material replacement = Material(Color.blue);
            using (var transaction = new ManagePrefabs.RuntimeMaterialTransaction())
            {
                transaction.CreateMaterial(original.shader, path);
                Assert.IsTrue(AssetDatabase.DeleteAsset(path));
                AssetDatabase.CreateAsset(replacement, path);
            }
            Assert.IsTrue(AssetDatabase.LoadAssetAtPath<Material>(path) == replacement);
            AssertColor(replacement, Color.blue);
        }

        [Test]
        public void PreparationRejectsCallerOwnedDestination()
        {
            Material existing = Persist(Material(Color.green), "Existing.mat");
            string path = AssetDatabase.GetAssetPath(existing);
            string guid = AssetDatabase.AssetPathToGUID(path);
            using (var transaction = new ManagePrefabs.RuntimeMaterialTransaction())
                Assert.Throws<InvalidOperationException>(() => transaction.CreateMaterial(existing.shader, path));
            Assert.IsTrue(AssetDatabase.LoadAssetAtPath<Material>(path) == existing);
            Assert.AreEqual(guid, AssetDatabase.AssetPathToGUID(path));
            AssertColor(existing, Color.green);
        }

        [Test]
        public void ObservedSavedDependencyRetainsMaterialWhileSourceSlotIsRestored()
        {
            GameObject root = Root();
            Material original = Material(Color.red);
            Renderer source = Child(root, "Shared", original);
            EnsureMaterialsFolder();
            string materialPath = assetRoot + "/Materials/Owned.mat";
            string prefabPath = assetRoot + "/Partial.prefab";
            Material owned;
            using (var transaction = new ManagePrefabs.RuntimeMaterialTransaction())
            {
                owned = transaction.CreateMaterial(original.shader, materialPath);
                transaction.Assign(source, 0, original, owned);
                PrefabUtility.SaveAsPrefabAsset(root, prefabPath, out bool saved);
                Assert.IsTrue(saved);
                transaction.RetainSavedDependencies(prefabPath);
            }
            Assert.IsTrue(source.sharedMaterials[0] == original);
            Assert.IsTrue(AssetDatabase.LoadAssetAtPath<Material>(materialPath) == owned);
            Assert.IsTrue(AssetDatabase.LoadAssetAtPath<GameObject>(prefabPath).GetComponentInChildren<Renderer>().sharedMaterials[0] == owned);
        }

        private GameObject Root()
        {
            var root = new GameObject("PrefabIntegrity_" + Guid.NewGuid().ToString("N"));
            SceneManager.MoveGameObjectToScene(root, ownedScene);
            runtimeObjects.Add(root);
            return root;
        }

        private Renderer Child(GameObject root, string name, params Material[] materials)
        {
            var child = new GameObject(name);
            child.transform.SetParent(root.transform);
            var renderer = child.AddComponent<MeshRenderer>();
            renderer.sharedMaterials = materials;
            return renderer;
        }

        private Material Material(Color color)
        {
            Shader shader = Shader.Find("Standard") ?? Shader.Find("Unlit/Color");
            if (shader == null)
                Assert.Ignore("Requires an installed shader with _Color for the serialization fixture.");
            var material = new Material(shader);
            Assert.IsTrue(material.HasProperty("_Color"));
            material.SetColor("_Color", color);
            runtimeObjects.Add(material);
            return material;
        }

        private void EnsureMaterialsFolder()
        {
            if (!AssetDatabase.IsValidFolder(assetRoot + "/Materials"))
                AssetDatabase.CreateFolder(assetRoot, "Materials");
        }

        private Material Persist(Material material, string fileName)
        {
            EnsureMaterialsFolder();
            AssetDatabase.CreateAsset(material, assetRoot + "/Materials/" + fileName);
            return material;
        }

        private JObject Create(GameObject root, string fileName)
        {
            var response = JObject.FromObject(
                ManagePrefabs.HandleCommand(
                    new JObject
                    {
                        ["action"] = "create_from_gameobject",
                        ["target"] = root.name,
                        ["prefabPath"] = assetRoot + "/" + fileName + ".prefab",
                    }
                )
            );
            Assert.IsTrue((bool)response["success"], response.ToString());
            return response;
        }

        private GameObject Saved(string fileName)
        {
            var prefab = AssetDatabase.LoadAssetAtPath<GameObject>(assetRoot + "/" + fileName + ".prefab");
            Assert.IsNotNull(prefab);
            return prefab;
        }

        private static MaterialPropertyBlock Read(Renderer renderer, int slot)
        {
            var block = new MaterialPropertyBlock();
            renderer.GetPropertyBlock(block, slot);
            return block;
        }

        private string ColorProperty(Material material)
        {
            if (material == null)
            {
                Shader shader = RenderPipelineUtility.ResolveShader("Standard");
                if (shader == null)
                    Assert.Ignore("Requires a resolved pipeline shader for missing-material color persistence.");
                material = new Material(shader);
                runtimeObjects.Add(material);
            }
            if (material.HasProperty("_BaseColor"))
                return "_BaseColor";
            if (material.HasProperty("_Color"))
                return "_Color";
            Assert.Ignore("Resolved shader must expose _BaseColor or _Color for this color-persistence fixture.");
            return null;
        }

        private static void AssertColor(Material material, Color expected, string colorProperty = "_Color")
        {
            Assert.IsNotNull(material);
            Assert.IsTrue(material.HasProperty(colorProperty));
            Assert.AreEqual(expected, material.GetColor(colorProperty));
        }
    }
}
