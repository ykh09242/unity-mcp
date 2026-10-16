using System;
using System.Collections;
using System.IO;
using System.Linq;
using System.Reflection;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Editor.Tools;
using MCPForUnity.Runtime.Helpers;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;
using UnityEngine.TestTools;
using UnityEngine.UIElements;

namespace MCPForUnityTests.EditMode.Tools
{
    public class UIToolkitLayoutDiagnosticsTests
    {
        private GameObject owner;
        private UIDocument document;
        private VisualElement root;
        private EditorWindow window;
        private PanelSettings settings;
        private ThemeStyleSheet theme;
        private string folder;

        private sealed class TestWindow : EditorWindow { }

        private sealed class TextTrap : TextElement
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

        [SetUp]
        public void SetUp()
        {
            owner = new GameObject("UI06_" + Guid.NewGuid().ToString("N"));
            document = owner.AddComponent<UIDocument>();
            root = document.rootVisualElement;
            root.Clear();
            root.name = "Root";
            root.style.width = 600;
            root.style.height = 500;
        }

        [TearDown]
        public void TearDown()
        {
            var helper = typeof(ManageUI).Assembly.GetType("MCPForUnity.Editor.Tools.UitoolkitLayoutDiagnostics");
            helper?.GetField("ProbeAvailableForTests", BindingFlags.Static | BindingFlags.NonPublic)?.SetValue(null, null);
            if (window != null)
                window.Close();
            if (owner != null)
                UnityEngine.Object.DestroyImmediate(owner);
            if (settings != null)
                UnityEngine.Object.DestroyImmediate(settings);
            if (theme != null)
                UnityEngine.Object.DestroyImmediate(theme);
            if (folder != null && AssetDatabase.IsValidFolder(folder))
                AssetDatabase.DeleteAsset(folder);
        }

        private IEnumerator Ready()
        {
            settings = ScriptableObject.CreateInstance<PanelSettings>();
            theme = ScriptableObject.CreateInstance<ThemeStyleSheet>();
            settings.themeStyleSheet = theme;
            document.panelSettings = settings;
            window = ScriptableObject.CreateInstance<TestWindow>();
            window.position = new Rect(0, 0, 650, 550);
            window.Show();
            window.rootVisualElement.Add(root);
            yield return Settle();
        }

        private IEnumerator Settle()
        {
            for (int i = 0; i < 6; i++)
            {
                window.Repaint();
                yield return null;
            }
            Assert.IsNotNull(root.panel);
            Assert.Greater(root.panel.visualTree.worldBound.width, 0);
        }

        private static VisualElement Box(string name, float x = 0, float y = 0, float width = 100, float height = 30)
        {
            var e = new VisualElement { name = name };
            e.style.position = Position.Absolute;
            e.style.left = x;
            e.style.top = y;
            e.style.width = width;
            e.style.height = height;
            return e;
        }

        private JObject Call(JObject options = null)
        {
            var request = new JObject { ["action"] = "diagnose", ["target"] = owner.GetInstanceIDCompat().ToString() };
            if (options != null)
                foreach (var property in options.Properties())
                    request[property.Name] = property.Value.DeepClone();
            return JObject.FromObject(ManageUI.HandleCommand(request));
        }

        private JObject Data(JObject options = null)
        {
            var r = Call(options);
            Assert.IsTrue(r.Value<bool>("success"), r.ToString());
            Assert.AreEqual("uitoolkit_layout_diagnostics", r["data"]?.Value<string>("kind"), r.ToString());
            Assert.AreEqual(1, r["data"].Value<int>("schema_version"));
            return (JObject)r["data"];
        }

        private static JObject Node(JObject data, string name) => (JObject)data["nodes"].Single(n => n.Value<string>("name") == name);

        private static void Export(string name, JObject data) => File.WriteAllText(Path.Combine(Path.GetTempPath(), "ui06-" + name + ".json"), data.ToString());

        private static JArray RectValues(Rect rect) => new JArray(rect.x, rect.y, rect.width, rect.height);

        private static void GeometryDiagnostic(string name, JObject data, params VisualElement[] elements)
        {
            var geometry = new JArray(
                elements.Select(e => new JObject
                {
                    ["name"] = e.name,
                    ["layout"] = RectValues(e.layout),
                    ["world_bound"] = RectValues(e.worldBound),
                    ["content_rect"] = RectValues(e.contentRect),
                    ["matrix"] = new JArray(e.worldTransform.m00, e.worldTransform.m11, e.worldTransform.m03, e.worldTransform.m13),
                    ["border"] = new JArray(e.resolvedStyle.borderLeftWidth, e.resolvedStyle.borderRightWidth),
                    ["padding"] = new JArray(e.resolvedStyle.paddingLeft, e.resolvedStyle.paddingRight),
                })
            );
            File.WriteAllText(
                Path.Combine(Path.GetTempPath(), "ui06diag-" + name + ".json"),
                new JObject { ["response"] = data, ["geometry"] = geometry }.ToString()
            );
        }

        [Test]
        public void DetachedPanelIsUnavailableAndNeverClean()
        {
            var d = Data();
            Assert.AreEqual("unavailable", d.Value<string>("status"));
            Assert.AreEqual("panel_unavailable", d.Value<string>("layout_status"));
            Assert.AreEqual(0, d["findings"].Count());
            Export("unavailable", d);
        }

        private static readonly object[] InvalidBudgets =
        {
            new object[] { "max_depth", -1 },
            new object[] { "max_depth", 25 },
            new object[] { "max_nodes", 0 },
            new object[] { "max_nodes", 1001 },
            new object[] { "max_evaluations", 0 },
            new object[] { "max_evaluations", 20001 },
            new object[] { "max_characters", 0 },
            new object[] { "max_characters", 262145 },
            new object[] { "max_pairs", -1 },
            new object[] { "max_pairs", 20001 },
            new object[] { "max_findings", 0 },
            new object[] { "max_findings", 2001 },
            new object[] { "max_nodes", true },
            new object[] { "max_nodes", "2" },
            new object[] { "max_nodes", 1.5 },
        };

        [TestCaseSource(nameof(InvalidBudgets))]
        public void InvalidBudgetsRefuseBeforeTargetLookup(string key, object value)
        {
            var r = Call(new JObject { ["target"] = "MISSING_PRIVATE_SENTINEL", [key] = JToken.FromObject(value) });
            Assert.IsFalse(r.Value<bool>("success"));
            StringAssert.Contains(key, r.Value<string>("error"));
            StringAssert.DoesNotContain("MISSING_PRIVATE_SENTINEL", r.ToString());
            Assert.Less(r.ToString().Length, 500);
        }

        [TestCase("element_path")]
        [TestCase("element_id")]
        [TestCase("element_name")]
        [TestCase("identity_map")]
        [TestCase("text")]
        [TestCase("resolutions")]
        public void UnsupportedParametersNeverSelectOrMutate(string key)
        {
            var r = Call(new JObject { [key] = "PRIVATE_VALUE" });
            Assert.IsFalse(r.Value<bool>("success"));
            StringAssert.DoesNotContain("PRIVATE_VALUE", r.ToString());
            StringAssert.Contains("unsupported", r.Value<string>("error").ToLowerInvariant());
        }

        [UnityTest]
        public IEnumerator CurrentTreeAndSourceStayUnchangedAndNoTextGetterRuns()
        {
            var ordinary = new TextTrap { name = "Ordinary", text = "UI06_SECRET" };
            var input = new TextField { name = "Input", value = "UI06_SECRET" };
            var guarded = new TextTrap { name = "InputRenderer", text = "UI06_SECRET" };
            input.hierarchy.Add(guarded);
            root.Add(ordinary);
            root.Add(input);
            yield return Ready();
            var order = root.hierarchy.Children().ToArray();
            var width = root.style.width;
            var classes = root.GetClasses().ToArray();
            bool sceneDirty = owner.scene.isDirty;
            bool componentDirty = EditorUtility.IsDirty(document);
            bool settingsDirty = EditorUtility.IsDirty(settings);
            bool themeDirty = EditorUtility.IsDirty(theme);
            var focused = root.panel.focusController.focusedElement;
            ordinary.Armed = guarded.Armed = true;
            JObject d;
            try
            {
                d = Data();
            }
            finally
            {
                ordinary.Armed = guarded.Armed = false;
            }
            Assert.AreEqual(0, ordinary.Reads);
            Assert.AreEqual(0, guarded.Reads);
            StringAssert.DoesNotContain("UI06_SECRET", d.ToString());
            CollectionAssert.AreEqual(order, root.hierarchy.Children());
            CollectionAssert.AreEqual(classes, root.GetClasses());
            Assert.AreEqual(width, root.style.width);
            Assert.AreEqual(sceneDirty, owner.scene.isDirty);
            Assert.AreEqual(componentDirty, EditorUtility.IsDirty(document));
            Assert.AreEqual(settingsDirty, EditorUtility.IsDirty(settings));
            Assert.AreEqual(themeDirty, EditorUtility.IsDirty(theme));
            Assert.AreSame(focused, root.panel.focusController.focusedElement);
            Assert.AreEqual("physical_indices", d["evidence"].Value<string>("path_domain"));
            Export("complete", d);
            ordinary.Armed = guarded.Armed = true;
            try
            {
                Data(new JObject { ["max_evaluations"] = 1 });
                Call(new JObject { ["element_path"] = "" });
            }
            finally
            {
                ordinary.Armed = guarded.Armed = false;
            }
            Assert.AreEqual(0, ordinary.Reads);
            Assert.AreEqual(0, guarded.Reads);
        }

        [UnityTest]
        public IEnumerator DisplayOpacityVisibilityAndZeroSizeAreDistinct()
        {
            var display = Box("Display");
            display.style.display = DisplayStyle.None;
            display.Add(Box("Suppressed"));
            var opacity = Box("Opacity");
            opacity.style.opacity = 0;
            var hidden = Box("Hidden");
            hidden.style.visibility = Visibility.Hidden;
            var visible = Box("VisibleOverride");
            visible.style.visibility = Visibility.Visible;
            hidden.Add(visible);
            root.Add(display);
            root.Add(opacity);
            root.Add(hidden);
            root.Add(Box("Empty", width: 0));
            yield return Ready();
            var d = Data();
            Assert.IsFalse(Node(d, "Suppressed").Value<bool>("displayed"));
            Assert.AreEqual("not_applicable", Node(d, "Suppressed").Value<string>("clipping"));
            Assert.AreEqual(0, Node(d, "Opacity").Value<double>("effective_opacity"));
            Assert.AreEqual("hidden", Node(d, "Hidden").Value<string>("visibility"));
            Assert.AreEqual("visible", Node(d, "VisibleOverride").Value<string>("visibility"));
            Assert.AreEqual("empty", Node(d, "Empty").Value<string>("layout_status"));
            Export("suppression", d);
            root.style.display = DisplayStyle.None;
            yield return Settle();
            Assert.AreNotEqual("unavailable", Data().Value<string>("status"), "Attached display:none root is not a missing panel.");
        }

        [UnityTest]
        public IEnumerator UssClipAndInlineOverrideUseComputedStyleAndPreserveAsset()
        {
            folder = "Assets/UI06_" + Guid.NewGuid().ToString("N");
            AssetDatabase.CreateFolder("Assets", folder.Substring(7));
            string path = folder + "/clip.uss";
            File.WriteAllText(path, ".clip { overflow: hidden; }");
            AssetDatabase.ImportAsset(path);
            var sheet = AssetDatabase.LoadAssetAtPath<StyleSheet>(path);
            var parent = Box("Clip", width: 100, height: 100);
            parent.AddToClassList("clip");
            parent.Add(Box("Partial", 80));
            parent.Add(Box("Full", 180));
            root.Add(parent);
            root.styleSheets.Add(sheet);
            yield return Ready();
            var bytes = File.ReadAllBytes(path);
            string guid = AssetDatabase.AssetPathToGUID(path);
            bool dirty = EditorUtility.IsDirty(sheet);
            var d = Data();
            Assert.AreEqual(StyleKeyword.Null, parent.style.overflow.keyword);
            Assert.AreEqual("hidden", Node(d, "Clip").Value<string>("overflow"));
            Assert.AreEqual("partial", Node(d, "Partial").Value<string>("clipping"));
            Assert.AreEqual("full", Node(d, "Full").Value<string>("clipping"));
            Assert.IsTrue(d["findings"].Any(f => f.Value<string>("code") == "outside_parent_content"));
            Export("uss-clipping", d);
            parent.style.overflow = Overflow.Visible;
            yield return Settle();
            var inline = parent.style.overflow;
            var visible = Data();
            Assert.AreEqual("not_clipped", Node(visible, "Partial").Value<string>("clipping"));
            Assert.AreEqual(inline, parent.style.overflow);
            CollectionAssert.AreEqual(bytes, File.ReadAllBytes(path));
            Assert.AreEqual(guid, AssetDatabase.AssetPathToGUID(path));
            Assert.AreEqual(dirty, EditorUtility.IsDirty(sheet));
            Export("overflow-visible", visible);
        }

        [UnityTest]
        public IEnumerator PhysicalViewportClipsAndScrollIsNotChanged()
        {
            var scroll = new ScrollView { name = "Scroll" };
            scroll.style.width = 100;
            scroll.style.height = 100;
            var child = Box("Outside", y: 150);
            scroll.Add(child);
            root.Add(scroll);
            yield return Ready();
            var offset = scroll.scrollOffset;
            var d = Data();
            Assert.Greater(d["nodes"].Count(), 3, "Physical viewport/internal controls must be inspected.");
            Assert.AreEqual("full", Node(d, "Outside").Value<string>("clipping"));
            Assert.AreEqual(offset, scroll.scrollOffset);
            Export("scroll-clipping", d);
        }

        [UnityTest]
        public IEnumerator SiblingOverlapIsCandidateAndNotAncestorOrTouchingPair()
        {
            var a = Box("A");
            var b = Box("B", 50);
            var edge = Box("Edge", 150);
            a.Add(Box("Child"));
            root.Add(a);
            root.Add(b);
            root.Add(edge);
            yield return Ready();
            var d = Data();
            var pairs = d["findings"].Where(f => f.Value<string>("code") == "overlap_candidate").ToArray();
            Assert.AreEqual(1, pairs.Length, d.ToString());
            Assert.AreEqual("candidate", pairs[0].Value<string>("severity"));
            Assert.AreEqual(3, d["traversal"].Value<int>("pairs_considered"));
            Export("overlap", d);
        }

        [UnityTest]
        public IEnumerator UnsupportedClipShapesAndProbeAreUnknown()
        {
            var clip = Box("Rounded", width: 100, height: 100);
            clip.style.overflow = Overflow.Hidden;
            clip.style.borderTopLeftRadius = 10;
            clip.Add(Box("Child", 80));
            root.Add(clip);
            yield return Ready();
            Assert.AreEqual("unknown", Node(Data(), "Child").Value<string>("clipping"));
            var helper = typeof(ManageUI).Assembly.GetType("MCPForUnity.Editor.Tools.UitoolkitLayoutDiagnostics");
            var field = helper?.GetField("ProbeAvailableForTests", BindingFlags.Static | BindingFlags.NonPublic);
            Assert.IsNotNull(field);
            field.SetValue(null, false);
            var d = Data();
            Assert.AreEqual("partial", d.Value<string>("status"));
            Assert.AreEqual("unknown", Node(d, "Child").Value<string>("clipping"));
            Export("unknown", d);
        }

        [UnityTest]
        public IEnumerator CapsReturnExplicitPartialAndEvaluationCategoriesSum()
        {
            for (int i = 0; i < 1100; i++)
                root.Add(Box("N" + i));
            yield return Ready();
            foreach (
                var options in new[]
                {
                    new JObject { ["max_nodes"] = 3 },
                    new JObject { ["max_depth"] = 0 },
                    new JObject { ["max_evaluations"] = 2 },
                    new JObject { ["max_characters"] = 1 },
                    new JObject { ["max_pairs"] = 0 },
                    new JObject { ["max_pairs"] = 1 },
                    new JObject { ["max_findings"] = 1 },
                }
            )
            {
                var d = Data(options);
                var t = d["traversal"];
                Assert.AreEqual("partial", d.Value<string>("status"), d.ToString());
                Assert.LessOrEqual(t.Value<int>("evaluations"), t.Value<int>("max_evaluations"));
                Assert.AreEqual(
                    t.Value<int>("evaluations"),
                    t.Value<int>("ancestor_inspections")
                        + t.Value<int>("node_admissions")
                        + t.Value<int>("child_fetches")
                        + t.Value<int>("style_probes")
                        + t.Value<int>("pairs_considered")
                );
                Assert.LessOrEqual(d["nodes"].Count(), t.Value<int>("max_nodes"));
                Assert.LessOrEqual(d["findings"].Count(), t.Value<int>("max_findings"));
                Export("partial-" + options.Properties().Single().Name + "-" + options.Properties().Single().Value.ToString(), d);
            }
            var max = Data(
                new JObject
                {
                    ["max_depth"] = 24,
                    ["max_nodes"] = 1000,
                    ["max_evaluations"] = 20000,
                    ["max_characters"] = 262144,
                    ["max_pairs"] = 20000,
                    ["max_findings"] = 2000,
                }
            );
            Assert.AreEqual(1000, max["nodes"].Count());
            Export("maximum", max);
        }

        [UnityTest]
        public IEnumerator LocalStringCapContinuesAndDeepTreeDoesNotRecurse()
        {
            var first = Box(new string('x', 200000));
            root.Add(first);
            root.Add(Box("Later"));
            var deep = first;
            for (int i = 0; i < 160; i++)
            {
                var next = Box("D" + i);
                deep.Add(next);
                deep = next;
            }
            yield return Ready();
            var d = Data();
            Assert.IsNotNull(Node(d, "Later"));
            Assert.IsTrue(d["nodes"].Any(n => n["truncated_fields"]?.Any() == true));
            Assert.IsTrue(d["traversal"]["reasons"].Any(r => r.Value<string>() == "max_depth"));
            Assert.Less(d["traversal"].Value<int>("characters"), 65536);
        }

        [UnityTest]
        public IEnumerator BorderContentBoxAndExternalAncestorAreObserved()
        {
            var clip = Box("ClipBox", width: 100, height: 100);
            clip.style.overflow = Overflow.Hidden;
            clip.style.borderLeftWidth = clip.style.borderRightWidth = 10;
            clip.style.paddingLeft = clip.style.paddingRight = 20;
            // Absolute offsets include the parent border; keep the entire child inside padding.
            clip.Add(Box("PaddingChild", 5, width: 10));
            root.Add(clip);
            yield return Ready();
            Assert.GreaterOrEqual(clip[0].worldBound.xMin, clip.worldBound.xMin + clip.resolvedStyle.borderLeftWidth);
            Assert.Less(
                clip[0].worldBound.xMax,
                clip.worldBound.xMin + clip.contentRect.xMin,
                "The child must be wholly outside content before expecting full clipping."
            );
            var paddingData = Data();
            GeometryDiagnostic("padding-box", paddingData, clip, clip[0]);
            Assert.AreEqual("not_clipped", Node(paddingData, "PaddingChild").Value<string>("clipping"));
            clip.style.unityOverflowClipBox = OverflowClipBox.ContentBox;
            yield return Settle();
            var contentData = Data();
            GeometryDiagnostic("content-box", contentData, clip, clip[0]);
            Assert.AreEqual("full", Node(contentData, "PaddingChild").Value<string>("clipping"));
            root.Add(Box("ExternalChild", 200));
            var wrapper = Box("ExternalClip", width: 100, height: 100);
            wrapper.style.overflow = Overflow.Hidden;
            root.RemoveFromHierarchy();
            window.rootVisualElement.Add(wrapper);
            wrapper.Add(root);
            yield return Settle();
            var d = Data();
            Assert.AreEqual("full", Node(d, "ExternalChild").Value<string>("clipping"));
            Assert.Greater(d["traversal"].Value<int>("ancestor_inspections"), 0);
            Export("clip-box-and-ancestor", d);
        }

        [UnityTest]
        public IEnumerator SourceUxmlBytesGuidAndDirtyStateRemainUnchanged()
        {
            folder = "Assets/UI06_" + Guid.NewGuid().ToString("N");
            AssetDatabase.CreateFolder("Assets", folder.Substring(7));
            string path = folder + "/source.uxml";
            File.WriteAllText(path, "<ui:UXML xmlns:ui=\"UnityEngine.UIElements\"><ui:VisualElement name=\"SourceBox\" /></ui:UXML>");
            AssetDatabase.ImportAsset(path);
            var asset = AssetDatabase.LoadAssetAtPath<VisualTreeAsset>(path);
            document.visualTreeAsset = asset;
            root = document.rootVisualElement;
            root.name = "Root";
            root.style.width = 600;
            root.style.height = 500;
            yield return Ready();
            var bytes = File.ReadAllBytes(path);
            string guid = AssetDatabase.AssetPathToGUID(path);
            bool dirty = EditorUtility.IsDirty(asset),
                sceneDirty = owner.scene.isDirty;
            Assert.IsNotNull(Node(Data(), "SourceBox"));
            CollectionAssert.AreEqual(bytes, File.ReadAllBytes(path));
            Assert.AreEqual(guid, AssetDatabase.AssetPathToGUID(path));
            Assert.AreEqual(dirty, EditorUtility.IsDirty(asset));
            Assert.AreEqual(sceneDirty, owner.scene.isDirty);
            Assert.AreSame(asset, document.visualTreeAsset);
        }

        [Test]
        public void NullCamelBudgetsUseDefaultsAndDuplicateTargetRefuses()
        {
            var d = Data(new JObject { ["maxDepth"] = JValue.CreateNull(), ["maxPairs"] = JValue.CreateNull() });
            Assert.AreEqual(10, d["traversal"].Value<int>("max_depth"));
            Assert.AreEqual(2000, d["traversal"].Value<int>("max_pairs"));
            var duplicate = new GameObject(owner.name);
            try
            {
                var r = Call(new JObject { ["target"] = owner.name });
                Assert.IsFalse(r.Value<bool>("success"));
                Assert.AreEqual("invalid_or_ambiguous_diagnostics_target", r.Value<string>("error"));
            }
            finally
            {
                UnityEngine.Object.DestroyImmediate(duplicate);
            }
        }

#if UNITY_2022_1_OR_NEWER
        [UnityTest]
        public IEnumerator RotatedAndNegativeClipTransformsRemainUnknown()
        {
            var clip = Box("TransformedClip", width: 100, height: 100);
            clip.style.overflow = Overflow.Hidden;
            clip.Add(Box("TransformedChild", 80));
            root.Add(clip);
            yield return Ready();
            clip.style.rotate = new Rotate(45);
            yield return Settle();
            Assert.AreEqual("unknown", Node(Data(), "TransformedChild").Value<string>("clipping"));
            clip.style.rotate = new Rotate(0);
            clip.style.scale = new Scale(new Vector3(-1, 1, 1));
            yield return Settle();
            Assert.AreEqual("unknown", Node(Data(), "TransformedChild").Value<string>("clipping"));
        }
#endif

        [UnityTest]
        public IEnumerator ExhaustedStyleProbeDoesNotPublishUnchargedObservations()
        {
            root.Add(Box("Child"));
            yield return Ready();
            int ancestors = Data()["traversal"].Value<int>("ancestor_inspections");
            var d = Data(new JObject { ["max_evaluations"] = ancestors * 2 + 1 });
            Assert.AreEqual(1, d["nodes"].Count());
            var node = d["nodes"][0];
            Assert.AreEqual(JTokenType.Null, node["bounds"].Type, "No geometry may be observed after style-probe admission fails.");
            Assert.AreEqual("unknown", node.Value<string>("visibility"));
            Assert.AreEqual(JTokenType.Null, node["displayed"].Type);
            Assert.AreEqual(JTokenType.Null, node["effective_opacity"].Type);
            Assert.AreEqual("unknown", node.Value<string>("clipping"));
            Assert.AreEqual(ancestors, d["traversal"].Value<int>("style_probes"));
            Export("exhausted-probe", d);
        }

        [TestCase("max__nodes")]
        [TestCase("MaxNodes")]
        [TestCase("maxnodes")]
        [TestCase("_max_nodes")]
        public void UnresolvedAliasSpellingsRefuseBeforeLookup(string key)
        {
            Assert.IsNull(new ToolParams(new JObject { [key] = false }).GetRaw("max_nodes"), "Fixture must use an unresolved alias.");
            var r = Call(new JObject { ["target"] = "MISSING_PRIVATE_SENTINEL", [key] = false });
            Assert.IsFalse(r.Value<bool>("success"));
            Assert.AreEqual("unsupported_diagnostics_parameter", r.Value<string>("error"));
            StringAssert.DoesNotContain("MISSING_PRIVATE_SENTINEL", r.ToString());
        }

        [UnityTest]
        public IEnumerator PositiveClippingSliverRemainsPartial()
        {
            var clip = Box("SliverClip", width: 100, height: 100);
            clip.style.overflow = Overflow.Hidden;
            clip.Add(Box("Control", 80, width: 30));
            var sliver = Box("Sliver", 100, width: 20);
            // Yoga rounds layout positions to pixels; a translation preserves the positive subpixel overlap.
#if UNITY_2022_1_OR_NEWER
            sliver.style.translate = new Translate(-0.005f, 0);
#else
            sliver.transform.position = new Vector3(-0.005f, 0, 0);
#endif
            clip.Add(sliver);
            root.Add(clip);
            yield return Ready();
            var d = Data();
            GeometryDiagnostic("sliver", d, clip, clip[0], clip[1]);
            float actualIntersection = clip.worldBound.xMax - sliver.worldBound.xMin;
            Assert.Greater(actualIntersection, 0, "The fixture must expose a real positive world-space intersection.");
            Assert.Less(actualIntersection, 0.01f, "The fixture must exercise the former clipping epsilon boundary.");
            Assert.AreEqual("partial", Node(d, "Control").Value<string>("clipping"), "Rectangular clipping prerequisite.");
            Assert.AreEqual("partial", Node(d, "Sliver").Value<string>("clipping"), "A positive intersection is not fully clipped.");
            Export("positive-sliver", d);
        }

#if UNITY_2022_1_OR_NEWER
        [UnityTest]
        public IEnumerator OrdinaryUnsupportedTransformCannotClaimKnownClipping()
        {
            var ordinary = Box("OrdinaryTransform");
            ordinary.style.rotate = new Rotate(45);
            root.Add(ordinary);
            root.Add(Box("Other", 20));
            yield return Ready();
            var d = Data();
            var node = Node(d, "OrdinaryTransform");
            int index = d["nodes"].ToList().IndexOf(node);
            Assert.AreEqual("unknown", node.Value<string>("clipping"));
            Assert.AreEqual("partial", d.Value<string>("status"));
            Assert.IsFalse(d["findings"].Any(f => f.Value<string>("code") == "outside_parent_content" && f.Value<int>("node_index") == index));
            Assert.IsTrue(d["findings"].Where(f => f.Value<string>("code") == "overlap_candidate").All(f => f.Value<bool>("clipping_unknown")));
            Export("ordinary-transform", d);
        }
#endif

#if UI06_LOOKALIKE_FIXTURE && !UNITY_6000_5_OR_NEWER
        [Test]
        public void ProjectPanelRendererLookalikeNeverRunsAtDiagnoseBoundary()
        {
            UnityEngine.Object.DestroyImmediate(document);
            document = null;
            var fake = owner.AddComponent<UnityEngine.UIElements.PanelRenderer>();
            fake.InitializeTree();
            // This is an actual unguarded adapter invocation, proving the trap can run.
            var adapter = UIComponentAdapter.Resolve(owner, "panel_renderer");
            Assert.IsNotNull(adapter.Root);
            Assert.Greater(fake.RegisterCalls, 0);
            Assert.Greater(fake.RootReads, 0);
            Assert.Greater(fake.ReceiverCalls, 0);
            fake.ResetCounters();
            foreach (string selection in new[] { "auto", "panel_renderer" })
            {
                var r = Call(new JObject { ["component_type"] = selection });
                Assert.IsFalse(r.Value<bool>("success"), r.ToString());
                Assert.AreEqual("unsupported_ui_component", r.Value<string>("error"));
                Assert.AreEqual(0, fake.RegisterCalls + fake.UnregisterCalls + fake.RootReads + fake.SourceReads + fake.PanelReads + fake.ReceiverCalls);
            }
        }
#endif
    }
}

#if UI06_LOOKALIKE_FIXTURE && !UNITY_6000_5_OR_NEWER
namespace UnityEngine.UIElements
{
    public class PanelRenderer : MonoBehaviour
    {
        public int RegisterCalls,
            UnregisterCalls,
            RootReads,
            SourceReads,
            PanelReads,
            ReceiverCalls;
        private VisualElement tree;

        public void InitializeTree() => tree = new VisualElement();

        private VisualTreeAsset source;
        private PanelSettings panel;
        public VisualTreeAsset visualTreeAsset
        {
            get
            {
                SourceReads++;
                return source;
            }
            set => source = value;
        }
        public PanelSettings panelSettings
        {
            get
            {
                PanelReads++;
                return panel;
            }
            set => panel = value;
        }
        public int sortingOrder { get; set; }
        private VisualElement Root
        {
            get
            {
                RootReads++;
                return tree;
            }
        }

        public void RegisterUIReloadCallback(Action<PanelRenderer, VisualElement, int> callback)
        {
            RegisterCalls++;
            callback(this, Root, 0);
            ReceiverCalls++;
        }

        public void UnregisterUIReloadCallback(Action<PanelRenderer, VisualElement, int> callback)
        {
            UnregisterCalls++;
        }

        public void ResetCounters() => RegisterCalls = UnregisterCalls = RootReads = SourceReads = PanelReads = ReceiverCalls = 0;
    }
}
#endif
