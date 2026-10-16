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
    public class UISnapshotIdentityTests
    {
        private const string Secret = "UI05_PRIVATE_SENTINEL_512f";
        private GameObject target;
        private string framework;
        private VisualElement tree;
        private EditorWindow window;
        private PanelSettings settings;
        private ThemeStyleSheet theme;

        private sealed class TestWindow : EditorWindow { }

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
            if (target != null)
                UnityEngine.Object.DestroyImmediate(target);
            if (settings != null)
                UnityEngine.Object.DestroyImmediate(settings);
            if (theme != null)
                UnityEngine.Object.DestroyImmediate(theme);
        }

        private IEnumerator Build(string engine)
        {
            framework = engine;
            target = new GameObject("UI05_" + Guid.NewGuid().ToString("N"), typeof(RectTransform), typeof(Canvas));
            if (engine == "ugui")
                yield break;
            var document = target.AddComponent<UIDocument>();
            settings = ScriptableObject.CreateInstance<PanelSettings>();
            theme = ScriptableObject.CreateInstance<ThemeStyleSheet>();
            settings.themeStyleSheet = theme;
            document.panelSettings = settings;
            tree = document.rootVisualElement;
            tree.Clear();
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
            if (framework == "ugui")
            {
                Canvas.ForceUpdateCanvases();
                yield break;
            }
            for (int i = 0; i < 12; i++)
            {
                window.Repaint();
                yield return null;
                if (i >= 2 && tree.panel != null && tree.worldBound.width > 0 && tree.worldBound.height > 0)
                    yield break;
            }
            Assert.IsNotNull(tree.panel);
            Assert.Greater(tree.worldBound.width, 0);
            Assert.Greater(tree.worldBound.height, 0);
        }

        private object Row(string name, float x = 0)
        {
            if (framework == "ugui")
            {
                var go = new GameObject(name, typeof(RectTransform));
                go.transform.SetParent(target.transform, false);
                ((RectTransform)go.transform).sizeDelta = new Vector2(100, 20);
                ((RectTransform)go.transform).anchoredPosition = new Vector2(x, 0);
                go.AddComponent(UnityTypeResolver.ResolveComponent("UnityEngine.UI.Toggle"));
                return go;
            }
            var row = new Toggle { name = name };
            row.hierarchy.Clear();
            row.style.position = Position.Absolute;
            row.style.left = x;
            row.style.top = 0;
            row.style.width = 100;
            row.style.height = 20;
            tree.Add(row);
            return row;
        }

        private void First(object row)
        {
            if (row is GameObject go)
                go.transform.SetAsFirstSibling();
            else
                tree.Insert(0, (VisualElement)row);
        }

        private static void Remove(object row)
        {
            if (row is GameObject go)
                UnityEngine.Object.DestroyImmediate(go);
            else
                ((VisualElement)row).RemoveFromHierarchy();
        }

        private static void SetValue(object row, bool value)
        {
            if (row is GameObject go)
            {
                var control = go.GetComponent(UnityTypeResolver.ResolveComponent("UnityEngine.UI.Toggle"));
                control.GetType().GetProperty("isOn").SetValue(control, value);
            }
            else
                ((Toggle)row).SetValueWithoutNotify(value);
        }

        private static JArray Map(params string[] pathIds)
        {
            var result = new JArray();
            for (int i = 0; i < pathIds.Length; i += 2)
                result.Add(new JObject { ["element_path"] = pathIds[i], ["id"] = pathIds[i + 1] });
            return result;
        }

        private JObject Capture(JToken map = null, int maxNodes = 200)
        {
            var request = new JObject
            {
                ["framework"] = framework,
                ["target"] = target.GetInstanceIDCompat(),
                ["max_nodes"] = maxNodes,
            };
            if (map != null)
                request["identity_map"] = map;
            return JObject.FromObject(CaptureUISnapshot.HandleCommand(request));
        }

        private JObject Data(JToken map)
        {
            var response = Capture(map);
            Assert.IsTrue(response.Value<bool>("success"), response.ToString());
            var data = (JObject)response["data"];
            Assert.AreEqual(3, data.Value<int>("schema_version"));
            Assert.AreEqual("explicit_path_map_v1", data["evidence"].Value<string>("identity_method"));
            Assert.AreEqual("recognized_input_ownership_v1", data["evidence"].Value<string>("input_privacy"));
            return data;
        }

        private static JToken Item(JObject data, string id) =>
            data["nodes"].Single(n => n.Value<string>("id").EndsWith("/@id:" + Uri.EscapeDataString(id), StringComparison.Ordinal));

        private void Export(string name, JObject data) =>
            File.WriteAllText(Path.Combine(Path.GetTempPath(), "ui05-" + framework + "-" + name + ".json"), data.ToString());

        private IEnumerator RepeatedRows(string engine)
        {
            yield return Build(engine);
            Row("Row");
            Row("Row", 120);
            yield return Settle();
            Assert.IsFalse(Capture().Value<bool>("success"), "Default duplicate refusal is preserved.");
            var data = Data(Map("/0", "item-A", "/1", "item-B"));
            Assert.IsNotNull(Item(data, "item-A"));
            Assert.IsNotNull(Item(data, "item-B"));
        }

        [UnityTest]
        public IEnumerator UguiTwoAssignedRowsHaveExplicitIdentities() => RepeatedRows("ugui");

        [UnityTest]
        public IEnumerator ToolkitTwoAssignedRowsHaveExplicitIdentities() => RepeatedRows("uitoolkit");

        private IEnumerator Partial(string engine, bool ambiguous)
        {
            yield return Build(engine);
            Row("Row");
            Row("Row", 120);
            if (ambiguous)
                Row("Row", 240);
            yield return Settle();
            var response = Capture(Map("/0", "item-A"));
            Assert.AreEqual(!ambiguous, response.Value<bool>("success"), response.ToString());
            if (!ambiguous)
            {
                Assert.AreEqual(3, response["data"]["nodes"].Count());
                Assert.IsNotNull(Item((JObject)response["data"], "item-A"));
            }
            else
                Assert.IsNull(response["data"]);
        }

        [UnityTest]
        public IEnumerator UguiOneAssignedAndOneUnmappedDuplicateCoexist() => Partial("ugui", false);

        [UnityTest]
        public IEnumerator ToolkitOneAssignedAndOneUnmappedDuplicateCoexist() => Partial("uitoolkit", false);

        [UnityTest]
        public IEnumerator UguiTwoUnmappedDuplicatesStillRefuse() => Partial("ugui", true);

        [UnityTest]
        public IEnumerator ToolkitTwoUnmappedDuplicatesStillRefuse() => Partial("uitoolkit", true);

        private IEnumerator Namespace(string engine)
        {
            yield return Build(engine);
            Row("Row");
            Row("@id:item-A", 120);
            yield return Settle();
            var data = Data(Map("/0", "item-A"));
            Assert.AreEqual(3, data["nodes"].Select(n => n.Value<string>("id")).Distinct().Count());
            Assert.IsNotNull(Item(data, "item-A"));
            var missing = Capture(Map("/0", "item-A", "/1/0", "unused"));
            Assert.IsFalse(missing.Value<bool>("success"), "A sibling probe must not make a nonexistent assignment valid.");
            Assert.IsNull(missing["data"]);
        }

        [UnityTest]
        public IEnumerator UguiReservedNamespaceAndUnusedPathAreSafe() => Namespace("ugui");

        [UnityTest]
        public IEnumerator ToolkitReservedNamespaceAndUnusedPathAreSafe() => Namespace("uitoolkit");

        private IEnumerator Changes(string engine)
        {
            yield return Build(engine);
            object a = Row("Row"),
                b = Row("Row", 120);
            yield return Settle();
            var before = Data(Map("/0", "item-A", "/1", "item-B"));
            First(b);
            var reordered = Data(Map("/0", "item-B", "/1", "item-A"));
            CollectionAssert.AreEquivalent(before["nodes"].Select(n => n.Value<string>("id")), reordered["nodes"].Select(n => n.Value<string>("id")));
            Assert.AreEqual(0, Item(before, "item-A").Value<int>("sibling_index"));
            Assert.AreEqual(1, Item(reordered, "item-A").Value<int>("sibling_index"));
            Export("reorder-before", before);
            Export("reorder-after", reordered);
            Remove(a);
            a = Row("Row");
            yield return Settle();
            var recreated = Data(Map("/0", "item-B", "/1", "item-A"));
            Export("recreate-before", reordered);
            Export("recreate-after", recreated);
            Assert.IsTrue(JToken.DeepEquals(reordered, recreated), "Same-key recreation with same observations must retain identity.");
            SetValue(a, true);
            var changed = Data(Map("/0", "item-B", "/1", "item-A"));
            Assert.IsTrue(Item(changed, "item-A")["state"]["toggle"].Value<bool>("value"));
            var left = (JObject)recreated.DeepClone();
            var right = (JObject)changed.DeepClone();
            foreach (var n in left["nodes"])
                ((JObject)n).Remove("state");
            foreach (var n in right["nodes"])
                ((JObject)n).Remove("state");
            Assert.IsTrue(JToken.DeepEquals(left, right), "Only actual item state should change.");
            Export("state-before", recreated);
            Export("state-after", changed);
            var rebound = Data(Map("/0", "item-B", "/1", "item-C"));
            Assert.IsFalse(rebound["nodes"].Any(n => n.Value<string>("id").EndsWith("/@id:item-A", StringComparison.Ordinal)));
            Assert.IsNotNull(Item(rebound, "item-C"));
            Export("rebind-before", changed);
            Export("rebind-after", rebound);
        }

        [UnityTest]
        public IEnumerator UguiReorderRecreateStateAndRebindExportActualSnapshots() => Changes("ugui");

        [UnityTest]
        public IEnumerator ToolkitReorderRecreateStateAndRebindExportActualSnapshots() => Changes("uitoolkit");

        [UnityTest]
        public IEnumerator ToolkitMappedInputNeverReadsActualOwnedGetter()
        {
            yield return Build("uitoolkit");
            var row = (VisualElement)Row("Row");
            Row("Row", 120);
            var field = new TextField { name = "Input", value = Secret };
            var guarded = new GetterCounter { name = "Guarded", text = Secret };
            field.hierarchy.Add(guarded);
            row.Add(field);
            var ordinary = new GetterCounter { name = "Public", text = "Public" };
            tree.Add(ordinary);
            yield return Settle();
            guarded.Armed = ordinary.Armed = true;
            JObject data;
            try
            {
                data = Data(Map("/0", "item-A", "/1", "item-B"));
            }
            finally
            {
                guarded.Armed = ordinary.Armed = false;
            }
            Assert.AreEqual(0, guarded.Reads);
            Assert.Greater(ordinary.Reads, 0);
            StringAssert.DoesNotContain(Secret, data.ToString());
            Export("privacy", data);
            guarded.Reads = 0;
            guarded.Armed = true;
            JObject refused;
            try
            {
                refused = Capture(Map("/0", "item-A", "/1", "item-B", "/999", "unused"));
            }
            finally
            {
                guarded.Armed = false;
            }
            Assert.IsFalse(refused.Value<bool>("success"));
            Assert.IsNull(refused["data"]);
            Assert.AreEqual(0, guarded.Reads, "Unused-map refusal must still never read owned text.");
            StringAssert.DoesNotContain(Secret, refused.ToString());
            row.tooltip = "Before";
            var mapped = Item(data, "item-A").Value<string>("id");
            var mutation = JObject.FromObject(
                ManageUI.HandleCommand(
                    new JObject
                    {
                        ["action"] = "modify_visual_element",
                        ["target"] = target.GetInstanceIDCompat().ToString(),
                        ["element_id"] = mapped,
                        ["tooltip"] = "Not applied",
                    }
                )
            );
            Assert.IsFalse(mutation.Value<bool>("success"), "Mapped IDs are not mutation handles.");
            Assert.AreEqual("Before", row.tooltip);
            Assert.AreEqual("Public", ordinary.text);
        }

        [UnityTest]
        public IEnumerator UguiMappedInputPreservesRendererPrivacy()
        {
            yield return Build("ugui");
            var row = (GameObject)Row("Row");
            Row("Row", 120);
            var owner = new GameObject("Input", typeof(RectTransform));
            owner.transform.SetParent(row.transform, false);
            var display = new GameObject("Renderer", typeof(RectTransform));
            display.transform.SetParent(owner.transform, false);
            var text = display.AddComponent(UnityTypeResolver.ResolveComponent("UnityEngine.UI.Text"));
            var input = owner.AddComponent(UnityTypeResolver.ResolveComponent("UnityEngine.UI.InputField"));
            input.GetType().GetProperty("textComponent").SetValue(input, text);
            owner.SetActive(false);
            text.GetType().GetProperty("text").SetValue(text, Secret);
            var data = Data(Map("/0", "item-A", "/1", "item-B"));
            StringAssert.DoesNotContain(Secret, data.ToString());
            Assert.IsTrue(data["nodes"].Any(n => n["state"]?["input"] != null));
            Export("privacy", data);
        }

        public static IEnumerable InvalidMaps()
        {
            int caseIndex = 0;
            foreach (
                string json in new[]
                {
                    "null",
                    "[]",
                    "{}",
                    "[null]",
                    "[{\"element_path\":\"\",\"id\":\"A\"}]",
                    "[{\"element_path\":\"/00\",\"id\":\"A\"}]",
                    "[{\"element_path\":\"/0\",\"id\":\"A/B\"}]",
                    "[{\"element_path\":\"/0\",\"id\":\"A\",\"extra\":1}]",
                    "[{\"element_path\":\"/0\",\"id\":true}]",
                    "[{\"element_path\":\"/0\",\"id\":\"A\"},{\"element_path\":\"/0\",\"id\":\"B\"}]",
                    "[{\"element_path\":\"/0\",\"id\":\"A\"},{\"element_path\":\"/1\",\"id\":\"A\"}]",
                }
            )
                yield return new TestCaseData(json).SetName("InvalidMap_" + caseIndex++);
            yield return new TestCaseData(Map("/0", new string('x', 129)).ToString());
            yield return new TestCaseData(Map("/1000", "A").ToString());
            yield return new TestCaseData(Map(string.Concat(Enumerable.Repeat("/0", 128)), "A").ToString());
            yield return new TestCaseData(Map("/0", "A B").ToString());
            yield return new TestCaseData(Map("/0", "").ToString()).SetName("InvalidMap_EmptyId");
            yield return new TestCaseData(Map("/0", "\uD55C").ToString()).SetName("InvalidMap_UnicodeId");
            yield return new TestCaseData(CharacterMap(65537).ToString()).SetName("InvalidMap_CharacterOverflow");
        }

        [TestCaseSource(nameof(InvalidMaps))]
        public void InvalidMapRejectsBeforeTargetLookup(string json)
        {
            var response = JObject.FromObject(
                CaptureUISnapshot.HandleCommand(
                    new JObject
                    {
                        ["framework"] = "ugui",
                        ["target"] = "UI05_MISSING_TARGET",
                        ["identity_map"] = JToken.Parse(json),
                    }
                )
            );
            Assert.IsFalse(response.Value<bool>("success"));
            StringAssert.Contains("identity_map", response.ToString());
            StringAssert.DoesNotContain("UI05_MISSING_TARGET", response.ToString());
        }

        private static JArray CharacterMap(int characters)
        {
            var result = new JArray();
            string prefix = string.Concat(Enumerable.Repeat("/999", 126)) + "/";
            for (int i = 0; i < 104; i++)
                result.Add(new JObject { ["element_path"] = prefix + i, ["id"] = "i" + i.ToString("D3") + "_" + new string('x', 123) });
            int excess = result.Sum(n => n.Value<string>("element_path").Length + n.Value<string>("id").Length) - characters;
            Assert.GreaterOrEqual(excess, 0);
            foreach (var entry in result)
            {
                string id = entry.Value<string>("id");
                int remove = Math.Min(excess, id.Length - 5);
                entry["id"] = id.Substring(0, id.Length - remove);
                excess -= remove;
            }
            Assert.AreEqual(0, excess);
            return result;
        }

        [TestCase(999, true)]
        [TestCase(1000, false)]
        public void AssignmentCountCapIsExact(int count, bool success)
        {
            var values = new JArray(Enumerable.Range(0, count).Select(i => new JObject { ["element_path"] = "/" + i, ["id"] = "item" + i }));
            AssertParser(values, success);
        }

        [TestCase(65536, true)]
        [TestCase(65537, false)]
        public void SharedCharacterCapIsExact(int count, bool success) => AssertParser(CharacterMap(count), success);

        private static void AssertParser(JArray values, bool success)
        {
            var type = typeof(CaptureUISnapshot).Assembly.GetType("MCPForUnity.Editor.Tools.UISnapshotIdentityMap");
            Assert.IsNotNull(type, "New parser facility; baseline absence is recorded separately.");
            var parse = type.GetMethod("Parse", BindingFlags.Static | BindingFlags.NonPublic);
            if (success)
                Assert.DoesNotThrow(() => parse.Invoke(null, new object[] { values, 1000 }));
            else
                Assert.Throws<TargetInvocationException>(() => parse.Invoke(null, new object[] { values, 1000 }));
        }

        [Test]
        public void AssignmentCountCannotExceedNonRootNodeBudget()
        {
            var response = JObject.FromObject(
                CaptureUISnapshot.HandleCommand(
                    new JObject
                    {
                        ["framework"] = "ugui",
                        ["target"] = "UI05_MISSING_TARGET",
                        ["max_nodes"] = 1,
                        ["identity_map"] = Map("/0", "A"),
                    }
                )
            );
            Assert.IsFalse(response.Value<bool>("success"));
            StringAssert.Contains("identity_map", response.ToString());
            StringAssert.DoesNotContain("UI05_MISSING_TARGET", response.ToString());
        }

        [Test]
        public void IdentityLookupDoesNotMarkAssignmentsVisited()
        {
            var type = typeof(CaptureUISnapshot).Assembly.GetType("MCPForUnity.Editor.Tools.UISnapshotIdentityMap");
            Assert.IsNotNull(type, "Future narrow parser facility, not behavioral getter evidence.");
            const BindingFlags flags = BindingFlags.Static | BindingFlags.Instance | BindingFlags.NonPublic;
            var map = type.GetMethod("Parse", flags).Invoke(null, new object[] { Map("/0", "A"), 200 });
            var args = new object[] { "/0", null };
            Assert.IsTrue((bool)type.GetMethod("TryGetSegment", flags).Invoke(map, args));
            Assert.AreEqual("@id:A", args[1]);
            Assert.Throws<TargetInvocationException>(() => type.GetMethod("EnsureAllVisited", flags).Invoke(map, null));
            type.GetMethod("MarkVisited", flags).Invoke(map, new object[] { "/0" });
            Assert.DoesNotThrow(() => type.GetMethod("EnsureAllVisited", flags).Invoke(map, null));
        }
    }
}
