using System;
using System.IO;
using System.Linq;
using MCPForUnity.Editor.Tools;
using MCPForUnity.Runtime.Helpers;
using MCPForUnityTests.Editor.Helpers;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;
using UnityEngine.SceneManagement;

namespace MCPForUnityTests.Editor.Tools
{
    public class ComponentMutationIntegrityTests
    {
        private readonly PrefabTestSceneFixture sceneFixture = new PrefabTestSceneFixture();
        private Scene ownedScene;
        private UnityEngine.Object[] originalSelection;
        private UnityEngine.Object originalActiveObject;
        private GameObject target;
        private BoxCollider first;
        private BoxCollider second;
        private string assetRoot;
        private string assetRootGuid;
        private bool capturedState;
        private bool ownsAssetRoot;
        private bool boxDefaultTrigger;

        [OneTimeSetUp]
        public void OneTimeSetUp() => sceneFixture.PrepareRunnerBootstrap();

        [OneTimeTearDown]
        public void OneTimeTearDown() => sceneFixture.RestoreRunnerBootstrap();

        [SetUp]
        public void SetUp()
        {
            capturedState = false;
            ownsAssetRoot = false;
            assetRootGuid = null;
            ownedScene = default;
            target = null;
            if (PrefabStageUtility.GetCurrentPrefabStage() != null)
                Assert.Ignore("This fixture does not change an existing Prefab Stage or its lookup context.");
            originalSelection = Selection.objects;
            originalActiveObject = Selection.activeObject;
            capturedState = true;
            string suffix = Guid.NewGuid().ToString("N");
            ownedScene = sceneFixture.Create("McpComponentMutationIntegrity_", suffix);
            assetRoot = "Assets/__McpComponentMutationIntegrity_" + suffix;
            Assert.IsFalse(AssetDatabase.IsValidFolder(assetRoot));
            Assert.IsFalse(Directory.Exists(Path.Combine(Application.dataPath, assetRoot.Substring("Assets/".Length))));
            assetRootGuid = AssetDatabase.CreateFolder("Assets", assetRoot.Substring("Assets/".Length));
            Assert.IsNotEmpty(assetRootGuid);
            Assert.AreEqual(assetRoot, AssetDatabase.GUIDToAssetPath(assetRootGuid));
            ownsAssetRoot = true;
            target = new GameObject("ComponentIntegrity_" + Guid.NewGuid().ToString("N"));
            Assert.AreEqual(ownedScene, target.scene);
            target.AddComponent<BoxCollider>();
            target.AddComponent<BoxCollider>();
            BoxCollider[] components = target.GetComponents<BoxCollider>();
            Assert.AreEqual(2, components.Length, "The regression requires two distinct same-type components.");
            first = components[0];
            second = components[1];
            Assert.AreNotSame(first, second);
            boxDefaultTrigger = first.isTrigger;
            Assert.AreEqual(boxDefaultTrigger, second.isTrigger);
            first.isTrigger = second.isTrigger = true;
            Assert.IsTrue(EditorSceneManager.SaveScene(ownedScene, assetRoot + "/" + ownedScene.name + ".unity"));
            Assert.IsFalse(ownedScene.isDirty, "Only the saved owned scene establishes a clean dirty-state oracle.");
        }

        [TearDown]
        public void TearDown()
        {
            if (!capturedState)
                return;
            try
            {
                if (target != null)
                {
                    foreach (Component component in target.GetComponents<Component>().Where(component => component != null))
                        Undo.ClearUndo(component);
                    Undo.ClearUndo(target);
                    UnityEngine.Object.DestroyImmediate(target);
                }
                sceneFixture.Close();
                if (ownsAssetRoot)
                {
                    Assert.IsTrue(assetRoot.StartsWith("Assets/__McpComponentMutationIntegrity_", StringComparison.Ordinal));
                    Assert.AreEqual(assetRootGuid, AssetDatabase.AssetPathToGUID(assetRoot));
                    Assert.IsTrue(AssetDatabase.DeleteAsset(assetRoot), "Only the exact folder successfully created by this fixture is deleted.");
                }
            }
            finally
            {
                Selection.objects = originalSelection;
                Selection.activeObject = originalActiveObject;
            }
        }

        private JObject Request(string action) =>
            new JObject
            {
                ["action"] = action,
                ["target"] = target.GetInstanceIDCompat(),
                ["searchMethod"] = "by_id",
                ["componentType"] = "BoxCollider",
                ["property"] = "isTrigger",
                ["value"] = false,
            };

        private static JObject Call(JObject request) => JObject.FromObject(ManageComponents.HandleCommand(request));

        private static void Success(JObject response) => Assert.IsTrue(response.Value<bool>("success"), response.ToString());

        private void Unchanged()
        {
            CollectionAssert.AreEqual(new[] { first, second }, target.GetComponents<BoxCollider>());
            Assert.IsTrue(first.isTrigger && second.isTrigger);
            Assert.IsFalse(ownedScene.isDirty);
        }

        [TestCase("set_property", "string")]
        [TestCase("set_property", "boolean")]
        [TestCase("set_property", "array")]
        [TestCase("set_property", "object")]
        [TestCase("set_property", "overflow")]
        [TestCase("set_property", "blank")]
        [TestCase("set_property", "fraction")]
        [TestCase("remove", "string")]
        [TestCase("remove", "boolean")]
        [TestCase("remove", "array")]
        [TestCase("remove", "object")]
        [TestCase("remove", "overflow")]
        [TestCase("remove", "blank")]
        [TestCase("remove", "fraction")]
        public void InvalidExplicitIndex_PreservesBothComponentsAndCleanScene(string action, string kind)
        {
            JToken index =
                kind == "string" ? new JValue("bad")
                : kind == "blank" ? new JValue("  ")
                : kind == "fraction" ? new JValue(1.9)
                : kind == "boolean" ? new JValue(true)
                : kind == "array" ? new JArray(1)
                : kind == "object" ? new JObject()
                : new JValue(2147483648L);
            JObject request = Request(action);
            request["componentIndex"] = index;
            Assert.IsFalse(Call(request).Value<bool>("success"));
            Unchanged();
        }

        [TestCase("set_property")]
        [TestCase("remove")]
        public void SnakeIndexValidationAndCamelNullPrecedence_ArePreserved(string action)
        {
            JObject request = Request(action);
            request["component_index"] = "bad";
            Assert.IsFalse(Call(request).Value<bool>("success"));
            Unchanged();
            request["componentIndex"] = JValue.CreateNull();
            request["component_index"] = 1;
            Success(Call(request));
            if (action == "remove")
                CollectionAssert.AreEqual(new[] { second }, target.GetComponents<BoxCollider>());
            else
            {
                Assert.IsFalse(first.isTrigger);
                Assert.IsTrue(second.isTrigger);
            }
        }

        [TestCase("set_property", "omitted")]
        [TestCase("set_property", "null")]
        [TestCase("set_property", "zero")]
        [TestCase("set_property", "one")]
        [TestCase("set_property", "string")]
        [TestCase("remove", "omitted")]
        [TestCase("remove", "null")]
        [TestCase("remove", "zero")]
        [TestCase("remove", "one")]
        [TestCase("remove", "string")]
        public void ValidIndexValues_SelectOnlyRequestedComponent(string action, string kind)
        {
            JObject request = Request(action);
            if (kind != "omitted")
                request["componentIndex"] =
                    kind == "null" ? JValue.CreateNull()
                    : kind == "zero" ? new JValue(0)
                    : kind == "one" ? new JValue(1)
                    : new JValue("1");
            bool selectsSecond = kind == "one" || kind == "string";
            Success(Call(request));
            if (action == "remove")
                CollectionAssert.AreEqual(new[] { selectsSecond ? first : second }, target.GetComponents<BoxCollider>());
            else
            {
                Assert.AreEqual(selectsSecond, first.isTrigger);
                Assert.AreEqual(!selectsSecond, second.isTrigger);
            }
            Assert.IsTrue(ownedScene.isDirty);
        }

        [TestCase("array")]
        [TestCase("string")]
        [TestCase("boolean")]
        [TestCase("secondary")]
        public void MalformedAddProperties_DoesNotAllocateOrDirty(string kind)
        {
            JObject request = Request("add");
            request[kind == "secondary" ? "componentProperties" : "properties"] =
                kind == "array" ? new JArray()
                : kind == "string" ? new JValue("bad")
                : new JValue(false);
            Assert.IsFalse(Call(request).Value<bool>("success"));
            Unchanged();
        }

        [TestCase("omitted")]
        [TestCase("null")]
        [TestCase("empty")]
        [TestCase("fallback")]
        [TestCase("empty_primary")]
        [TestCase("ignored_secondary")]
        public void ValidAddPropertiesDefaultsAndAliasPrecedence_ArePreserved(string kind)
        {
            JObject request = Request("add");
            if (kind == "null")
                request["properties"] = JValue.CreateNull();
            if (kind == "empty" || kind == "empty_primary")
                request["properties"] = new JObject();
            if (kind == "fallback")
                request["properties"] = false;
            if (kind == "fallback" || kind == "empty_primary")
                request["componentProperties"] = new JObject { ["isTrigger"] = !boxDefaultTrigger };
            if (kind == "ignored_secondary")
            {
                request["properties"] = new JObject { ["isTrigger"] = !boxDefaultTrigger };
                request["componentProperties"] = false;
            }
            Success(Call(request));
            BoxCollider[] components = target.GetComponents<BoxCollider>();
            Assert.AreEqual(3, components.Length);
            Assert.AreSame(first, components[0]);
            Assert.AreSame(second, components[1]);
            Assert.AreEqual(kind == "fallback" || kind == "ignored_secondary" ? !boxDefaultTrigger : boxDefaultTrigger, components[2].isTrigger);
        }

        [TestCase("add")]
        [TestCase("set_property")]
        public void BestEffortPropertyFailures_RetainEarlierWritesAndUsefulResult(string action)
        {
            JObject request = Request(action);
            bool expectedValue = action == "add" ? !boxDefaultTrigger : false;
            request["properties"] = new JObject { ["isTrigger"] = expectedValue, ["MissingPropertyForIntegrityTest"] = 1 };
            JObject response = Call(request);
            Assert.IsFalse(response.Value<bool>("success"));
            Assert.IsTrue(((JArray)response["data"]["errors"]).Count > 0);
            BoxCollider[] components = target.GetComponents<BoxCollider>();
            Assert.AreEqual(action == "add" ? 3 : 2, components.Length);
            Assert.AreEqual(expectedValue, components[action == "add" ? 2 : 0].isTrigger);
            if (action == "add")
                Assert.IsTrue(response["data"].Value<bool>("componentAdded"));
            Assert.IsTrue(ownedScene.isDirty);
        }

        [Test]
        public void MissingSingleValue_DoesNotApplyValidBulkProperties()
        {
            JObject request = Request("set_property");
            request.Remove("value");
            request["properties"] = new JObject { ["isTrigger"] = false };
            Assert.IsFalse(Call(request).Value<bool>("success"));
            Unchanged();
        }
    }
}
