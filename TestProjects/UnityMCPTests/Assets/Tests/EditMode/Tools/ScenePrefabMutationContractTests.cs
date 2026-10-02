using System;
using System.Collections.Generic;
using MCPForUnity.Editor.Tools.GameObjects;
using MCPForUnity.Editor.Tools.Prefabs;
using MCPForUnity.Runtime.Helpers;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEditor;
using UnityEngine;
using Object = UnityEngine.Object;

namespace MCPForUnityTests.Editor.Tools
{
    public class ScenePrefabMutationContractTests
    {
        private readonly List<GameObject> _objects = new List<GameObject>();
        private string _prefabPath;

        private GameObject Create(string name, GameObject parent = null)
        {
            var go = new GameObject(name);
            _objects.Add(go);
            if (parent != null) go.transform.SetParent(parent.transform);
            return go;
        }

        [TearDown]
        public void TearDown()
        {
            foreach (var go in _objects) if (go != null) Object.DestroyImmediate(go);
            _objects.Clear();
            if (_prefabPath != null) AssetDatabase.DeleteAsset(_prefabPath);
            _prefabPath = null;
        }

        [TestCase("missing_parent")]
        [TestCase("invalid_layer")]
        [TestCase("descendant_parent")]
        public void InvalidDeterministicModify_DoesNotRenameOrMoveTarget(string invalid)
        {
            // Given a valid target with a simultaneous rename and invalid deterministic input.
            var go = Create("contract-original-" + Guid.NewGuid().ToString("N"));
            var child = Create("Child", go);
            var request = new JObject
            {
                ["action"] = "modify", ["target"] = go.GetInstanceIDCompat(),
                ["name"] = "contract-renamed", ["position"] = new JArray(1, 2, 3)
            };
            if (invalid == "missing_parent") request["parent"] = "missing-parent-" + Guid.NewGuid().ToString("N");
            if (invalid == "descendant_parent") request["parent"] = child.GetInstanceIDCompat();
            if (invalid == "invalid_layer") request["layer"] = "missing-layer-" + Guid.NewGuid().ToString("N");
            string originalName = go.name;
            // When the public handler rejects it, earlier fields must remain unchanged.
            var response = JObject.FromObject(ManageGameObject.HandleCommand(request));
            Assert.IsFalse(response.Value<bool>("success"), response.ToString());
            Assert.AreEqual(originalName, go.name);
            Assert.AreEqual(Vector3.zero, go.transform.localPosition);
            Assert.IsNull(go.transform.parent);
            Assert.AreEqual(go.transform, child.transform.parent);
        }

        [TestCase(false)]
        [TestCase(true)]
        public void ValidParentAndLayer_ModifyStillWorks(bool detach)
        {
            var parent = Create("contract-parent");
            var go = Create("contract-target", detach ? parent : null);
            var request = new JObject
            {
                ["action"] = "modify", ["target"] = go.GetInstanceIDCompat(),
                ["name"] = "contract-renamed", ["layer"] = "Default",
                ["parent"] = detach ? JValue.CreateNull() : new JValue(parent.GetInstanceIDCompat())
            };
            var response = JObject.FromObject(ManageGameObject.HandleCommand(request));
            Assert.IsTrue(response.Value<bool>("success"), response.ToString());
            Assert.AreEqual("contract-renamed", go.name);
            Assert.AreEqual(detach ? null : parent.transform, go.transform.parent);
        }

        [TestCase("Child1", "Child1/Grandchild")]
        [TestCase("Child1/Grandchild", "Child1")]
        [TestCase("Child1", "Child1")]
        [TestCase("Child1/Grandchild", "Sibling")]
        [TestCase("Child1", "Child10/Grandchild")]
        public void DeleteChildren_ResolvesOverlapsBeforeDestroying(string first, string second)
        {
            // Given the documented ancestor/descendant deletion array (and identity/order controls).
            var root = Create("Root");
            var child = Create("Child1", root);
            Create("Grandchild", child);
            Create("Sibling", root);
            Create("Grandchild", Create("Child10", root));
            _prefabPath = "Assets/scene-prefab-contract-" + Guid.NewGuid().ToString("N") + ".prefab";
            Assert.IsNotNull(PrefabUtility.SaveAsPrefabAsset(root, _prefabPath));
            // When modify_contents executes the entire request, overlapping targets are one removal.
            var response = JObject.FromObject(ManagePrefabs.HandleCommand(new JObject
            {
                ["action"] = "modify_contents", ["prefabPath"] = _prefabPath,
                ["deleteChild"] = new JArray(first, second)
            }));
            Assert.IsTrue(response.Value<bool>("success"), response.ToString());
            var saved = AssetDatabase.LoadAssetAtPath<GameObject>(_prefabPath);
            Assert.IsNotNull(saved);
            Assert.IsNull(saved.transform.Find(first));
            Assert.IsNull(saved.transform.Find(second));
            if (second != "Sibling") Assert.IsNotNull(saved.transform.Find("Sibling"));
            Assert.IsNotNull(saved.transform.Find("Child10"));
        }

        [Test]
        public void DeleteChildren_ObjectNameEntriesShareTheOriginalSnapshot()
        {
            var root = Create("Root");
            Create("Grandchild", Create("Child1", root));
            _prefabPath = "Assets/scene-prefab-contract-" + Guid.NewGuid().ToString("N") + ".prefab";
            Assert.IsNotNull(PrefabUtility.SaveAsPrefabAsset(root, _prefabPath));
            var response = JObject.FromObject(ManagePrefabs.HandleCommand(new JObject
            {
                ["action"] = "modify_contents", ["prefabPath"] = _prefabPath,
                ["deleteChild"] = new JArray("Child1", new JObject { ["name"] = "Child1/Grandchild" })
            }));
            Assert.IsTrue(response.Value<bool>("success"), response.ToString());
            Assert.IsNull(AssetDatabase.LoadAssetAtPath<GameObject>(_prefabPath).transform.Find("Child1"));
        }

        [TestCase("Missing")]
        [TestCase("")]
        [TestCase("Grandchild")]
        public void InvalidDeleteChild_DoesNotSaveEarlierDeletion(string invalid)
        {
            var root = Create("Root");
            Create("Grandchild", Create("Child1", root));
            _prefabPath = "Assets/scene-prefab-contract-" + Guid.NewGuid().ToString("N") + ".prefab";
            Assert.IsNotNull(PrefabUtility.SaveAsPrefabAsset(root, _prefabPath));
            var response = JObject.FromObject(ManagePrefabs.HandleCommand(new JObject
            {
                ["action"] = "modify_contents", ["prefabPath"] = _prefabPath,
                ["deleteChild"] = new JArray("Child1", invalid)
            }));
            Assert.IsFalse(response.Value<bool>("success"), response.ToString());
            Assert.IsNotNull(AssetDatabase.LoadAssetAtPath<GameObject>(_prefabPath).transform.Find("Child1"));
        }
    }
}
