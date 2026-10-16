using System;
using System.Collections;
using System.IO;
using System.Linq;
using System.Text;
using MCPForUnity.Editor.Tools;
using MCPForUnity.Runtime.Helpers;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEngine;
using UnityEngine.TestTools;
using UnityEngine.UIElements;

namespace MCPForUnityTests.Editor.Tools
{
    public class UIVisualTreeBudgetTests
    {
        private GameObject owner;
        private UIDocument document;
        private VisualElement root;
        private PanelSettings panel;
        private ThemeStyleSheet theme;

        [SetUp]
        public void SetUp()
        {
            owner = new GameObject("G");
            document = owner.AddComponent<UIDocument>();
            root = document.rootVisualElement;
            Assert.IsNotNull(root);
            root.Clear();
            root.ClearClassList();
            root.name = "R";
        }

        [TearDown]
        public void TearDown()
        {
            UnityEngine.Object.DestroyImmediate(owner);
            if (panel != null)
                UnityEngine.Object.DestroyImmediate(panel);
            if (theme != null)
                UnityEngine.Object.DestroyImmediate(theme);
        }

        private JObject Read(JObject budgets = null)
        {
            var request = new JObject { ["action"] = "get_visual_tree", ["target"] = owner.GetInstanceIDCompat().ToString() };
            if (budgets != null)
                request.Merge(budgets);
            return JObject.FromObject(ManageUI.HandleCommand(request));
        }

        private JObject Data(JObject budgets = null)
        {
            var response = Read(budgets);
            Assert.IsTrue(response.Value<bool>("success"), response.ToString());
            Assert.IsNotNull(response["data"]?["traversal"], response.ToString());
            return (JObject)response["data"];
        }

        private static VisualElement Element(string name)
        {
            var element = new VisualElement { name = name };
            element.ClearClassList();
            return element;
        }

        [Test]
        public void SmallTreePreservesValuesAndCompleteEvidenceWithoutMutation()
        {
            var label = new Label("hello") { name = "L" };
            label.ClearClassList();
            label.AddToClassList("first");
            label.AddToClassList("second");
            root.Add(label);
            var data = Data();
            var tree = data["tree"];
            Assert.AreEqual("R", tree.Value<string>("name"));
            Assert.AreEqual("hello", tree["children"][0].Value<string>("text"));
            CollectionAssert.AreEqual(new[] { "first", "second" }, tree["children"][0]["classes"].Values<string>());
            Assert.IsTrue(data["traversal"].Value<bool>("complete"));
            Assert.IsFalse(data["traversal"].Value<bool>("truncated"));
            Assert.AreEqual(2, data["traversal"].Value<int>("returned_nodes"));
            Assert.AreEqual(7, data["traversal"].Value<int>("evaluations"));
            Assert.AreEqual(1, root.childCount);
            Assert.AreEqual("hello", label.text);
            Assert.IsNull(root.panel, "A read must not require or create a panel.");
        }

        [Test]
        public void WideTreeStopsAtNodePrefixWithoutCountingDescendants()
        {
            for (int i = 0; i < 2000; i++)
                root.Add(Element("n" + i));
            var data = Data(new JObject { ["max_nodes"] = 3 });
            Assert.AreEqual(3, data["traversal"].Value<int>("returned_nodes"));
            Assert.AreEqual(8, data["traversal"].Value<int>("evaluations"));
            Assert.AreEqual(2000, data["tree"].Value<int>("childCount"));
            Assert.IsTrue(data["tree"].Value<bool>("truncated"));
            CollectionAssert.AreEqual(new[] { "n0", "n1" }, data["tree"]["children"].Select(n => n.Value<string>("name")));
            CollectionAssert.Contains(data["traversal"]["reasons"].Values<string>(), "max_nodes");
        }

        [TestCase(0)]
        [TestCase(1)]
        [TestCase(24)]
        public void DeepTreeHonorsInclusiveDepthWithoutRecursivePrewalk(int depth)
        {
            var parent = root;
            for (int i = 0; i < 300; i++)
            {
                var child = Element("N");
                parent.Add(child);
                parent = child;
            }
            var data = Data(new JObject { ["max_depth"] = depth });
            Assert.AreEqual(depth + 1, data["traversal"].Value<int>("returned_nodes"));
            var node = data["tree"];
            for (int i = 0; i < depth; i++)
                node = node["children"][0];
            Assert.IsTrue(node.Value<bool>("truncated"));
            Assert.AreEqual(1, node.Value<int>("childCount"));
            CollectionAssert.Contains(data["traversal"]["reasons"].Values<string>(), "max_depth");
        }

        [Test]
        public void DepthCutoffContinuesLaterSiblings()
        {
            var first = Element("first");
            first.Add(Element("hidden"));
            root.Add(first);
            root.Add(Element("later"));
            var data = Data(new JObject { ["max_depth"] = 1 });
            Assert.AreEqual("later", data["tree"]["children"][1].Value<string>("name"));
            Assert.AreEqual(3, data["traversal"].Value<int>("returned_nodes"));
        }

        [TestCase(1, 1)]
        [TestCase(2, 1)]
        [TestCase(3, 1)]
        [TestCase(4, 2)]
        public void EvaluationBudgetChargesClassTerminationAndChildFetch(int budget, int nodes)
        {
            root.Add(Element("child"));
            var data = Data(new JObject { ["max_evaluations"] = budget });
            Assert.AreEqual(budget, data["traversal"].Value<int>("evaluations"));
            Assert.AreEqual(nodes, data["traversal"].Value<int>("returned_nodes"));
            CollectionAssert.Contains(data["traversal"]["reasons"].Values<string>(), "max_evaluations");
            Assert.IsFalse(data["traversal"].Value<bool>("complete"));
        }

        [Test]
        public void ExactLeafAndWorkBoundariesDoNotInventTruncation()
        {
            var data = Data(new JObject { ["max_nodes"] = 1, ["max_evaluations"] = 2 });
            Assert.IsTrue(data["traversal"].Value<bool>("complete"));
            Assert.AreEqual(2, data["traversal"].Value<int>("evaluations"));
            int characters = data["traversal"].Value<int>("characters");
            Assert.IsTrue(Data(new JObject { ["max_characters"] = characters })["traversal"].Value<bool>("complete"));
            Assert.IsFalse(Data(new JObject { ["max_characters"] = characters - 1 })["traversal"].Value<bool>("complete"));
        }

        [Test]
        public void LongStringsAndMetadataShareBudgetAndPreserveSurrogatePairs()
        {
            owner.name = new string('g', 20000);
            root.name = new string('a', 8191) + "\U0001F680tail";
            var data = Data(new JObject { ["max_characters"] = 20000 });
            Assert.AreEqual(8192, data.Value<string>("gameObject").Length);
            Assert.AreEqual(8191, data["tree"].Value<string>("name").Length);
            CollectionAssert.Contains(data["traversal"]["metadata_truncated_fields"].Values<string>(), "gameObject");
            CollectionAssert.Contains(data["tree"]["truncated_fields"].Values<string>(), "name");
            CollectionAssert.Contains(data["traversal"]["reasons"].Values<string>(), "per_string_limit");
            new UTF8Encoding(false, true).GetBytes(data.ToString());
            Assert.LessOrEqual(data["traversal"].Value<int>("characters"), 20000);
            Assert.Less(Read().Value<string>("message").Length, 200);
        }

        [Test]
        public void CharacterExhaustionEmitsPartialRootRatherThanMissingTree()
        {
            root.Add(Element("child"));
            var data = Data(new JObject { ["max_characters"] = 1 });
            Assert.IsNotNull(data["tree"]);
            Assert.AreEqual("ready", data["traversal"].Value<string>("tree_status"));
            Assert.AreEqual(1, data["traversal"].Value<int>("returned_nodes"));
            Assert.AreEqual(1, data["traversal"].Value<int>("characters"));
            Assert.IsTrue(data["tree"].Value<bool>("truncated"));
            CollectionAssert.Contains(data["tree"]["truncated_fields"].Values<string>(), "type");
        }

        [UnityTest]
        public IEnumerator ColorValuesAreWholeOrExplicitlyOmittedAtCharacterBoundary()
        {
            panel = ScriptableObject.CreateInstance<PanelSettings>();
            theme = ScriptableObject.CreateInstance<ThemeStyleSheet>();
            panel.themeStyleSheet = theme;
            document.panelSettings = panel;
            root = document.rootVisualElement;
            root.Clear();
            root.ClearClassList();
            root.name = "R";
            root.style.color = Color.red;
            root.style.backgroundColor = Color.blue;
            yield return null;
            var full = Data();
            Assert.AreEqual("#FF0000FF", full["tree"]["resolvedStyle"].Value<string>("color"));
            Assert.AreEqual("#0000FFFF", full["tree"]["resolvedStyle"].Value<string>("backgroundColor"));
            int beforeColors =
                full.Value<string>("gameObject").Length
                + full["tree"].Value<string>("type").Length
                + full["tree"].Value<string>("name").Length
                + full["tree"]["classes"].Values<string>().Sum(value => value.Length);
            var limited = Data(new JObject { ["max_characters"] = beforeColors + 8 });
            Assert.IsNull(limited["tree"]["resolvedStyle"]?["color"]);
            Assert.IsNull(limited["tree"]["resolvedStyle"]?["backgroundColor"]);
            CollectionAssert.Contains(limited["tree"]["truncated_fields"].Values<string>(), "resolvedStyle.color");
            CollectionAssert.Contains(limited["tree"]["truncated_fields"].Values<string>(), "resolvedStyle.backgroundColor");
            Assert.AreEqual(beforeColors, limited["traversal"].Value<int>("characters"));
        }

        [Test]
        public void ClassLimitAndLongClassAreLocalRatherThanWholeTreeStops()
        {
            root.AddToClassList(new string('c', 10000));
            for (int i = 1; i < 200; i++)
                root.AddToClassList("c" + i);
            root.Add(Element("later"));
            var data = Data(new JObject { ["max_characters"] = 262144 });
            Assert.AreEqual(64, data["tree"]["classes"].Count());
            Assert.AreEqual(8192, data["tree"]["classes"][0].Value<string>().Length);
            Assert.AreEqual("later", data["tree"]["children"][0].Value<string>("name"));
            CollectionAssert.Contains(data["traversal"]["reasons"].Values<string>(), "class_limit");
            Assert.IsNull(data["tree"]["truncated"]);
        }

        [TestCase("metadata")]
        [TestCase("name")]
        [TestCase("text")]
        [TestCase("class")]
        public void HugeLocalStringCapDoesNotSpendRemainingSharedBudget(string field)
        {
            string huge = new string('x', 200000);
            var container = Element("container");
            if (field == "metadata")
                owner.name = huge;
            if (field == "name")
                container.name = huge;
            if (field == "class")
                container.AddToClassList(huge);
            if (field == "text")
            {
                var label = new Label(huge) { name = "label" };
                label.ClearClassList();
                container.Add(label);
            }
            container.Add(Element("later-child"));
            root.Add(container);
            root.Add(Element("later-sibling"));
            var data = Data();
            Assert.AreEqual("later-sibling", data["tree"]["children"][1].Value<string>("name"));
            Assert.AreEqual("later-child", data["tree"]["children"][0]["children"].Last.Value<string>("name"));
            CollectionAssert.AreEqual(new[] { "per_string_limit" }, data["traversal"]["reasons"].Values<string>());
            Assert.Less(data["traversal"].Value<int>("characters"), 65536);
            Assert.IsFalse(data["traversal"].Value<bool>("complete"));
            Assert.IsTrue(data["traversal"].Value<bool>("truncated"));
        }

        [Test]
        public void UninitializedRootIsUnavailableRatherThanBudgetTruncated()
        {
            document.enabled = false;
            Assert.IsNull(document.rootVisualElement, "Unity fixture must expose an uninitialized root.");
            var data = Data();
            Assert.AreEqual(JTokenType.Null, data["tree"].Type);
            Assert.AreEqual("not_initialized", data["traversal"].Value<string>("tree_status"));
            Assert.AreEqual(0, data["traversal"].Value<int>("returned_nodes"));
            Assert.AreEqual(0, data["traversal"].Value<int>("evaluations"));
            Assert.IsFalse(data["traversal"].Value<bool>("complete"));
            Assert.IsFalse(data["traversal"].Value<bool>("truncated"));
        }

        private sealed class ContentHost : VisualElement
        {
            private readonly VisualElement content = new VisualElement();

            public ContentHost()
            {
                hierarchy.Add(new Label("internal"));
                hierarchy.Add(content);
            }

            public override VisualElement contentContainer => content;
        }

        [Test]
        public void ContentContainerOrderAndDuplicateNamesRemainReadable()
        {
            var host = new ContentHost { name = "host" };
            host.Add(Element("same"));
            host.Add(Element("same"));
            root.Add(host);
            var data = Data();
            var children = data["tree"]["children"][0]["children"];
            Assert.AreEqual(2, children.Count());
            CollectionAssert.AreEqual(new[] { "same", "same" }, children.Select(n => n.Value<string>("name")));
            Assert.IsTrue(data["traversal"].Value<bool>("complete"));
        }

        [TestCase("max_depth", "-1")]
        [TestCase("max_depth", "25")]
        [TestCase("max_nodes", "0")]
        [TestCase("max_nodes", "1001")]
        [TestCase("max_evaluations", "0")]
        [TestCase("max_evaluations", "20001")]
        [TestCase("max_characters", "0")]
        [TestCase("max_characters", "262145")]
        [TestCase("max_depth", "true")]
        [TestCase("max_nodes", "1.5")]
        [TestCase("max_evaluations", "[]")]
        [TestCase("max_characters", "{}")]
        [TestCase("max_depth", "999999999999999999999999")]
        public void InvalidBudgetsRefuseBeforeTargetLookup(string key, string json)
        {
            var response = Read(new JObject { ["target"] = "__unresolvable_ui03__", [key] = JToken.Parse(json) });
            Assert.IsFalse(response.Value<bool>("success"));
            StringAssert.Contains(key, response.Value<string>("error"));
            StringAssert.DoesNotContain("Could not find", response.Value<string>("error"));
        }

        [Test]
        public void NullDefaultsAndCamelCaseZeroDepthArePreserved()
        {
            root.Add(Element("child"));
            var data = Data(new JObject { ["maxDepth"] = 0, ["max_nodes"] = JValue.CreateNull() });
            Assert.AreEqual(0, data["traversal"].Value<int>("max_depth"));
            Assert.AreEqual(200, data["traversal"].Value<int>("max_nodes"));
            Assert.AreEqual(5000, data["traversal"].Value<int>("max_evaluations"));
            Assert.AreEqual(65536, data["traversal"].Value<int>("max_characters"));
            Assert.AreEqual(1, data["traversal"].Value<int>("returned_nodes"));
        }

        [Test]
        public void DiagnosticsDoNotEchoHugeTargetOrMissingComponentName()
        {
            var response = Read(new JObject { ["target"] = new string('x', 100000) });
            Assert.IsFalse(response.Value<bool>("success"));
            Assert.Less(response.ToString().Length, 2000);
            UnityEngine.Object.DestroyImmediate(document);
            owner.name = new string('g', 100000);
            Assert.Less(Read().ToString().Length, 2000);
        }

        [Test]
        public void MaximumFixtureExportsActualBoundedResponseForTransportVerification()
        {
            var parent = root;
            for (int i = 0; i < 24; i++)
            {
                var child = Element("chain");
                parent.Add(child);
                parent = child;
            }
            for (int i = 0; i < 1100; i++)
            {
                var label = new Label(new string('"', 8191) + "\U0001F680") { name = new string('n', 8192) };
                label.ClearClassList();
                root.Add(label);
            }
            var response = Read(
                new JObject
                {
                    ["max_depth"] = 24,
                    ["max_nodes"] = 1000,
                    ["max_evaluations"] = 20000,
                    ["max_characters"] = 262144,
                }
            );
            Assert.IsTrue(response.Value<bool>("success"), response.ToString());
            Assert.IsNotNull(response["data"]?["traversal"]);
            Assert.LessOrEqual(response["data"]["traversal"].Value<int>("characters"), 262144);
            File.WriteAllText(Path.Combine(Path.GetTempPath(), "ui03-max-response.json"), response.ToString(), new UTF8Encoding(false, true));
            // A second actual response reaches the node cap while remaining below the shared string/work caps.
            root.Clear();
            parent = root;
            for (int i = 0; i < 24; i++)
            {
                var child = Element("chain");
                parent.Add(child);
                parent = child;
            }
            for (int i = 0; i < 1100; i++)
            {
                var child = Element("N");
                for (int c = 0; c < 16; c++)
                    child.AddToClassList("c" + c);
                root.Add(child);
            }
            var nodes = Read(
                new JObject
                {
                    ["max_depth"] = 24,
                    ["max_nodes"] = 1000,
                    ["max_evaluations"] = 20000,
                    ["max_characters"] = 262144,
                }
            );
            Assert.IsTrue(nodes.Value<bool>("success"));
            Assert.AreEqual(1000, nodes["data"]["traversal"].Value<int>("returned_nodes"));
            File.WriteAllText(Path.Combine(Path.GetTempPath(), "ui03-max-nodes-response.json"), nodes.ToString(), new UTF8Encoding(false, true));
        }
    }
}
