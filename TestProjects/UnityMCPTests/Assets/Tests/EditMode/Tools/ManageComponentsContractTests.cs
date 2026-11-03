using System.Collections.Generic;
using System.Reflection;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Editor.Tools;
using MCPForUnity.Runtime.Helpers;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEngine;

namespace MCPForUnityTests.Editor.Tools
{
    public class ManageComponentsContractProbe : MonoBehaviour
    {
        public float amount = 1f;
        public GameObject reference;
    }

    public class ManageComponentsContractTests
    {
        private readonly List<GameObject> _objects = new List<GameObject>();

        private GameObject Create(string name)
        {
            var go = new GameObject(name);
            _objects.Add(go);
            return go;
        }

        [TearDown]
        public void TearDown()
        {
            foreach (var go in _objects)
                Object.DestroyImmediate(go);
            _objects.Clear();
        }

        private static JObject Send(JObject request) => JObject.FromObject(ManageComponents.HandleCommand(request));

        private static JObject Request(string action, JToken target) =>
            new JObject
            {
                ["action"] = action,
                ["target"] = target,
                ["componentType"] = typeof(ManageComponentsContractProbe).FullName,
            };

        [TestCase("add", false)]
        [TestCase("remove", false)]
        [TestCase("set_property", false)]
        [TestCase("set_property", true)]
        public void ExplicitByName_HonorsNumericNameInsteadOfOtherObjectId(string action, bool integer)
        {
            var unrelated = Create("unrelated-component-target");
            var unrelatedComponent = unrelated.AddComponent<ManageComponentsContractProbe>();
            var named = Create(unrelated.GetInstanceIDCompat().ToString());
            if (action != "add")
                named.AddComponent<ManageComponentsContractProbe>();
            var request = Request(action, integer ? new JValue(unrelated.GetInstanceIDCompat()) : new JValue(named.name));
            request["searchMethod"] = "by_name";
            if (action == "set_property")
            {
                request["property"] = "amount";
                request["value"] = 7;
            }
            var result = Send(request);
            Assert.IsTrue(result.Value<bool>("success"), result.ToString());
            Assert.AreEqual(named.GetInstanceIDCompat(), result["data"].Value<int>("instanceID"));
            Assert.AreEqual(1f, unrelatedComponent.amount);
            Assert.AreEqual(1, unrelated.GetComponents<ManageComponentsContractProbe>().Length);
            Assert.AreEqual(action == "remove" ? 0 : 1, named.GetComponents<ManageComponentsContractProbe>().Length);
            if (action == "set_property")
                Assert.AreEqual(7f, named.GetComponent<ManageComponentsContractProbe>().amount);
        }

        [Test]
        public void ExplicitByPath_HonorsNumericRootName()
        {
            var unrelated = Create("unrelated-path-target");
            unrelated.AddComponent<ManageComponentsContractProbe>();
            var named = Create(unrelated.GetInstanceIDCompat().ToString());
            named.AddComponent<ManageComponentsContractProbe>();
            var request = Request("set_property", named.name);
            request["search_method"] = "by_path";
            request["property"] = "amount";
            request["value"] = 9;
            var result = Send(request);
            Assert.IsTrue(result.Value<bool>("success"), result.ToString());
            Assert.AreEqual(9f, named.GetComponent<ManageComponentsContractProbe>().amount);
            Assert.AreEqual(1f, unrelated.GetComponent<ManageComponentsContractProbe>().amount);
        }

        [TestCase(false)]
        [TestCase(true)]
        public void ImplicitInstanceId_StillSelectsObject(bool integer)
        {
            var go = Create("implicit-target");
            var component = go.AddComponent<ManageComponentsContractProbe>();
            var request = Request("set_property", integer ? new JValue(go.GetInstanceIDCompat()) : new JValue(go.GetInstanceIDCompat().ToString()));
            request["property"] = "amount";
            request["value"] = 3;
            var result = Send(request);
            Assert.IsTrue(result.Value<bool>("success"), result.ToString());
            Assert.AreEqual(3f, component.amount);
        }

        [TestCase(false)]
        [TestCase(true)]
        public void ImplicitHierarchyPath_SelectsChild(bool strict)
        {
            var root = Create("component-path-root");
            var child = Create("component-path-child");
            child.transform.SetParent(root.transform);
            var component = child.AddComponent<ManageComponentsContractProbe>();
            var request = Request("set_property", root.name + "/" + child.name);
            request["strict_selection"] = strict;
            request["property"] = "amount";
            request["value"] = 3;
            var result = Send(request);
            Assert.IsTrue(result.Value<bool>("success"), result.ToString());
            Assert.AreEqual(3f, component.amount);
        }

        [TestCase(false)]
        [TestCase(true)]
        public void SlashNameCollision_RequiresExplicitMethod(bool strict)
        {
            var root = Create("component-collision-root");
            var child = Create("component-collision-child");
            child.transform.SetParent(root.transform);
            string path = root.name + "/" + child.name;
            var literal = Create(path);
            var childComponent = child.AddComponent<ManageComponentsContractProbe>();
            var literalComponent = literal.AddComponent<ManageComponentsContractProbe>();
            var request = Request("set_property", path);
            request["strict_selection"] = strict;
            request["property"] = "amount";
            request["value"] = 3;
            var result = Send(request);
            Assert.IsFalse(result.Value<bool>("success"), result.ToString());
            Assert.AreEqual(1f, childComponent.amount);
            Assert.AreEqual(1f, literalComponent.amount);

            request["search_method"] = "by_path";
            result = Send(request);
            Assert.IsTrue(result.Value<bool>("success"), result.ToString());
            Assert.AreEqual(3f, childComponent.amount);
            Assert.AreEqual(1f, literalComponent.amount);

            request["search_method"] = "by_name";
            request["value"] = 4;
            result = Send(request);
            Assert.IsTrue(result.Value<bool>("success"), result.ToString());
            Assert.AreEqual(3f, childComponent.amount);
            Assert.AreEqual(4f, literalComponent.amount);
        }

        [TestCase(false)]
        [TestCase(true)]
        public void ImplicitSlashNameWithoutHierarchy_StillResolves(bool strict)
        {
            var literal = Create("component-only/literal-name");
            var component = literal.AddComponent<ManageComponentsContractProbe>();
            var request = Request("set_property", literal.name);
            request["strict_selection"] = strict;
            request["property"] = "amount";
            request["value"] = 5;
            var result = Send(request);
            Assert.IsTrue(result.Value<bool>("success"), result.ToString());
            Assert.AreEqual(5f, component.amount);
        }

        [TestCase(false)]
        [TestCase(true)]
        public void ImplicitRelativePath_WithMultipleHierarchiesIsRejected(bool strict)
        {
            var root = Create("component-duplicate-root");
            var first = Create("component-duplicate-child");
            first.transform.SetParent(root.transform);
            var container = Create("component-duplicate-container");
            var nestedRoot = Create(root.name);
            nestedRoot.transform.SetParent(container.transform);
            var second = Create(first.name);
            second.transform.SetParent(nestedRoot.transform);
            var firstComponent = first.AddComponent<ManageComponentsContractProbe>();
            var secondComponent = second.AddComponent<ManageComponentsContractProbe>();
            var request = Request("set_property", root.name + "/" + first.name);
            request["strict_selection"] = strict;
            request["property"] = "amount";
            request["value"] = 8;
            var result = Send(request);
            Assert.IsFalse(result.Value<bool>("success"), result.ToString());
            Assert.AreEqual(1f, firstComponent.amount);
            Assert.AreEqual(1f, secondComponent.amount);
            request["target"] = "/" + root.name + "/" + first.name;
            result = Send(request);
            Assert.IsTrue(result.Value<bool>("success"), result.ToString());
            Assert.AreEqual(8f, firstComponent.amount);
            Assert.AreEqual(1f, secondComponent.amount);
        }

        [Test]
        public void MissingSingleValue_IsRejectedWithoutAssigningOtherProperties()
        {
            var go = Create("missing-value-target");
            var component = go.AddComponent<ManageComponentsContractProbe>();
            var request = Request("set_property", go.GetInstanceIDCompat());
            request["property"] = "reference";
            request["properties"] = new JObject { ["amount"] = 8 };
            var result = Send(request);
            Assert.IsFalse(result.Value<bool>("success"), result.ToString());
            StringAssert.Contains("value", result.Value<string>("error"));
            Assert.AreEqual(1f, component.amount);
        }

        [Test]
        public void ExplicitNull_ClearsObjectReference()
        {
            var go = Create("null-target");
            var component = go.AddComponent<ManageComponentsContractProbe>();
            component.reference = Create("referenced-object");
            var request = Request("set_property", go.GetInstanceIDCompat());
            request["property"] = "reference";
            request["value"] = JValue.CreateNull();
            var result = Send(request);
            Assert.IsTrue(result.Value<bool>("success"), result.ToString());
            Assert.IsNull(component.reference);
        }

        [TestCase(false)]
        [TestCase(true)]
        public void AddPropertyFailure_ReportsCreatedComponentAndPartialState(bool mixed)
        {
            var go = Create("partial-add-target");
            var request = Request("add", go.GetInstanceIDCompat());
            var properties = new JObject { ["missingField"] = 4 };
            if (mixed)
                properties["amount"] = 6;
            request["properties"] = properties;
            var result = Send(request);
            var component = go.GetComponent<ManageComponentsContractProbe>();
            Assert.IsNotNull(component, "The created component must remain inspectable; no rollback is claimed.");
            Assert.AreEqual(mixed ? 6f : 1f, component.amount);
            Assert.IsFalse(result.Value<bool>("success"), result.ToString());
            Assert.IsTrue(result["data"].Value<bool>("componentAdded"));
            Assert.AreEqual(component.GetInstanceIDCompat(), result["data"].Value<int>("componentInstanceID"));
            Assert.AreEqual(go.GetInstanceIDCompat(), result["data"].Value<int>("instanceID"));
            StringAssert.Contains("missingField", result["data"]["errors"].ToString());
        }

        [Test]
        public void AddValidProperties_RetainsSuccessAndConfiguredValue()
        {
            var go = Create("successful-add-target");
            var request = Request("add", go.GetInstanceIDCompat());
            request["properties"] = new JObject { ["amount"] = 5 };
            var result = Send(request);
            Assert.IsTrue(result.Value<bool>("success"), result.ToString());
            Assert.AreEqual(5f, go.GetComponent<ManageComponentsContractProbe>().amount);
        }

        [TestCase("add")]
        [TestCase("set_property")]
        public void PropertyErrors_AreRecognizedAsFailuresByBatchClassifier(string action)
        {
            var go = Create("batch-property-error");
            if (action == "set_property")
                go.AddComponent<ManageComponentsContractProbe>();
            var request = Request(action, go.GetInstanceIDCompat());
            request["properties"] = new JObject { ["missingField"] = 2 };
            var rawResult = ManageComponents.HandleCommand(request);
            var classifier = typeof(BatchExecute).GetMethod("DetermineCallSucceeded", BindingFlags.NonPublic | BindingFlags.Static);
            Assert.IsNotNull(classifier);
            Assert.IsFalse((bool)classifier.Invoke(null, new[] { rawResult }), JObject.FromObject(rawResult).ToString());
        }

        [TestCase(false)]
        [TestCase(true)]
        public void RootAbsolutePath_ResolvesOnlyRootedHierarchyIncludingInactive(bool inactive)
        {
            var root = Create("root-path-contract");
            var child = Create("child-path-contract");
            child.transform.SetParent(root.transform);
            child.SetActive(!inactive);
            var container = Create("other-container-contract");
            var nestedRoot = Create(root.name);
            nestedRoot.transform.SetParent(container.transform);
            var nestedChild = Create(child.name);
            nestedChild.transform.SetParent(nestedRoot.transform);
            var path = root.name + "/" + child.name;
            var rooted = GameObjectLookup.SearchGameObjects("by_path", "/" + path, true);
            CollectionAssert.AreEqual(new[] { child.GetInstanceIDCompat() }, rooted);
            var relative = GameObjectLookup.SearchGameObjects("by_path", path, true);
            CollectionAssert.AreEquivalent(new[] { child.GetInstanceIDCompat(), nestedChild.GetInstanceIDCompat() }, relative);
        }

        [Test]
        public void PathSearch_MaxResultsCapsMatchesWithoutChangingFirstResult()
        {
            for (int i = 0; i < 200; i++)
            {
                var root = Create("limited-path-root-" + i);
                var child = Create("limited-path-child");
                child.transform.SetParent(root.transform);
            }
            var all = GameObjectLookup.SearchGameObjects("by_path", "limited-path-child", true);
            Assert.AreEqual(200, all.Count);
            CollectionAssert.AreEqual(all, GameObjectLookup.SearchGameObjects("by_path", "limited-path-child", true, -1));
            var limited = GameObjectLookup.SearchGameObjects("by_path", "limited-path-child", true, 1);
            Assert.AreEqual(1, limited.Count);
            Assert.AreEqual(all[0], limited[0]);
        }
    }
}
