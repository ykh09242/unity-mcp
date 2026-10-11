using System;
using System.Collections;
using System.Collections.Generic;
using MCPForUnity.Editor.Helpers;
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

        private static GameObject Child(string childName, GameObject parent)
        {
            var child = new GameObject(childName, typeof(RectTransform));
            child.transform.SetParent(parent.transform, false);
            return child;
        }

        private static Component Image(GameObject target)
        {
            var type = UnityTypeResolver.ResolveComponent("UnityEngine.UI.Image");
            if (type == null)
                Assert.Ignore("Optional uGUI Image component is not installed.");
            return target.AddComponent(type);
        }

        private static JToken CapturedNode(GameObject target)
        {
            var response = Capture(target);
            Assert.That(response["success"].Value<bool>(), Is.True, response.ToString());
            return response["data"]["nodes"][0];
        }

        [TestCase(0f)]
        [TestCase(0.25f)]
        [TestCase(1f)]
        public void CapturesCurrentCanvasRendererAlphaWithoutChangingGraphicOrGeometry(float rendererAlpha)
        {
            var child = Child("FadedGraphic", Root());
            var image = Image(child);
            var renderer = child.GetComponent<CanvasRenderer>();
            Canvas.ForceUpdateCanvases();
            renderer.SetAlpha(1);
            var baseline = CapturedNode(child);
            renderer.SetAlpha(rendererAlpha);
            Canvas.ForceUpdateCanvases();
            string imageBefore = EditorJsonUtility.ToJson(image);
            string rectBefore = EditorJsonUtility.ToJson(child.GetComponent<RectTransform>());
            string rendererBefore = EditorJsonUtility.ToJson(renderer);

            var candidate = CapturedNode(child);

            Assert.That(((Color)image.GetType().GetProperty("color").GetValue(image)).a, Is.EqualTo(1));
            Assert.That(renderer.GetAlpha(), Is.EqualTo(rendererAlpha).Within(0.00001f));
            Assert.That(candidate["alpha"].Value<float>(), Is.EqualTo(rendererAlpha).Within(0.00001f));
            Assert.That(candidate["visible"].Value<bool>(), Is.EqualTo(rendererAlpha > 0));
            Assert.That(JToken.DeepEquals(baseline["geometry"], candidate["geometry"]), Is.True);
            Assert.That(JToken.DeepEquals(baseline, candidate), Is.EqualTo(rendererAlpha == 1));
            Assert.That(EditorJsonUtility.ToJson(image), Is.EqualTo(imageBefore));
            Assert.That(EditorJsonUtility.ToJson(child.GetComponent<RectTransform>()), Is.EqualTo(rectBefore));
            Assert.That(EditorJsonUtility.ToJson(renderer), Is.EqualTo(rendererBefore));
        }

        [Test]
        public void CapturesGraphicRendererAndInheritedAlphaExactlyOnce()
        {
            var root = Root();
            root.AddComponent<CanvasGroup>().alpha = 0.5f;
            var child = Child("PartiallyTransparentGraphic", root);
            var image = Image(child);
            image.GetType().GetProperty("color").SetValue(image, new Color(1, 1, 1, 0.4f));
            Canvas.ForceUpdateCanvases();
            var renderer = child.GetComponent<CanvasRenderer>();
            renderer.SetAlpha(0.25f);
            Canvas.ForceUpdateCanvases();
            Assert.That(renderer.GetInheritedAlpha(), Is.EqualTo(0.5f).Within(0.00001f), "Inherited alpha excludes the renderer's own color alpha.");
            Assert.That(renderer.GetAlpha(), Is.EqualTo(0.25f).Within(0.00001f));

            var node = CapturedNode(child);

            Assert.That(node["alpha"].Value<float>(), Is.EqualTo(0.4f * renderer.GetAlpha() * renderer.GetInheritedAlpha()).Within(0.00001f));
            Assert.That(node["visible"].Value<bool>(), Is.True);
        }

        [TestCase(false, false, false)]
        [TestCase(false, true, false)]
        [TestCase(true, false, false)]
        [TestCase(true, true, false)]
        [TestCase(false, false, true)]
        [TestCase(false, true, true)]
        [TestCase(true, false, true)]
        [TestCase(true, true, true)]
        public void CanvasGroupAlphaMatchesNativeIgnoreBoundary(bool ignoreParents, bool boundaryEnabled, bool rendererless)
        {
            var root = Root();
            root.AddComponent<CanvasGroup>().alpha = 0;
            var boundary = Child("AlphaBoundary", root);
            var group = boundary.AddComponent<CanvasGroup>();
            group.alpha = 0.5f;
            group.ignoreParentGroups = ignoreParents;
            group.enabled = boundaryEnabled;
            var child = Child("AlphaTarget", boundary);
            var control = rendererless ? Child("NativeAlphaControl", boundary) : child;
            Image(control);
            Canvas.ForceUpdateCanvases();
            var renderer = control.GetComponent<CanvasRenderer>();
            float inheritedAlpha = renderer.GetInheritedAlpha();
            Assert.That(inheritedAlpha, Is.EqualTo(ignoreParents && boundaryEnabled ? 0.5f : 0).Within(0.00001f));
            if (rendererless)
                Assert.That(child.GetComponent<CanvasRenderer>(), Is.Null);
            string groupBefore = EditorJsonUtility.ToJson(group);

            var node = CapturedNode(child);

            Assert.That(node["alpha"].Value<float>(), Is.EqualTo(inheritedAlpha).Within(0.00001f));
            Assert.That(node["visible"].Value<bool>(), Is.EqualTo(inheritedAlpha > 0));
            Assert.That(EditorJsonUtility.ToJson(group), Is.EqualTo(groupBefore));
        }

        [TestCase(false)]
        [TestCase(true)]
        public void DisabledCanvasGroupDoesNotReduceCapturedAlpha(bool rendererless)
        {
            var root = Root();
            root.AddComponent<CanvasGroup>().alpha = 0.6f;
            var child = Child("DisabledAlphaGroup", root);
            var group = child.AddComponent<CanvasGroup>();
            group.alpha = 0;
            group.ignoreParentGroups = true;
            group.enabled = false;
            var control = rendererless ? Child("NativeAlphaControl", child) : child;
            Image(control);
            Canvas.ForceUpdateCanvases();
            float inheritedAlpha = control.GetComponent<CanvasRenderer>().GetInheritedAlpha();
            Assert.That(inheritedAlpha, Is.EqualTo(0.6f).Within(0.00001f));

            var node = CapturedNode(child);

            Assert.That(node["alpha"].Value<float>(), Is.EqualTo(inheritedAlpha).Within(0.00001f));
            Assert.That(node["visible"].Value<bool>(), Is.True);
        }

        [TestCase(false, false)]
        [TestCase(false, true)]
        [TestCase(true, false)]
        [TestCase(true, true)]
        public void HiddenMaskGraphicChangesSnapshotWhilePreservingChildVisibility(bool showGraphic, bool maskEnabled)
        {
            var masked = Child("StencilGraphic", Root());
            var image = Image(masked);
            var maskType = UnityTypeResolver.ResolveComponent("UnityEngine.UI.Mask");
            if (maskType == null)
                Assert.Ignore("Optional uGUI Mask component is not installed.");
            var mask = masked.AddComponent(maskType);
            ((Behaviour)mask).enabled = maskEnabled;
            var child = Child("StencilContent", masked);
            Image(child);
            var showProperty = mask.GetType().GetProperty("showMaskGraphic");
            showProperty.SetValue(mask, true);
            Canvas.ForceUpdateCanvases();
            var baseline = CapturedNode(masked);
            showProperty.SetValue(mask, showGraphic);
            Canvas.ForceUpdateCanvases();
            var baseMaterial = (Material)image.GetType().GetProperty("material").GetValue(image);
            var material = (Material)mask.GetType().GetMethod("GetModifiedMaterial").Invoke(mask, new object[] { baseMaterial });
            bool nativeVisible = material.GetInt("_ColorMask") != 0;
            Assert.That(nativeVisible, Is.EqualTo(showGraphic || !maskEnabled), "Native Mask controls its own color writes.");
            string maskBefore = EditorJsonUtility.ToJson(mask);
            string imageBefore = EditorJsonUtility.ToJson(image);
            string materialBefore = EditorJsonUtility.ToJson(material);

            var candidate = CapturedNode(masked);

            Assert.That(candidate["visible"].Value<bool>(), Is.EqualTo(nativeVisible));
            Assert.That(candidate["alpha"].Value<float>(), Is.EqualTo(1));
            Assert.That(CapturedNode(child)["visible"].Value<bool>(), Is.True);
            Assert.That(JToken.DeepEquals(baseline["geometry"], candidate["geometry"]), Is.True);
            Assert.That(JToken.DeepEquals(baseline, candidate), Is.EqualTo(nativeVisible));
            Assert.That(EditorJsonUtility.ToJson(mask), Is.EqualTo(maskBefore));
            Assert.That(EditorJsonUtility.ToJson(image), Is.EqualTo(imageBefore));
            Assert.That(EditorJsonUtility.ToJson(material), Is.EqualTo(materialBefore));
        }

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
