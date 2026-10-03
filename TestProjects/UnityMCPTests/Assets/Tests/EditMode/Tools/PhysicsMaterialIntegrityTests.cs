using System;
using System.Collections.Generic;
using System.Text.RegularExpressions;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Editor.Tools.Physics;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEditor;
using UnityEngine;
using MCPForUnity.Runtime.Helpers;
using UnityEngine.TestTools;
#if UNITY_6000_0_OR_NEWER
using Material3D = UnityEngine.PhysicsMaterial;
using Combine3D = UnityEngine.PhysicsMaterialCombine;
#else
using Material3D = UnityEngine.PhysicMaterial;
using Combine3D = UnityEngine.PhysicMaterialCombine;
#endif

namespace MCPForUnityTests.Editor.Tools
{
    // Authored native regressions. This audit compiles them without launching the Editor.
    public class PhysicsMaterialIntegrityTests
    {
        private string assetRoot;
        private bool ownsAssetRoot;
        private List<GameObject> ownedObjects;

        [SetUp]
        public void SetUp()
        {
            assetRoot = "Assets/__McpPhysicsMaterialIntegrity_" + Guid.NewGuid().ToString("N");
            ownsAssetRoot = false;
            ownedObjects = new List<GameObject>();
            Assert.IsFalse(AssetDatabase.IsValidFolder(assetRoot), "Unique owned root must not already exist.");
        }

        [TearDown]
        public void TearDown()
        {
            try
            {
                foreach (var gameObject in ownedObjects)
                    if (gameObject != null) UnityEngine.Object.DestroyImmediate(gameObject);
            }
            finally
            {
                if (ownsAssetRoot)
                {
                    Assert.IsTrue(assetRoot.StartsWith("Assets/__McpPhysicsMaterialIntegrity_", StringComparison.Ordinal));
                    Assert.IsTrue(AssetDatabase.DeleteAsset(assetRoot), "Delete only the exact folder created by this fixture.");
                    ownsAssetRoot = false;
                }
            }
        }

        private void EnsureOwnedRoot()
        {
            if (ownsAssetRoot) return;
            string leaf = assetRoot.Substring("Assets/".Length);
            string guid = AssetDatabase.CreateFolder("Assets", leaf);
            ownsAssetRoot = !string.IsNullOrEmpty(guid);
            Assert.IsTrue(ownsAssetRoot, "Capture successful ownership before later assertions.");
        }

        private static JObject Call(JObject parameters) => JObject.FromObject(ManagePhysics.HandleCommand(parameters));

        private UnityEngine.Object CreateMaterial(string dimension)
        {
            EnsureOwnedRoot();
            var result = Call(new JObject
            {
                ["action"] = "create_physics_material", ["name"] = "Fixture",
                ["dimension"] = dimension, ["path"] = assetRoot
            });
            Assert.IsTrue(result.Value<bool>("success"), result.ToString());
            string path = result["data"].Value<string>("path");
            var material = AssetDatabase.LoadAssetAtPath<UnityEngine.Object>(path);
            Assert.IsNotNull(material);
            Assert.IsTrue(EditorUtility.IsPersistent(material));
            return material;
        }

        private GameObject NewObject(string name)
        {
            var gameObject = new GameObject(name);
            ownedObjects.Add(gameObject);
            return gameObject;
        }

        private static JObject Configure(UnityEngine.Object material, string dimension, JObject properties) => new JObject
        {
            ["action"] = "configure_physics_material", ["path"] = AssetDatabase.GetAssetPath(material),
            ["dimension"] = dimension, ["properties"] = properties
        };

        private static JObject Assign(UnityEngine.Object material, JToken target, string method = null, int? index = null)
        {
            var parameters = new JObject
            {
                ["action"] = "assign_physics_material", ["material_path"] = AssetDatabase.GetAssetPath(material), ["target"] = target
            };
            if (method != null) parameters["search_method"] = method;
            if (index.HasValue) parameters["component_index"] = index.Value;
            return parameters;
        }

        private static JObject Snapshot(UnityEngine.Object material)
        {
            if (material is Material3D m)
                return new JObject
                {
                    ["dynamic"] = m.dynamicFriction, ["static"] = m.staticFriction, ["bounce"] = m.bounciness,
                    ["frictionCombine"] = (int)m.frictionCombine, ["bounceCombine"] = (int)m.bounceCombine
                };
            var m2 = (PhysicsMaterial2D)material;
            return new JObject { ["friction"] = m2.friction, ["bounce"] = m2.bounciness };
        }

        [TestCase("bad")]
        [TestCase("null")]
        public void InvalidDimensionDoesNotCreateFolders(string dimension)
        {
            var parameters = new JObject
            {
                ["action"] = "create_physics_material", ["name"] = "Fixture", ["path"] = assetRoot + "/Nested",
                ["dimension"] = dimension == "null" ? JValue.CreateNull() : new JValue(dimension)
            };
            var result = Call(parameters);
            // If the regression creates this previously absent unique root, retain exact cleanup ownership.
            ownsAssetRoot = AssetDatabase.IsValidFolder(assetRoot);
            Assert.IsFalse(result.Value<bool>("success"));
            Assert.IsFalse(AssetDatabase.IsValidFolder(assetRoot));
            Assert.IsFalse(AssetDatabase.IsValidFolder(assetRoot + "/Nested"));
        }

        [TestCase("3d", "friction_combine", false)]
        [TestCase("3d", "bounce_combine", false)]
        [TestCase("3d", "static_friction", true)]
        [TestCase("3d", "bounciness", true)]
        [TestCase("2d", "bounciness", true)]
        public void LateInvalidPropertyPreservesWholeMaterial(string dimension, string invalidKey, bool throwsConversion)
        {
            var material = CreateMaterial(dimension);
            var before = Snapshot(material);
            bool dirty = EditorUtility.IsDirty(material);
            var properties = new JObject { [dimension == "3d" ? "dynamic_friction" : "friction"] = 0.2, [invalidKey] = "bad" };
            if (throwsConversion)
                LogAssert.Expect(LogType.Error, new Regex("\\[ManagePhysics\\] Action 'configure_physics_material' failed:"));
            var result = Call(Configure(material, dimension, properties));
            Assert.IsFalse(result.Value<bool>("success"));
            Assert.IsTrue(JToken.DeepEquals(before, Snapshot(material)), "No earlier setter may run before all conversions succeed.");
            Assert.AreEqual(dirty, EditorUtility.IsDirty(material));
        }

        [Test]
        public void NullScalarPreservesMaterial()
        {
            var material = CreateMaterial("2d");
            var before = Snapshot(material);
            LogAssert.Expect(LogType.Error, new Regex("\\[ManagePhysics\\] Action 'configure_physics_material' failed:"));
            var result = Call(Configure(material, "2d", new JObject { ["friction"] = JValue.CreateNull() }));
            Assert.IsFalse(result.Value<bool>("success"));
            Assert.IsTrue(JToken.DeepEquals(before, Snapshot(material)));
        }

        [Test]
        public void ValidAliasesPreserveSuccessfulOrderAndScalarForms()
        {
            var material = (Material3D)CreateMaterial("3d");
            var result = Call(Configure(material, "3D", new JObject
            {
                ["dynamic_friction"] = "0", ["dynamicFriction"] = 0.3,
                ["staticFriction"] = false, ["bounciness"] = 0.25,
                ["friction_combine"] = "minimum", ["frictionCombine"] = "multiply"
            }));
            Assert.IsTrue(result.Value<bool>("success"), result.ToString());
            Assert.AreEqual(0.3f, material.dynamicFriction);
            Assert.AreEqual(0, material.staticFriction);
            Assert.AreEqual(0.25f, material.bounciness);
            Assert.AreEqual(Combine3D.Multiply, material.frictionCombine);
            CollectionAssert.AreEqual(new[] { "dynamicFriction", "dynamicFriction", "staticFriction", "bounciness", "frictionCombine", "frictionCombine" }, result["data"]["changed"].ToObject<string[]>());
        }

        [TestCase("3d")]
        [TestCase("2d")]
        public void DuplicateCreatePreservesPersistentAsset(string dimension)
        {
            var material = CreateMaterial(dimension);
            string path = AssetDatabase.GetAssetPath(material);
            string guid = AssetDatabase.AssetPathToGUID(path);
            var before = Snapshot(material);
            var result = Call(new JObject
            {
                ["action"] = "create_physics_material", ["name"] = "Fixture", ["dimension"] = dimension,
                ["path"] = assetRoot, ["bounciness"] = 0.8
            });
            Assert.IsFalse(result.Value<bool>("success"));
            Assert.AreSame(material, AssetDatabase.LoadAssetAtPath<UnityEngine.Object>(path));
            Assert.AreEqual(guid, AssetDatabase.AssetPathToGUID(path));
            Assert.IsTrue(JToken.DeepEquals(before, Snapshot(material)));
        }

        [TestCase(false)]
        [TestCase(true)]
        public void OmittedSelectorAssignsIdInsteadOfNumericName(bool integerTarget)
        {
            var material = (Material3D)CreateMaterial("3d");
            var wanted = NewObject("__McpMaterialWanted_" + Guid.NewGuid().ToString("N"));
            var collider = wanted.AddComponent<BoxCollider>();
            var named = NewObject(wanted.GetInstanceIDCompat().ToString());
            var other = named.AddComponent<BoxCollider>();
            JToken target = integerTarget ? new JValue(wanted.GetInstanceIDCompat()) : new JValue(wanted.GetInstanceIDCompat().ToString());
            var result = Call(Assign(material, target));
            Assert.IsTrue(result.Value<bool>("success"), result.ToString());
            Assert.AreSame(material, collider.sharedMaterial);
            Assert.IsNull(other.sharedMaterial);
        }

        [Test]
        public void ExplicitNumericNameRemainsNameLookup()
        {
            var material = (Material3D)CreateMaterial("3d");
            var byId = NewObject("__McpMaterialById_" + Guid.NewGuid().ToString("N"));
            var original = byId.AddComponent<BoxCollider>();
            var byName = NewObject(byId.GetInstanceIDCompat().ToString());
            var named = byName.AddComponent<BoxCollider>();
            if (GameObjectLookup.FindByTarget(new JValue(byName.name), "by_name") != byName)
                Assert.Ignore("Explicit numeric-name control requires no preexisting scene object with the same name.");
            var result = Call(Assign(material, byName.name, "by_name"));
            Assert.IsTrue(result.Value<bool>("success"), result.ToString());
            Assert.AreSame(material, named.sharedMaterial);
            Assert.IsNull(original.sharedMaterial);
        }

        [Test]
        public void ExplicitPathAndIndexAssignExactSecondCollider()
        {
            var material = (PhysicsMaterial2D)CreateMaterial("2d");
            var parent = NewObject("__McpMaterialParent_" + Guid.NewGuid().ToString("N"));
            var child = NewObject("Child");
            child.transform.SetParent(parent.transform);
            var first = child.AddComponent<BoxCollider2D>();
            var second = child.AddComponent<BoxCollider2D>();
            var parameters = Assign(material, parent.name + "/Child", "by_path", 1);
            parameters["collider_type"] = "BoxCollider2D";
            var result = Call(parameters);
            Assert.IsTrue(result.Value<bool>("success"), result.ToString());
            Assert.IsNull(first.sharedMaterial);
            Assert.AreSame(material, second.sharedMaterial);
        }

        [Test]
        public void InactiveInferredIdDoesNotFallBackToActiveNumericName()
        {
            var material = (Material3D)CreateMaterial("3d");
            var inactive = NewObject("__McpMaterialInactive_" + Guid.NewGuid().ToString("N"));
            var first = inactive.AddComponent<BoxCollider>();
            inactive.SetActive(false);
            var other = NewObject(inactive.GetInstanceIDCompat().ToString()).AddComponent<BoxCollider>();
            var result = Call(Assign(material, inactive.GetInstanceIDCompat()));
            Assert.IsFalse(result.Value<bool>("success"));
            Assert.IsNull(first.sharedMaterial);
            Assert.IsNull(other.sharedMaterial);
        }

        [TestCase(-1)]
        [TestCase(1)]
        public void InvalidColliderIndexDoesNotAssign(int index)
        {
            var material = (Material3D)CreateMaterial("3d");
            var gameObject = NewObject("__McpMaterialIndex_" + Guid.NewGuid().ToString("N"));
            var collider = gameObject.AddComponent<BoxCollider>();
            var result = Call(Assign(material, gameObject.GetInstanceIDCompat(), "by_id", index));
            Assert.IsFalse(result.Value<bool>("success"));
            Assert.IsNull(collider.sharedMaterial);
        }
    }
}
