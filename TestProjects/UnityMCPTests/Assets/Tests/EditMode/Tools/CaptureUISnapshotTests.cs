using System;
using System.Collections;
using System.Collections.Generic;
using System.Linq;
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

        private static PanelSettings CreateOwnedPanelSettings()
        {
            const string path = "Assets/UI Toolkit/UnityThemes/UnityDefaultRuntimeTheme.tss";
            var theme = AssetDatabase.LoadAssetAtPath<ThemeStyleSheet>(path);
            Assert.IsNotNull(theme, "Exact preseeded owned theme prerequisite: " + path);
            var settings = ScriptableObject.CreateInstance<PanelSettings>();
            settings.themeStyleSheet = theme;
            Assert.AreSame(theme, settings.themeStyleSheet, "Explicit theme must be assigned before UIDocument creates its runtime panel.");
            return settings;
        }

        private sealed class ToolkitPanel : IDisposable
        {
            private readonly PanelSettings settings;
            private readonly SnapshotWindow window;
            internal readonly UIDocument Document;
            internal readonly VisualElement Host;
            internal readonly VisualElement Tree;

            internal ToolkitPanel(GameObject root)
            {
                settings = CreateOwnedPanelSettings();
                Document = root.AddComponent<UIDocument>();
                Document.panelSettings = settings;
                Tree = Document.rootVisualElement;
                Assert.That(Tree, Is.Not.Null);
                Tree.style.width = 320;
                Tree.style.height = 200;
                Tree.Clear();
                Host = new VisualElement { name = "DocumentHost" };
                Host.style.width = 360;
                Host.style.height = 240;
                Host.Add(Tree);
                window = ScriptableObject.CreateInstance<SnapshotWindow>();
                window.position = new Rect(0, 0, 400, 300);
                window.Show();
                window.rootVisualElement.Add(Host);
            }

            public void Dispose()
            {
                window.Close();
                if (window != null)
                    UnityEngine.Object.DestroyImmediate(window);
                UnityEngine.Object.DestroyImmediate(settings);
            }
        }

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

        [TestCase(false)]
        [TestCase(true)]
        public void DisabledNestedCanvasUsesEffectiveRootForGraphicAndContainer(bool rendererless)
        {
            var root = Root();
            var nested = Child("DisabledNestedCanvas", root);
            var nestedCanvas = nested.AddComponent<Canvas>();
            nestedCanvas.enabled = false;
            var child = Child("SnapshotTarget", nested);
            var rect = (RectTransform)child.transform;
            rect.anchoredPosition = new Vector2(17, 23);
            rect.sizeDelta = new Vector2(31, 47);
            var control = rendererless ? Child("NativeGraphicControl", nested) : child;
            var image = Image(control);
            Canvas.ForceUpdateCanvases();
            Assert.That(image.GetType().GetProperty("canvas").GetValue(image), Is.SameAs(root.GetComponent<Canvas>()));
            Assert.That(control.GetComponent<CanvasRenderer>().cull, Is.False);
            var sources = new Dictionary<UnityEngine.Object, string>();
            var dirty = new Dictionary<UnityEngine.Object, bool>();
            foreach (var component in root.GetComponentsInChildren<Component>(true))
            {
                sources[component] = EditorJsonUtility.ToJson(component);
                dirty[component] = EditorUtility.IsDirty(component);
                sources[component.gameObject] = EditorJsonUtility.ToJson(component.gameObject);
                dirty[component.gameObject] = EditorUtility.IsDirty(component.gameObject);
            }
            bool sceneDirty = root.scene.isDirty;
            var previousSelection = Selection.objects;
            try
            {
                Selection.activeGameObject = child;
                var selection = Selection.objects;

                var node = CapturedNode(child);

                Assert.That(node["visible"].Value<bool>(), Is.True, "A disabled nested Canvas falls back to the enabled root Canvas.");
                Assert.That(CapturedNode(nested)["visible"].Value<bool>(), Is.True, "Containers inherit eligibility from the nearest usable Canvas.");
                Assert.That(node["geometry"]["anchored_position"].ToObject<float[]>(), Is.EqualTo(new[] { 17f, 23f }));
                Assert.That(rect.sizeDelta, Is.EqualTo(new Vector2(31, 47)));
                foreach (var source in sources)
                {
                    Assert.That(EditorJsonUtility.ToJson(source.Key), Is.EqualTo(source.Value), source.Key.name);
                    Assert.That(EditorUtility.IsDirty(source.Key), Is.EqualTo(dirty[source.Key]), source.Key.name);
                }
                Assert.That(root.scene.isDirty, Is.EqualTo(sceneDirty));
                Assert.That(Selection.objects, Is.EqualTo(selection));
            }
            finally
            {
                Selection.objects = previousSelection;
            }
        }

        [TestCase(false, false, true, false, false)]
        [TestCase(false, false, true, true, false)]
        [TestCase(false, true, true, false, true)]
        [TestCase(false, true, true, true, true)]
        [TestCase(true, false, false, false, false)]
        [TestCase(true, false, false, true, false)]
        [TestCase(true, true, true, false, true)]
        [TestCase(true, true, true, true, true)]
        public void CanvasVisibilityRequiresUsableCanvasAndActiveHierarchy(
            bool rootEnabled,
            bool nestedEnabled,
            bool targetActive,
            bool rendererless,
            bool expectedVisible
        )
        {
            var root = Root();
            var nested = Child("NestedCanvas", root);
            var nestedCanvas = nested.AddComponent<Canvas>();
            var child = Child("SnapshotTarget", nested);
            var image = rendererless ? null : Image(child);
            root.GetComponent<Canvas>().enabled = rootEnabled;
            nestedCanvas.enabled = nestedEnabled;
            child.SetActive(targetActive);
            Canvas.ForceUpdateCanvases();
            if (image != null && targetActive)
            {
                Assert.That(
                    image.GetType().GetProperty("canvas").GetValue(image),
                    Is.SameAs(nestedEnabled ? nestedCanvas : null),
                    "The Graphic's effective Canvas is the native rendering evidence."
                );
            }

            var node = CapturedNode(child);

            Assert.That(node["active"].Value<bool>(), Is.EqualTo(targetActive));
            Assert.That(node["visible"].Value<bool>(), Is.EqualTo(expectedVisible));
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
            var settings = CreateOwnedPanelSettings();
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

        private static JArray ToolkitNodes(GameObject root)
        {
            var response = Capture(root, framework: "uitoolkit");
            Assert.That(response["success"].Value<bool>(), Is.True, response.ToString());
            return (JArray)response["data"]["nodes"];
        }

        [UnityTest]
        public IEnumerator ToolkitChildExplicitVisibilityOverridesHiddenParentInSnapshot()
        {
            var root = Root();
            using (var panel = new ToolkitPanel(root))
            {
                var parent = new VisualElement { name = "HiddenParent" };
                parent.style.width = 200;
                parent.style.height = 100;
                parent.style.visibility = Visibility.Hidden;
                var child = new Label("Visible override") { name = "VisibilityOverride" };
                child.style.width = 160;
                child.style.height = 40;
                child.style.visibility = Visibility.Visible;
                parent.Add(child);
                panel.Tree.Add(parent);
                for (int i = 0; i < 8; i++)
                    yield return null;
                Assert.That(HasLayout(child), Is.True, "Attached child layout must settle before checking inherited visibility.");
                Assert.That(panel.Document.rootVisualElement, Is.SameAs(panel.Tree));
                Assert.That(parent.resolvedStyle.visibility, Is.EqualTo(Visibility.Hidden));
                Assert.That(
                    child.resolvedStyle.visibility,
                    Is.EqualTo(Visibility.Visible),
                    "Native styles allow an explicit visible child under a hidden parent."
                );
                var baseline = ToolkitNodes(root);
                child.style.visibility = Visibility.Hidden;
                for (int i = 0; i < 8; i++)
                    yield return null;
                Assert.That(child.resolvedStyle.visibility, Is.EqualTo(Visibility.Hidden));
                var boundsBefore = child.worldBound;
                string rootBefore = EditorJsonUtility.ToJson(root.GetComponent<RectTransform>());

                var candidate = ToolkitNodes(root);

                Assert.That(baseline[1]["visible"].Value<bool>(), Is.False);
                Assert.That(baseline[2]["visible"].Value<bool>(), Is.True, "The child must use its own native resolved visibility.");
                Assert.That(candidate[2]["visible"].Value<bool>(), Is.False);
                Assert.That(JToken.DeepEquals(baseline[2]["geometry"], candidate[2]["geometry"]), Is.True);
                Assert.That(JToken.DeepEquals(baseline, candidate), Is.False, "Toggling the explicit child visibility must change comparable evidence.");
                Assert.That(child.worldBound, Is.EqualTo(boundsBefore));
                Assert.That(child.style.visibility.value, Is.EqualTo(Visibility.Hidden));
                Assert.That(parent.style.visibility.value, Is.EqualTo(Visibility.Hidden));
                Assert.That(EditorJsonUtility.ToJson(root.GetComponent<RectTransform>()), Is.EqualTo(rootBefore));
            }
        }

        [UnityTest]
        public IEnumerator ToolkitDisplayNoneAncestorSuppressesExplicitVisibleDescendantInSnapshot()
        {
            var root = Root();
            using (var panel = new ToolkitPanel(root))
            {
                var parent = new VisualElement { name = "DisplayParent" };
                parent.style.width = 200;
                parent.style.height = 100;
                var child = new Label("Display control") { name = "DisplayChild" };
                child.style.width = 160;
                child.style.height = 40;
                child.style.visibility = Visibility.Visible;
                parent.Add(child);
                panel.Tree.Add(parent);
                for (int i = 0; i < 8; i++)
                    yield return null;
                Assert.That(HasLayout(child), Is.True);
                var baseline = ToolkitNodes(root);
                parent.style.display = DisplayStyle.None;
                for (int i = 0; i < 8; i++)
                    yield return null;
                Assert.That(HasLayout(panel.Tree), Is.True, "The captured document retains layout while its child branch is removed from display.");
                Assert.That(parent.resolvedStyle.display, Is.EqualTo(DisplayStyle.None));
                Assert.That(child.resolvedStyle.visibility, Is.EqualTo(Visibility.Visible));

                var candidate = ToolkitNodes(root);

                Assert.That(baseline[2]["visible"].Value<bool>(), Is.True);
                Assert.That(candidate[1]["visible"].Value<bool>(), Is.False);
                Assert.That(
                    candidate[2]["visible"].Value<bool>(),
                    Is.False,
                    "An explicit visibility override cannot restore an ancestor's display:none branch."
                );
                Assert.That(parent.style.display.value, Is.EqualTo(DisplayStyle.None));
                Assert.That(child.style.visibility.value, Is.EqualTo(Visibility.Visible));
            }
        }

        [UnityTest]
        public IEnumerator ToolkitDocumentSnapshotIncludesSurroundingAncestorOpacity()
        {
            var root = Root();
            using (var panel = new ToolkitPanel(root))
            {
                panel.Tree.style.opacity = 0.5f;
                var child = new Label("Opacity control") { name = "OpacityChild" };
                child.style.width = 160;
                child.style.height = 40;
                child.style.opacity = 0.5f;
                panel.Tree.Add(child);
                for (int i = 0; i < 8; i++)
                    yield return null;
                Assert.That(HasLayout(panel.Tree) && HasLayout(child), Is.True);
                Assert.That(panel.Document.rootVisualElement, Is.SameAs(panel.Tree));
                // UIDocument can reattach its root beneath the native panel root during an update.
                var ancestor = panel.Tree.parent;
                Assert.That(ancestor, Is.Not.Null);
                Assert.That(ancestor.panel, Is.SameAs(panel.Tree.panel));
                ancestor.style.opacity = 0.5f;
                for (int i = 0; i < 8; i++)
                    yield return null;
                Assert.That(panel.Tree.parent, Is.SameAs(ancestor), "The real panel ancestor must remain attached before baseline capture.");
                Assert.That(ancestor.resolvedStyle.opacity, Is.EqualTo(0.5f));
                Assert.That(
                    panel.Tree.resolvedStyle.opacity,
                    Is.EqualTo(0.5f),
                    "Opacity remains local in resolved styles and must be composed through ancestors."
                );
                var baseline = ToolkitNodes(root);
                ancestor.style.opacity = 0;
                for (int i = 0; i < 8; i++)
                    yield return null;
                Assert.That(panel.Tree.parent, Is.SameAs(ancestor), "The same panel ancestor must contribute opacity after the toggle.");
                Assert.That(ancestor.resolvedStyle.opacity, Is.EqualTo(0));
                var boundsBefore = child.worldBound;
                string rootBefore = EditorJsonUtility.ToJson(root.GetComponent<RectTransform>());

                var candidate = ToolkitNodes(root);

                Assert.That(baseline[0]["alpha"].Value<float>(), Is.EqualTo(0.25f).Within(0.00001f));
                Assert.That(baseline[1]["alpha"].Value<float>(), Is.EqualTo(0.125f).Within(0.00001f));
                Assert.That(baseline[0]["visible"].Value<bool>() && baseline[1]["visible"].Value<bool>(), Is.True);
                for (int i = 0; i < candidate.Count; i++)
                {
                    Assert.That(candidate[i]["alpha"].Value<float>(), Is.EqualTo(0));
                    Assert.That(candidate[i]["visible"].Value<bool>(), Is.False);
                    Assert.That(JToken.DeepEquals(baseline[i]["geometry"], candidate[i]["geometry"]), Is.True);
                }
                Assert.That(JToken.DeepEquals(baseline, candidate), Is.False);
                Assert.That(child.worldBound, Is.EqualTo(boundsBefore));
                Assert.That(ancestor.style.opacity.value, Is.EqualTo(0));
                Assert.That(panel.Tree.style.opacity.value, Is.EqualTo(0.5f));
                Assert.That(child.style.opacity.value, Is.EqualTo(0.5f));
                Assert.That(EditorJsonUtility.ToJson(root.GetComponent<RectTransform>()), Is.EqualTo(rootBefore));
            }
        }

        [TestCase(false)]
        [TestCase(true)]
        public void ProjectionCompanionDoesNotChangeWorldXYSnapshotContract(bool explicitIdentity)
        {
            var root = Root();
            root.GetComponent<Canvas>().renderMode = RenderMode.WorldSpace;
            root.transform.position = new Vector3(1, 2, 5);
            var child = Child("ProjectionRow", root);
            ((RectTransform)child.transform).sizeDelta = new Vector2(2, 1);
            var cameraObject = new GameObject("SnapshotProjectionCamera", typeof(Camera));
            roots.Add(cameraObject);
            cameraObject.GetComponent<Camera>().enabled = false;
            var request = new JObject { ["framework"] = "ugui", ["target"] = root.GetInstanceIDCompat() };
            if (explicitIdentity)
                request["identity_map"] = new JArray(new JObject { ["element_path"] = "/0", ["id"] = "row-1" });
            var beforeResponse = JObject.FromObject(CaptureUISnapshot.HandleCommand(request));
            Assert.IsTrue(beforeResponse.Value<bool>("success"), beforeResponse.ToString());
            var before = beforeResponse["data"];
            cameraObject.transform.SetPositionAndRotation(new Vector3(3, 1, 0), Quaternion.Euler(0, 25, 0));
            var afterResponse = JObject.FromObject(CaptureUISnapshot.HandleCommand(request));
            Assert.IsTrue(afterResponse.Value<bool>("success"), afterResponse.ToString());
            Assert.IsTrue(JToken.DeepEquals(before, afterResponse["data"]));
            string exportPrefix = System.IO.Path.Combine(System.IO.Path.GetTempPath(), explicitIdentity ? "ui07-snapshot-v3" : "ui07-snapshot-v2");
            System.IO.File.WriteAllText(exportPrefix + "-before.json", before.ToString(Newtonsoft.Json.Formatting.None));
            System.IO.File.WriteAllText(exportPrefix + "-after.json", afterResponse["data"].ToString(Newtonsoft.Json.Formatting.None));
            Assert.AreEqual(explicitIdentity ? 3 : 2, before.Value<int>("schema_version"));
            Assert.AreEqual("world_aabb", before["evidence"].Value<string>("geometry_space"));
            Assert.IsTrue(
                before["limitations"].Any(v => v.Value<string>().Contains("diagnose_projection")),
                "The fixed limitation points callers to explicit camera evidence."
            );
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
