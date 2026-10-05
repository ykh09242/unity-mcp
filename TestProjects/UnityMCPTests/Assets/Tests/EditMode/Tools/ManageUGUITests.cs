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

        [TestCase(false)]
        [TestCase(true)]
        public void InactiveChildExcludedByParentLayoutAllowsRectEditsAndUndo(bool previouslyLaidOut)
        {
            var groupType = UnityTypeResolver.ResolveComponent("UnityEngine.UI.VerticalLayoutGroup");
            if (groupType == null)
                Assert.Ignore("uGUI is not installed.");
            var root = Root();
            var group = root.AddComponent(groupType);
            var child = new GameObject("InactiveChild", typeof(RectTransform));
            child.transform.SetParent(root.transform, false);
            var rect = (RectTransform)child.transform;
            var calculate = groupType.GetMethod("CalculateLayoutInputHorizontal");
            if (previouslyLaidOut)
            {
                calculate.Invoke(group, null);
                groupType.GetMethod("SetLayoutHorizontal").Invoke(group, null);
                groupType.GetMethod("SetLayoutVertical").Invoke(group, null);
            }

            child.SetActive(false);
            calculate.Invoke(group, null);
            var children = (System.Collections.IList)groupType.GetProperty("rectChildren", BindingFlags.Instance | BindingFlags.NonPublic)
                .GetValue(group);
            Assert.That(children.Count, Is.EqualTo(0), "Installed uGUI excludes inactive children from parent layout.");
            Assert.That(typeof(RectTransform).GetProperty("drivenByObject")?.GetValue(rect), Is.Null, "A normal layout calculation releases any previous native ownership.");
            Vector2 before = rect.sizeDelta;
            Undo.ClearAll();
            var p = new JObject
            {
                ["action"] = "set_rect",
                ["target"] = child.GetInstanceIDCompat(),
                ["include_inactive"] = true,
                ["properties"] = new JObject { ["sizeDelta"] = new JArray(240, 90) }
            };
            Success(JObject.FromObject(ManageUGUI.HandleCommand(p)));
            Assert.That(rect.sizeDelta, Is.EqualTo(new Vector2(240, 90)));
            p["action"] = "get_hierarchy";
            p.Remove("properties");
            var hierarchy = JObject.FromObject(ManageUGUI.HandleCommand(p));
            Success(hierarchy);
            Assert.That(hierarchy["data"]["nodes"][0]["rect"]["layout_driven"].Value<bool>(), Is.False);
            Undo.PerformUndo();
            Assert.That(rect.sizeDelta, Is.EqualTo(before));
        }

        [TestCase(false)]
        [TestCase(true)]
        public void LaterActiveContentSizeFitterRejectsRectEditsBeforeLayoutRuns(bool disableFirst)
        {
            var fitterType = UnityTypeResolver.ResolveComponent("UnityEngine.UI.ContentSizeFitter");
            var elementType = UnityTypeResolver.ResolveComponent("UnityEngine.UI.LayoutElement");
            if (fitterType == null || elementType == null)
                Assert.Ignore("uGUI is not installed.");
            if (fitterType.IsDefined(typeof(DisallowMultipleComponent), true))
                Assert.Ignore("Installed uGUI disallows multiple ContentSizeFitters.");
            var go = Root();
            var rect = (RectTransform)go.transform;
            var element = go.AddComponent(elementType);
            elementType.GetProperty("preferredWidth").SetValue(element, 310f);
            var first = go.AddComponent(fitterType);
            if (disableFirst)
                ((Behaviour)first).enabled = false;
            var later = go.AddComponent(fitterType);
            var horizontalFit = fitterType.GetProperty("horizontalFit");
            horizontalFit.SetValue(later, Enum.Parse(horizontalFit.PropertyType, "PreferredSize"));
            Assert.That(go.GetComponents(fitterType).Length, Is.EqualTo(2));
            var drivenProperty = typeof(RectTransform).GetProperty("drivenByObject");
            Assert.That(drivenProperty?.GetValue(rect), Is.Null, "The requested edit occurs before the first native layout pass.");
            Vector2 before = rect.sizeDelta;
            var r = Call("set_rect", go, new JObject { ["sizeDelta"] = new JArray(240, 90) });
            Vector2 afterCommand = rect.sizeDelta;
            fitterType.GetMethod("SetLayoutHorizontal").Invoke(later, null);
            Assert.That(rect.sizeDelta.x, Is.EqualTo(310f), "The later active fitter controls native preferred width.");
            Failure(r);
            StringAssert.Contains("layout-driven", r["error"].ToString());
            Assert.That(afterCommand, Is.EqualTo(before), "An active later fitter must not be hidden by an inactive first component.");
        }

        [TestCase(false)]
        [TestCase(true)]
        public void OnlyUnconstrainedOrDisabledContentSizeFittersAllowRectEdits(bool disableLater)
        {
            var fitterType = UnityTypeResolver.ResolveComponent("UnityEngine.UI.ContentSizeFitter");
            if (fitterType == null)
                Assert.Ignore("uGUI is not installed.");
            if (fitterType.IsDefined(typeof(DisallowMultipleComponent), true))
                Assert.Ignore("Installed uGUI disallows multiple ContentSizeFitters.");
            var go = Root();
            var rect = (RectTransform)go.transform;
            var first = go.AddComponent(fitterType);
            var later = go.AddComponent(fitterType);
            if (disableLater)
            {
                var horizontalFit = fitterType.GetProperty("horizontalFit");
                horizontalFit.SetValue(later, Enum.Parse(horizontalFit.PropertyType, "PreferredSize"));
                ((Behaviour)later).enabled = false;
            }
            Vector2 before = rect.sizeDelta;
            fitterType.GetMethod("SetLayoutHorizontal").Invoke(first, null);
            Assert.That(rect.sizeDelta, Is.EqualTo(before), "The unconstrained active fitter leaves size unchanged.");
            rect.sizeDelta = new Vector2(270, 95);
            fitterType.GetMethod("SetLayoutHorizontal").Invoke(first, null);
            if (((Behaviour)later).isActiveAndEnabled)
                fitterType.GetMethod("SetLayoutHorizontal").Invoke(later, null);
            Assert.That(rect.sizeDelta, Is.EqualTo(new Vector2(270, 95)), "Active unconstrained layout preserves direct native size edits.");
            rect.sizeDelta = before;
            Success(Call("set_rect", go, new JObject { ["sizeDelta"] = new JArray(240, 90) }));
            Assert.That(rect.sizeDelta, Is.EqualTo(new Vector2(240, 90)));
            Undo.PerformUndo();
            Assert.That(rect.sizeDelta, Is.EqualTo(before));
        }

        [Test]
        public void UnconstrainedFitterTransitionRejectsEditsUntilLayoutPreservesUndo()
        {
            var fitterType = UnityTypeResolver.ResolveComponent("UnityEngine.UI.ContentSizeFitter");
            var elementType = UnityTypeResolver.ResolveComponent("UnityEngine.UI.LayoutElement");
            if (fitterType == null || elementType == null)
                Assert.Ignore("uGUI is not installed.");
            var go = Root();
            var rect = (RectTransform)go.transform;
            var element = go.AddComponent(elementType);
            elementType.GetProperty("preferredWidth").SetValue(element, 310f);
            var fitter = go.AddComponent(fitterType);
            var horizontalFit = fitterType.GetProperty("horizontalFit");
            var layout = fitterType.GetMethod("SetLayoutHorizontal");
            horizontalFit.SetValue(fitter, Enum.Parse(horizontalFit.PropertyType, "PreferredSize"));
            layout.Invoke(fitter, null);
            Assert.That(rect.sizeDelta.x, Is.EqualTo(310f));
            horizontalFit.SetValue(fitter, Enum.Parse(horizontalFit.PropertyType, "Unconstrained"));
            Vector2 before = rect.sizeDelta;
            Undo.ClearAll();
            var rejected = Call("set_rect", go, new JObject { ["sizeDelta"] = new JArray(240, 90) });
            Failure(rejected);
            StringAssert.Contains("layout-driven", rejected["error"].ToString());
            Assert.That(rect.sizeDelta, Is.EqualTo(before), "Reject edits while native layout still owns the size.");
            layout.Invoke(fitter, null);
            Assert.That(rect.sizeDelta, Is.EqualTo(before));
            Success(Call("set_rect", go, new JObject { ["sizeDelta"] = new JArray(240, 90) }));
            layout.Invoke(fitter, null);
            Assert.That(rect.sizeDelta, Is.EqualTo(new Vector2(240, 90)));
            Undo.PerformUndo();
            Assert.That(rect.sizeDelta, Is.EqualTo(before));
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
        public void MixedRectAnchorsOffsetsAndScaleMatchNativeSettersAndUndo()
        {
            var parent = Root();
            var go = new GameObject("Edited", typeof(RectTransform));
            go.transform.SetParent(parent.transform, false);
            var twin = new GameObject("NativeControl", typeof(RectTransform));
            twin.transform.SetParent(parent.transform, false);
            var rect = (RectTransform)go.transform;
            var native = (RectTransform)twin.transform;
            Vector2 beforeSize = rect.sizeDelta;
            Vector2 beforePosition = rect.anchoredPosition;
            var r = Call("set_rect", go, new JObject
            {
                ["offsetMax"] = new JArray(31, 29),
                ["localScale"] = new JArray(-1, 0, 2),
                ["pivot"] = new JArray(.25f, .75f),
                ["anchorMax"] = new JArray(.9f, .8f),
                ["offsetMin"] = new JArray(-17, -13),
                ["localEulerAngles"] = new JArray(0, 0, 30),
                ["anchorMin"] = new JArray(.1f, .2f)
            });
            Success(r);
            native.anchorMax = new Vector2(.9f, .8f);
            native.anchorMin = new Vector2(.1f, .2f);
            native.pivot = new Vector2(.25f, .75f);
            native.localScale = new Vector3(-1, 0, 2);
            native.localEulerAngles = new Vector3(0, 0, 30);
            native.offsetMax = new Vector2(31, 29);
            native.offsetMin = new Vector2(-17, -13);
            Assert.That(rect.sizeDelta, Is.EqualTo(native.sizeDelta));
            Assert.That(rect.anchoredPosition, Is.EqualTo(native.anchoredPosition));
            Assert.That(rect.localScale, Is.EqualTo(native.localScale));
            Assert.That(Quaternion.Angle(rect.localRotation, native.localRotation), Is.LessThan(.001f));
            Undo.PerformUndo();
            Assert.That(rect.sizeDelta, Is.EqualTo(beforeSize));
            Assert.That(rect.anchoredPosition, Is.EqualTo(beforePosition));
            Assert.That(rect.anchorMin, Is.EqualTo(new Vector2(.5f, .5f)));
            Assert.That(rect.anchorMax, Is.EqualTo(new Vector2(.5f, .5f)));
            Assert.That(rect.pivot, Is.EqualTo(new Vector2(.5f, .5f)));
            Assert.That(rect.localScale, Is.EqualTo(Vector3.one));
            Assert.That(Quaternion.Angle(rect.localRotation, Quaternion.identity), Is.LessThan(.001f));
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
        public void MissingOptionalUiPackagesStillReadHierarchyToTheRequestedNodeLimit()
        {
            if (UnityTypeResolver.ResolveComponent("UnityEngine.UI.Text") != null
                || UnityTypeResolver.ResolveComponent("TMPro.TextMeshProUGUI") != null)
                Assert.Ignore("This case verifies absent uGUI and TMP.");
            var root = Root();
            var expected = new List<GameObject> { root };
            for (int i = 0; i < 200; i++)
            {
                var child = new GameObject("Child" + i, typeof(RectTransform));
                child.transform.SetParent(root.transform, false);
                expected.Add(child);
            }

            var p = new JObject
            {
                ["action"] = "get_hierarchy",
                ["target"] = root.GetInstanceIDCompat(),
                ["max_nodes"] = 200
            };
            var capped = JObject.FromObject(ManageUGUI.HandleCommand(p));
            Success(capped);
            Assert.That(capped["data"]["count"].Value<int>(), Is.EqualTo(200));
            Assert.That(capped["data"]["truncated"].Value<bool>(), Is.True);
            p["max_nodes"] = 1000;
            var complete = JObject.FromObject(ManageUGUI.HandleCommand(p));
            Success(complete);
            var nodes = (JArray)complete["data"]["nodes"];
            Assert.That(nodes.Count, Is.EqualTo(expected.Count));
            Assert.That(complete["data"]["truncated"].Value<bool>(), Is.False);
            for (int i = 0; i < nodes.Count; i++)
            {
                Assert.That(nodes[i]["instance_id"].Value<int>(), Is.EqualTo(expected[i].GetInstanceIDCompat()));
                Assert.That(nodes[i]["path"].ToString(), Is.EqualTo(i == 0 ? root.name : root.name + "/" + expected[i].name));
                Assert.That(nodes[i]["rect"]["layout_driven"].Value<bool>(), Is.False);
                Assert.That(nodes[i]["text"], Is.Null);
                if (i < 200)
                    Assert.That(capped["data"]["nodes"][i]["instance_id"].Value<int>(), Is.EqualTo(expected[i].GetInstanceIDCompat()));
            }
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
        public void CreatedCanvasRedoPreservesConfiguredComponents()
        {
            var scalerType = UnityTypeResolver.ResolveComponent("UnityEngine.UI.CanvasScaler");
            var raycasterType = UnityTypeResolver.ResolveComponent("UnityEngine.UI.GraphicRaycaster");
            if (scalerType == null || raycasterType == null)
                Assert.Ignore("uGUI is not installed.");
            var r = JObject.FromObject(ManageUGUI.HandleCommand(new JObject
            {
                ["action"] = "create",
                ["element_type"] = "canvas",
                ["name"] = prefix
            }));
            Success(r);
            int id = r["data"]["instance_id"].Value<int>();
            var go = GameObjectLookup.FindById(id);
            roots.Add(go);
            Undo.PerformUndo();
            Assert.That(go == null, Is.True);
            Undo.PerformRedo();
            go = GameObjectLookup.FindById(id);
            Assert.That(go, Is.Not.Null);
            roots.Add(go);
            Assert.That(go.GetComponent<Canvas>().renderMode, Is.EqualTo(RenderMode.ScreenSpaceOverlay));
            Assert.That(go.GetComponent(raycasterType), Is.Not.Null);
            var scaler = go.GetComponent(scalerType);
            Assert.That(scaler, Is.Not.Null);
            Assert.That(scalerType.GetProperty("uiScaleMode").GetValue(scaler).ToString(), Is.EqualTo("ScaleWithScreenSize"));
            Assert.That(scalerType.GetProperty("referenceResolution").GetValue(scaler), Is.EqualTo(new Vector2(1920, 1080)));
            Assert.That(scalerType.GetProperty("matchWidthOrHeight").GetValue(scaler), Is.EqualTo(.5f));
        }

        [TestCase("panel")]
        [TestCase("button")]
        public void CreatedChildRedoPreservesParentRectAndVisualProperties(string kind)
        {
            var imageType = UnityTypeResolver.ResolveComponent("UnityEngine.UI.Image");
            var buttonType = UnityTypeResolver.ResolveComponent("UnityEngine.UI.Button");
            if (imageType == null || buttonType == null)
                Assert.Ignore("uGUI is not installed.");
            var root = Root(true);
            var r = JObject.FromObject(ManageUGUI.HandleCommand(new JObject
            {
                ["action"] = "create",
                ["element_type"] = kind,
                ["parent"] = root.GetInstanceIDCompat(),
                ["properties"] = new JObject
                {
                    ["sizeDelta"] = new JArray(240, 90),
                    ["pivot"] = new JArray(.25f, .75f),
                    ["color"] = new JArray(.2f, .4f, .6f, 1)
                }
            }));
            Success(r);
            int id = r["data"]["instance_id"].Value<int>();
            Undo.PerformUndo();
            Assert.That(root.transform.childCount, Is.EqualTo(0));
            Undo.PerformRedo();
            var go = GameObjectLookup.FindById(id);
            Assert.That(go, Is.Not.Null);
            Assert.That(go.transform.parent, Is.SameAs(root.transform));
            Assert.That(((RectTransform)go.transform).sizeDelta, Is.EqualTo(new Vector2(240, 90)));
            Assert.That(((RectTransform)go.transform).pivot, Is.EqualTo(new Vector2(.25f, .75f)));
            var image = go.GetComponent(imageType);
            Assert.That(imageType.GetProperty("color").GetValue(image), Is.EqualTo(new Color(.2f, .4f, .6f, 1)));
            Assert.That(imageType.GetProperty("raycastTarget").GetValue(image), Is.EqualTo(kind == "button"));
            if (kind == "button")
                Assert.That(buttonType.GetProperty("targetGraphic").GetValue(go.GetComponent(buttonType)), Is.SameAs(image));
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

        [TestCase(32768)]
        [TestCase(-32769)]
        public void OutOfRangeCanvasSortingOrderIsRejectedBeforeOtherPropertiesChange(int order)
        {
            var go = Root(true);
            var canvas = go.GetComponent<Canvas>();
            bool beforePixelPerfect = canvas.pixelPerfect;
            var r = Call("set_canvas", go, new JObject
            {
                ["pixelPerfect"] = !beforePixelPerfect,
                ["sortingOrder"] = order
            });
            Assert.That(r["success"]?.Value<bool>(), Is.False, r + " Native sortingOrder: " + canvas.sortingOrder);
            Assert.That(canvas.sortingOrder, Is.EqualTo(0));
            Assert.That(canvas.pixelPerfect, Is.EqualTo(beforePixelPerfect));
        }

        [TestCase(-32768)]
        [TestCase(32767)]
        public void CanvasSortingOrderBoundariesRoundTripAndUndo(int order)
        {
            var go = Root(true);
            var canvas = go.GetComponent<Canvas>();
            Success(Call("set_canvas", go, new JObject { ["sortingOrder"] = order }));
            Assert.That(canvas.sortingOrder, Is.EqualTo(order));
            Undo.PerformUndo();
            Assert.That(canvas.sortingOrder, Is.EqualTo(0));
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

        [TestCase(false)]
        [TestCase(true)]
        public void CanvasScaleEditsRequireEnabledRootScalerAndAreAtomic(bool addDisabledScaler)
        {
            var type = UnityTypeResolver.ResolveComponent("UnityEngine.UI.CanvasScaler");
            if (addDisabledScaler && type == null)
                Assert.Ignore("uGUI is not installed.");
            var go = Root(true);
            var canvas = go.GetComponent<Canvas>();
            var scaler = addDisabledScaler ? go.AddComponent(type) : null;
            if (scaler != null)
                ((Behaviour)scaler).enabled = false;
            float beforeScale = canvas.scaleFactor;
            float beforePixels = canvas.referencePixelsPerUnit;
            var r = Call("set_canvas", go, new JObject { ["scaleFactor"] = 2, ["referencePixelsPerUnit"] = 200, ["sortingOrder"] = 15 });
            Failure(r);
            StringAssert.Contains("enabled CanvasScaler", r["error"].ToString());
            StringAssert.Contains("undoable", r["error"].ToString());
            Assert.That(canvas.scaleFactor, Is.EqualTo(beforeScale));
            Assert.That(canvas.referencePixelsPerUnit, Is.EqualTo(beforePixels));
            Assert.That(canvas.sortingOrder, Is.EqualTo(0));
            if (scaler != null)
            {
                Assert.That(type.GetProperty("scaleFactor").GetValue(scaler), Is.EqualTo(1f));
                Assert.That(type.GetProperty("referencePixelsPerUnit").GetValue(scaler), Is.EqualTo(100f));
            }
        }

        [Test]
        public void NestedCanvasScaleEditsRejectIneffectiveScaleFactorBeforeOtherPropertiesChange()
        {
            var type = UnityTypeResolver.ResolveComponent("UnityEngine.UI.CanvasScaler");
            if (type == null)
                Assert.Ignore("uGUI is not installed.");
            var root = Root(true);
            root.GetComponent<Canvas>().renderMode = RenderMode.ScreenSpaceOverlay;
            var child = new GameObject("NestedCanvas", typeof(RectTransform), typeof(Canvas));
            child.transform.SetParent(root.transform, false);
            var canvas = child.GetComponent<Canvas>();
            var scaler = child.AddComponent(type);
            var handle = type.GetMethod("Handle", BindingFlags.Instance | BindingFlags.NonPublic);
            Assert.That(canvas.isRootCanvas, Is.False);
            canvas.scaleFactor = 3;
            canvas.referencePixelsPerUnit = 300;
            handle.Invoke(scaler, null);
            Assert.That(canvas.scaleFactor, Is.EqualTo(root.GetComponent<Canvas>().scaleFactor), "Native nested scale follows the root Canvas despite the direct write.");
            Assert.That(canvas.referencePixelsPerUnit, Is.EqualTo(300f), "Nested Canvas retains its own reference pixel density.");
            float beforeReferencePixels = canvas.referencePixelsPerUnit;
            var r = Call("set_canvas", child, new JObject { ["scaleFactor"] = 2, ["sortingOrder"] = 15 });
            handle.Invoke(scaler, null);
            Failure(r);
            StringAssert.Contains("root Canvas", r["error"].ToString());
            Assert.That(canvas.scaleFactor, Is.EqualTo(root.GetComponent<Canvas>().scaleFactor));
            Assert.That(canvas.referencePixelsPerUnit, Is.EqualTo(beforeReferencePixels));
            Assert.That(canvas.sortingOrder, Is.EqualTo(0));
            Assert.That(type.GetProperty("scaleFactor").GetValue(scaler), Is.EqualTo(1f));
            Assert.That(type.GetProperty("referencePixelsPerUnit").GetValue(scaler), Is.EqualTo(100f));
        }

        [TestCase(false)]
        [TestCase(true)]
        public void NestedCanvasPixelDensityEditsAreRejectedAtomically(bool addScaler)
        {
            var type = UnityTypeResolver.ResolveComponent("UnityEngine.UI.CanvasScaler");
            if (addScaler && type == null)
                Assert.Ignore("uGUI is not installed.");
            var root = Root(true);
            root.GetComponent<Canvas>().renderMode = RenderMode.ScreenSpaceOverlay;
            var child = new GameObject("NestedCanvas", typeof(RectTransform), typeof(Canvas));
            child.transform.SetParent(root.transform, false);
            var canvas = child.GetComponent<Canvas>();
            var scaler = addScaler ? child.AddComponent(type) : null;
            var handle = type?.GetMethod("Handle", BindingFlags.Instance | BindingFlags.NonPublic);
            canvas.referencePixelsPerUnit = 300;
            if (scaler != null)
                handle.Invoke(scaler, null);
            Assert.That(canvas.referencePixelsPerUnit, Is.EqualTo(300f));
            var r = Call("set_canvas", child, new JObject { ["referencePixelsPerUnit"] = 200, ["sortingOrder"] = 15 });
            if (scaler != null)
                handle.Invoke(scaler, null);
            Failure(r);
            StringAssert.Contains("root Canvas", r["error"].ToString());
            Assert.That(canvas.referencePixelsPerUnit, Is.EqualTo(300f));
            Assert.That(canvas.sortingOrder, Is.EqualTo(0));
            if (scaler != null)
                Assert.That(type.GetProperty("referencePixelsPerUnit").GetValue(scaler), Is.EqualTo(100f));
        }

        [Test]
        public void InactiveRootCanvasScalerConfigurationRemainsEditableAndUndoable()
        {
            var type = UnityTypeResolver.ResolveComponent("UnityEngine.UI.CanvasScaler");
            if (type == null)
                Assert.Ignore("uGUI is not installed.");
            var go = Root(true);
            var canvas = go.GetComponent<Canvas>();
            canvas.renderMode = RenderMode.ScreenSpaceOverlay;
            var scaler = go.AddComponent(type);
            go.SetActive(false);
            Assert.That(canvas.isRootCanvas, Is.True);
            var r = JObject.FromObject(ManageUGUI.HandleCommand(new JObject
            {
                ["action"] = "set_canvas",
                ["target"] = go.GetInstanceIDCompat(),
                ["include_inactive"] = true,
                ["properties"] = new JObject
                {
                    ["uiScaleMode"] = "ScaleWithScreenSize",
                    ["referenceResolution"] = new JArray(1280, 720),
                    ["scaleFactor"] = 2,
                    ["referencePixelsPerUnit"] = 200
                }
            }));
            Success(r);
            Assert.That(type.GetProperty("uiScaleMode").GetValue(scaler).ToString(), Is.EqualTo("ScaleWithScreenSize"));
            Assert.That(type.GetProperty("referenceResolution").GetValue(scaler), Is.EqualTo(new Vector2(1280, 720)));
            Assert.That(type.GetProperty("scaleFactor").GetValue(scaler), Is.EqualTo(2f));
            Assert.That(type.GetProperty("referencePixelsPerUnit").GetValue(scaler), Is.EqualTo(200f));
            Undo.PerformUndo();
            Assert.That(type.GetProperty("uiScaleMode").GetValue(scaler).ToString(), Is.EqualTo("ConstantPixelSize"));
            Assert.That(type.GetProperty("referenceResolution").GetValue(scaler), Is.EqualTo(new Vector2(800, 600)));
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
