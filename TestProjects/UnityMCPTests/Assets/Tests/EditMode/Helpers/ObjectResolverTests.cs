using System;
using System.Collections.Generic;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Runtime.Helpers;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEditor;
using UnityEngine;

namespace MCPForUnityTests.Editor.Helpers
{
    public class ObjectResolverTests
    {
        private string _assetRoot;
        private readonly List<GameObject> _objects = new List<GameObject>();

        [SetUp]
        public void SetUp()
        {
            string folderName = "ObjectResolverTests_" + Guid.NewGuid().ToString("N");
            _assetRoot = "Assets/" + folderName;
            AssetDatabase.CreateFolder("Assets", folderName);
        }

        [TearDown]
        public void TearDown()
        {
            foreach (GameObject go in _objects)
                if (go != null)
                    UnityEngine.Object.DestroyImmediate(go);
            _objects.Clear();
            AssetDatabase.DeleteAsset(_assetRoot);
        }

        private GameObject CreateObject(string name, Transform parent = null)
        {
            var go = new GameObject(name);
            if (parent != null)
                go.transform.SetParent(parent);
            _objects.Add(go);
            return go;
        }

        private Material CreateMaterial(string name, string folder = null)
        {
            Shader shader = Shader.Find("Standard") ?? Shader.Find("Universal Render Pipeline/Lit");
            Assert.IsNotNull(shader, "A standard material shader is required by this fixture.");
            var material = new Material(shader) { name = name };
            AssetDatabase.CreateAsset(material, $"{folder ?? _assetRoot}/{name}.mat");
            return material;
        }

        [TestCase(false)]
        [TestCase(true)]
        public void MissingMaterialPath_DoesNotResolveSameNameAssetElsewhere(bool backslashes)
        {
            string name = "ResolverMaterial_" + Guid.NewGuid().ToString("N");
            Material material = CreateMaterial(name);
            string missingPath = $"{_assetRoot}/Missing/{name}.mat";
            if (backslashes)
                missingPath = missingPath.Replace('/', '\\');

            Assert.IsNull(ObjectResolver.ResolveMaterial(missingPath));
            Assert.AreSame(material, ObjectResolver.ResolveMaterial(name));
            Assert.AreSame(material, ObjectResolver.ResolveMaterial($"{_assetRoot}/{name}.mat"));
        }

        [Test]
        public void MissingRelativeMaterialPath_DoesNotFallBackToBasename()
        {
            string name = "ResolverRelative_" + Guid.NewGuid().ToString("N");
            CreateMaterial(name);
            Assert.IsNull(ObjectResolver.ResolveMaterial($"Missing/{name}.mat"));
        }

        [Test]
        public void AmbiguousMaterialName_StillRequiresAnExactPath()
        {
            string name = "ResolverDuplicate_" + Guid.NewGuid().ToString("N");
            Material first = CreateMaterial(name);
            AssetDatabase.CreateFolder(_assetRoot, "Duplicate");
            CreateMaterial(name, _assetRoot + "/Duplicate");

            Assert.IsNull(ObjectResolver.ResolveMaterial(name));
            Assert.AreSame(first, ObjectResolver.ResolveMaterial($"{_assetRoot}/{name}.mat"));
        }

        [Test]
        public void MissingTexturePath_DoesNotResolveSameNameAssetElsewhere()
        {
            string name = "ResolverTexture_" + Guid.NewGuid().ToString("N");
            var texture = new Texture2D(1, 1) { name = name };
            string path = $"{_assetRoot}/{name}.asset";
            AssetDatabase.CreateAsset(texture, path);

            Assert.IsNull(ObjectResolver.ResolveTexture($"{_assetRoot}/Missing/{name}.asset"));
            Assert.AreSame(texture, ObjectResolver.ResolveTexture(name));
            Assert.AreSame(texture, ObjectResolver.ResolveTexture(path));
        }

        [Test]
        public void AssetPathWithWrongType_DoesNotSelectMaterialWithSameBasename()
        {
            string name = "ResolverWrongType_" + Guid.NewGuid().ToString("N");
            CreateMaterial(name);
            var texture = new Texture2D(1, 1) { name = name };
            string path = $"{_assetRoot}/{name}.asset";
            AssetDatabase.CreateAsset(texture, path);

            Assert.IsNull(ObjectResolver.ResolveMaterial(path));
        }

        [Test]
        public void MissingPrefabPath_DoesNotResolveAnotherPrefabOrSceneHierarchy()
        {
            string name = "ResolverPrefab_" + Guid.NewGuid().ToString("N");
            GameObject source = CreateObject(name);
            string path = $"{_assetRoot}/{name}.prefab";
            GameObject prefab = PrefabUtility.SaveAsPrefabAsset(source, path);
            GameObject assets = CreateObject("Assets");
            GameObject folder = CreateObject(_assetRoot.Substring("Assets/".Length), assets.transform);
            GameObject missing = CreateObject("Missing", folder.transform);
            CreateObject(name + ".prefab", missing.transform);

            Assert.IsNull(ObjectResolver.Resolve<GameObject>(new JObject { ["find"] = $"{_assetRoot}/Missing/{name}.prefab" }));
            Assert.AreSame(prefab, ObjectResolver.Resolve<GameObject>(new JObject { ["find"] = path }));
        }

        [TestCase(null)]
        [TestCase("")]
        [TestCase("by_id_or_name_or_path")]
        public void AutomaticNumericTarget_UsesIdEvenWhenAnotherObjectHasThatName(string method)
        {
            GameObject wanted = CreateObject("ResolverId_" + Guid.NewGuid().ToString("N"));
            string id = wanted.GetInstanceIDCompat().ToString();
            CreateObject(id);

            Assert.AreSame(wanted, ObjectResolver.ResolveGameObject(new JValue(id), method));
            Assert.AreSame(wanted, ObjectResolver.ResolveGameObject(new JValue(wanted.GetInstanceIDCompat()), method));
            var instruction = new JObject { ["find"] = id };
            if (method != null)
                instruction["method"] = method;
            Assert.AreSame(wanted, ObjectResolver.Resolve<GameObject>(instruction));
        }

        [Test]
        public void ExplicitByName_PreservesNumericNameTargeting()
        {
            GameObject wanted = CreateObject("ResolverExplicitId_" + Guid.NewGuid().ToString("N"));
            string id = wanted.GetInstanceIDCompat().ToString();
            GameObject numericName = CreateObject(id);

            Assert.AreSame(numericName, ObjectResolver.ResolveGameObject(new JValue(id), "by_name"));
            Assert.AreSame(numericName, ObjectResolver.Resolve<GameObject>(new JObject { ["find"] = id, ["method"] = "by_name" }));
            Assert.AreSame(wanted, ObjectResolver.ResolveGameObject(new JValue(id), "by_id"));
        }

        [Test]
        public void AutomaticHierarchyPath_ResolvesGameObjectAndComponent()
        {
            GameObject root = CreateObject("ResolverRoot_" + Guid.NewGuid().ToString("N"));
            GameObject child = CreateObject("Child", root.transform);
            string path = root.name + "/Child";

            Assert.AreSame(child, ObjectResolver.ResolveGameObject(new JValue(path)));
            Assert.AreSame(child, ObjectResolver.Resolve<GameObject>(new JObject { ["find"] = path }));
            Assert.AreSame(child.transform, ObjectResolver.Resolve<Transform>(new JObject { ["find"] = path }));
            Assert.AreSame(child, ObjectResolver.ResolveGameObject(new JValue(path), "by_path"));
            Assert.AreSame(root, ObjectResolver.ResolveGameObject(new JValue(root.name)));
        }

        [Test]
        public void AutomaticRootedPath_PreservesExactRootMatching()
        {
            string rootName = "ResolverExact_" + Guid.NewGuid().ToString("N");
            string childName = "ResolverExactChild_" + Guid.NewGuid().ToString("N");
            GameObject other = CreateObject("ResolverOther_" + Guid.NewGuid().ToString("N"));
            GameObject nestedRoot = CreateObject(rootName, other.transform);
            CreateObject(childName, nestedRoot.transform);
            GameObject root = CreateObject(rootName);
            GameObject child = CreateObject(childName, root.transform);

            Assert.AreSame(child, ObjectResolver.ResolveGameObject(new JValue($"/{rootName}/{child.name}")));
            Assert.IsNull(ObjectResolver.ResolveGameObject(new JValue("/" + child.name)));
        }

        [Test]
        public void ExplicitByName_PreservesNamesContainingSlash()
        {
            GameObject go = CreateObject("Resolver/" + Guid.NewGuid().ToString("N"));
            Assert.AreSame(go, ObjectResolver.ResolveGameObject(new JValue(go.name), "by_name"));
        }

        [Test]
        public void InactiveAutomaticId_DoesNotFallBackToNumericName()
        {
            GameObject wanted = CreateObject("ResolverInactive_" + Guid.NewGuid().ToString("N"));
            string id = wanted.GetInstanceIDCompat().ToString();
            GameObject numericName = CreateObject(id);
            wanted.SetActive(false);

            Assert.IsNull(ObjectResolver.ResolveGameObject(new JValue(id)));
            Assert.AreSame(numericName, ObjectResolver.ResolveGameObject(new JValue(id), "by_name"));
        }

        [Test]
        public void TypedResolution_PreservesExclusionOfInactiveNamesAndPaths()
        {
            GameObject root = CreateObject("ResolverInactiveRoot_" + Guid.NewGuid().ToString("N"));
            GameObject child = CreateObject("ResolverInactiveChild_" + Guid.NewGuid().ToString("N"), root.transform);
            child.SetActive(false);

            Assert.IsNull(ObjectResolver.Resolve<GameObject>(new JObject { ["find"] = child.name, ["method"] = "by_name" }));
            Assert.IsNull(ObjectResolver.Resolve<GameObject>(new JObject { ["find"] = root.name + "/" + child.name, ["method"] = "by_path" }));
            Assert.IsNull(ObjectResolver.Resolve<GameObject>(new JObject { ["find"] = root.name + "/" + child.name }));
        }
    }
}
