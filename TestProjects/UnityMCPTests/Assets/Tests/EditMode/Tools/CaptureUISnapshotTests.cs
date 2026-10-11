using System;
using System.Collections;
using System.Collections.Generic;
using MCPForUnity.Editor.Tools;
using MCPForUnity.Runtime.Helpers;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEditor;
using UnityEngine;
using UnityEngine.TestTools;
using UnityEngine.UIElements;

namespace MCPForUnityTests.EditMode.Tools
{
    public class CaptureUISnapshotTests
    {
        private sealed class SnapshotWindow : EditorWindow { }

        private readonly List<GameObject> roots = new List<GameObject>();
        private string name;

        [SetUp]
        public void SetUp() => name = "UISnapshot_" + Guid.NewGuid().ToString("N");

        [TearDown]
        public void TearDown()
        {
            foreach (var root in roots)
                if (root != null)
                    UnityEngine.Object.DestroyImmediate(root);
            roots.Clear();
        }

        private GameObject Root()
        {
            var root = new GameObject(name, typeof(RectTransform), typeof(Canvas));
            roots.Add(root);
            return root;
        }

        private static JObject Capture(GameObject target, int maxNodes = 200, string framework = "ugui") =>
            JObject.FromObject(
                CaptureUISnapshot.HandleCommand(
                    new JObject
                    {
                        ["target"] = target.GetInstanceIDCompat(),
                        ["framework"] = framework,
                        ["max_nodes"] = maxNodes,
                    }
                )
            );

        [Test]
        public void CapturesInactiveNodesAndCurrentRectWithoutMutatingLayout()
        {
            // Given
            var root = Root();
            var child = new GameObject("Disabled", typeof(RectTransform));
            child.transform.SetParent(root.transform, false);
            var rect = (RectTransform)child.transform;
            rect.anchoredPosition = new Vector2(17, 23);
            rect.sizeDelta = new Vector2(31, 47);
            child.SetActive(false);
            // When
            var response = Capture(root);
            // Then
            Assert.That(response["success"].Value<bool>(), Is.True, response.ToString());
            Assert.That(response["data"]["complete"].Value<bool>(), Is.True);
            var node = response["data"]["nodes"][1];
            Assert.That(node["active"].Value<bool>(), Is.False);
            Assert.That(node["visible"].Value<bool>(), Is.False);
            Assert.That(node["geometry"]["anchored_position"].ToObject<float[]>(), Is.EqualTo(new[] { 17f, 23f }));
            Assert.That(rect.anchoredPosition, Is.EqualTo(new Vector2(17, 23)));
            Assert.That(rect.sizeDelta, Is.EqualTo(new Vector2(31, 47)));
            Assert.That(child.activeSelf, Is.False);
        }

        [Test]
        public void SemanticIdentitySurvivesRecreatedObjects()
        {
            // Given
            var first = Root();
            var baseline = Capture(first)["data"];
            UnityEngine.Object.DestroyImmediate(first);
            var second = Root();
            // When
            var candidate = Capture(second)["data"];
            // Then
            Assert.That(JToken.DeepEquals(baseline, candidate), Is.True);
            Assert.That(candidate["nodes"][0]["instance_id"], Is.Null);
        }

        [Test]
        public void OverflowReturnsFailureWithoutAComparablePartialTree()
        {
            // Given
            var root = Root();
            new GameObject("Child", typeof(RectTransform)).transform.SetParent(root.transform, false);
            // When
            var response = Capture(root, 1);
            // Then
            Assert.That(response["success"].Value<bool>(), Is.False);
            Assert.That(response["data"], Is.Null);
        }

        [Test]
        public void DuplicateSiblingNamesRejectSemanticAmbiguity()
        {
            // Given
            var root = Root();
            for (int i = 0; i < 2; i++)
                new GameObject("Duplicate", typeof(RectTransform)).transform.SetParent(root.transform, false);
            // When
            var response = Capture(root);
            // Then
            Assert.That(response["success"].Value<bool>(), Is.False);
            Assert.That(response["data"], Is.Null);
        }

        [Test]
        public void MissingToolkitPanelReportsUnavailableEvidence()
        {
            // Given
            var root = Root();
            root.AddComponent<UIDocument>();
            // When
            var response = Capture(root, framework: "uitoolkit");
            // Then
            Assert.That(response["success"].Value<bool>(), Is.False);
            Assert.That(response["data"], Is.Null);
        }

        [UnityTest]
        public IEnumerator LiveToolkitPanelCapturesNamedTextAndEnabledState()
        {
            // Given: an actual UIDocument root mounted in an Editor panel with computed layout.
            var root = Root();
            var settings = ScriptableObject.CreateInstance<PanelSettings>();
            var document = root.AddComponent<UIDocument>();
            var window = ScriptableObject.CreateInstance<SnapshotWindow>();
            try
            {
                document.panelSettings = settings;
                var tree = document.rootVisualElement;
                Assert.That(tree, Is.Not.Null);
                tree.style.width = 320;
                tree.style.height = 200;
                tree.Clear();
                var label = new Label("Ready") { name = "Status" };
                label.SetEnabled(false);
                tree.Add(label);
                window.position = new Rect(0, 0, 400, 300);
                window.Show();
                window.rootVisualElement.Add(tree);
                for (int i = 0; i < 8 && (!HasLayout(tree) || !HasLayout(label)); i++)
                    yield return null;
                Assert.That(HasLayout(tree), Is.True, "UIDocument root layout did not settle: " + tree.worldBound);
                Assert.That(HasLayout(label), Is.True, "Label layout did not settle: " + label.worldBound);
                Assert.That(document.rootVisualElement, Is.SameAs(tree), "UIDocument replaced the mounted root during layout.");
                // When
                var response = Capture(root, framework: "uitoolkit");
                // Then
                Assert.That(response["success"].Value<bool>(), Is.True, response.ToString());
                var node = response["data"]["nodes"][1];
                Assert.That(node["text"].Value<string>(), Is.EqualTo("Ready"));
                Assert.That(node["interactable"].Value<bool>(), Is.False);
                Assert.That(response["data"]["evidence"]["geometry_space"].Value<string>(), Is.EqualTo("panel_points"));
            }
            finally
            {
                window.Close();
                if (window != null)
                    UnityEngine.Object.DestroyImmediate(window);
                UnityEngine.Object.DestroyImmediate(settings);
            }
        }

        private static bool HasLayout(VisualElement element)
        {
            var bounds = element.worldBound;
            return element.panel != null
                && bounds.width > 0
                && bounds.height > 0
                && Finite(bounds.x)
                && Finite(bounds.y)
                && Finite(bounds.width)
                && Finite(bounds.height);
        }

        private static bool Finite(float value) => !float.IsNaN(value) && !float.IsInfinity(value);
    }
}
