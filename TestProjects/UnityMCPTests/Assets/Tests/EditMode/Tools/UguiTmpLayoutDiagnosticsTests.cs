using System;
using System.Collections;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Security.Cryptography;
using System.Text;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Editor.Tools;
using MCPForUnity.Runtime.Helpers;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEditor;
using UnityEngine;
using UnityEngine.TestTools;

namespace MCPForUnityTests.Editor.Tools
{
    [Explicit("Requires a fresh isolated TMP fixture and supplied font-input/UI08.ttf.")]
    [Parallelizable(ParallelScope.None)]
    public class UguiTmpLayoutDiagnosticsTests
    {
        private GameObject root;
        private Type textType,
            baseText,
            fontType,
            settingsType;
        private ScriptableObject settings,
            font,
            fallback;
        private object oldSettings;
        private FieldInfo settingsSlot;
        private string nativeFontPath;
        private readonly List<UnityEngine.Object> owned = new List<UnityEngine.Object>();
        private int renderCallbacks;
        private int textChangedCallbacks;
        private Delegate renderHandler;
        private Component armed;
        private object textChangedEvent;
        private Action<UnityEngine.Object> textChangedHandler;
        private Type resourceManager;
        private IDictionary[] fontRegistries;
        private IList fontCallbacks;
        private object[] originalFontCallbacks;
        private bool settingsInstalled;

        [SetUp]
        public void SetUp()
        {
            renderCallbacks = 0;
            textChangedCallbacks = 0;
            renderHandler = null;
            armed = null;
            textChangedEvent = null;
            textChangedHandler = null;
            settingsInstalled = false;
            resourceManager = null;
            fontRegistries = null;
            fontCallbacks = null;
            originalFontCallbacks = null;
            var assembly = AppDomain.CurrentDomain.GetAssemblies().Single(a => a.GetName().Name == "Unity.TextMeshPro");
            textType = assembly.GetType("TMPro.TextMeshProUGUI", true);
            baseText = assembly.GetType("TMPro.TMP_Text", true);
            fontType = assembly.GetType("TMPro.TMP_FontAsset", true);
            settingsType = assembly.GetType("TMPro.TMP_Settings", true);
            // All static reference changes are fixture setup in QA's fresh owned Editor; no user asset/prefs is edited.
            settingsSlot = settingsType.GetField("s_Instance", BindingFlags.NonPublic | BindingFlags.Static);
            oldSettings = settingsSlot.GetValue(null);
            Assert.AreEqual(
                0,
                UnityEngine.Resources.FindObjectsOfTypeAll(fontType).Length,
                "Fresh owned fixture refuses pre-existing TMP font assets before creating fonts or changing settings."
            );
            resourceManager = assembly.GetType("TMPro.TMP_ResourceManager", true);
            fontRegistries = new[] { "s_FontAssetReferences", "s_FontAssetNameReferenceLookup", "s_FontAssetFamilyNameAndStyleReferenceLookup" }
                .Select(name => (IDictionary)resourceManager.GetField(name, BindingFlags.NonPublic | BindingFlags.Static).GetValue(null))
                .ToArray();
            foreach (var registry in fontRegistries)
                Assert.AreEqual(0, registry.Count, "Owned font setup refuses existing TMP registry entries before changing any font or settings.");
            fontCallbacks = (IList)fontType.GetField("s_CallbackInstances", BindingFlags.NonPublic | BindingFlags.Static).GetValue(null);
            Assert.LessOrEqual(fontCallbacks.Count, 4096, "Bounded owned-font callback inventory prerequisite.");
            var weakFontType = typeof(WeakReference<>).MakeGenericType(fontType);
            Assert.AreEqual(typeof(List<>).MakeGenericType(weakFontType), fontCallbacks.GetType(), "Known installed weak-font callback list required.");
            Assert.IsTrue(fontCallbacks.Cast<object>().All(entry => entry != null && entry.GetType() == weakFontType));
            originalFontCallbacks = fontCallbacks.Cast<object>().ToArray();
            settings = ScriptableObject.CreateInstance(settingsType);
            settings.hideFlags = HideFlags.HideAndDontSave;
            // These letter-only CJK cases do not test a punctuation prohibition policy.
            foreach (string name in new[] { "m_leadingCharacters", "m_followingCharacters" })
            {
                var characters = new TextAsset(string.Empty) { hideFlags = HideFlags.HideAndDontSave };
                owned.Add(characters);
                settingsType.GetField(name, BindingFlags.NonPublic | BindingFlags.Instance).SetValue(settings, characters);
            }
            var version = settingsType.GetField("s_CurrentAssetVersion", BindingFlags.NonPublic | BindingFlags.Static);
            var assetVersion = settingsType.GetField("m_AssetVersion", BindingFlags.NonPublic | BindingFlags.Instance);
            if (version != null && assetVersion != null)
                assetVersion.SetValue(settings, version.GetValue(null));
            settingsSlot.SetValue(null, settings);
            settingsInstalled = true;
            nativeFontPath = Path.GetFullPath(Path.Combine(Application.dataPath, "..", "..", "font-input", "UI08.ttf"));
            Assert.IsTrue(
                File.Exists(nativeFontPath),
                "Supply a compatible proportional Latin/CJK font file at the declared fixture font-input/UI08.ttf path; missing input is a setup failure."
            );
            font = MakeFont("UI08OwnedPrimary", " ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789.,!?:/<>-=\"\n");
            fallback = MakeFont("UI08OwnedFallback", " 한국어漢字");
            var list = fontType.GetProperty("fallbackFontAssetTable").GetValue(font) as IList;
            if (list == null)
            {
                list = (IList)Activator.CreateInstance(typeof(List<>).MakeGenericType(fontType));
                fontType.GetProperty("fallbackFontAssetTable").SetValue(font, list);
            }
            list.Add(fallback);
            settingsType.GetField("m_defaultFontAsset", BindingFlags.NonPublic | BindingFlags.Instance).SetValue(settings, font);
            root = new GameObject("UI08OwnedRoot", typeof(RectTransform), typeof(Canvas));
            root.GetComponent<Canvas>().renderMode = RenderMode.WorldSpace;
            ((RectTransform)root.transform).sizeDelta = new Vector2(500, 300);
        }

        private ScriptableObject MakeFont(string name, string glyphs)
        {
            var create = fontType
                .GetMethods(BindingFlags.Public | BindingFlags.Static)
                .Single(method =>
                    method.Name == "CreateFontAsset" && method.GetParameters().Length == 7 && method.GetParameters()[0].ParameterType == typeof(string)
                );
            var renderMode = create.GetParameters()[4].ParameterType;
            Assert.AreEqual("UnityEngine.TextCore.LowLevel.GlyphRenderMode", renderMode.FullName, "Known installed TMP file-path overload required.");
            var value = (ScriptableObject)create.Invoke(null, new object[] { nativeFontPath, 0, 90, 9, Enum.Parse(renderMode, "SDFAA"), 1024, 1024 });
            Assert.IsNotNull(value, "The supplied fixture font face must be loadable by TMP before glyph seeding.");
            value.name = name + Guid.NewGuid().ToString("N");
            owned.Add(value);
            var add = fontType.GetMethod("TryAddCharacters", new[] { typeof(string), typeof(string).MakeByRefType(), typeof(bool) });
            Assert.IsNotNull(add, "Known installed TMP glyph-seeding overload required.");
            var arguments = new object[] { glyphs.Replace("\n", ""), null, false };
            bool added = (bool)add.Invoke(value, arguments);
            string missing = (string)arguments[1] ?? "";
            Assert.IsTrue(
                added,
                "Owned font must contain every requested fixture glyph. Missing/unadded: "
                    + missing
                    + " ("
                    + string.Join(" ", missing.Select(character => "U+" + ((int)character).ToString("X4")))
                    + ")."
            );
            fontType.GetProperty("atlasPopulationMode").SetValue(value, Enum.ToObject(fontType.GetProperty("atlasPopulationMode").PropertyType, 0));
            return value;
        }

        [TearDown]
        public void TearDown()
        {
            var failures = new List<Exception>();
            void Attempt(Action action)
            {
                try
                {
                    action();
                }
                catch (Exception error)
                {
                    failures.Add(error);
                }
            }
            var ownedFonts = owned.Where(value => value != null && value.GetType() == fontType).ToArray();
            try
            {
                Attempt(() =>
                {
                    if (armed != null && renderHandler != null)
                        textType.GetEvent("OnPreRenderText").RemoveEventHandler(armed, renderHandler);
                });
                Attempt(() =>
                {
                    if (textChangedEvent != null)
                        textChangedEvent.GetType().GetMethod("Remove").Invoke(textChangedEvent, new object[] { textChangedHandler });
                });
                var helper = typeof(ManageUGUI).Assembly.GetType("MCPForUnity.Editor.Tools.UguiTmpLayoutDiagnostics");
                Attempt(() => helper?.GetField("InventoryForTests", BindingFlags.Static | BindingFlags.NonPublic)?.SetValue(null, null));
                Attempt(() => helper?.GetField("ObservationForTests", BindingFlags.Static | BindingFlags.NonPublic)?.SetValue(null, null));
                Attempt(() =>
                {
                    if (root != null)
                        UnityEngine.Object.DestroyImmediate(root);
                });
                // Installed RemoveFontAsset removes lookup keys blindly: admit only currently owned values.
                // Never clear/rebuild caches, or delete a key that now belongs to a foreign font.
                if (resourceManager != null)
                    foreach (var value in ownedFonts)
                        Attempt(() =>
                        {
                            foreach (var registry in fontRegistries.Skip(1))
                            {
                                Assert.LessOrEqual(registry.Count, 4096, "Bounded teardown font lookup prerequisite.");
                                foreach (object current in registry.Values)
                                    if (!ownedFonts.Any(candidate => ReferenceEquals(candidate, current)))
                                        throw new InvalidOperationException("Foreign font lookup preserved; owned removal refused.");
                            }
                            resourceManager.GetMethod("RemoveFontAsset", new[] { fontType }).Invoke(null, new object[] { value });
                        });
                Attempt(() =>
                {
                    if (fontCallbacks == null || originalFontCallbacks == null)
                        return;
                    Assert.LessOrEqual(fontCallbacks.Count, 4096 + ownedFonts.Length);
                    for (int i = fontCallbacks.Count - 1; i >= 0; i--)
                    {
                        object weak = fontCallbacks[i];
                        var target = new object[] { null };
                        if ((bool)weak.GetType().GetMethod("TryGetTarget").Invoke(weak, target) && ownedFonts.Any(value => ReferenceEquals(value, target[0])))
                        {
                            if (i < originalFontCallbacks.Length)
                                fontCallbacks[i] = originalFontCallbacks[i];
                            else
                                fontCallbacks.RemoveAt(i);
                        }
                    }
                });
                foreach (var value in owned.AsEnumerable().Reverse())
                    Attempt(() =>
                    {
                        if (value is RenderTexture texture)
                            foreach (var cameraObject in owned.OfType<GameObject>())
                            {
                                if (cameraObject == null)
                                    continue;
                                var camera = cameraObject.GetComponent<Camera>();
                                if (camera != null && camera.targetTexture == texture)
                                    camera.targetTexture = null;
                            }
                        if (value != null)
                            UnityEngine.Object.DestroyImmediate(value);
                    });
                owned.Clear();
            }
            finally
            {
                Attempt(() =>
                {
                    if (!settingsInstalled)
                        return;
                    if (!ReferenceEquals(settingsSlot.GetValue(null), settings))
                        throw new InvalidOperationException("Foreign TMP settings replacement preserved; restoration refused.");
                    settingsSlot.SetValue(null, oldSettings);
                });
                Attempt(() =>
                {
                    if (settings != null)
                        UnityEngine.Object.DestroyImmediate(settings);
                });
            }
            if (fontRegistries != null && ownedFonts.Length != 0)
                foreach (var registry in fontRegistries)
                    Attempt(() => Assert.AreEqual(0, registry.Count, "Only owned font entries were admitted and must be removed immediately."));
            if (originalFontCallbacks != null)
                Attempt(() =>
                    CollectionAssert.AreEqual(
                        originalFontCallbacks,
                        fontCallbacks.Cast<object>().ToArray(),
                        "Preserve all original weak callback entries, including dead slots reused by owned fonts."
                    )
                );
            if (failures.Count != 0)
                throw new AggregateException("Owned TMP fixture cleanup failed after attempting all cleanup steps.", failures);
        }

        private Component Text(string content, float width = 240, float height = 100, bool render = true)
        {
            var go = new GameObject("UI08Label", typeof(RectTransform));
            go.transform.SetParent(root.transform, false);
            ((RectTransform)go.transform).sizeDelta = new Vector2(width, height);
            var text = go.AddComponent(textType);
            Set(text, "font", font);
            Set(text, "fontSize", 32f);
            Set(text, "text", content);
            Set(text, "enableAutoSizing", false);
            Set(text, "enableWordWrapping", true);
            Set(text, "color", Color.red);
            if (render)
                Render(text);
            return text;
        }

        private static void Set(Component text, string name, object value) => text.GetType().GetProperty(name).SetValue(text, value);

        private static T Get<T>(Component text, string name) => (T)text.GetType().GetProperty(name).GetValue(text);

        private void Render(Component text)
        {
            Canvas.ForceUpdateCanvases();
            textType.GetMethod("ForceMeshUpdate").Invoke(text, new object[] { true, true });
        }

        private JObject Call(JObject options = null)
        {
            var request = options ?? new JObject();
            request["action"] = "diagnose_text";
            if (request["target"] == null)
                request["target"] = root.GetInstanceIDCompat();
            return JObject.FromObject(ManageUGUI.HandleCommand(request));
        }

        private JObject Data(JObject response)
        {
            Assert.IsTrue(response.Value<bool>("success"), response.ToString());
            return (JObject)response["data"];
        }

        private JObject Node(JObject data, Component text) =>
            (JObject)data["nodes"].Single(n => n.Value<int>("instance_id") == text.gameObject.GetInstanceIDCompat());

        private void Export(string name, JObject value) =>
            File.WriteAllText(Path.Combine(Path.GetTempPath(), "ui08-" + name + ".json"), value.ToString(Formatting.None));

        private void Rendered(object value) => renderCallbacks++;

        private void Arm(Component text)
        {
            armed = text;
            var evt = textType.GetEvent("OnPreRenderText");
            renderHandler = Delegate.CreateDelegate(
                evt.EventHandlerType,
                this,
                GetType().GetMethod("Rendered", BindingFlags.Instance | BindingFlags.NonPublic)
            );
            evt.AddEventHandler(text, renderHandler);
            var manager = textType.Assembly.GetType("TMPro.TMPro_EventManager", true);
            textChangedEvent = manager.GetField("TEXT_CHANGED_EVENT", BindingFlags.Public | BindingFlags.Static).GetValue(null);
            textChangedHandler = _ => textChangedCallbacks++;
            textChangedEvent.GetType().GetMethod("Add").Invoke(textChangedEvent, new object[] { textChangedHandler });
        }

        private string State(Component text)
        {
            var all = new StringBuilder(EditorJsonUtility.ToJson(text));
            all.Append(EditorJsonUtility.ToJson(settings));
            all.Append(EditorJsonUtility.ToJson(text.GetComponent<RectTransform>()));
            all.Append(EditorUtility.IsDirty(text)).Append(EditorUtility.IsDirty(font)).Append(EditorUtility.IsDirty(fallback));
            foreach (var asset in new[] { font, fallback })
            {
                all.Append(EditorJsonUtility.ToJson(asset));
                foreach (Texture2D atlas in (Array)fontType.GetProperty("atlasTextures").GetValue(asset))
                {
                    Assert.LessOrEqual(atlas.width * atlas.height, 1024 * 1024);
                    all.Append(Convert.ToBase64String(SHA256.Create().ComputeHash(atlas.GetRawTextureData())));
                }
            }
            // Compare actual shared material properties and the render mesh buffers, not only references.
            all.Append(EditorJsonUtility.ToJson(Get<Material>(text, "fontSharedMaterial")));
            foreach (var asset in new[] { font, fallback })
                all.Append(EditorJsonUtility.ToJson((Material)fontType.GetProperty("material").GetValue(asset)));
            var mesh = Get<Mesh>(text, "mesh");
            foreach (var point in mesh.vertices)
                all.Append(point.ToString("R"));
            foreach (var point in mesh.normals)
                all.Append(point.ToString("R"));
            foreach (var point in mesh.tangents)
                all.Append(point.ToString("R"));
            foreach (var color in mesh.colors32)
                all.Append(color.r).Append(',').Append(color.g).Append(',').Append(color.b).Append(',').Append(color.a).Append(';');
            for (int channel = 0; channel < 8; channel++)
            {
                var coordinates = new List<Vector4>();
                mesh.GetUVs(channel, coordinates);
                all.Append(channel).Append(':');
                foreach (var point in coordinates)
                    all.Append(point.ToString("R"));
            }
            all.Append(mesh.subMeshCount);
            for (int submesh = 0; submesh < mesh.subMeshCount; submesh++)
            {
                all.Append(mesh.GetTopology(submesh));
                foreach (int index in mesh.GetIndices(submesh))
                    all.Append(index).Append(',');
            }
            return all.ToString();
        }

        [TestCase("max_nodes", 0)]
        [TestCase("max_depth", 25)]
        [TestCase("max_evaluations", 0)]
        [TestCase("max_characters", 0)]
        [TestCase("max__nodes", 1)]
        [TestCase("resolutions", 1)]
        public void InvalidOptionsRejectBeforeMissingTarget(string key, int value)
        {
            var result = Call(new JObject { ["target"] = "UI08NeverExists", [key] = value });
            Assert.IsFalse(result.Value<bool>("success"));
            Assert.AreEqual("invalid_text_request", result.Value<string>("code"), result.ToString());
        }

        [Test]
        public void ProportionalMetricsDistinguishSameLengthAndPreserveGeneratedMeshAtlasAndCallbacks()
        {
            var text = Text("iiii");
            Arm(text);
            Render(text);
            Assert.Greater(renderCallbacks, 0, "Armed positive control must actually run on explicit owned rendering.");
            Assert.Greater(textChangedCallbacks, 0, "Global text-changed positive control must actually run on explicit owned rendering.");
            int beforeCallbacks = renderCallbacks,
                beforeChanged = textChangedCallbacks;
            EditorUtility.ClearDirty(text);
            EditorUtility.ClearDirty(text.GetComponent<RectTransform>());
            EditorUtility.ClearDirty(font);
            EditorUtility.ClearDirty(fallback);
            string before = State(text);
            var first = Data(Call());
            var narrow = Node(first, text);
            Assert.AreEqual("observed", narrow.Value<string>("status"));
            Assert.AreEqual(Get<bool>(text, "isTextOverflowing"), narrow.Value<bool>("native_overflow"));
            Assert.AreEqual(before, State(text));
            Assert.AreEqual(beforeCallbacks, renderCallbacks);
            Assert.AreEqual(beforeChanged, textChangedCallbacks);
            Set(text, "text", "WWWW");
            Render(text);
            var second = Data(Call());
            var wide = Node(second, text);
            Assert.Greater((float)wide["ink_bounds"][2], (float)narrow["ink_bounds"][2] * 1.5f);
            Export("proportional-before", first);
            Export("proportional-after", second);
        }

        [TestCase("한국어漢字", false)]
        [TestCase("Hello\nWorld", false)]
        [TestCase("<size=48>W</size><color=red>i</color>", false)]
        [TestCase("WWWW WWWW WWWW", true)]
        public void ActualCjkNewlineRichTextAndAutosizeAreObserved(string content, bool autosize)
        {
            var text = Text(content, 140, 45);
            Set(text, "enableAutoSizing", autosize);
            Set(text, "fontSizeMin", 8f);
            Set(text, "fontSizeMax", 40f);
            Render(text);
            EditorUtility.ClearDirty(text);
            EditorUtility.ClearDirty(text.GetComponent<RectTransform>());
            EditorUtility.ClearDirty(font);
            EditorUtility.ClearDirty(fallback);
            string before = State(text);
            var data = Data(Call());
            var node = Node(data, text);
            Assert.AreEqual("observed", node.Value<string>("status"), node.ToString());
            Assert.AreEqual(Get<bool>(text, "isTextOverflowing"), node.Value<bool>("native_overflow"));
            Assert.AreEqual(autosize, node.Value<bool>("autosize"));
            Assert.AreEqual(before, State(text));
            if (content.Contains("한국"))
                Assert.Greater(node.Value<int>("fallback_characters"), 0, "Actual configured fallback must be exercised.");
            if (content.Contains("\n"))
                Assert.GreaterOrEqual(node.Value<int>("line_count"), 2);
            if (content.Contains("size="))
                Assert.Greater(node.Value<float>("max_point_size"), node.Value<float>("min_point_size"));
            if (autosize)
                Assert.Less(node.Value<float>("base_font_size"), 40f);
            Export(
                "layout-"
                    + (
                        autosize ? "autosize"
                        : content.Contains("한국") ? "cjk"
                        : content.Contains("\n") ? "newline"
                        : "rich"
                    ),
                data
            );
        }

        [Test]
        public void DirtyAndUninitializedAreUnknownWithoutRebuild()
        {
            var text = Text("Hello");
            Arm(text);
            Set(text, "text", "WWWW WWWW");
            EditorUtility.ClearDirty(text);
            EditorUtility.ClearDirty(text.GetComponent<RectTransform>());
            EditorUtility.ClearDirty(font);
            EditorUtility.ClearDirty(fallback);
            string before = State(text);
            var data = Data(Call());
            Assert.AreEqual("unknown", Node(data, text).Value<string>("status"));
            Assert.AreEqual("dirty_layout", Node(data, text).Value<string>("reason"));
            Assert.AreEqual(before, State(text));
            Assert.AreEqual(0, renderCallbacks);
            Export("dirty", data);
        }

        [TestCase("UnityEngine.UI.InputField")]
        [TestCase("TMPro.TMP_InputField")]
        public void InputOwnsExternalInactiveRendererAndNeverObservesItsCache(string inputName)
        {
            var text = Text("PrivateSentinel");
            var inputGo = new GameObject("UI08Input", typeof(RectTransform));
            inputGo.transform.SetParent(root.transform, false);
            var input = inputGo.AddComponent(UnityTypeResolver.ResolveComponent(inputName));
            if (inputName.StartsWith("TMPro", StringComparison.Ordinal))
                Set(input, "textComponent", text);
            else
                text.transform.SetParent(inputGo.transform, false);
            Set(input, "contentType", Enum.ToObject(input.GetType().GetProperty("contentType").PropertyType, 7));
            Set(input, "text", "PrivateSentinel");
            inputGo.SetActive(false);
            int privateReads = 0;
            var helper = typeof(ManageUGUI).Assembly.GetType("MCPForUnity.Editor.Tools.UguiTmpLayoutDiagnostics");
            Assert.IsNotNull(helper, "Future observation seam is facility-only on baseline.");
            helper
                .GetField("ObservationForTests", BindingFlags.Static | BindingFlags.NonPublic)
                .SetValue(
                    null,
                    new Action<Component, string>(
                        (component, field) =>
                        {
                            if (component == text)
                                privateReads++;
                        }
                    )
                );
            var data = Data(Call(new JObject { ["include_inactive"] = true }));
            var node = Node(data, text);
            Assert.AreEqual("redacted", node.Value<string>("status"));
            Assert.AreEqual(JTokenType.Null, node["character_count"].Type);
            Assert.AreEqual(0, privateReads);
            StringAssert.DoesNotContain("PrivateSentinel", data.ToString());
            Export(inputName.StartsWith("TMPro", StringComparison.Ordinal) ? "tmp-input-redacted" : "ugui-input-redacted", data);
        }

        [TestCase("UnityEngine.UI.InputField", false)]
        [TestCase("UnityEngine.UI.InputField", true)]
        [TestCase("TMPro.TMP_InputField", false)]
        [TestCase("TMPro.TMP_InputField", true)]
        public void ExternalInputOwnerPlaceholderIsRedactedWhenOnlyRendererIsTargeted(string inputName, bool password)
        {
            var text = Text("PrivatePlaceholderSentinel");
            var inputGo = new GameObject("UI08ExternalInput", typeof(RectTransform));
            inputGo.transform.SetParent(root.transform, false);
            var input = inputGo.AddComponent(UnityTypeResolver.ResolveComponent(inputName));
            Set(input, "placeholder", text);
            Set(input, "contentType", Enum.ToObject(input.GetType().GetProperty("contentType").PropertyType, password ? 7 : 0));
            inputGo.SetActive(false);
            Assert.AreNotSame(inputGo.transform, text.transform.parent, "Owner is outside the renderer-only target subtree.");
            int reads = 0;
            var helper = typeof(ManageUGUI).Assembly.GetType("MCPForUnity.Editor.Tools.UguiTmpLayoutDiagnostics");
            Assert.IsNotNull(helper, "Future observation seam is facility-only on baseline.");
            helper
                .GetField("ObservationForTests", BindingFlags.Static | BindingFlags.NonPublic)
                .SetValue(null, new Action<Component, string>((_, __) => reads++));
            var data = Data(Call(new JObject { ["target"] = text.gameObject.GetInstanceIDCompat(), ["include_inactive"] = true }));
            Assert.AreEqual("redacted", Node(data, text).Value<string>("status"));
            Assert.AreEqual(JTokenType.Null, Node(data, text)["character_count"].Type);
            Assert.AreEqual(0, reads, "External owner inventory must redact before every cache observation.");
            StringAssert.DoesNotContain("PrivatePlaceholderSentinel", data.ToString());
            Export(
                (inputName.StartsWith("TMPro", StringComparison.Ordinal) ? "tmp" : "ugui") + "-external-placeholder-" + (password ? "password" : "ordinary"),
                data
            );
        }

        [Test]
        public void IncompletePrivacyFailsBeforeAnyTextObservation()
        {
            Text("Hello");
            var helper = typeof(ManageUGUI).Assembly.GetType("MCPForUnity.Editor.Tools.UguiTmpLayoutDiagnostics");
            Assert.IsNotNull(helper, "Future inventory seam is facility-only on baseline.");
            int reads = 0;
            helper
                .GetField("InventoryForTests", BindingFlags.Static | BindingFlags.NonPublic)
                .SetValue(null, new Func<Type, UnityEngine.Object[]>(_ => new UnityEngine.Object[4097]));
            helper
                .GetField("ObservationForTests", BindingFlags.Static | BindingFlags.NonPublic)
                .SetValue(null, new Action<Component, string>((_, __) => reads++));
            var result = Call();
            Assert.AreEqual("privacy_unavailable", result.Value<string>("code"));
            Assert.AreEqual(0, reads);
        }

        [TestCase(1)]
        [TestCase(10)]
        public void WorkAndNodeBudgetsTruncateBeforeFullTraversal(int budget)
        {
            Text("Hello");
            for (int i = 0; i < 32; i++)
                new GameObject("UI08Wide" + i, typeof(RectTransform)).transform.SetParent(root.transform, false);
            var data = Data(Call(new JObject { ["max_evaluations"] = budget, ["max_nodes"] = 2 }));
            Assert.IsTrue(data["traversal"].Value<bool>("truncated"));
            Assert.LessOrEqual(data["traversal"].Value<int>("evaluations"), budget);
            Assert.LessOrEqual(data["traversal"].Value<int>("nodes_visited"), 2);
            Export("budget-" + budget, data);
        }

        [Test]
        public void LineArrayAndHeightReadsArePrechargedBeforeAnyCacheScan()
        {
            var text = Text(string.Join("\n", Enumerable.Repeat("i", 100)), height: 5000);
            var complete = Node(Data(Call()), text);
            Assert.AreEqual("observed", complete.Value<string>("status"), "Actual generated multiline cache is the positive control.");
            int count = complete.Value<int>("character_count"),
                lines = complete.Value<int>("line_count");
            Assert.GreaterOrEqual(lines, 100);
            int cacheCost = count * 14 + lines * 2 + 8;
            var data = Data(Call(new JObject { ["max_evaluations"] = cacheCost - 1 }));
            var limited = Node(data, text);
            Assert.AreEqual("unknown", limited.Value<string>("status"));
            Assert.AreEqual("max_evaluations", limited.Value<string>("reason"));
            Assert.AreEqual(0, data["traversal"].Value<int>("cached_characters_scanned"));
            Assert.AreEqual(0, data["traversal"].Value<int>("cached_lines_scanned"));
            Export("line-work-boundary", data);
        }

        [TestCase(false)]
        [TestCase(true)]
        public void OversizedCharacterOrLineCacheIsUnknownBeforeScanning(bool manyLines)
        {
            var text = Text(manyLines ? string.Concat(Enumerable.Repeat("i\n", 514)) : new string('W', 4097));
            EditorUtility.ClearDirty(text);
            EditorUtility.ClearDirty(text.GetComponent<RectTransform>());
            EditorUtility.ClearDirty(font);
            EditorUtility.ClearDirty(fallback);
            string before = State(text);
            var data = Data(Call(new JObject { ["max_evaluations"] = 200000 }));
            Assert.AreEqual("unknown", Node(data, text).Value<string>("status"));
            Assert.AreEqual(manyLines ? "cache_line_limit" : "cache_character_limit", Node(data, text).Value<string>("reason"));
            Assert.AreEqual(0, data["traversal"].Value<int>("cached_characters_scanned"));
            Assert.AreEqual(before, State(text));
            Export(manyLines ? "line-limit" : "character-limit", data);
        }

        [Test]
        public void UninitializedComponentRemainsUninitialized()
        {
            var go = new GameObject("UI08Uninitialized", typeof(RectTransform));
            go.transform.SetParent(root.transform, false);
            go.SetActive(false);
            var text = go.AddComponent(textType);
            var cache = baseText.GetField("m_textInfo", BindingFlags.Instance | BindingFlags.NonPublic);
            object before = cache.GetValue(text);
            var data = Data(Call(new JObject { ["include_inactive"] = true }));
            Assert.AreEqual("unknown", Node(data, text).Value<string>("status"));
            Assert.AreSame(before, cache.GetValue(text), "Diagnosis must not call allocating textInfo getter.");
        }

        [Test]
        public void MetadataBudgetKeepsPositionalPathsAtomicAndValidUnicode()
        {
            Text("Hello");
            Text("World");
            root.transform.GetChild(0).name = new string('n', 300) + "\U0001F600";
            var data = Data(Call(new JObject { ["max_characters"] = 1 }));
            Assert.AreEqual(0, ((JArray)data["nodes"]).Count, "Insufficient path budget must omit the node rather than clip its identity.");
            Assert.IsTrue(data["traversal"].Value<bool>("truncated"));
            Assert.LessOrEqual(data["traversal"].Value<int>("characters"), 1);
            Export("metadata-limit", data);
        }

        [Test]
        public void MaximumNativeCompositionExportsActualResponseForRealLimiterReplay()
        {
            for (int i = 0; i < 999; i++)
            {
                var text = Text("i", render: false);
                text.name = new string('界', 254) + "\U0001F600";
            }
            // One owned batch update, not999 full-tree updates.
            Canvas.ForceUpdateCanvases();
            var response = Call(
                new JObject
                {
                    ["max_nodes"] = 1000,
                    ["max_evaluations"] = 200000,
                    ["max_characters"] = 262144,
                }
            );
            Export("maximum-response", response);
            if (!response.Value<bool>("success"))
            {
                Assert.AreEqual("text_response_too_large", response.Value<string>("code"));
                return;
            }
            var data = Data(response);
            Assert.AreEqual(1000, data["traversal"].Value<int>("nodes_visited"));
            Assert.AreEqual(999, ((JArray)data["nodes"]).Count);
            Assert.LessOrEqual(Encoding.UTF8.GetByteCount(response.ToString(Formatting.None)), 2 * 1024 * 1024 + 128);
            Export("maximum", data);
        }

        [Test]
        public void DiagnosisPreservesExistingCanvasRegistryAndCallbackRegistration()
        {
            var registry = AppDomain
                .CurrentDomain.GetAssemblies()
                .Single(a => a.GetName().Name == "UnityEngine.UI")
                .GetType("UnityEngine.UI.CanvasUpdateRegistry", true);
            Assert.IsNotNull(registry);
            var slot = registry.GetField("s_Instance", BindingFlags.NonPublic | BindingFlags.Static);
            var callbacks = typeof(Canvas).GetField("willRenderCanvases", BindingFlags.NonPublic | BindingFlags.Static);
            Assert.IsNotNull(slot);
            Assert.IsNotNull(callbacks, "Installed Canvas delegate shape is a fixture prerequisite.");
            object before = slot.GetValue(null),
                beforeCallbacks = callbacks.GetValue(null);
            Data(Call());
            Assert.AreSame(before, slot.GetValue(null));
            Assert.AreEqual(beforeCallbacks, callbacks.GetValue(null));
        }

        [TestCase(1)]
        [TestCase(2)]
        [TestCase(3)]
        public void NativeEllipsisMaskingAndTruncationRemainLayoutEvidence(int mode)
        {
            var text = Text("WWWW WWWW WWWW", 65, 30);
            Set(text, "overflowMode", Enum.ToObject(textType.GetProperty("overflowMode").PropertyType, mode));
            Render(text);
            var data = Data(Call());
            var node = Node(data, text);
            Assert.AreEqual("observed", node.Value<string>("status"));
            Assert.AreEqual(Get<bool>(text, "isTextOverflowing"), node.Value<bool>("native_overflow"));
            Export("overflow-mode-" + mode, data);
        }

        [TestCase(5)]
        [TestCase(6)]
        public void PagedAndLinkedLayoutsRefuseAnIncompleteLocalInterpretation(int mode)
        {
            var text = Text("Hello");
            Set(text, "overflowMode", Enum.ToObject(textType.GetProperty("overflowMode").PropertyType, mode));
            Render(text);
            var data = Data(Call());
            var node = Node(data, text);
            Assert.AreEqual("unknown", node.Value<string>("status"));
            Assert.AreEqual("unsupported_overflow_mode", node.Value<string>("reason"));
            Export("unsupported-overflow-" + mode, data);
        }

        [UnityTest]
        public IEnumerator ObservedInkTracksActualRedPixelsWithoutDiagnosisRebuilding()
        {
            var text = Text("WWWW");
            root.transform.localScale = Vector3.one * .01f;
            root.transform.position = new Vector3(0, 0, 5);
            var cameraGo = new GameObject("UI08OwnedCamera", typeof(Camera));
            owned.Add(cameraGo);
            var camera = cameraGo.GetComponent<Camera>();
            camera.clearFlags = CameraClearFlags.SolidColor;
            camera.backgroundColor = Color.black;
            camera.allowHDR = camera.allowMSAA = false;
            var rt = new RenderTexture(320, 240, 24);
            owned.Add(rt);
            camera.targetTexture = rt;
            string[] contents = { "WWWW", "한국어漢字", "Hello\nWorld", "<size=48>W</size><color=red>i</color>", "WWWW WWWW WWWW" };
            for (int scenario = 0; scenario < contents.Length; scenario++)
            {
                Set(text, "text", contents[scenario]);
                Set(text, "enableAutoSizing", scenario == 4);
                Set(text, "fontSizeMin", 8f);
                Set(text, "fontSizeMax", 40f);
                camera.targetTexture = rt;
                Render(text);
                camera.Render();
                yield return null;
                var previous = RenderTexture.active;
                var pixels = new Texture2D(320, 240, TextureFormat.RGBA32, false);
                try
                {
                    RenderTexture.active = rt;
                    pixels.ReadPixels(new Rect(0, 0, 320, 240), 0, 0);
                    pixels.Apply();
                    Assert.Greater(pixels.GetPixels32().Count(p => p.r > 128 && p.g < 96 && p.b < 96), 10, "Actual red render prerequisite.");
                    EditorUtility.ClearDirty(text);
                    EditorUtility.ClearDirty(text.GetComponent<RectTransform>());
                    EditorUtility.ClearDirty(font);
                    EditorUtility.ClearDirty(fallback);
                    string before = State(text);
                    var data = Data(Call());
                    var node = Node(data, text);
                    Assert.AreEqual("observed", node.Value<string>("status"));
                    Assert.Greater((float)node["ink_bounds"][2], 0);
                    var ink = (JArray)node["ink_bounds"];
                    var predictedMin = camera.WorldToScreenPoint(text.transform.TransformPoint(new Vector3((float)ink[0], (float)ink[1], 0)));
                    var predictedMax = camera.WorldToScreenPoint(
                        text.transform.TransformPoint(new Vector3((float)ink[0] + (float)ink[2], (float)ink[1] + (float)ink[3], 0))
                    );
                    int minX = 320,
                        minY = 240,
                        maxX = -1,
                        maxY = -1;
                    var actual = pixels.GetPixels32();
                    for (int y = 0; y < 240; y++)
                    for (int x = 0; x < 320; x++)
                        if (actual[y * 320 + x].r > 128 && actual[y * 320 + x].g < 96 && actual[y * 320 + x].b < 96)
                        {
                            minX = Math.Min(minX, x);
                            maxX = Math.Max(maxX, x);
                            minY = Math.Min(minY, y);
                            maxY = Math.Max(maxY, y);
                        }
                    Assert.GreaterOrEqual(minX, predictedMin.x - 2);
                    Assert.GreaterOrEqual(minY, predictedMin.y - 2);
                    Assert.LessOrEqual(maxX, predictedMax.x + 2);
                    Assert.LessOrEqual(maxY, predictedMax.y + 2);
                    Assert.Greater(
                        maxX - minX + 1,
                        (predictedMax.x - predictedMin.x) * .5f,
                        "Actual glyph ink must cover a useful fraction of the observed quad envelope."
                    );
                    Assert.AreEqual(before, State(text));
                    Export("actual-render-" + scenario, data);
                    File.WriteAllBytes(Path.Combine(Path.GetTempPath(), "ui08-actual-render-" + scenario + ".png"), pixels.EncodeToPNG());
                }
                finally
                {
                    RenderTexture.active = previous;
                    camera.targetTexture = null;
                    UnityEngine.Object.DestroyImmediate(pixels);
                }
            }
        }
    }
}
