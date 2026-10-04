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
            .GetType("MCPForUnity.Editor.Tools.UguiDiagnostics").GetMethod("Diagnose", BindingFlags.Static | BindingFlags.NonPublic | BindingFlags.Public);

        [SetUp]
        public void SetUp()
        {
            root = new GameObject("UguiDiagnosticsOwned", typeof(RectTransform), typeof(Canvas));
            root.GetComponent<Canvas>().renderMode = RenderMode.ScreenSpaceOverlay;
        }

        [TearDown]
        public void TearDown()
        {
            if (root != null) UnityEngine.Object.DestroyImmediate(root);
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
            if (type == null) Assert.Ignore("Optional uGUI component is not installed: " + name);
            return go.AddComponent(type);
        }

        private static void Set(Component component, string property, object value)
        {
            var info = component.GetType().GetProperty(property);
            if (info.PropertyType.IsEnum) value = Enum.ToObject(info.PropertyType, value);
            info.SetValue(component, value);
        }

        private JObject Diagnose(GameObject target = null, JArray sizes = null, bool includeInactive = false, int maxNodes = 200)
        {
            return JObject.FromObject(DiagnoseMethod.Invoke(null, new object[] { target ?? root, sizes ?? Sizes(800, 600), includeInactive, maxNodes }));
        }

        private static JArray Sizes(int width, int height) => new JArray(new JObject { ["width"] = width, ["height"] = height });
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
            var sizes = Sizes(800, 600); sizes.Add(new JObject { ["width"] = 1920, ["height"] = 1080 });
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
            var element = Add(child, "UnityEngine.UI.LayoutElement"); Set(element, "ignoreLayout", true);
            Assert.IsFalse(Has(Diagnose(), "layout_driver_conflict", child));
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
            Set(content, "horizontalFit", horizontal); Set(content, "verticalFit", vertical);
            var aspect = Add(child, "UnityEngine.UI.AspectRatioFitter");
            Set(aspect, "aspectMode", aspectMode);
            Assert.AreEqual(conflict, Has(Diagnose(), "layout_driver_conflict", child));
        }

        [Test]
        public void DifferentAspectRatiosProduceDifferentRectsAndOffCanvasCandidates()
        {
            var child = Child("Responsive");
            child.GetComponent<RectTransform>().anchoredPosition = new Vector2(300, 0);
            var sizes = Sizes(800, 600); sizes.Add(new JObject { ["width"] = 400, ["height"] = 800 });
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
            panelRect.anchorMin = Vector2.zero; panelRect.anchorMax = Vector2.one;
            panelRect.sizeDelta = Vector2.zero;
            var layoutHost = Child("LayoutHost", panel);
            var hostRect = layoutHost.GetComponent<RectTransform>();
            hostRect.anchorMin = Vector2.zero; hostRect.anchorMax = Vector2.one;
            hostRect.sizeDelta = Vector2.zero;
            var group = Add(layoutHost, "UnityEngine.UI.HorizontalLayoutGroup");
            Set(group, "childControlWidth", true); Set(group, "childForceExpandWidth", true);
            var first = Child("First", layoutHost); Child("Second", layoutHost);
            string before = EditorJsonUtility.ToJson(first.GetComponent<RectTransform>());
            var sizes = Sizes(1920, 1080); sizes.Add(new JObject { ["width"] = 960, ["height"] = 1080 });
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
            rect.anchorMin = new Vector2(0, 0.5f); rect.anchorMax = new Vector2(1, 0.5f);
            rect.sizeDelta = new Vector2(-40, 30);
            var text = Add(child, "UnityEngine.UI.Text");
            Set(text, "font", Resources.GetBuiltinResource<Font>("LegacyRuntime.ttf"));
            Set(text, "fontSize", 20); Set(text, "text", "Responsive text needs enough horizontal room to wrap properly");
            var sizes = Sizes(1600, 600); sizes.Add(new JObject { ["width"] = 180, ["height"] = 600 });
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
            var first = Button("First"); var second = Button("Second");
            var overlay = Child("Overlay"); var graphic = Add(overlay, "UnityEngine.UI.Image");
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
            if (groupAlpha) overlay.AddComponent<CanvasGroup>().alpha = 0;
            else Set(graphic, "color", new Color(1, 1, 1, 0));
            var result = Diagnose();
            Assert.IsTrue(Has(result, "raycast_blocker"), "Visual alpha does not disable GraphicRaycaster hits.");
            Assert.IsFalse(result["data"]["rects"].Single(r => (int)r["instanceID"] == overlay.GetInstanceID()).Value<bool>("visible"));
            var group = overlay.GetComponent<CanvasGroup>();
            if (!group) group = overlay.AddComponent<CanvasGroup>();
            group.blocksRaycasts = false;
            Assert.IsFalse(Has(Diagnose(), "raycast_blocker"));
        }

        [TestCase(false)]
        [TestCase(true)]
        public void TargetGraphicIsOptionalAndChildGraphicsForwardToSelectableAncestor(bool childGraphic)
        {
            var button = Child("ButtonReceiver");
            var selectable = Add(button, "UnityEngine.UI.Button");
            Set(selectable, "targetGraphic", null); Set(selectable, "transition", 0);
            if (childGraphic) Add(Child("ChildGraphic", button), "UnityEngine.UI.Image");
            else Add(button, "UnityEngine.UI.Image");
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
            var mask = Child("Mask"); Add(mask, "UnityEngine.UI.RectMask2D");
            var outside = Child("Outside", mask);
            outside.GetComponent<RectTransform>().anchoredPosition = new Vector2(150, 0);
            var zero = Child("Zero"); zero.GetComponent<RectTransform>().sizeDelta = Vector2.zero;
            var inactive = Child("IntentionallyHidden"); inactive.GetComponent<RectTransform>().sizeDelta = Vector2.zero; inactive.SetActive(false);
            var result = Diagnose(includeInactive: true);
            Assert.IsTrue(Has(result, "clipped_by_mask", outside));
            Assert.IsTrue(Has(result, "zero_size", zero));
            Assert.IsFalse(Has(result, "zero_size", inactive));
        }

        [Test]
        public void BudgetReturnsTruncationAndNullResolutionExplainsCurrentMode()
        {
            Child("One"); Child("Two");
            var result = Diagnose(maxNodes: 1);
            Findings(result);
            Assert.IsTrue(result["data"].Value<bool>("truncated"));
            var current = JObject.FromObject(DiagnoseMethod.Invoke(null, new object[] { root, null, false, 200 }));
            Findings(current);
            Assert.AreEqual("current", (string)current["data"]["resolutions"][0]["resolution"]["mode"]);
            Assert.IsTrue(current["data"]["limitations"].Values<string>().Any(x => x.Contains("No explicit resolutions")));
        }
    }

    [ExecuteAlways]
    public class UguiDiagnosticsCallbackProbe : MonoBehaviour
    {
        public static int Callbacks;
        private void OnEnable() { Callbacks++; }
        private void OnDisable() { Callbacks++; }
        private void OnValidate() { Callbacks++; }
    }
}
