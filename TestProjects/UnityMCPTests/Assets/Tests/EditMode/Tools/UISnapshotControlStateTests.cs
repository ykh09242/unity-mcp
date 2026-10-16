using System;
using System.Collections;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Reflection;
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
    public class UISnapshotControlStateTests
    {
        private const string Secret = "UI04_PRIVATE_SENTINEL_7a29";
        private readonly List<GameObject> roots = new();
        private TestWindow window;
        private GameObject target;
        private VisualElement tree;
        private PanelSettings settings;
        private ThemeStyleSheet theme;

        private sealed class TestWindow : EditorWindow { }

        // This overrides the very virtual getter the baseline serializer invokes through TextElement.
        private sealed class GetterCounter : TextElement
        {
            internal bool Armed;
            internal int Reads;
            public override string text
            {
                get
                {
                    if (Armed)
                        Reads++;
                    return base.text;
                }
                set => base.text = value;
            }
        }

        [TearDown]
        public void TearDown()
        {
            if (window != null)
                window.Close();
            foreach (var root in roots)
                if (root != null)
                    UnityEngine.Object.DestroyImmediate(root);
            roots.Clear();
            if (settings != null)
                UnityEngine.Object.DestroyImmediate(settings);
            if (theme != null)
                UnityEngine.Object.DestroyImmediate(theme);
            SetInventoryOverride(null, required: false);
            var toolkit = typeof(CaptureUISnapshot).Assembly.GetType("MCPForUnity.Editor.Tools.UitoolkitSnapshotCapture");
            toolkit.GetField("InputPrivacyForTests", BindingFlags.Static | BindingFlags.NonPublic)?.SetValue(null, null);
        }

        private GameObject Root()
        {
            var result = new GameObject("UI04_" + Guid.NewGuid().ToString("N"), typeof(RectTransform), typeof(Canvas));
            roots.Add(result);
            return result;
        }

        private static Component Add(GameObject go, string typeName)
        {
            var type = UnityTypeResolver.ResolveComponent(typeName);
            if (type == null)
                Assert.Ignore("Optional control type is not installed: " + typeName);
            return go.AddComponent(type);
        }

        private static GameObject Child(GameObject parent, string name)
        {
            var go = new GameObject(name, typeof(RectTransform));
            go.transform.SetParent(parent.transform, false);
            return go;
        }

        private static object Get(Component component, string property) => component.GetType().GetProperty(property).GetValue(component);

        private static void Set(Component component, string property, object value) => component.GetType().GetProperty(property).SetValue(component, value);

        private static JObject Capture(GameObject go, string framework = "ugui", int maxNodes = 200) =>
            JObject.FromObject(
                CaptureUISnapshot.HandleCommand(
                    new JObject
                    {
                        ["framework"] = framework,
                        ["target"] = go.GetInstanceIDCompat(),
                        ["max_nodes"] = maxNodes,
                    }
                )
            );

        private static JObject Data(GameObject go, string framework = "ugui")
        {
            var response = Capture(go, framework);
            Assert.IsTrue(response.Value<bool>("success"), response.ToString());
            Assert.AreEqual(2, response["data"].Value<int>("schema_version"), response.ToString());
            Assert.AreEqual("typed_allowlist_v1", response["data"]["evidence"].Value<string>("control_state_method"));
            Assert.AreEqual("recognized_input_ownership_v1", response["data"]["evidence"].Value<string>("input_privacy"));
            return (JObject)response["data"];
        }

        private static JToken Node(JObject snapshot, string name) =>
            snapshot["nodes"]
                .Single(n =>
                    n.Value<string>("id")
                        .EndsWith(
                            "/" + Uri.EscapeDataString(snapshot.Value<string>("framework") == "uitoolkit" ? "name:" + name : name),
                            StringComparison.Ordinal
                        )
                );

        private static void OnlyStateChanged(JObject before, JObject after)
        {
            var left = (JObject)before.DeepClone();
            var right = (JObject)after.DeepClone();
            bool changed = false;
            for (int i = 0; i < left["nodes"].Count(); i++)
            {
                changed |= !JToken.DeepEquals(left["nodes"][i]["state"], right["nodes"][i]["state"]);
                ((JObject)left["nodes"][i]).Remove("state");
                ((JObject)right["nodes"][i]).Remove("state");
            }
            Assert.IsTrue(changed, "Real native control state must differ.");
            Assert.IsTrue(JToken.DeepEquals(left, right), "Fixture must establish a state-only change, not layout/text/classes changes.");
        }

        private static void Export(string name, JObject value) =>
            File.WriteAllText(Path.Combine(Path.GetTempPath(), "ui04-" + name + ".json"), value.ToString());

        [TestCase("UnityEngine.UI.Toggle", "isOn", "toggle")]
        [TestCase("UnityEngine.UI.Slider", "value", "slider")]
        [TestCase("UnityEngine.UI.Dropdown", "value", "selection")]
        [TestCase("TMPro.TMP_Dropdown", "value", "selection")]
        public void UguiStateOnlyChangeExportsActualSnapshots(string typeName, string property, string category)
        {
            var root = Root();
            var go = Child(root, "Control");
            var control = Add(go, typeName);
            if (category == "slider")
            {
                Set(control, "minValue", 0f);
                Set(control, "maxValue", 1f);
                Set(control, property, 0.25f);
            }
            if (category == "selection")
            {
                var options = (IList)Get(control, "options");
                var option = control.GetType().GetNestedType("OptionData");
                options.Add(Activator.CreateInstance(option));
                options.Add(Activator.CreateInstance(option));
            }
            Canvas.ForceUpdateCanvases();
            string unchanged = EditorJsonUtility.ToJson(control);
            var before = Data(root);
            Assert.AreEqual(unchanged, EditorJsonUtility.ToJson(control), "Capture must not set the control value.");
            Assert.IsNotNull(Node(before, "Control")["state"]?[category]);
            Set(
                control,
                property,
                category == "toggle" ? (object)true
                    : category == "slider" ? (object)0.75f
                    : 1
            );
            Canvas.ForceUpdateCanvases();
            var after = Data(root);
            OnlyStateChanged(before, after);
            string suffix = typeName.Replace('.', '-');
            Export(suffix + "-before", before);
            Export(suffix + "-after", after);
        }

        [TestCase("UnityEngine.UI.InputField", false)]
        [TestCase("UnityEngine.UI.InputField", true)]
        [TestCase("TMPro.TMP_InputField", false)]
        [TestCase("TMPro.TMP_InputField", true)]
        public void UguiInputOwnsInactiveExternalRendererAndPreservesOrdinaryText(string typeName, bool password)
        {
            var root = Root();
            var outsideOwner = Root();
            var input = Add(Child(outsideOwner, "Owner"), typeName);
            var display = Child(root, "ExternalRenderer");
            var text = Add(display, typeName.StartsWith("TMPro", StringComparison.Ordinal) ? "TMPro.TextMeshProUGUI" : "UnityEngine.UI.Text");
            Set(input, "textComponent", text);
            Set(input, "readOnly", true);
            var inputType = input.GetType().GetProperty("inputType").PropertyType;
            Set(input, "inputType", Enum.Parse(inputType, password ? "Password" : "Standard"));
            input.gameObject.SetActive(false);
            // Set after the input renderer assignment so the fixture proves raw text is present.
            Set(text, "text", Secret);
            Assert.AreEqual(Secret, Get(text, "text"));
            var ordinary = Add(Child(root, "OrdinaryLabel"), "UnityEngine.UI.Text");
            Set(ordinary, "text", "Public label");
            var result = Capture(root);
            Assert.IsTrue(result.Value<bool>("success"), result.ToString());
            StringAssert.DoesNotContain(Secret, result.ToString());
            Assert.AreEqual(JTokenType.Null, Node((JObject)result["data"], "ExternalRenderer")["text"].Type);
            Assert.AreEqual("Public label", Node((JObject)result["data"], "OrdinaryLabel").Value<string>("text"));
            var ownerData = Data(outsideOwner);
            var state = Node(ownerData, "Owner")["state"]["input"];
            Assert.IsTrue(state.Value<bool>("redacted"));
            Assert.IsTrue(state.Value<bool>("read_only"));
            Assert.AreEqual(password, state.Value<bool>("password"));
            Assert.AreEqual(JTokenType.Null, Node(ownerData, "Owner")["text"].Type);
            Set(text, "text", Secret + "_CHANGED");
            var changed = Data(root);
            Assert.IsTrue(JToken.DeepEquals(result["data"], changed), "Changing only owned raw renderer contents must be unobservable.");
            string suffix = "redacted-" + typeName.Replace('.', '-') + "-" + password;
            Export(suffix + "-before", (JObject)result["data"]);
            Export(suffix + "-after", changed);
        }

        private static FieldInfo SetInventoryOverride(Func<Type, UnityEngine.Object[]> inventory, bool required = true)
        {
            var type = typeof(CaptureUISnapshot).Assembly.GetType("MCPForUnity.Editor.Tools.UguiSnapshotCapture");
            var field = type.GetField("InventoryForTests", BindingFlags.Static | BindingFlags.NonPublic);
            if (required)
                Assert.IsNotNull(
                    field,
                    "A narrow inventory seam is required to verify managed privacy bounds without allocating thousands of native controls."
                );
            field?.SetValue(null, inventory);
            return field;
        }

        [Test]
        public void IncompletePrivacyInventoryFailsBeforePublishingAnyText()
        {
            var root = Root();
            var label = Add(Child(root, "Label"), "UnityEngine.UI.Text");
            Set(label, "text", Secret);
            SetInventoryOverride(_ => throw new InvalidOperationException(Secret));
            var response = Capture(root);
            Assert.IsFalse(response.Value<bool>("success"));
            Assert.IsNull(response["data"]);
            StringAssert.DoesNotContain(Secret, response.ToString());
        }

        [TestCase(4096, true)]
        [TestCase(4097, false)]
        public void PrivacyInventoryManagedLimitIsCompleteOrRefused(int count, bool success)
        {
            var root = Root();
            bool first = true;
            SetInventoryOverride(_ =>
            {
                if (!first)
                    return Array.Empty<UnityEngine.Object>();
                first = false;
                return new UnityEngine.Object[count];
            });
            var response = Capture(root);
            Assert.AreEqual(success, response.Value<bool>("success"), response.ToString());
            if (!success)
                Assert.IsNull(response["data"]);
        }

        [Test]
        public void UguiScrollStateUsesCurrentNormalizedPositionWithoutMutation()
        {
            var root = Root();
            var control = Add(Child(root, "Scroll"), "UnityEngine.UI.ScrollRect");
            var view = (RectTransform)control.transform;
            view.sizeDelta = new Vector2(100, 100);
            var content = (RectTransform)Child(control.gameObject, "Content").transform;
            content.sizeDelta = new Vector2(300, 300);
            Set(control, "content", content);
            Set(control, "viewport", view);
            Set(control, "inertia", false);
            Canvas.ForceUpdateCanvases();
            Set(control, "normalizedPosition", new Vector2(0.25f, 0.75f));
            var position = (Vector2)Get(control, "normalizedPosition");
            string serialized = EditorJsonUtility.ToJson(control);
            var state = Node(Data(root), "Scroll")["state"]["scroll"];
            Assert.AreEqual("normalized", state.Value<string>("kind"));
            CollectionAssert.AreEqual(new[] { position.x, position.y }, state["position"].Values<float>());
            Assert.AreEqual(serialized, EditorJsonUtility.ToJson(control));
        }

        private IEnumerator Toolkit()
        {
            target = Root();
            var document = target.AddComponent<UIDocument>();
            settings = ScriptableObject.CreateInstance<PanelSettings>();
            theme = ScriptableObject.CreateInstance<ThemeStyleSheet>();
            settings.themeStyleSheet = theme;
            document.panelSettings = settings;
            tree = document.rootVisualElement;
            tree.Clear();
            tree.name = "Tree";
            tree.style.width = 600;
            tree.style.height = 500;
            window = ScriptableObject.CreateInstance<TestWindow>();
            window.position = new Rect(0, 0, 650, 550);
            window.Show();
            window.rootVisualElement.Add(tree);
            yield return Settle();
        }

        private IEnumerator Settle()
        {
            for (int frame = 0; frame < 12; frame++)
            {
                window.Repaint();
                yield return null;
                if (frame >= 2 && tree.panel != null && tree.worldBound.width > 0 && tree.worldBound.height > 0)
                    yield break;
            }
            Assert.IsNotNull(tree.panel, "Owned UIDocument root detached before layout settled.");
            Assert.Greater(tree.worldBound.width, 0, "Owned test root width did not settle.");
            Assert.Greater(tree.worldBound.height, 0, "Owned test root height did not settle.");
        }

        [UnityTest]
        public IEnumerator ToolkitOwnedTextActualVirtualGetterIsNeverReadAndOrdinaryGetterIsRead()
        {
            yield return Toolkit();
            var input = new TextField("Prompt")
            {
                name = "Input",
                value = Secret,
                isPasswordField = true,
            };
            var guarded = new GetterCounter { name = "Guarded", text = Secret };
            input.hierarchy.Add(guarded);
            var ordinary = new GetterCounter { name = "Public", text = "Public label" };
            tree.Add(input);
            tree.Add(ordinary);
            tree.Add(new Button { name = "Button", text = "Public button" });
            yield return Settle();
            guarded.Reads = ordinary.Reads = 0;
            guarded.Armed = ordinary.Armed = true;
            JObject response;
            try
            {
                response = Capture(target, "uitoolkit");
            }
            finally
            {
                guarded.Armed = ordinary.Armed = false;
            }
            Assert.IsTrue(response.Value<bool>("success"), response.ToString());
            Assert.Greater(ordinary.Reads, 0, "Control assertion: the actual virtual getter is reached for allowed ordinary text.");
            Assert.AreEqual(0, guarded.Reads, "The existing baseline invokes this exact virtual getter under the input.");
            StringAssert.DoesNotContain(Secret, response.ToString());
            var data = (JObject)response["data"];
            Assert.AreEqual("Public label", Node(data, "Public").Value<string>("text"));
            Assert.AreEqual("Public button", Node(data, "Button").Value<string>("text"));
            Assert.AreEqual(JTokenType.Null, Node(data, "Guarded")["text"].Type);
            Assert.IsTrue(Node(data, "Input")["state"]["input"].Value<bool>("redacted"));
            Assert.AreEqual(JTokenType.Null, Node(data, "Input")["text"].Type);
            Export("toolkit-privacy", data);
        }

        private static IEnumerable<VisualElement> Physical(VisualElement root)
        {
            var stack = new Stack<VisualElement>();
            stack.Push(root);
            int count = 0;
            while (stack.Count > 0)
            {
                Assert.Less(count++, 1000);
                var element = stack.Pop();
                yield return element;
                for (int i = element.hierarchy.childCount - 1; i >= 0; i--)
                    stack.Push(element.hierarchy[i]);
            }
        }

        [UnityTest]
        public IEnumerator ToolkitPrivacyFailureNeverReadsActualOwnedGetterOrEchoesFailure()
        {
            yield return Toolkit();
            var field = new TextField { name = "Input" };
            var guarded = new GetterCounter { name = "Guarded", text = Secret };
            field.hierarchy.Add(guarded);
            tree.Add(field);
            yield return Settle();
            var captureType = typeof(CaptureUISnapshot).Assembly.GetType("MCPForUnity.Editor.Tools.UitoolkitSnapshotCapture");
            var seam = captureType.GetField("InputPrivacyForTests", BindingFlags.Static | BindingFlags.NonPublic);
            Assert.IsNotNull(seam, "Narrow privacy failure injection is separate from the real virtual getter trap.");
            seam.SetValue(null, (Action<VisualElement>)(_ => throw new InvalidOperationException(Secret)));
            guarded.Reads = 0;
            guarded.Armed = true;
            JObject response;
            try
            {
                response = Capture(target, "uitoolkit");
            }
            finally
            {
                guarded.Armed = false;
                seam.SetValue(null, null);
            }
            Assert.IsFalse(response.Value<bool>("success"));
            Assert.IsNull(response["data"]);
            Assert.AreEqual(0, guarded.Reads);
            StringAssert.DoesNotContain(Secret, response.ToString());
        }

        [UnityTest]
        public IEnumerator ToolkitNativeInternalOwnershipIsCharacterizedAndDetachedInputStaysPrivate()
        {
            yield return Toolkit();
            var input = new TextField { name = "Input", value = Secret };
            tree.Add(input);
            yield return Settle();
            var internals = Physical(input).OfType<TextElement>().Where(item => item.text == Secret).ToArray();
            Assert.IsNotEmpty(internals, "Characterize real engine internal raw text before asserting capture privacy.");
            var internalText = internals[0];
            internalText.RemoveFromHierarchy();
            internalText.name = "DetachedInput";
            tree.Add(internalText);
            yield return Settle();
            var data = Data(target, "uitoolkit");
            StringAssert.DoesNotContain(Secret, data.ToString());
            Assert.AreEqual(JTokenType.Null, Node(data, "DetachedInput")["text"].Type);
            Export(
                "toolkit-internal-types",
                new JObject { ["types"] = new JArray(internals.Select(item => item.GetType().FullName)), ["unity"] = Application.unityVersion }
            );
        }

        [UnityTest]
        public IEnumerator ToolkitStateOnlyToggleExportsActualSnapshots()
        {
            yield return Toolkit();
            var toggle = new Toggle { name = "Control" };
            toggle.hierarchy.Clear();
            toggle.style.width = 100;
            toggle.style.height = 24;
            tree.Add(toggle);
            int events = 0;
            toggle.RegisterValueChangedCallback(_ => events++);
            yield return Settle();
            var before = Data(target, "uitoolkit");
            Assert.AreEqual(0, events);
            toggle.SetValueWithoutNotify(true);
            var after = Data(target, "uitoolkit");
            Assert.AreEqual(0, events);
            OnlyStateChanged(before, after);
            Export("toolkit-toggle-before", before);
            Export("toolkit-toggle-after", after);
        }

        [UnityTest]
        public IEnumerator ToolkitSliderSelectionInputAndScrollUseTypedValues()
        {
            yield return Toolkit();
            var slider = new Slider(0, 1) { name = "Slider", value = 0.375f };
            var integer = new SliderInt(0, 10) { name = "Integer", value = 7 };
            var dropdown = new DropdownField(new List<string> { "a", "b" }, 1) { name = "Dropdown" };
            var field = new TextField
            {
                name = "Input",
                value = Secret,
                isReadOnly = true,
            };
            var scroll = new ScrollView { name = "Scroll" };
            scroll.style.width = 100;
            scroll.style.height = 50;
            var content = new VisualElement { name = "Content" };
            content.style.width = 400;
            content.style.height = 400;
            scroll.Add(content);
            foreach (var element in new VisualElement[] { slider, integer, dropdown, field, scroll })
                tree.Add(element);
            yield return Settle();
            scroll.scrollOffset = new Vector2(15, 25);
            var observed = scroll.scrollOffset;
            var data = Data(target, "uitoolkit");
            Assert.AreEqual(0.375f, Node(data, "Slider")["state"]["slider"].Value<float>("value"));
            Assert.AreEqual("integer", Node(data, "Integer")["state"]["slider"].Value<string>("kind"));
            Assert.AreEqual(7, Node(data, "Integer")["state"]["slider"].Value<int>("value"));
            Assert.AreEqual(1, Node(data, "Dropdown")["state"]["selection"].Value<int>("index"));
            Assert.IsTrue(Node(data, "Input")["state"]["input"].Value<bool>("read_only"));
            CollectionAssert.AreEqual(new[] { observed.x, observed.y }, Node(data, "Scroll")["state"]["scroll"]["position"].Values<float>());
            StringAssert.DoesNotContain(Secret, data.ToString());
        }

        [UnityTest]
        public IEnumerator ToolkitListSelectionIsIndexOnlyAndRefusesOverflow()
        {
            yield return Toolkit();
            var list = new ListView
            {
                name = "List",
                selectionType = SelectionType.Multiple,
                itemsSource = Enumerable.Range(0, 70).ToArray(),
                makeItem = () => new Label(),
                bindItem = (element, index) =>
                {
                    element.name = "Row" + index;
                    ((Label)element).text = "Row";
                },
            };
            list.style.height = 100;
            tree.Add(list);
            yield return Settle();
            list.SetSelectionWithoutNotify(new[] { 3, 1 });
            var data = Data(target, "uitoolkit");
            CollectionAssert.AreEqual(new[] { 1, 3 }, Node(data, "List")["state"]["selection"]["indices"].Values<int>());
            list.SetSelectionWithoutNotify(Enumerable.Range(0, 64));
            Assert.AreEqual(64, Node(Data(target, "uitoolkit"), "List")["state"]["selection"]["indices"].Count());
            list.SetSelectionWithoutNotify(Enumerable.Range(0, 65));
            var response = Capture(target, "uitoolkit");
            Assert.IsFalse(response.Value<bool>("success"));
            Assert.IsNull(response["data"]);
        }
    }
}
