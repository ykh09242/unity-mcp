using System;
using System.Collections.Generic;
using System.Globalization;
using System.Linq;
using System.Reflection;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Editor.Tools;
using MCPForUnity.Runtime.Helpers;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEditor;
using UnityEngine;

namespace MCPForUnityTests.EditMode.Tools
{
    public class ManageUGUITests
    {
        private readonly List<GameObject> roots = new List<GameObject>();
        private string prefix;

        [SetUp]
        public void SetUp()
        {
            prefix = "UGUI_" + Guid.NewGuid().ToString("N");
        }

        [TearDown]
        public void TearDown()
        {
            foreach (var root in roots)
                if (root != null)
                    UnityEngine.Object.DestroyImmediate(root);
            roots.Clear();
            Undo.ClearAll();
        }

        private GameObject Root(bool canvas = false)
        {
            var go = new GameObject(prefix, typeof(RectTransform));
            if (canvas)
                go.AddComponent<Canvas>();
            roots.Add(go);
            return go;
        }

        private static JObject Call(string action, GameObject go = null, JObject properties = null)
        {
            var p = new JObject
            {
                ["action"] = action
            };
            if (go != null)
                p["target"] = go.GetInstanceIDCompat();
            if (properties != null)
                p["properties"] = properties;
            return JObject.FromObject(ManageUGUI.HandleCommand(p));
        }

        private static void Success(JObject response) => Assert.That(response["success"]?.Value<bool>(), Is.True, response.ToString());

        private static void Failure(JObject response) => Assert.That(response["success"]?.Value<bool>(), Is.False, response.ToString());

        [Test]
        public void PingWorksWithOrWithoutOptionalPackages()
        {
            var r = Call("ping");
            Success(r);
            Assert.That(r["data"]["tool"].ToString(), Is.EqualTo("manage_ugui"));
            Assert.That(r["data"]["ugui"].Value<bool>(), Is.EqualTo(UnityTypeResolver.ResolveComponent("UnityEngine.UI.Image") != null));
            Assert.That(r["data"]["tmp"].Value<bool>(), Is.EqualTo(UnityTypeResolver.ResolveComponent("TMPro.TextMeshProUGUI") != null));
        }

        [Test]
        public void MissingUguiPackageRejectsCreateAndLayoutWithoutPartialChanges()
        {
            if (UnityTypeResolver.ResolveComponent("UnityEngine.UI.Image") != null)
                Assert.Ignore("This case verifies absent uGUI.");
            var root = Root(true);
            var r = JObject.FromObject(ManageUGUI.HandleCommand(new JObject
            {
                ["action"] = "create",
                ["element_type"] = "image",
                ["parent"] = root.GetInstanceIDCompat()
            }));
            Failure(r);
            StringAssert.Contains("optional component", r["error"].ToString());
            Assert.That(root.transform.childCount, Is.EqualTo(0));
            Failure(Call("set_layout", root, new JObject { ["type"] = "vertical", ["spacing"] = 10 }));
            Assert.That(root.GetComponents<Component>().Length, Is.EqualTo(2));
        }

        [Test]
        public void ExistingLegacyTextCanBeEditedAndInvalidPayloadIsAtomic()
        {
            var type = UnityTypeResolver.ResolveComponent("UnityEngine.UI.Text");
            if (type == null)
                Assert.Ignore("uGUI is not installed.");
            var root = Root(true);
            var text = root.AddComponent(type);
            Success(Call("set_text", root, new JObject { ["text"] = "Updated label", ["fontSize"] = 30 }));
            Assert.That(type.GetProperty("text").GetValue(text), Is.EqualTo("Updated label"));
            Failure(Call("set_text", root, new JObject { ["text"] = "Should not apply", ["fontSize"] = -2 }));
            Assert.That(type.GetProperty("text").GetValue(text), Is.EqualTo("Updated label"));
        }

        [Test]
        public void MissingTargetAndAmbiguousNameAreRejected()
        {
            Failure(Call("set_rect", properties: new JObject { ["sizeDelta"] = new JArray(100, 100) }));
            Root();
            Root();
            var r = JObject.FromObject(ManageUGUI.HandleCommand(new JObject { ["action"] = "get_hierarchy", ["target"] = prefix }));
            Failure(r);
            StringAssert.Contains("ambiguous", r["error"].ToString());
        }

        [TestCase(2147483648L)]
        [TestCase(-2147483649L)]
        public void OutOfRangeIntegerTargetCannotFallBackToAnObjectName(long invalidId)
        {
            var go = Root();
            go.name = invalidId.ToString(System.Globalization.CultureInfo.InvariantCulture);
            var rect = (RectTransform)go.transform;
            Vector2 before = rect.sizeDelta;
            var r = JObject.FromObject(ManageUGUI.HandleCommand(new JObject
            {
                ["action"] = "set_rect",
                ["target"] = invalidId,
                ["properties"] = new JObject { ["sizeDelta"] = new JArray(240, 90) }
            }));
            Failure(r);
            Assert.That(rect.sizeDelta, Is.EqualTo(before), "An invalid numeric identity must not edit an object sharing its textual representation.");
        }

        [Test]
        public void InRangeMissingIntegerTargetCannotFallBackToAnObjectName()
        {
            // 0 is Unity's null instance ID and cannot identify a loaded object.
            var go = Root();
            go.name = "0";
            Vector2 before = ((RectTransform)go.transform).sizeDelta;
            var r = JObject.FromObject(ManageUGUI.HandleCommand(new JObject
            {
                ["action"] = "set_rect",
                ["target"] = 0,
                ["properties"] = new JObject { ["sizeDelta"] = new JArray(240, 90) }
            }));
            Failure(r);
            Assert.That(((RectTransform)go.transform).sizeDelta, Is.EqualTo(before));
        }

        [Test]
        public void IntegerPropertyParsingUsesProtocolCulture()
        {
            var go = Root(true);
            CultureInfo previous = CultureInfo.CurrentCulture;
            var custom = (CultureInfo)CultureInfo.InvariantCulture.Clone();
            custom.NumberFormat.NegativeSign = "~";
            try
            {
                CultureInfo.CurrentCulture = custom;
                var r = JObject.FromObject(ManageUGUI.HandleCommand(new JObject
                {
                    ["action"] = "set_canvas",
                    ["target"] = go.name,
                    ["properties"] = new JObject { ["sortingOrder"] = -15 }
                }));
                Success(r);
                Assert.That(go.GetComponent<Canvas>().sortingOrder, Is.EqualTo(-15));
            }
            finally
            {
                CultureInfo.CurrentCulture = previous;
            }
        }

        [Test]
        public void IntegerTargetParsingUsesProtocolCulture()
        {
            var go = Root();
            int id = go.GetInstanceIDCompat();
            if (id >= 0)
                Assert.Ignore("This case needs a negative runtime instance ID to distinguish editor/protocol notation.");
            CultureInfo previous = CultureInfo.CurrentCulture;
            var custom = (CultureInfo)CultureInfo.InvariantCulture.Clone();
            custom.NumberFormat.NegativeSign = "~";
            try
            {
                CultureInfo.CurrentCulture = custom;
                Success(Call("set_rect", go, new JObject { ["sizeDelta"] = new JArray(240, 90) }));
                Assert.That(((RectTransform)go.transform).sizeDelta, Is.EqualTo(new Vector2(240, 90)));
            }
            finally
            {
                CultureInfo.CurrentCulture = previous;
            }
        }

        [Test]
        public void MixedLayoutIgnorersStillRejectDrivenRectEdits()
        {
            var groupType = UnityTypeResolver.ResolveComponent("UnityEngine.UI.VerticalLayoutGroup");
            var elementType = UnityTypeResolver.ResolveComponent("UnityEngine.UI.LayoutElement");
            if (groupType == null || elementType == null)
                Assert.Ignore("uGUI is not installed.");
            var root = Root();
            var group = root.AddComponent(groupType);
            var child = new GameObject("Child", typeof(RectTransform));
            child.transform.SetParent(root.transform, false);
            elementType.GetProperty("ignoreLayout").SetValue(child.AddComponent(elementType), true);
            elementType.GetProperty("ignoreLayout").SetValue(child.AddComponent(elementType), false);
            groupType.GetMethod("CalculateLayoutInputHorizontal").Invoke(group, null);
            var children = (System.Collections.IList)groupType.GetProperty("rectChildren", BindingFlags.Instance | BindingFlags.NonPublic)
                .GetValue(group);
            Assert.That(children.Count, Is.EqualTo(1), "Installed uGUI includes a child if any ILayoutIgnorer returns false.");
            var r = Call("set_rect", child, new JObject { ["sizeDelta"] = new JArray(240, 90) });
            Failure(r);
            StringAssert.Contains("layout-driven", r["error"].ToString());
        }

        [Test]
        public void DisabledLayoutIgnorerStillExcludesChildFromParentLayout()
        {
            var groupType = UnityTypeResolver.ResolveComponent("UnityEngine.UI.VerticalLayoutGroup");
            var elementType = UnityTypeResolver.ResolveComponent("UnityEngine.UI.LayoutElement");
            if (groupType == null || elementType == null)
                Assert.Ignore("uGUI is not installed.");
            var root = Root();
            var group = root.AddComponent(groupType);
            var child = new GameObject("Child", typeof(RectTransform));
            child.transform.SetParent(root.transform, false);
            var element = child.AddComponent(elementType);
            elementType.GetProperty("ignoreLayout").SetValue(element, true);
            ((Behaviour)element).enabled = false;
            groupType.GetMethod("CalculateLayoutInputHorizontal").Invoke(group, null);
            var children = (System.Collections.IList)groupType.GetProperty("rectChildren", BindingFlags.Instance | BindingFlags.NonPublic)
                .GetValue(group);
            Assert.That(children.Count, Is.EqualTo(0), "Installed uGUI evaluates ignoreLayout even on disabled components.");
            Success(Call("set_rect", child, new JObject { ["sizeDelta"] = new JArray(240, 90) }));
            Assert.That(((RectTransform)child.transform).sizeDelta, Is.EqualTo(new Vector2(240, 90)));
        }

        [Test]
        public void RectEditIsUndoable()
        {
            var go = Root();
            var rect = (RectTransform)go.transform;
            Vector2 before = rect.sizeDelta;
            Success(Call("set_rect", go, new JObject { ["sizeDelta"] = new JArray(240, 90), ["pivot"] = new JArray(1, 1) }));
            Assert.That(rect.sizeDelta, Is.EqualTo(new Vector2(240, 90)));
            Undo.PerformUndo();
            Assert.That(rect.sizeDelta, Is.EqualTo(before));
            Assert.That(rect.pivot, Is.EqualTo(new Vector2(.5f, .5f)));
        }

        [Test]
        public void SameFrameEditsAndInvalidRequestPreserveSeparateUndoGroups()
        {
            var go = Root();
            var rect = (RectTransform)go.transform;
            Vector2 beforeSize = rect.sizeDelta;
            Success(Call("set_rect", go, new JObject { ["sizeDelta"] = new JArray(240, 90) }));
            Success(Call("set_rect", go, new JObject { ["anchoredPosition"] = new JArray(20, 30) }));
            Failure(Call("set_rect", go, new JObject { ["pivot"] = new JArray(2, 1) }));
            Undo.PerformUndo();
            Assert.That(rect.anchoredPosition, Is.EqualTo(Vector2.zero));
            Assert.That(rect.sizeDelta, Is.EqualTo(new Vector2(240, 90)));
            Undo.PerformUndo();
            Assert.That(rect.sizeDelta, Is.EqualTo(beforeSize));
        }

        [Test]
        public void RectEditsRecordPrefabOverridesAndUndoWithoutEditingAsset()
        {
            string path = "Assets/" + prefix + ".prefab";
            var source = Root();
            Vector2 beforeSize = ((RectTransform)source.transform).sizeDelta;
            try
            {
                var asset = PrefabUtility.SaveAsPrefabAsset(source, path);
                Assert.That(asset, Is.Not.Null);
                var instance = (GameObject)PrefabUtility.InstantiatePrefab(asset);
                roots.Add(instance);
                var rect = (RectTransform)instance.transform;
                Success(Call("set_rect", instance, new JObject
                {
                    ["sizeDelta"] = new JArray(240, 90),
                    ["localScale"] = new JArray(2, 2, 2)
                }));
                var modifications = PrefabUtility.GetPropertyModifications(instance);
                Assert.That(modifications, Is.Not.Null);
                Assert.That(
                    modifications.Any(m => m.target == asset.transform && m.propertyPath == "m_LocalScale.x" && m.value == "2"),
                    Is.True,
                    "Record the changed property as a prefab-instance override.");
                Assert.That(asset.transform.localScale, Is.EqualTo(Vector3.one));
                Assert.That(((RectTransform)asset.transform).sizeDelta, Is.EqualTo(beforeSize));
                Undo.PerformUndo();
                Assert.That(rect.localScale, Is.EqualTo(Vector3.one));
                Assert.That(rect.sizeDelta, Is.EqualTo(beforeSize));
                Assert.That(asset.transform.localScale, Is.EqualTo(Vector3.one));
                // Persistent prefab assets cannot be edited through an instance-ID target.
                Failure(Call("set_rect", asset, new JObject { ["localScale"] = new JArray(3, 3, 3) }));
                Assert.That(asset.transform.localScale, Is.EqualTo(Vector3.one));
            }
            finally
            {
                AssetDatabase.DeleteAsset(path);
            }
        }

        [Test]
        public void InvalidRectPayloadDoesNotPartiallyApply()
        {
            var go = Root();
            var rect = (RectTransform)go.transform;
            Vector2 before = rect.sizeDelta;
            Failure(Call("set_rect", go, new JObject { ["sizeDelta"] = new JArray(240, 90), ["pivot"] = new JArray(2, 1) }));
            Assert.That(rect.sizeDelta, Is.EqualTo(before));
            Failure(Call("set_rect", go, new JObject { ["sizeDelta"] = new JArray(240, 90), ["unsupported"] = true }));
            Assert.That(rect.sizeDelta, Is.EqualTo(before));
            Failure(Call("set_rect", go, new JObject { ["anchoredPosition"] = new JArray(double.NaN, 0) }));
            Assert.That(rect.anchoredPosition, Is.EqualTo(Vector2.zero));
        }

        [Test]
        public void FiniteOffsetsCannotOverflowSerializedRectState()
        {
            var go = Root();
            var rect = (RectTransform)go.transform;
            Vector2 beforeSize = rect.sizeDelta;
            Vector2 beforePosition = rect.anchoredPosition;
            var r = Call("set_rect", go, new JObject
            {
                ["offsetMin"] = new JArray(-float.MaxValue, -float.MaxValue),
                ["offsetMax"] = new JArray(float.MaxValue, float.MaxValue)
            });
            Failure(r);
            Assert.That(rect.sizeDelta, Is.EqualTo(beforeSize));
            Assert.That(rect.anchoredPosition, Is.EqualTo(beforePosition));
        }

        [Test]
        public void HierarchyCapAndInactiveFilteringAreDeterministic()
        {
            var go = Root();
            for (int i = 0; i < 5; i++)
                new GameObject("Child" + i, typeof(RectTransform)).transform.SetParent(go.transform, false);
            go.transform.GetChild(0).gameObject.SetActive(false);
            var p = new JObject
            {
                ["action"] = "get_hierarchy",
                ["target"] = go.GetInstanceIDCompat(),
                ["maxNodes"] = 2
            };
            var r = JObject.FromObject(ManageUGUI.HandleCommand(p));
            Success(r);
            Assert.That(((JArray)r["data"]["nodes"]).Count, Is.EqualTo(2));
            Assert.That(r["data"]["truncated"].Value<bool>(), Is.True);
            Assert.That(r["data"]["nodes"][1]["name"].ToString(), Is.EqualTo("Child1"));
            p["includeInactive"] = true;
            r = JObject.FromObject(ManageUGUI.HandleCommand(p));
            Assert.That(r["data"]["nodes"][1]["name"].ToString(), Is.EqualTo("Child0"));
            p["maxNodes"] = 1001;
            Failure(JObject.FromObject(ManageUGUI.HandleCommand(p)));
        }

        [Test]
        public void CanvasCreationProvidesScalerAndRaycasterAndCanBeUndone()
        {
            if (UnityTypeResolver.ResolveComponent("UnityEngine.UI.CanvasScaler") == null)
                Assert.Ignore("uGUI is not installed.");
            var r = JObject.FromObject(ManageUGUI.HandleCommand(new JObject
            {
                ["action"] = "create",
                ["elementType"] = "canvas",
                ["name"] = prefix
            }));
            Success(r);
            var go = GameObjectLookup.FindById(r["data"]["instance_id"].Value<int>());
            roots.Add(go);
            Assert.That(go.GetComponent<Canvas>().renderMode, Is.EqualTo(RenderMode.ScreenSpaceOverlay));
            Assert.That(go.GetComponent(UnityTypeResolver.ResolveComponent("UnityEngine.UI.CanvasScaler")), Is.Not.Null);
            Assert.That(go.GetComponent(UnityTypeResolver.ResolveComponent("UnityEngine.UI.GraphicRaycaster")), Is.Not.Null);
            Undo.PerformUndo();
            Assert.That(go == null, Is.True);
        }

        [Test]
        public void PanelCreationRequiresCanvasParentAndPreservesExistingChildren()
        {
            if (UnityTypeResolver.ResolveComponent("UnityEngine.UI.Image") == null)
                Assert.Ignore("uGUI is not installed.");
            var root = Root(true);
            var existing = new GameObject("Existing", typeof(RectTransform));
            existing.transform.SetParent(root.transform, false);
            var p = new JObject
            {
                ["action"] = "create",
                ["element_type"] = "panel",
                ["parent"] = root.GetInstanceIDCompat(),
                ["name"] = "NewPanel"
            };
            var r = JObject.FromObject(ManageUGUI.HandleCommand(p));
            Success(r);
            Assert.That(root.transform.childCount, Is.EqualTo(2));
            Assert.That(existing != null, Is.True);
            var panel = GameObjectLookup.FindById(r["data"]["instance_id"].Value<int>());
            Assert.That(((RectTransform)panel.transform).anchorMax, Is.EqualTo(Vector2.one));
            Assert.That(((RectTransform)panel.transform).offsetMax, Is.EqualTo(Vector2.zero));
            p["properties"] = new JObject
            {
                ["sizeDelta"] = new JArray(100, 100),
                ["unknown"] = 1
            };
            Failure(JObject.FromObject(ManageUGUI.HandleCommand(p)));
            Assert.That(root.transform.childCount, Is.EqualTo(2));
            p.Remove("parent");
            Failure(JObject.FromObject(ManageUGUI.HandleCommand(p)));
        }

        [Test]
        public void InvalidLayoutDoesNotAddComponentAndDrivenChildrenRejectRectEdits()
        {
            var type = UnityTypeResolver.ResolveComponent("UnityEngine.UI.VerticalLayoutGroup");
            if (type == null)
                Assert.Ignore("uGUI is not installed.");
            var root = Root();
            Failure(Call("set_layout", root, new JObject { ["type"] = "vertical", ["spacing"] = 10, ["childControlWidth"] = "invalid" }));
            Assert.That(root.GetComponent(type), Is.Null);
            Success(Call("set_layout", root, new JObject { ["type"] = "vertical", ["spacing"] = 10, ["childControlWidth"] = true }));
            var child = new GameObject("Child", typeof(RectTransform));
            child.transform.SetParent(root.transform, false);
            var r = Call("set_rect", child, new JObject { ["sizeDelta"] = new JArray(100, 50) });
            Failure(r);
            StringAssert.Contains("layout-driven", r["error"].ToString());
        }

        [TestCase("anchorMin", .75f)]
        [TestCase("anchorMax", .25f)]
        public void PanelCreationValidatesSingleAnchorAgainstStretchDefaults(string key, float value)
        {
            if (UnityTypeResolver.ResolveComponent("UnityEngine.UI.Image") == null)
                Assert.Ignore("uGUI is not installed.");
            var root = Root(true);
            var r = JObject.FromObject(ManageUGUI.HandleCommand(new JObject
            {
                ["action"] = "create",
                ["element_type"] = "panel",
                ["parent"] = root.GetInstanceIDCompat(),
                ["properties"] = new JObject { [key] = new JArray(value, value) }
            }));
            Success(r);
            var panel = GameObjectLookup.FindById(r["data"]["instance_id"].Value<int>());
            var rect = (RectTransform)panel.transform;
            Assert.That(key == "anchorMin" ? rect.anchorMin : rect.anchorMax, Is.EqualTo(new Vector2(value, value)));
            Assert.That(key == "anchorMin" ? rect.anchorMax : rect.anchorMin, Is.EqualTo(key == "anchorMin" ? Vector2.one : Vector2.zero));
        }

        [Test]
        public void InvalidCanvasAndResolutionPayloadsDoNotChangeState()
        {
            var go = Root(true);
            var canvas = go.GetComponent<Canvas>();
            Failure(Call("set_canvas", go, new JObject { ["sortingOrder"] = 15, ["renderMode"] = "Unknown" }));
            Assert.That(canvas.sortingOrder, Is.EqualTo(0));
            var r = JObject.FromObject(ManageUGUI.HandleCommand(new JObject
            {
                ["action"] = "diagnose",
                ["target"] = go.GetInstanceIDCompat(),
                ["resolutions"] = new JArray(new JObject
                {
                    ["width"] = 0,
                    ["height"] = 1080
                })
            }));
            Failure(r);
        }

        [Test]
        public void CanvasScaleEditsPersistInEnabledScalerAndUndoTogether()
        {
            var type = UnityTypeResolver.ResolveComponent("UnityEngine.UI.CanvasScaler");
            if (type == null)
                Assert.Ignore("uGUI is not installed.");
            var go = Root(true);
            var canvas = go.GetComponent<Canvas>();
            canvas.renderMode = RenderMode.ScreenSpaceOverlay;
            var scaler = go.AddComponent(type);
            type.GetProperty("uiScaleMode").SetValue(scaler, Enum.Parse(type.GetProperty("uiScaleMode").PropertyType, "ConstantPixelSize"));
            var handle = type.GetMethod("Handle", BindingFlags.Instance | BindingFlags.NonPublic);
            Success(Call("set_canvas", go, new JObject { ["scaleFactor"] = 2, ["referencePixelsPerUnit"] = 200 }));
            Assert.That(type.GetProperty("scaleFactor")
                .GetValue(scaler), Is.EqualTo(2f), "Persist the value in the component that controls the Canvas.");
            Assert.That(type.GetProperty("referencePixelsPerUnit").GetValue(scaler), Is.EqualTo(200f));
            handle.Invoke(scaler, null);
            Assert.That(canvas.scaleFactor, Is.EqualTo(2f));
            Assert.That(canvas.referencePixelsPerUnit, Is.EqualTo(200f));
            Undo.PerformUndo();
            handle.Invoke(scaler, null);
            Assert.That(type.GetProperty("scaleFactor").GetValue(scaler), Is.EqualTo(1f));
            Assert.That(type.GetProperty("referencePixelsPerUnit").GetValue(scaler), Is.EqualTo(100f));
            Assert.That(canvas.scaleFactor, Is.EqualTo(1f));
            Assert.That(canvas.referencePixelsPerUnit, Is.EqualTo(100f));
        }

        [Test]
        public void CanvasScaleEditsUseCanvasWhenScalerIsDisabled()
        {
            var type = UnityTypeResolver.ResolveComponent("UnityEngine.UI.CanvasScaler");
            if (type == null)
                Assert.Ignore("uGUI is not installed.");
            var go = Root(true);
            var canvas = go.GetComponent<Canvas>();
            var scaler = go.AddComponent(type);
            ((Behaviour)scaler).enabled = false;
            Success(Call("set_canvas", go, new JObject { ["scaleFactor"] = 2, ["referencePixelsPerUnit"] = 200 }));
            Assert.That(canvas.scaleFactor, Is.EqualTo(2f));
            Assert.That(canvas.referencePixelsPerUnit, Is.EqualTo(200f));
            Assert.That(type.GetProperty("scaleFactor").GetValue(scaler), Is.EqualTo(1f));
            Assert.That(type.GetProperty("referencePixelsPerUnit").GetValue(scaler), Is.EqualTo(100f));
        }

        [Test]
        public void TmpTextCreationAndEditingUseConfiguredFontAndSupportUndo()
        {
            var textType = UnityTypeResolver.ResolveComponent("TMPro.TextMeshProUGUI");
            var settings = UnityTypeResolver.ResolveAny("TMPro.TMP_Settings");
            UnityEngine.Object font = null;
            try
            {
                font = settings?.GetProperty("defaultFontAsset")?.GetValue(null) as UnityEngine.Object;
            }
            catch (TargetInvocationException e)when (e.InnerException is NullReferenceException)
            { /* TMP settings resource is absent. */
            }

            if (textType == null || font == null)
                Assert.Ignore("TMP/default font must be configured for the positive text case.");
            var root = Root(true);
            var r = JObject.FromObject(ManageUGUI.HandleCommand(new JObject
            {
                ["action"] = "create",
                ["element_type"] = "text",
                ["parent"] = root.GetInstanceIDCompat(),
                ["properties"] = new JObject
                {
                    ["text"] = "Created label",
                    ["fontSize"] = 32
                }
            }));
            Success(r);
            var go = GameObjectLookup.FindById(r["data"]["instance_id"].Value<int>());
            var text = go.GetComponent(textType);
            Assert.That(textType.GetProperty("font").GetValue(text), Is.SameAs(font));
            Assert.That(textType.GetProperty("text").GetValue(text), Is.EqualTo("Created label"));
            Assert.That(textType.GetProperty("fontSize").GetValue(text), Is.EqualTo(32f));
            Success(Call("set_text", go, new JObject
            {
                ["text"] = "Edited label",
                ["fontSize"] = 36,
                ["color"] = new JArray(1, .8, .6, 1)
            }));
            Assert.That(textType.GetProperty("text").GetValue(text), Is.EqualTo("Edited label"));
            Undo.PerformUndo();
            Assert.That(textType.GetProperty("text").GetValue(text), Is.EqualTo("Created label"));
            Assert.That(textType.GetProperty("fontSize").GetValue(text), Is.EqualTo(32f));
        }

        [Test]
        public void MissingTextDependencyHasExplicitErrorAndNoPartialObject()
        {
            var root = Root(true);
            var textType = UnityTypeResolver.ResolveComponent("TMPro.TextMeshProUGUI");
            var settings = UnityTypeResolver.ResolveAny("TMPro.TMP_Settings");
            UnityEngine.Object font = null;
            try
            {
                font = settings?.GetProperty("defaultFontAsset")?.GetValue(null) as UnityEngine.Object;
            }
            catch (TargetInvocationException e)when (e.InnerException is NullReferenceException)
            { /* TMP settings resource is absent. */
            }

            if (textType != null && font != null)
                Assert.Ignore("TMP default font is configured; this case verifies dependency failure.");
            var r = JObject.FromObject(ManageUGUI.HandleCommand(new JObject
            {
                ["action"] = "create",
                ["element_type"] = "text",
                ["parent"] = root.GetInstanceIDCompat()
            }));
            Failure(r);
            StringAssert.Contains("TMP", r["error"].ToString());
            Assert.That(root.transform.childCount, Is.EqualTo(0));
        }
    }
}
