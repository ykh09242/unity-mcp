using System;
using System.Linq;
using System.Reflection;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Editor.Tools;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEditor;
using UnityEngine;

namespace MCPForUnityTests.Editor.Tools
{
    [Parallelizable(ParallelScope.None)]
    public class UguiDiagnosticsTests
    {
        private GameObject root;
        private static readonly MethodInfo DiagnoseMethod = typeof(ManageUI).Assembly
            .GetType("MCPForUnity.Editor.Tools.UguiDiagnostics")
            .GetMethod("Diagnose", BindingFlags.Static | BindingFlags.NonPublic | BindingFlags.Public);

        [SetUp]
        public void SetUp()
        {
            root = new GameObject("UguiDiagnosticsOwned", typeof(RectTransform), typeof(Canvas));
            root.GetComponent<Canvas>().renderMode = RenderMode.ScreenSpaceOverlay;
        }

        [TearDown]
        public void TearDown()
        {
            if (root != null)
                UnityEngine.Object.DestroyImmediate(root);
        }

        private GameObject Child(string name, GameObject parent = null)
        {
            var go = new GameObject(name, typeof(RectTransform));
            go.transform.SetParent((parent ?? root).transform, false);
            go.GetComponent<RectTransform>().sizeDelta = new Vector2(100, 40);
            return go;
        }

        private static Component Add(GameObject go, string name)
        {
            Type type = UnityTypeResolver.ResolveComponent(name);
            if (type == null)
                Assert.Ignore("Optional uGUI component is not installed: " + name);
            return go.AddComponent(type);
        }

        private static void Set(Component component, string property, object value)
        {
            var info = component.GetType().GetProperty(property);
            if (info.PropertyType.IsEnum)
                value = Enum.ToObject(info.PropertyType, value);
            info.SetValue(component, value);
        }

        private JObject Diagnose(GameObject target = null, JArray sizes = null, bool includeInactive = false, int maxNodes = 200)
        {
            return JObject.FromObject(DiagnoseMethod.Invoke(null, new object[]
            {
                target ?? root,
                sizes ?? Sizes(800, 600),
                includeInactive,
                maxNodes
            }));
        }

        private static JArray Sizes(int width, int height) => new JArray(new JObject
        {
            ["width"] = width,
            ["height"] = height
        });
        private static JArray Findings(JObject response)
        {
            Assert.IsTrue(response.Value<bool>("success"), response.ToString());
            return (JArray)response["data"]["findings"];
        }

        private static bool Has(JObject response, string code, GameObject go = null)
        {
            return Findings(response).Any(f => (string)f["code"] == code && (go == null || (int)f["instanceID"] == go.GetInstanceID()));
        }

        [TestCase(63, 600)]
        [TestCase(800, 8193)]
        public void InvalidResolutionRejectsBeforeChangingOriginal(int width, int height)
        {
            var child = Child("Original");
            string before = EditorJsonUtility.ToJson(child.GetComponent<RectTransform>());
            var result = Diagnose(sizes: Sizes(width, height));
            Assert.IsFalse(result.Value<bool>("success"));
            Assert.AreEqual("invalid_resolutions", result.Value<string>("code"));
            Assert.AreEqual(before, EditorJsonUtility.ToJson(child.GetComponent<RectTransform>()));
        }

        [Test]
        public void InvalidBudgetAndNonOverlayRejectExplicitly()
        {
            Assert.AreEqual("invalid_max_nodes", Diagnose(maxNodes: 1001).Value<string>("code"));
            root.GetComponent<Canvas>().renderMode = RenderMode.WorldSpace;
            Assert.AreEqual("unsupported_canvas_mode", Diagnose().Value<string>("code"));
        }

        [Test]
        public void PhysicalScalingRejectsMissingDpi()
        {
            var scaler = Add(root, "UnityEngine.UI.CanvasScaler");
            Set(scaler, "uiScaleMode", 2);
            Assert.AreEqual("unsupported_physical_scaling", Diagnose().Value<string>("code"));
        }

        [Test]
        public void ResolutionPreviewPreservesOriginalTransformsComponentsAndDirtyFlags()
        {
            var child = Child("Original");
            var layout = Add(root, "UnityEngine.UI.HorizontalLayoutGroup");
            var probe = child.AddComponent<UguiDiagnosticsCallbackProbe>();
            var rect = child.GetComponent<RectTransform>();
            rect.sizeDelta = new Vector2(117, 33);
            string before = EditorJsonUtility.ToJson(rect);
            string layoutBefore = EditorJsonUtility.ToJson(layout);
            bool dirty = EditorUtility.IsDirty(rect);
            int callbacks = UguiDiagnosticsCallbackProbe.Callbacks;
            int scenes = UnityEngine.SceneManagement.SceneManager.sceneCount;
            var sizes = Sizes(800, 600);
            sizes.Add(new JObject
            {
                ["width"] = 1920,
                ["height"] = 1080
            });
            Findings(Diagnose(sizes: sizes));
            Assert.AreEqual(before, EditorJsonUtility.ToJson(rect));
            Assert.AreEqual(layoutBefore, EditorJsonUtility.ToJson(layout));
            Assert.AreEqual(dirty, EditorUtility.IsDirty(rect));
            Assert.AreEqual(callbacks, UguiDiagnosticsCallbackProbe.Callbacks, "Original/custom script callbacks must not execute in a preview clone.");
            Assert.AreEqual(scenes, UnityEngine.SceneManagement.SceneManager.sceneCount);
            Assert.IsNotNull(probe);
        }

        [Test]
        public void LayoutGroupAndOwnContentFitterIsValidButChildFitterConflictIsReported()
        {
            var container = Child("Container");
            var group = Add(container, "UnityEngine.UI.VerticalLayoutGroup");
            Set(group, "childControlWidth", true);
            var ownFitter = Add(container, "UnityEngine.UI.ContentSizeFitter");
            Set(ownFitter, "verticalFit", 2);
            var child = Child("Child", container);
            var fitter = Add(child, "UnityEngine.UI.ContentSizeFitter");
            Set(fitter, "horizontalFit", 2);
            var result = Diagnose();
            Assert.IsFalse(Has(result, "layout_driver_conflict", container), "The parent's own ContentSizeFitter drives the parent, while its group drives children.");
            Assert.IsTrue(Has(result, "layout_driver_conflict", child));
            var element = Add(child, "UnityEngine.UI.LayoutElement");
            Set(element, "ignoreLayout", true);
            Assert.IsFalse(Has(Diagnose(), "layout_driver_conflict", child));
            ((Behaviour)element).enabled = false;
            Assert.IsFalse(Has(Diagnose(), "layout_driver_conflict", child), "LayoutGroup still evaluates disabled ILayoutIgnorer components.");
            ((Behaviour)element).enabled = true;
            var otherElement = Add(child, "UnityEngine.UI.LayoutElement");
            Set(otherElement, "ignoreLayout", false);
            Assert.IsTrue(Has(Diagnose(), "layout_driver_conflict", child), "A child participates if any ILayoutIgnorer returns false.");
        }

        [TestCase(2, 0, 1, false)]
        [TestCase(0, 2, 2, false)]
        [TestCase(0, 2, 1, true)]
        [TestCase(2, 0, 2, true)]
        [TestCase(2, 0, 3, true)]
        public void ContentAndAspectFittersOnlyConflictOnSharedDrivenAxes(int horizontal, int vertical, int aspectMode, bool conflict)
        {
            var child = Child("Aspect");
            var content = Add(child, "UnityEngine.UI.ContentSizeFitter");
            Set(content, "horizontalFit", horizontal);
            Set(content, "verticalFit", vertical);
            var aspect = Add(child, "UnityEngine.UI.AspectRatioFitter");
            Set(aspect, "aspectMode", aspectMode);
            Assert.AreEqual(conflict, Has(Diagnose(), "layout_driver_conflict", child));
        }

        [Test]
        public void DifferentAspectRatiosProduceDifferentRectsAndOffCanvasCandidates()
        {
            var child = Child("Responsive");
            child.GetComponent<RectTransform>().anchoredPosition = new Vector2(300, 0);
            var sizes = Sizes(800, 600);
            sizes.Add(new JObject
            {
                ["width"] = 400,
                ["height"] = 800
            });
            var result = Diagnose(sizes: sizes);
            Assert.IsTrue(Has(result, "off_canvas", child));
            var findings = Findings(result).Where(f => (string)f["code"] == "off_canvas" && (int)f["instanceID"] == child.GetInstanceID()).ToArray();
            Assert.AreEqual(1, findings.Length);
            Assert.AreEqual(400, findings[0]["resolution"].Value<int>("width"));
        }

        [Test]
        public void PlainCanvasAndStretchedPanelRebuildDescendantLayoutAtEveryResolution()
        {
            root.GetComponent<Canvas>().scaleFactor = 1;
            var panel = Child("PlainPanel");
            var panelRect = panel.GetComponent<RectTransform>();
            panelRect.anchorMin = Vector2.zero;
            panelRect.anchorMax = Vector2.one;
            panelRect.sizeDelta = Vector2.zero;
            var layoutHost = Child("LayoutHost", panel);
            var hostRect = layoutHost.GetComponent<RectTransform>();
            hostRect.anchorMin = Vector2.zero;
            hostRect.anchorMax = Vector2.one;
            hostRect.sizeDelta = Vector2.zero;
            var group = Add(layoutHost, "UnityEngine.UI.HorizontalLayoutGroup");
            Set(group, "childControlWidth", true);
            Set(group, "childForceExpandWidth", true);
            var first = Child("First", layoutHost);
            Child("Second", layoutHost);
            string before = EditorJsonUtility.ToJson(first.GetComponent<RectTransform>());
            var sizes = Sizes(1920, 1080);
            sizes.Add(new JObject
            {
                ["width"] = 960,
                ["height"] = 1080
            });
            var result = Diagnose(sizes: sizes);
            Findings(result);
            var rects = result["data"]["rects"].Where(r => (int)r["instanceID"] == first.GetInstanceID()).ToArray();
            Assert.AreEqual(2, rects.Length);
            Assert.AreEqual(960, rects[0]["rect"].Value<float>("width"), 0.1f);
            Assert.AreEqual(480, rects[1]["rect"].Value<float>("width"), 0.1f);
            Assert.AreEqual(before, EditorJsonUtility.ToJson(first.GetComponent<RectTransform>()));
        }

        [Test]
        public void LegacyTextOverflowChangesWithAvailableWidth()
        {
            var child = Child("Text");
            var rect = child.GetComponent<RectTransform>();
            rect.anchorMin = new Vector2(0, 0.5f);
            rect.anchorMax = new Vector2(1, 0.5f);
            rect.sizeDelta = new Vector2(-40, 30);
            var text = Add(child, "UnityEngine.UI.Text");
            Set(text, "font", Resources.GetBuiltinResource<Font>("LegacyRuntime.ttf"));
            Set(text, "fontSize", 20);
            Set(text, "text", "Responsive text needs enough horizontal room to wrap properly");
            var sizes = Sizes(1600, 600);
            sizes.Add(new JObject
            {
                ["width"] = 180,
                ["height"] = 600
            });
            var findings = Findings(Diagnose(sizes: sizes)).Where(f => (string)f["code"] == "text_overflow").ToArray();
            Assert.AreEqual(1, findings.Length);
            Assert.AreEqual(180, findings[0]["resolution"].Value<int>("width"));
        }

        private GameObject Button(string name)
        {
            var go = Child(name);
            var graphic = Add(go, "UnityEngine.UI.Image");
            var button = Add(go, "UnityEngine.UI.Button");
            Set(button, "targetGraphic", graphic);
            return go;
        }

        [Test]
        public void InteractionOverlapsAndBlockersAreCandidatesAndIgnoreDisabledTargets()
        {
            var first = Button("First");
            var second = Button("Second");
            var overlay = Child("Overlay");
            var graphic = Add(overlay, "UnityEngine.UI.Image");
            var result = Diagnose();
            Assert.IsTrue(Has(result, "interactive_overlap"));
            Assert.IsTrue(Has(result, "raycast_blocker"));
            Assert.IsTrue(Findings(result).Where(f => (string)f["code"] == "interactive_overlap" || (string)f["code"] == "raycast_blocker").All(f => (string)f["status"] == "candidate"));
            Set(graphic, "raycastTarget", false);
            second.SetActive(false);
            Assert.IsFalse(Has(Diagnose(includeInactive: true), "interactive_overlap"));
            Assert.IsFalse(Has(Diagnose(includeInactive: true), "raycast_blocker"));
            first.AddComponent<CanvasGroup>().blocksRaycasts = false;
            Assert.IsFalse(Has(Diagnose(), "interactive_overlap"));
        }

        [TestCase(false)]
        [TestCase(true)]
        public void TransparentGraphicsAndCanvasGroupsStillProduceRaycastBlockerCandidates(bool groupAlpha)
        {
            Button("VisibleButton");
            var overlay = Child("TransparentOverlay");
            var graphic = Add(overlay, "UnityEngine.UI.Image");
            if (groupAlpha)
                overlay.AddComponent<CanvasGroup>().alpha = 0;
            else
                Set(graphic, "color", new Color(1, 1, 1, 0));
            var result = Diagnose();
            Assert.IsTrue(Has(result, "raycast_blocker"), "Visual alpha does not disable GraphicRaycaster hits.");
            Assert.IsFalse(result["data"]["rects"].Single(r => (int)r["instanceID"] == overlay.GetInstanceID()).Value<bool>("visible"));
            var group = overlay.GetComponent<CanvasGroup>();
            if (!group)
                group = overlay.AddComponent<CanvasGroup>();
            group.blocksRaycasts = false;
            Assert.IsFalse(Has(Diagnose(), "raycast_blocker"));
        }

        [TestCase(false)]
        [TestCase(true)]
        public void TargetGraphicIsOptionalAndChildGraphicsForwardToSelectableAncestor(bool childGraphic)
        {
            var button = Child("ButtonReceiver");
            var selectable = Add(button, "UnityEngine.UI.Button");
            Set(selectable, "targetGraphic", null);
            Set(selectable, "transition", 0);
            if (childGraphic)
                Add(Child("ChildGraphic", button), "UnityEngine.UI.Image");
            else
                Add(button, "UnityEngine.UI.Image");
            Button("OtherButton");
            var result = Diagnose();
            Assert.IsTrue(Has(result, "interactive_overlap"), "Pointer handling does not require Selectable.targetGraphic.");
            Assert.IsFalse(Has(result, "raycast_blocker"), "A Selectable's child Graphic belongs to that receiver.");
        }

        [Test]
        public void ChildGraphicGroupDoesNotDisableAncestorSelectableReceiver()
        {
            var button = Button("ParentButtonReceiver");
            Set(button.GetComponent(UnityTypeResolver.ResolveComponent("UnityEngine.UI.Image")), "raycastTarget", false);
            var child = Child("ChildGraphic", button);
            Add(child, "UnityEngine.UI.Image");
            var childGroup = child.AddComponent<CanvasGroup>();
            childGroup.interactable = false;
            childGroup.blocksRaycasts = true;
            Button("OtherButton");
            Assert.IsTrue(Has(Diagnose(), "interactive_overlap"), "Child CanvasGroup.interactable does not disable the ancestor Button receiving its pointer event.");
            button.AddComponent<CanvasGroup>().interactable = false;
            Assert.IsFalse(Has(Diagnose(), "interactive_overlap"), "The receiver Button's own CanvasGroup.interactable disables that receiver.");
        }

        [Test]
        public void MaskClippingAndZeroSizeIncludeInactiveWithoutFalseWarnings()
        {
            var mask = Child("Mask");
            Add(mask, "UnityEngine.UI.RectMask2D");
            var outside = Child("Outside", mask);
            outside.GetComponent<RectTransform>().anchoredPosition = new Vector2(150, 0);
            var zero = Child("Zero");
            zero.GetComponent<RectTransform>().sizeDelta = Vector2.zero;
            var inactive = Child("IntentionallyHidden");
            inactive.GetComponent<RectTransform>().sizeDelta = Vector2.zero;
            inactive.SetActive(false);
            var result = Diagnose(includeInactive: true);
            Assert.IsTrue(Has(result, "clipped_by_mask", outside));
            Assert.IsTrue(Has(result, "zero_size", zero));
            Assert.IsFalse(Has(result, "zero_size", inactive));
        }

        [TestCase("UnityEngine.UI.RectMask2D")]
        [TestCase("UnityEngine.UI.Mask")]
        public void MaskableFalseGraphicIsNotClippedByAncestorMasks(string maskType)
        {
            var mask = Child("Mask");
            Add(mask, "UnityEngine.UI.Image");
            Add(mask, maskType);
            var outside = Child("UnmaskedGraphic", mask);
            outside.GetComponent<RectTransform>().anchoredPosition = new Vector2(150, 0);
            var graphic = Add(outside, "UnityEngine.UI.Image");
            Set(graphic, "maskable", false);
            var result = Diagnose();
            Assert.IsFalse(Has(result, "clipped_by_mask", outside), "MaskableGraphic.maskable=false opts out of render clipping and mask raycast filters.");
            var rect = result["data"]["rects"].Single(r => (int)r["instanceID"] == outside.GetInstanceID());
            Assert.AreEqual(100, rect["visibleRect"].Value<float>("width"), 0.1f);
        }

        [Test]
        public void RectMaskPaddingShrinksEvaluatedVisibleRectangle()
        {
            var mask = Child("PaddedMask");
            mask.GetComponent<RectTransform>().sizeDelta = new Vector2(100, 100);
            var component = Add(mask, "UnityEngine.UI.RectMask2D");
            Set(component, "padding", new Vector4(10, 20, 30, 40));
            var child = Child("PaddedContent", mask);
            child.GetComponent<RectTransform>().sizeDelta = new Vector2(100, 100);
            Add(child, "UnityEngine.UI.Image");
            var result = Diagnose();
            Assert.IsTrue(Has(result, "clipped_by_mask", child));
            var rect = result["data"]["rects"].Single(r => (int)r["instanceID"] == child.GetInstanceID());
            Assert.AreEqual(60, rect["visibleRect"].Value<float>("width"), 0.1f);
            Assert.AreEqual(40, rect["visibleRect"].Value<float>("height"), 0.1f);
        }

        [TestCase("UnityEngine.UI.RectMask2D")]
        [TestCase("UnityEngine.UI.Mask")]
        public void OverrideSortingCanvasStopsAncestorMaskClipping(string maskType)
        {
            var mask = Child("OuterMask");
            Add(mask, "UnityEngine.UI.Image");
            Add(mask, maskType);
            var isolated = Child("IndependentCanvas", mask);
            var canvas = isolated.AddComponent<Canvas>();
            canvas.overrideSorting = true;
            var outside = Child("IndependentGraphic", isolated);
            outside.GetComponent<RectTransform>().anchoredPosition = new Vector2(150, 0);
            Add(outside, "UnityEngine.UI.Image");
            Assert.IsFalse(Has(Diagnose(), "clipped_by_mask", outside), "overrideSorting creates an independent mask/raycast-filter boundary.");
            canvas.overrideSorting = false;
            Assert.IsTrue(Has(Diagnose(), "clipped_by_mask", outside));
        }

        [Test]
        public void OverrideSortingCanvasStopsAncestorRaycastGroupsButNotReceiverInteractionGroups()
        {
            root.AddComponent<CanvasGroup>().blocksRaycasts = false;
            var isolated = Child("IndependentCanvas");
            var canvas = isolated.AddComponent<Canvas>();
            canvas.overrideSorting = true;
            var first = Button("First");
            first.transform.SetParent(isolated.transform, false);
            var second = Button("Second");
            second.transform.SetParent(isolated.transform, false);
            Assert.IsTrue(Has(Diagnose(), "interactive_overlap"), "Graphic.Raycast stops ancestor filters at overrideSorting Canvas.");
            canvas.overrideSorting = false;
            Assert.IsFalse(Has(Diagnose(), "interactive_overlap"));
            canvas.overrideSorting = true;
            root.GetComponent<CanvasGroup>().interactable = false;
            Assert.IsFalse(Has(Diagnose(), "interactive_overlap"), "Selectable interaction groups still traverse ancestors independently of Graphic.Raycast.");
        }

        [Test]
        public void InvalidRootCanvasAspectFitterDoesNotResizePreviewScreen()
        {
            var aspect = Add(root, "UnityEngine.UI.AspectRatioFitter");
            Set(aspect, "aspectMode", 1);
            Set(aspect, "aspectRatio", 2f);
            var result = Diagnose();
            Findings(result);
            var rect = result["data"]["rects"].Single(r => (int)r["instanceID"] == root.GetInstanceID());
            Assert.AreEqual(800, rect["rect"].Value<float>("width"), 0.1f);
            Assert.AreEqual(600, rect["rect"].Value<float>("height"), 0.1f, "AspectRatioFitter is invalid on a root screen-space Canvas and must remain inert in the preview.");
        }

        [Test]
        public void ImageLayoutPreferredSizeHonorsCanvasReferencePixelsPerUnit()
        {
            root.GetComponent<Canvas>().referencePixelsPerUnit = 200;
            var group = Add(root, "UnityEngine.UI.HorizontalLayoutGroup");
            Set(group, "childControlWidth", true);
            Set(group, "childForceExpandWidth", false);
            var child = Child("SpriteSized");
            var image = Add(child, "UnityEngine.UI.Image");
            var texture = new Texture2D(20, 20);
            var sprite = Sprite.Create(texture, new Rect(0, 0, 20, 20), new Vector2(0.5f, 0.5f), 100);
            try
            {
                Set(image, "sprite", sprite);
                var result = Diagnose();
                Findings(result);
                var rect = result["data"]["rects"].Single(r => (int)r["instanceID"] == child.GetInstanceID());
                Assert.AreEqual(40, rect["rect"].Value<float>("width"), 0.1f, "Image preferred width depends on sprite pixelsPerUnit / Canvas.referencePixelsPerUnit.");
            }
            finally
            {
                Set(image, "sprite", null);
                UnityEngine.Object.DestroyImmediate(sprite);
                UnityEngine.Object.DestroyImmediate(texture);
            }
        }

        [Test]
        public void InactiveTargetBeyondPreviewBudgetReturnsExplicitError()
        {
            for (int i = 0; i < 1000; i++)
                Child("Context" + i);
            var target = Child("InactiveTargetBeyondBudget");
            target.SetActive(false);
            var result = Diagnose(target, includeInactive: true, maxNodes: 1);
            Assert.IsFalse(result.Value<bool>("success"), "An omitted inactive target must not return a successful empty diagnostic result.");
            Assert.AreEqual("preview_limit_exceeded", result.Value<string>("code"));
        }

        [TestCase(true, false)]
        [TestCase(false, false)]
        [TestCase(true, true)]
        public void NestedLayoutRootsAndDisabledParentOrPlainGapsAdaptAcrossResolutions(bool parentEnabled, bool plainGap)
        {
            var host = Child("Host");
            var hostRect = host.GetComponent<RectTransform>();
            hostRect.anchorMin = Vector2.zero;
            hostRect.anchorMax = Vector2.one;
            hostRect.sizeDelta = Vector2.zero;
            var parentGroup = Add(host, "UnityEngine.UI.HorizontalLayoutGroup");
            Set(parentGroup, "childControlWidth", true);
            Set(parentGroup, "childForceExpandWidth", true);
            ((Behaviour)parentGroup).enabled = parentEnabled;
            var parent = host;
            if (plainGap)
            {
                parent = Child("PlainGap", host);
                var rect = parent.GetComponent<RectTransform>();
                rect.anchorMin = Vector2.zero;
                rect.anchorMax = Vector2.one;
                rect.sizeDelta = Vector2.zero;
            }

            var nested = Child("Nested", parent);
            var nestedRect = nested.GetComponent<RectTransform>();
            nestedRect.anchorMin = Vector2.zero;
            nestedRect.anchorMax = Vector2.one;
            nestedRect.sizeDelta = Vector2.zero;
            var nestedGroup = Add(nested, "UnityEngine.UI.VerticalLayoutGroup");
            Set(nestedGroup, "childControlWidth", true);
            Set(nestedGroup, "childForceExpandWidth", true);
            var leaf = Child("Leaf", nested);
            string before = EditorJsonUtility.ToJson(leaf.GetComponent<RectTransform>());
            var sizes = Sizes(1920, 1080);
            sizes.Add(new JObject
            {
                ["width"] = 960,
                ["height"] = 1080
            });
            var result = Diagnose(sizes: sizes);
            Findings(result);
            var rects = result["data"]["rects"].Where(r => (int)r["instanceID"] == leaf.GetInstanceID()).ToArray();
            Assert.AreEqual(1920, rects[0]["rect"].Value<float>("width"), 0.1f);
            Assert.AreEqual(960, rects[1]["rect"].Value<float>("width"), 0.1f);
            Assert.AreEqual(before, EditorJsonUtility.ToJson(leaf.GetComponent<RectTransform>()));
        }

        [TestCase(0, 1.41421356f)]
        [TestCase(1, 1f)]
        [TestCase(2, 2f)]
        public void PreviewCanvasDensityContextPreservesCanvasScalerMath(int matchMode, float expectedScale)
        {
            var scaler = Add(root, "UnityEngine.UI.CanvasScaler");
            Set(scaler, "uiScaleMode", 1);
            Set(scaler, "referenceResolution", new Vector2(800, 600));
            Set(scaler, "screenMatchMode", matchMode);
            Set(scaler, "matchWidthOrHeight", 0.5f);
            var child = Child("FixedSize");
            var result = Diagnose(sizes: Sizes(1600, 600));
            Findings(result);
            Assert.AreEqual(expectedScale, result["data"]["resolutions"][0]["resolution"].Value<float>("scaleFactor"), 0.001f);
            var rect = result["data"]["rects"].Single(r => (int)r["instanceID"] == child.GetInstanceID());
            Assert.AreEqual(100 * expectedScale, rect["rect"].Value<float>("width"), 0.1f);
        }

        [Test]
        public void DisabledNestedCanvasFallsBackToActiveRootForChildGraphics()
        {
            var container = Child("DisabledNestedCanvas");
            container.AddComponent<Canvas>().enabled = false;
            var first = Button("First");
            first.transform.SetParent(container.transform, false);
            var second = Button("Second");
            second.transform.SetParent(container.transform, false);
            var graphic = first.GetComponent(UnityTypeResolver.ResolveComponent("UnityEngine.UI.Image"));
            Assert.AreSame(root.GetComponent<Canvas>(), graphic.GetType().GetProperty("canvas").GetValue(graphic), "Graphic.CacheCanvas selects the nearest enabled Canvas.");
            Assert.IsTrue(Has(Diagnose(), "interactive_overlap"), "Disabling a nested Canvas does not disable child Graphics that fall back to the active root.");
        }

        [Test]
        public void ParentLayoutGroupAndChildAspectFitterConflictOnlyOnSharedSizeAxis()
        {
            var group = Add(root, "UnityEngine.UI.HorizontalLayoutGroup");
            Set(group, "childControlWidth", true);
            Set(group, "childControlHeight", false);
            var child = Child("AspectSizedChild");
            var aspect = Add(child, "UnityEngine.UI.AspectRatioFitter");
            Set(aspect, "aspectMode", 1);
            Set(aspect, "aspectRatio", 2f);
            Assert.IsFalse(Has(Diagnose(), "layout_driver_conflict", child), "Parent width control and child WidthControlsHeight are orthogonal.");
            Set(group, "childControlHeight", true);
            Assert.IsTrue(Has(Diagnose(), "layout_driver_conflict", child), "Both the group and WidthControlsHeight now drive the child's height.");
        }

        [Test]
        public void DisabledIgnoreParentGroupStillStopsSelectableGroupsButNotGraphicRaycastGroups()
        {
            root.AddComponent<CanvasGroup>().interactable = false;
            var container = Child("DisabledBoundaryGroup");
            var group = container.AddComponent<CanvasGroup>();
            group.ignoreParentGroups = true;
            group.enabled = false;
            var first = Button("First");
            first.transform.SetParent(container.transform, false);
            var second = Button("Second");
            second.transform.SetParent(container.transform, false);
            Assert.IsTrue(Has(Diagnose(), "interactive_overlap"), "Selectable.ParentGroupAllowsInteraction honors ignoreParentGroups even when that group is disabled.");
            root.GetComponent<CanvasGroup>().blocksRaycasts = false;
            Assert.IsFalse(Has(Diagnose(), "interactive_overlap"), "Graphic.Raycast skips disabled groups and still evaluates the ancestor's blocksRaycasts.");
        }

        private static Vector2 ScreenPoint(RectTransform rect, Vector3 localPoint)
        {
            return RectTransformUtility.WorldToScreenPoint(null, rect.TransformPoint(localPoint));
        }

        private static bool NativeGraphicRaycast(Component graphic, Vector2 point)
        {
            var method = graphic.GetType().GetMethod("Raycast", new[] { typeof(Vector2), typeof(Camera) });
            return (bool)method.Invoke(graphic, new object[] { point, null });
        }

        [TestCase(1f)]
        [TestCase(2f)]
        public void GraphicRaycastPaddingSeparatesOverlappingVisualsAtNativeTransformScale(float horizontalScale)
        {
            var first = Button("LeftPaddedButton");
            var second = Button("RightPaddedButton");
            var firstRect = first.GetComponent<RectTransform>();
            var secondRect = second.GetComponent<RectTransform>();
            firstRect.anchoredPosition = new Vector2(-25, 0);
            secondRect.anchoredPosition = new Vector2(25, 0);
            firstRect.localScale = new Vector3(horizontalScale, 1, 1);
            secondRect.localScale = new Vector3(horizontalScale, 1, 1);
            var imageType = UnityTypeResolver.ResolveComponent("UnityEngine.UI.Image");
            var firstPadding = new Vector4(0, 0, 60, 0);
            var secondPadding = new Vector4(60, 0, 0, 0);
            Set(first.GetComponent(imageType), "raycastPadding", firstPadding);
            Set(second.GetComponent(imageType), "raycastPadding", secondPadding);
            Vector2 point = (ScreenPoint(firstRect, Vector3.zero) + ScreenPoint(secondRect, Vector3.zero)) * 0.5f;
            Assert.IsTrue(RectTransformUtility.RectangleContainsScreenPoint(firstRect, point, null));
            Assert.IsTrue(RectTransformUtility.RectangleContainsScreenPoint(secondRect, point, null));
            Assert.IsFalse(RectTransformUtility.RectangleContainsScreenPoint(firstRect, point, null, firstPadding));
            Assert.IsFalse(RectTransformUtility.RectangleContainsScreenPoint(secondRect, point, null, secondPadding));
            Assert.IsFalse(Has(Diagnose(), "interactive_overlap"), "Raycast padding shrinks pointer regions without shrinking the overlapping rendered rectangles.");
        }

        [Test]
        public void GraphicRaycastPaddingPreventsFalseBlockerCandidate()
        {
            var button = Button("Button");
            var overlay = Child("Overlay");
            overlay.GetComponent<RectTransform>().anchoredPosition = new Vector2(40, 0);
            var image = Add(overlay, "UnityEngine.UI.Image");
            var padding = new Vector4(70, 0, 0, 0);
            Set(image, "raycastPadding", padding);
            Vector2 point = ScreenPoint(button.GetComponent<RectTransform>(), new Vector3(45, 0, 0));
            Assert.IsTrue(RectTransformUtility.RectangleContainsScreenPoint(overlay.GetComponent<RectTransform>(), point, null));
            Assert.IsFalse(RectTransformUtility.RectangleContainsScreenPoint(overlay.GetComponent<RectTransform>(), point, null, padding));
            Assert.IsFalse(Has(Diagnose(), "raycast_blocker"));
        }

        [Test]
        public void SameObjectRectMaskFiltersPointerBoundsWithoutClippingOwnVisual()
        {
            var first = Button("LeftSelfMaskedButton");
            var second = Button("RightSelfMaskedButton");
            first.GetComponent<RectTransform>().anchoredPosition = new Vector2(-25, 0);
            second.GetComponent<RectTransform>().anchoredPosition = new Vector2(25, 0);
            Set(Add(first, "UnityEngine.UI.RectMask2D"), "padding", new Vector4(0, 0, 80, 0));
            Set(Add(second, "UnityEngine.UI.RectMask2D"), "padding", new Vector4(80, 0, 0, 0));
            var imageType = UnityTypeResolver.ResolveComponent("UnityEngine.UI.Image");
            Vector2 point = (ScreenPoint(first.GetComponent<RectTransform>(), Vector3.zero)
                + ScreenPoint(second.GetComponent<RectTransform>(), Vector3.zero)) * 0.5f;
            Assert.IsFalse(NativeGraphicRaycast(first.GetComponent(imageType), point));
            Assert.IsFalse(NativeGraphicRaycast(second.GetComponent(imageType), point));
            var result = Diagnose();
            Assert.IsFalse(Has(result, "interactive_overlap"));
            var firstOutput = result["data"]["rects"].Single(r => (int)r["instanceID"] == first.GetInstanceID());
            Assert.AreEqual(100, firstOutput["visibleRect"].Value<float>("width"), 0.1f, "A RectMask2D does not clip its own Graphic's rendered rectangle.");
            Set(first.GetComponent(imageType), "maskable", false);
            Set(second.GetComponent(imageType), "maskable", false);
            Assert.IsTrue(NativeGraphicRaycast(first.GetComponent(imageType), point));
            Assert.IsTrue(Has(Diagnose(), "interactive_overlap"), "Maskable=false opts out of same-object mask raycast filters too.");
        }

        [Test]
        public void ScaledRectMaskUsesLocalPaddingForPointersAndCanvasPaddingForVisuals()
        {
            var mask = Child("ScaledMask");
            mask.GetComponent<RectTransform>().localScale = new Vector3(2, 1, 1);
            Set(Add(mask, "UnityEngine.UI.RectMask2D"), "padding", new Vector4(80, 0, 0, 0));
            var first = Button("MaskedButton");
            first.transform.SetParent(mask.transform, false);
            var second = Button("OutsideButton");
            second.GetComponent<RectTransform>().anchoredPosition = new Vector2(-25, 0);
            var imageType = UnityTypeResolver.ResolveComponent("UnityEngine.UI.Image");
            Vector2 point = ScreenPoint(first.GetComponent<RectTransform>(), Vector3.zero);
            Assert.IsFalse(NativeGraphicRaycast(first.GetComponent(imageType), point));
            Assert.IsTrue(NativeGraphicRaycast(second.GetComponent(imageType), point));
            var result = Diagnose();
            Assert.IsFalse(Has(result, "interactive_overlap"), "RectMask2D pointer padding is evaluated in its local transformed rectangle, unlike canvas-space render clipping padding.");
            var firstOutput = result["data"]["rects"].Single(r => (int)r["instanceID"] == first.GetInstanceID());
            Assert.AreEqual(120, firstOutput["visibleRect"].Value<float>("width"), 0.1f);
        }

        [Test]
        public void DisabledChildSelectableDoesNotHideActiveAncestorPointerReceiver()
        {
            var parent = Button("ParentReceiver");
            var imageType = UnityTypeResolver.ResolveComponent("UnityEngine.UI.Image");
            Set(parent.GetComponent(imageType), "raycastTarget", false);
            var child = Button("DisabledChildButton");
            child.transform.SetParent(parent.transform, false);
            var buttonType = UnityTypeResolver.ResolveComponent("UnityEngine.UI.Button");
            var childButton = child.GetComponent(buttonType);
            ((Behaviour)childButton).enabled = false;
            Button("OtherButton");
            var executeEvents = UnityTypeResolver.ResolveAny("UnityEngine.EventSystems.ExecuteEvents");
            var clickHandler = UnityTypeResolver.ResolveAny("UnityEngine.EventSystems.IPointerClickHandler");
            var method = executeEvents.GetMethods().Single(m => m.Name == "GetEventHandler" && m.IsGenericMethodDefinition);
            Assert.AreSame(parent, method.MakeGenericMethod(clickHandler).Invoke(null, new object[] { child }));
            Assert.IsTrue(Has(Diagnose(), "interactive_overlap"), "ExecuteEvents skips disabled child handlers and continues to the active ancestor Button.");
            ((Behaviour)childButton).enabled = true;
            Set(childButton, "interactable", false);
            Assert.AreSame(child, method.MakeGenericMethod(clickHandler).Invoke(null, new object[] { child }));
            Assert.IsFalse(Has(Diagnose(), "interactive_overlap"), "An enabled non-interactable child receives and consumes its own pointer event instead of forwarding to the parent.");
        }

        [Test]
        public void MaskGeometryRefreshesAcrossResolutionsThroughPlainTransformGap()
        {
            var mask = Child("ResponsiveMask");
            var maskRect = mask.GetComponent<RectTransform>();
            maskRect.anchorMin = Vector2.zero;
            maskRect.anchorMax = Vector2.one;
            maskRect.sizeDelta = new Vector2(-200, -200);
            Set(Add(mask, "UnityEngine.UI.RectMask2D"), "padding", new Vector4(100, 0, 100, 0));
            var gap = new GameObject("PlainTransformGap");
            gap.transform.SetParent(mask.transform, false);
            var button = Button("ClippedButton");
            button.transform.SetParent(gap.transform, false);
            button.GetComponent<RectTransform>().sizeDelta = new Vector2(600, 100);
            string before = EditorJsonUtility.ToJson(maskRect);
            var sizes = Sizes(800, 600);
            sizes.Add(Sizes(1200, 600)[0]);
            sizes.Add(Sizes(800, 600)[0]);
            var result = Diagnose(sizes: sizes);
            Findings(result);
            var outputs = result["data"]["rects"].Where(r => (int)r["instanceID"] == button.GetInstanceID()).ToArray();
            CollectionAssert.AreEqual(new[] { 400f, 600f, 400f }, outputs.Select(r => r["visibleRect"].Value<float>("width")).ToArray());
            CollectionAssert.AreEqual(new[] { 400f, 600f, 400f }, outputs.Select(r => r["raycastRect"].Value<float>("width")).ToArray());
            CollectionAssert.AreEqual(new[] { 200f, 300f, 200f }, outputs.Select(r => r["raycastRect"].Value<float>("x")).ToArray());
            Assert.AreEqual(before, EditorJsonUtility.ToJson(maskRect));
        }

        [Test]
        public void BudgetReturnsTruncationAndNullResolutionExplainsCurrentMode()
        {
            Child("One");
            Child("Two");
            var result = Diagnose(maxNodes: 1);
            Findings(result);
            Assert.IsTrue(result["data"].Value<bool>("truncated"));
            var current = JObject.FromObject(DiagnoseMethod.Invoke(null, new object[]
            {
                root,
                null,
                false,
                200
            }));
            Findings(current);
            Assert.AreEqual("current", (string)current["data"]["resolutions"][0]["resolution"]["mode"]);
            Assert.IsTrue(current["data"]["limitations"].Values<string>().Any(x => x.Contains("No explicit resolutions")));
        }
    }

    [ExecuteAlways]
    public class UguiDiagnosticsCallbackProbe : MonoBehaviour
    {
        public static int Callbacks;
        private void OnEnable()
        {
            Callbacks++;
        }

        private void OnDisable()
        {
            Callbacks++;
        }

        private void OnValidate()
        {
            Callbacks++;
        }
    }
}
