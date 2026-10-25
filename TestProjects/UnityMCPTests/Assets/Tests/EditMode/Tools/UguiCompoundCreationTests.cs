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
using UnityEditor.SceneManagement;
using UnityEngine;
using UnityEngine.SceneManagement;

namespace MCPForUnityTests.EditMode.Tools
{
    public class UguiCompoundAdmissionDiagnosticsTests
    {
        [Test]
        public void MissingAndConflictingControlFieldsHaveActionableAdmissionErrors()
        {
            var missing = JObject.FromObject(ManageUGUI.HandleCommand(new JObject { ["action"] = "create_control", ["element_type"] = "unknown" }));
            Assert.That(missing["success"].Value<bool>(), Is.False);
            Assert.That(missing["message"].Value<string>(), Is.EqualTo("invalid_compound_control_request"));
            Assert.That(missing["data"]["missing_fields"].Values<string>(), Is.EquivalentTo(new[] { "parent", "name" }));
            Assert.That(missing["data"]["allowed_element_types"].Values<string>(), Does.Contain("tmp_input"));
            var conflict = JObject.FromObject(
                ManageUGUI.HandleCommand(
                    new JObject
                    {
                        ["action"] = "create_control",
                        ["element_type"] = "slider",
                        ["target"] = "PRIVATE_VALUE",
                    }
                )
            );
            Assert.That(conflict["data"]["conflicting_fields"].Values<string>(), Is.EquivalentTo(new[] { "target" }));
            Assert.That(conflict["data"]["allowed_properties"].Values<string>(), Does.Contain("minValue"));
            Assert.That(conflict["data"]["allowed_values"]["properties.direction"].Values<string>(), Does.Contain("LeftToRight"));
            StringAssert.DoesNotContain("PRIVATE_VALUE", conflict.ToString());
        }
    }

    public abstract class CompoundOwnedFixture
    {
        protected const string EmptySceneTemplateGuid = "d8f5ae9136f356e190abdc5c9be58fb6";
        protected Scene previous,
            owned;
        protected GameObject root;
        protected ScriptableObject settings,
            font;
        protected Type settingsType,
            fontType;
        protected FieldInfo settingsSlot;
        protected object originalSettings;
        protected bool settingsInstalled;
        protected IDictionary[] fontRegistries;
        protected IList fontCallbacks;
        protected object[] originalFontCallbacks;
        protected Type resourceManager;
        protected readonly List<UnityEngine.Object> fontObjects = new List<UnityEngine.Object>();
        protected UnityEngine.Object previousSelection;
        protected SceneSnapshot[] preservedScenes;
        protected string folder,
            ownedFolderGuid;
        protected int undoGroup;
        protected bool ownsFolder,
            ownsScene,
            ownsUndoGroup,
            ownsSelection;

        [SetUp]
        public virtual void SetUp()
        {
            Assert.That(settingsInstalled, Is.False, "A previous owned TMP settings lease must have closed.");
            Assert.That(fontObjects.Count, Is.Zero);
            settings = font = null;
            settingsType = fontType = resourceManager = null;
            settingsSlot = null;
            originalSettings = null;
            fontRegistries = null;
            fontCallbacks = null;
            originalFontCallbacks = null;
            ownsFolder = ownsScene = ownsUndoGroup = ownsSelection = false;
            previous = owned = default;
            preservedScenes = null;
            ownedFolderGuid = null;
            try
            {
                Assert.That(Ui("CanvasScaler"), Is.Not.Null, "The native CLI control requires installed uGUI.");
                Assert.That(Ui("Image"), Is.Not.Null);
                Assert.That(PrefabStageUtility.GetCurrentPrefabStage(), Is.Null, "Preserve an existing Prefab Stage; this fixture requires the main stage.");
                previous = SceneManager.GetActiveScene();
                previousSelection = Selection.activeObject;
                ownsSelection = true;
                preservedScenes = Enumerable
                    .Range(0, SceneManager.sceneCount)
                    .Select(index => SceneManager.GetSceneAt(index))
                    .Select(original => new SceneSnapshot
                    {
                        Scene = original,
                        Path = original.path,
                        Dirty = original.isDirty,
                        Loaded = original.isLoaded,
                        RootIds = original.isLoaded ? RootIds(original) : Array.Empty<int>(),
                    })
                    .ToArray();
                string prefix = "UI16_" + Guid.NewGuid().ToString("N");
                folder = "Assets/" + prefix;
                string template = AssetDatabase.GUIDToAssetPath(EmptySceneTemplateGuid);
                Assert.That(AssetDatabase.LoadAssetAtPath<SceneAsset>(template), Is.Not.Null, "The shared empty test scene template must be imported.");
                Assert.That(AssetDatabase.IsValidFolder(folder), Is.False, "Never reuse or delete pre-existing assets.");
                Assert.That(AssetDatabase.AssetPathToGUID(folder), Is.Empty);
                ownedFolderGuid = AssetDatabase.CreateFolder("Assets", prefix);
                ownsFolder = !string.IsNullOrEmpty(ownedFolderGuid);
                Assert.That(ownedFolderGuid, Is.Not.Empty);
                Assert.That(AssetDatabase.GUIDToAssetPath(ownedFolderGuid), Is.EqualTo(folder));
                string ownedPath = folder + "/" + prefix + ".unity";
                Assert.That(AssetDatabase.AssetPathToGUID(ownedPath), Is.Empty);
                Assert.That(AssetDatabase.CopyAsset(template, ownedPath), Is.True);
                AssertPreservedScenes(false);
                owned = EditorSceneManager.OpenScene(ownedPath, OpenSceneMode.Additive);
                ownsScene = true;
                Assert.That(owned.path, Is.EqualTo(ownedPath));
                Assert.That(owned.rootCount, Is.Zero, "The owned copy must be an empty normal scene.");
                Assert.That(EditorSceneManager.IsPreviewScene(owned), Is.False);
                Assert.That(SceneManager.SetActiveScene(owned), Is.True, "Create owned targets only in the owned scene.");
                AssertPreservedScenes(true);
                Undo.IncrementCurrentGroup();
                undoGroup = Undo.GetCurrentGroup();
                ownsUndoGroup = true;
                root = new GameObject("UI16Canvas", typeof(RectTransform), typeof(Canvas));
                root.GetComponent<Canvas>().renderMode = RenderMode.WorldSpace;
                root.transform.position = new Vector3(43, -25, 3);
                root.transform.localEulerAngles = new Vector3(0, 0, 17);
                root.transform.localScale = new Vector3(1.5f, 0.75f, 1);
                ((RectTransform)root.transform).sizeDelta = new Vector2(600, 400);
            }
            catch (Exception setupError)
            {
                try
                {
                    TearDown();
                }
                catch (Exception cleanupError)
                {
                    throw new AggregateException("Owned compound fixture setup and cleanup failed.", setupError, cleanupError);
                }
                throw;
            }
        }

        [TearDown]
        public virtual void TearDown()
        {
            var errors = new List<Exception>();
            Cleanup(ResetHooks, errors);
            bool restoreSelection = ownsSelection;
            if (ownsUndoGroup)
                Cleanup(() => Undo.RevertAllDownToGroup(undoGroup), errors);
            ownsUndoGroup = false;
            Cleanup(CloseFontLease, errors);
            if (ownsSelection)
                Cleanup(() => Selection.activeObject = previousSelection, errors);
            ownsSelection = false;
            Cleanup(
                () =>
                {
                    if (preservedScenes != null && previous.IsValid() && previous.isLoaded)
                        SceneManager.SetActiveScene(previous);
                },
                errors
            );
            Cleanup(
                () =>
                {
                    if (ownsScene && owned.IsValid() && owned.isLoaded)
                        Assert.That(EditorSceneManager.CloseScene(owned, true), Is.True);
                },
                errors
            );
            ownsScene = false;
            Cleanup(
                () =>
                {
                    if (ownsFolder && AssetDatabase.IsValidFolder(folder))
                    {
                        Assert.That(
                            AssetDatabase.AssetPathToGUID(folder),
                            Is.EqualTo(ownedFolderGuid),
                            "Delete only the folder GUID acquired by this fixture."
                        );
                        Assert.That(AssetDatabase.DeleteAsset(folder), Is.True);
                    }
                },
                errors
            );
            ownsFolder = false;
            Cleanup(() => AssertPreservedScenes(false), errors);
            if (restoreSelection)
                Cleanup(() => Assert.That(Selection.activeObject, Is.EqualTo(previousSelection)), errors);
            preservedScenes = null;
            ownedFolderGuid = null;
            previous = owned = default;
            if (errors.Count > 0)
                throw new AggregateException("Owned device-profile fixture cleanup failed.", errors);
        }

        protected sealed class SceneSnapshot
        {
            public Scene Scene;
            public string Path;
            public bool Dirty,
                Loaded;
            public int[] RootIds;
        }

        protected static int[] RootIds(Scene scene) => scene.GetRootGameObjects().Select(go => go.GetInstanceIDCompat()).OrderBy(id => id).ToArray();

        protected void AssertPreservedScenes(bool expectOwnedScene)
        {
            if (preservedScenes == null)
                return;
            Scene[] current = Enumerable.Range(0, SceneManager.sceneCount).Select(index => SceneManager.GetSceneAt(index)).ToArray();
            Scene[] expected = preservedScenes.Select(original => original.Scene).Concat(expectOwnedScene ? new[] { owned } : Array.Empty<Scene>()).ToArray();
            Assert.That(current, Is.EquivalentTo(expected), "Preserve every previously open scene, including untitled scenes.");
            foreach (var original in preservedScenes)
            {
                Scene retained = current.Single(scene => scene.Equals(original.Scene));
                Assert.That(retained.IsValid(), Is.True);
                Assert.That(retained.isLoaded, Is.EqualTo(original.Loaded));
                Assert.That(retained.path, Is.EqualTo(original.Path));
                Assert.That(retained.isDirty, Is.EqualTo(original.Dirty));
                if (original.Loaded)
                    Assert.That(RootIds(retained), Is.EqualTo(original.RootIds));
            }
            Assert.That(SceneManager.GetActiveScene(), Is.EqualTo(expectOwnedScene ? owned : previous));
        }

        protected static void Cleanup(Action action, List<Exception> errors)
        {
            try
            {
                action();
            }
            catch (Exception error)
            {
                errors.Add(error);
            }
        }

        protected static Type Ui(string name) => UnityTypeResolver.ResolveComponent("UnityEngine.UI." + name);

        protected void InstallSettings()
        {
            Assert.That(settingsInstalled, Is.False);
            settingsType = UnityTypeResolver.ResolveAny("TMPro.TMP_Settings");
            Assert.That(settingsType, Is.Not.Null, "Installed TMP is required for this explicit dependency control.");
            settingsSlot = settingsType.GetField("s_Instance", BindingFlags.NonPublic | BindingFlags.Static);
            Assert.That(settingsSlot, Is.Not.Null);
            originalSettings = settingsSlot.GetValue(null);
            settings = ScriptableObject.CreateInstance(settingsType);
            settings.hideFlags = HideFlags.HideAndDontSave;
            foreach (string name in new[] { "m_leadingCharacters", "m_followingCharacters" })
            {
                var value = new TextAsset(string.Empty) { hideFlags = HideFlags.HideAndDontSave };
                fontObjects.Add(value);
                settingsType.GetField(name, BindingFlags.NonPublic | BindingFlags.Instance).SetValue(settings, value);
            }
            var version = settingsType.GetField("s_CurrentAssetVersion", BindingFlags.NonPublic | BindingFlags.Static);
            var assetVersion = settingsType.GetField("m_AssetVersion", BindingFlags.NonPublic | BindingFlags.Instance);
            if (version != null && assetVersion != null)
                assetVersion.SetValue(settings, version.GetValue(null));
            settingsSlot.SetValue(null, settings);
            settingsInstalled = true;
        }

        protected void UseFont()
        {
            // Source reuse: UI08's accepted fresh-owned TMP fixture lease and factory.
            // A later QA case must supply its own compatible font; no OS/protected font is copied.
            InstallSettings();
            fontType = UnityTypeResolver.ResolveAny("TMPro.TMP_FontAsset");
            Assert.That(fontType, Is.Not.Null);
            Assert.That(UnityEngine.Resources.FindObjectsOfTypeAll(fontType).Length, Is.Zero, "Positive controls require a fresh owned TMP font context.");
            resourceManager = fontType.Assembly.GetType("TMPro.TMP_ResourceManager", true);
            fontRegistries = new[] { "s_FontAssetReferences", "s_FontAssetNameReferenceLookup", "s_FontAssetFamilyNameAndStyleReferenceLookup" }
                .Select(name => (IDictionary)resourceManager.GetField(name, BindingFlags.NonPublic | BindingFlags.Static).GetValue(null))
                .ToArray();
            foreach (var registry in fontRegistries)
                Assert.That(registry.Count, Is.Zero);
            fontCallbacks = (IList)fontType.GetField("s_CallbackInstances", BindingFlags.NonPublic | BindingFlags.Static).GetValue(null);
            Assert.That(fontCallbacks.Count, Is.LessThanOrEqualTo(4096));
            var weakFontType = typeof(WeakReference<>).MakeGenericType(fontType);
            Assert.That(fontCallbacks.GetType(), Is.EqualTo(typeof(List<>).MakeGenericType(weakFontType)));
            originalFontCallbacks = fontCallbacks.Cast<object>().ToArray();
            string path = Path.GetFullPath(Path.Combine(Application.dataPath, "..", "..", "font-input", "UI16.ttf"));
            Assert.That(File.Exists(path), Is.True, "Supply an owned compatible Latin font at case/font-input/UI16.ttf; missing input is setup failure.");
            var create = fontType
                .GetMethods(BindingFlags.Public | BindingFlags.Static)
                .Single(method =>
                    method.Name == "CreateFontAsset" && method.GetParameters().Length == 7 && method.GetParameters()[0].ParameterType == typeof(string)
                );
            Type render = create.GetParameters()[4].ParameterType;
            Assert.That(render.FullName, Is.EqualTo("UnityEngine.TextCore.LowLevel.GlyphRenderMode"));
            font = (ScriptableObject)create.Invoke(null, new object[] { path, 0, 90, 9, Enum.Parse(render, "SDFAA"), 1024, 1024 });
            Assert.That(font, Is.Not.Null);
            fontObjects.Add(font);
            font.name = "UI16OwnedFont" + Guid.NewGuid().ToString("N");
            var add = fontType.GetMethod("TryAddCharacters", new[] { typeof(string), typeof(string).MakeByRefType(), typeof(bool) });
            var args = new object[] { " ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_-<>=/\".", null, false };
            Assert.That((bool)add.Invoke(font, args), Is.True, "The supplied owned font must cover the fixture text.");
            fontType.GetProperty("atlasPopulationMode").SetValue(font, Enum.ToObject(fontType.GetProperty("atlasPopulationMode").PropertyType, 0));
            settingsType.GetField("m_defaultFontAsset", BindingFlags.NonPublic | BindingFlags.Instance).SetValue(settings, font);
        }

        protected void CloseFontLease()
        {
            var errors = new List<Exception>();
            if (font != null && resourceManager != null)
                Cleanup(
                    () =>
                    {
                        foreach (var registry in fontRegistries.Skip(1))
                        {
                            Assert.That(registry.Count, Is.LessThanOrEqualTo(4096));
                            foreach (object current in registry.Values)
                                Assert.That(fontObjects.Any(value => ReferenceEquals(value, current)), Is.True, "Never remove a foreign font lookup.");
                        }
                        foreach (var ownedFont in fontObjects.Where(value => value != null && fontType.IsInstanceOfType(value)).ToArray())
                            resourceManager.GetMethod("RemoveFontAsset", new[] { fontType }).Invoke(null, new object[] { ownedFont });
                    },
                    errors
                );
            if (fontCallbacks != null && originalFontCallbacks != null)
                Cleanup(
                    () =>
                    {
                        Assert.That(fontCallbacks.Count, Is.LessThanOrEqualTo(4097));
                        for (int index = fontCallbacks.Count - 1; index >= 0; index--)
                        {
                            object weak = fontCallbacks[index];
                            var value = new object[] { null };
                            if ((bool)weak.GetType().GetMethod("TryGetTarget").Invoke(weak, value) && fontObjects.Any(item => ReferenceEquals(value[0], item)))
                            {
                                if (index < originalFontCallbacks.Length)
                                    fontCallbacks[index] = originalFontCallbacks[index];
                                else
                                    fontCallbacks.RemoveAt(index);
                            }
                        }
                        CollectionAssert.AreEqual(originalFontCallbacks, fontCallbacks.Cast<object>().ToArray());
                    },
                    errors
                );
            if (settingsInstalled)
                Cleanup(
                    () =>
                    {
                        Assert.That(ReferenceEquals(settingsSlot.GetValue(null), settings), Is.True, "Do not overwrite a foreign settings replacement.");
                        settingsSlot.SetValue(null, originalSettings);
                        settingsInstalled = false;
                    },
                    errors
                );
            foreach (var value in fontObjects.AsEnumerable().Reverse())
                Cleanup(
                    () =>
                    {
                        if (value != null)
                            UnityEngine.Object.DestroyImmediate(value);
                    },
                    errors
                );
            fontObjects.Clear();
            Cleanup(
                () =>
                {
                    if (settings != null)
                        UnityEngine.Object.DestroyImmediate(settings);
                },
                errors
            );
            settings = font = null;
            if (errors.Count > 0)
                throw new AggregateException("Owned compound TMP lease cleanup failed.", errors);
        }

        protected static Type Require(string name) => UnityTypeResolver.ResolveComponent(name) ?? throw new InvalidOperationException(name);

        protected static Type RequireAny(string name) => UnityTypeResolver.ResolveAny(name) ?? throw new InvalidOperationException(name);

        protected static object Read(object obj, string member)
        {
            var property = obj.GetType().GetProperty(member);
            if (property != null)
                return property.GetValue(obj);
            var field = obj.GetType().GetField(member, BindingFlags.Public | BindingFlags.Instance);
            Assert.That(field, Is.Not.Null, "Native public readback member: " + member);
            return field.GetValue(obj);
        }

        protected static void Write(object obj, string member, object value) => obj.GetType().GetProperty(member).SetValue(obj, value);

        protected static object Invoke(object obj, string name, params object[] args) =>
            obj.GetType()
                .GetMethods(BindingFlags.Public | BindingFlags.Instance)
                .Single(m =>
                    m.Name == name
                    && m.GetParameters().Length == args.Length
                    && m.GetParameters().Select((v, i) => v.ParameterType.IsInstanceOfType(args[i]) || args[i] == null).All(v => v)
                )
                .Invoke(obj, args);

        protected static object EventData(string kind, Component es) =>
            Activator.CreateInstance(RequireAny("UnityEngine.EventSystems." + kind), new object[] { es });

        protected static void AddStringListener(object ev, UnityEngine.Events.UnityAction<string> callback) => Invoke(ev, "AddListener", callback);

        protected static void AddBoolListener(object ev, UnityEngine.Events.UnityAction<bool> callback) => Invoke(ev, "AddListener", callback);

        protected static void AddFloatListener(object ev, UnityEngine.Events.UnityAction<float> callback) => Invoke(ev, "AddListener", callback);

        protected static void AddVectorListener(object ev, UnityEngine.Events.UnityAction<Vector2> callback) => Invoke(ev, "AddListener", callback);

        protected static void FlushScrollEvents(Component scroll)
        {
            // ScrollRect emits onValueChanged from LateUpdate, not directly from OnScroll/OnDrag.
            // Invoke that actual native lifecycle once after each handler segment; never invoke the event ourselves.
            MethodInfo late = scroll.GetType().GetMethod("LateUpdate", BindingFlags.NonPublic | BindingFlags.Instance);
            Assert.That(late, Is.Not.Null);
            late.Invoke(scroll, null);
        }

        protected static Type Helper => typeof(ManageUGUI).Assembly.GetType("MCPForUnity.Editor.Tools.UguiCompoundCreation", true);

        protected static void SetHook(string name, object value) => Helper.GetField(name, BindingFlags.NonPublic | BindingFlags.Static).SetValue(null, value);

        protected static void ResetHooks()
        {
            foreach (
                string name in new[]
                {
                    "FaultForTests",
                    "ResolveForTests",
                    "PropertyForTests",
                    "AfterAssignmentForTests",
                    "BeforeRollbackForTests",
                    "BeforeFinalizeForTests",
                }
            )
                SetHook(name, null);
        }

        protected static GameObject[] AllSceneObjects() =>
            Enumerable
                .Range(0, SceneManager.sceneCount)
                .Select(SceneManager.GetSceneAt)
                .Where(s => s.isLoaded)
                .SelectMany(s => s.GetRootGameObjects())
                .SelectMany(go => go.GetComponentsInChildren<Transform>(true))
                .Select(t => t.gameObject)
                .ToArray();

        protected static Component[] LoadedEventSystems() =>
            AllSceneObjects().Select(go => go.GetComponent(Require("UnityEngine.EventSystems.EventSystem"))).Where(c => c != null).ToArray();

        protected JObject Request(string kind, string name = "OwnedControl") =>
            new JObject
            {
                ["action"] = "create_control",
                ["parent"] = root.GetInstanceIDCompat(),
                ["name"] = name,
                ["element_type"] = kind,
                ["properties"] = new JObject(),
            };

        protected static JObject WithProperty(JObject request, string key, JToken value)
        {
            ((JObject)request["properties"])[key] = value;
            return request;
        }

        protected static JObject Send(JObject request) => JObject.FromObject(ManageUGUI.HandleCommand(request));

        protected static GameObject Created(JObject response)
        {
            Assert.That(response.Value<bool>("success"), Is.True, response.ToString());
            return GameObjectLookup.FindById(response["data"].Value<int>("instanceID"));
        }

        protected static GameObject Role(JObject response, string role) => GameObjectLookup.FindById(response["data"]["roles"].Value<int>(role));
    }

    [Parallelizable(ParallelScope.None)]
    [Explicit("Requires a fresh owned NativeQA case, the saved empty scene and owned font-input/UI16.ttf.")]
    public class UguiCompoundCreationTests : CompoundOwnedFixture
    {
        private ScriptableObject NewDynamicFont()
        {
            var factory = fontType
                .GetMethods(BindingFlags.Public | BindingFlags.Static)
                .Single(m => m.Name == "CreateFontAsset" && m.GetParameters().Length == 7 && m.GetParameters()[0].ParameterType == typeof(string));
            string path = Path.GetFullPath(Path.Combine(Application.dataPath, "..", "..", "font-input", "UI16.ttf"));
            var asset = (ScriptableObject)
                factory.Invoke(null, new object[] { path, 0, 90, 9, Enum.Parse(factory.GetParameters()[4].ParameterType, "SDFAA"), 1024, 1024 });
            Assert.That(asset, Is.Not.Null);
            fontObjects.Add(asset);
            Write(asset, "atlasPopulationMode", Enum.Parse(fontType.GetProperty("atlasPopulationMode").PropertyType, "Dynamic"));
            return asset;
        }

        private static string SharedFontState(ScriptableObject asset)
        {
            var material = (Material)Read(asset, "material");
            var textures = ((IEnumerable)Read(asset, "atlasTextures")).Cast<Texture2D>().ToArray();
            return new JObject
            {
                ["font"] = EditorJsonUtility.ToJson(asset),
                ["fontDirty"] = EditorUtility.IsDirty(asset),
                ["material"] = material == null ? null : EditorJsonUtility.ToJson(material),
                ["materialDirty"] = material != null && EditorUtility.IsDirty(material),
                ["textures"] = new JArray(textures.Select(texture => texture == null ? null : texture.imageContentsHash.ToString())),
                ["textureDirty"] = new JArray(textures.Select(texture => texture != null && EditorUtility.IsDirty(texture))),
            }.ToString(Newtonsoft.Json.Formatting.None);
        }

        private static JObject MaterialWithoutDerivedRatios(string serialized)
        {
            var material = JObject.Parse(serialized);
            var floats = material.SelectToken("Material.m_SavedProperties.m_Floats") as JArray;
            Assert.That(floats, Is.Not.Null);
            foreach (var property in floats.OfType<JObject>())
            {
                string name = property.Value<string>("first");
                if (name == "_ScaleRatioA" || name == "_ScaleRatioB" || name == "_ScaleRatioC")
                    property["second"] = 0f;
            }
            return material;
        }

        private static float SavedFloat(JObject material, string name) =>
            ((JArray)material.SelectToken("Material.m_SavedProperties.m_Floats"))
                .OfType<JObject>()
                .Single(entry => entry.Value<string>("first") == name)
                .Value<float>("second");

        private static JArray DerivedRatios(JObject material) =>
            new JArray(
                ((JArray)material.SelectToken("Material.m_SavedProperties.m_Floats"))
                    .OfType<JObject>()
                    .Where(entry => new[] { "_ScaleRatioA", "_ScaleRatioB", "_ScaleRatioC" }.Contains(entry.Value<string>("first")))
                    .Select(entry => entry.DeepClone())
            );

        private static void AssertDerivedRatios(Material material, JObject priorMaterial)
        {
            // TMP_ShaderUtilities.UpdateShaderRatios computes these shader values on every
            // normal text layout. The same initialization occurs for manual and static text.
            float scale = material.GetFloat("_GradientScale");
            float dilate = material.GetFloat("_FaceDilate");
            float outline = material.GetFloat("_OutlineWidth");
            float softness = material.GetFloat("_OutlineSoftness");
            float weight = Mathf.Max(material.GetFloat("_WeightNormal"), material.GetFloat("_WeightBold")) / 4f;
            bool enabled = !material.shaderKeywords.Contains("RATIOS_OFF");
            float a = enabled ? (scale - 1f) / (scale * Mathf.Max(1f, weight + dilate + outline + softness)) : 1f;
            Assert.That(material.GetFloat("_ScaleRatioA"), Is.EqualTo(a).Within(0.000001f));
            if (material.HasProperty("_GlowOffset"))
            {
                float range = (weight + dilate) * (scale - 1f);
                float t = Mathf.Max(1f, material.GetFloat("_GlowOffset") + material.GetFloat("_GlowOuter"));
                float b = enabled ? Mathf.Max(0f, scale - 1f - range) / (scale * t) : 1f;
                Assert.That(material.GetFloat("_ScaleRatioB"), Is.EqualTo(b).Within(0.000001f));
            }
            else
                Assert.That(material.GetFloat("_ScaleRatioB"), Is.EqualTo(SavedFloat(priorMaterial, "_ScaleRatioB")).Within(0.000001f));
            if (material.HasProperty("_UnderlayOffsetX"))
            {
                float range = (weight + dilate) * (scale - 1f);
                float offset = Mathf.Max(Mathf.Abs(material.GetFloat("_UnderlayOffsetX")), Mathf.Abs(material.GetFloat("_UnderlayOffsetY")));
                float t = Mathf.Max(1f, offset + material.GetFloat("_UnderlayDilate") + material.GetFloat("_UnderlaySoftness"));
                float c = enabled ? Mathf.Max(0f, scale - 1f - range) / (scale * t) : 1f;
                Assert.That(material.GetFloat("_ScaleRatioC"), Is.EqualTo(c).Within(0.000001f));
            }
            else
                Assert.That(material.GetFloat("_ScaleRatioC"), Is.EqualTo(SavedFloat(priorMaterial, "_ScaleRatioC")).Within(0.000001f));
        }

        private static void AssertSharedFontOnlyDerivedRatiosChanged(ScriptableObject asset, string before)
        {
            var previous = JObject.Parse(before);
            var current = JObject.Parse(SharedFontState(asset));
            var material = (Material)Read(asset, "material");
            var priorMaterial = JObject.Parse(previous.Value<string>("material"));
            var currentMaterial = JObject.Parse(current.Value<string>("material"));
            AssertDerivedRatios(material, priorMaterial);
            bool ratioChanged = !JToken.DeepEquals(DerivedRatios(priorMaterial), DerivedRatios(currentMaterial));
            Assert.That(current.Value<bool>("materialDirty"), Is.EqualTo(previous.Value<bool>("materialDirty") || ratioChanged));
            previous["material"] = MaterialWithoutDerivedRatios(previous.Value<string>("material"));
            current["material"] = MaterialWithoutDerivedRatios(current.Value<string>("material"));
            previous.Remove("materialDirty");
            current.Remove("materialDirty");
            Assert.That(
                JToken.DeepEquals(current, previous),
                Is.True,
                "Only TMP's derived material ratios may change; font, glyphs, atlas, and other material state must remain identical."
            );
        }

        private void CacheGlyphs(ScriptableObject asset, string glyphs)
        {
            var add = fontType.GetMethod("TryAddCharacters", new[] { typeof(string), typeof(string).MakeByRefType(), typeof(bool) });
            var args = new object[] { glyphs, null, false };
            Assert.That((bool)add.Invoke(asset, args), Is.True, "Owned test font must cache the requested glyphs before admission.");
        }

        private void EnableLigaturesWithOutput(uint output)
        {
            var tags = (IList)settingsType.GetField("m_ActiveFontFeatures", BindingFlags.NonPublic | BindingFlags.Instance).GetValue(settings);
            tags.Add(Enum.Parse(tags.GetType().GetGenericArguments()[0], "liga"));
            var records = (IList)Read(Read(font, "fontFeatureTable"), "ligatureRecords");
            Type recordType = records.GetType().GetGenericArguments()[0];
            object record = Activator.CreateInstance(recordType);
            recordType.GetProperty("componentGlyphIDs").SetValue(record, new uint[] { 1, 2 });
            recordType.GetProperty("ligatureGlyphID").SetValue(record, output);
            records.Add(record);
        }

        private void RenderAndUndo(JObject result)
        {
            var created = Created(result);
            Canvas.ForceUpdateCanvases();
            foreach (var text in created.GetComponentsInChildren(Require("TMPro.TextMeshProUGUI"), true))
                Invoke(text, "ForceMeshUpdate", true, true);
            Undo.PerformUndo();
            Assert.That(created == null, Is.True, "Undo must remove the complete owned control.");
        }

        [TestCase("toggle")]
        [TestCase("tmp_input")]
        public void PopulatedDynamicDefaultFontCreatesWithoutSharedFontMutation(string kind)
        {
            UseFont();
            Write(font, "atlasPopulationMode", Enum.Parse(fontType.GetProperty("atlasPopulationMode").PropertyType, "Dynamic"));
            CacheGlyphs(font, "\u2026");
            string before = SharedFontState(font);
            RenderAndUndo(Send(Request(kind)));
            AssertSharedFontOnlyDerivedRatiosChanged(font, before);
        }

        [Test]
        public void MissingDynamicCaretSentinelRefusesBeforeAllocation()
        {
            UseFont();
            Write(font, "atlasPopulationMode", Enum.Parse(fontType.GetProperty("atlasPopulationMode").PropertyType, "Dynamic"));
            CacheGlyphs(font, "\u2026");
            var lookup = (IDictionary)fontType.GetField("m_CharacterLookupDictionary", BindingFlags.NonPublic | BindingFlags.Instance).GetValue(font);
            Assert.That(lookup.Contains((uint)0x200B), Is.True, "Fixture must begin with TMP's synthesized caret sentinel.");
            lookup.Remove((uint)0x200B);
            string before = SharedFontState(font);
            var objects = AllSceneObjects();
            int group = Undo.GetCurrentGroup();
            var result = Send(Request("tmp_input"));
            Assert.That(result.Value<bool>("success"), Is.False, result.ToString());
            Assert.That(result["data"].Value<string>("role"), Is.EqualTo("caret_sentinel"));
            Assert.That(result["data"].Value<string>("unicode"), Is.EqualTo("U+200B"));
            Assert.That(AllSceneObjects(), Is.EquivalentTo(objects));
            Assert.That(Undo.GetCurrentGroup(), Is.EqualTo(group));
            Assert.That(SharedFontState(font), Is.EqualTo(before));
        }

        [TestCase("Ready\u0001", "U+0001")]
        [TestCase("Ready\u007F", "U+007F")]
        [TestCase("Ready\\u003F", "U+003F")]
        [TestCase("Ready\\U0000003F", "U+003F")]
        [TestCase("Ready\\u41G0", "U+005C")]
        public void DynamicDecodedPayloadMissingGlyphRefusesBeforeAllocation(string label, string unicode)
        {
            UseFont();
            Write(font, "atlasPopulationMode", Enum.Parse(fontType.GetProperty("atlasPopulationMode").PropertyType, "Dynamic"));
            CacheGlyphs(font, "\u2026");
            string before = SharedFontState(font);
            var objects = AllSceneObjects();
            int group = Undo.GetCurrentGroup();
            var result = Send(WithProperty(Request("toggle"), "label", label));
            Assert.That(result.Value<bool>("success"), Is.False, result.ToString());
            Assert.That(result["data"].Value<string>("role"), Is.EqualTo("label"));
            Assert.That(result["data"].Value<string>("unicode"), Is.EqualTo(unicode));
            Assert.That(AllSceneObjects(), Is.EquivalentTo(objects));
            Assert.That(Undo.GetCurrentGroup(), Is.EqualTo(group));
            Assert.That(SharedFontState(font), Is.EqualTo(before));
        }

        [Test]
        public void InputTextWithNullCharacterRefusesBeforeAllocation()
        {
            UseFont();
            var objects = AllSceneObjects();
            int group = Undo.GetCurrentGroup();
            var result = Send(WithProperty(Request("tmp_input"), "text", "Ready\0?"));
            Assert.That(result.Value<bool>("success"), Is.False, result.ToString());
            Assert.That(result.Value<string>("error"), Is.EqualTo("compound_input_null_character"));
            Assert.That(AllSceneObjects(), Is.EquivalentTo(objects));
            Assert.That(Undo.GetCurrentGroup(), Is.EqualTo(group));
        }

        [TestCase("Ready\\u0054")]
        [TestCase("Ready\nNext")]
        [TestCase("Ready\tNext")]
        public void DynamicSafeDecodedTextCreatesWithoutAtlasMutation(string label)
        {
            UseFont();
            Write(font, "atlasPopulationMode", Enum.Parse(fontType.GetProperty("atlasPopulationMode").PropertyType, "Dynamic"));
            CacheGlyphs(font, "\u2026");
            string before = SharedFontState(font);
            RenderAndUndo(Send(WithProperty(Request("toggle"), "label", label)));
            AssertSharedFontOnlyDerivedRatiosChanged(font, before);
        }

        [Test]
        public void MissingDynamicLigatureOutputRefusesBeforeAllocation()
        {
            UseFont();
            Write(font, "atlasPopulationMode", Enum.Parse(fontType.GetProperty("atlasPopulationMode").PropertyType, "Dynamic"));
            CacheGlyphs(font, "\u2026");
            EnableLigaturesWithOutput(uint.MaxValue);
            string before = SharedFontState(font);
            var objects = AllSceneObjects();
            int group = Undo.GetCurrentGroup();
            var result = Send(Request("toggle"));
            Assert.That(result.Value<bool>("success"), Is.False, result.ToString());
            Assert.That(result["data"].Value<string>("reason"), Is.EqualTo("ligature_glyph_may_populate_atlas"));
            Assert.That(result["data"].Value<uint>("glyph_index"), Is.EqualTo(uint.MaxValue));
            Assert.That(AllSceneObjects(), Is.EquivalentTo(objects));
            Assert.That(Undo.GetCurrentGroup(), Is.EqualTo(group));
            Assert.That(SharedFontState(font), Is.EqualTo(before));
        }

        [Test]
        public void CachedDynamicLigatureOutputCreatesWithoutAtlasMutation()
        {
            UseFont();
            Write(font, "atlasPopulationMode", Enum.Parse(fontType.GetProperty("atlasPopulationMode").PropertyType, "Dynamic"));
            CacheGlyphs(font, "\u2026");
            uint cachedOutput = (uint)Read(((IEnumerable)Read(font, "glyphTable")).Cast<object>().First(), "index");
            EnableLigaturesWithOutput(cachedOutput);
            string before = SharedFontState(font);
            RenderAndUndo(Send(Request("toggle")));
            AssertSharedFontOnlyDerivedRatiosChanged(font, before);
        }

        [TestCase("\uFE0F", "U+FE0F")]
        [TestCase("\udb40\udd00", "U+E0100")]
        public void DynamicVariationSequenceRefusesBeforeAllocation(string selector, string unicode)
        {
            UseFont();
            Write(font, "atlasPopulationMode", Enum.Parse(fontType.GetProperty("atlasPopulationMode").PropertyType, "Dynamic"));
            CacheGlyphs(font, "\u2026");
            string before = SharedFontState(font);
            var objects = AllSceneObjects();
            int group = Undo.GetCurrentGroup();
            var result = Send(WithProperty(Request("toggle"), "label", "a" + selector));
            Assert.That(result.Value<bool>("success"), Is.False, result.ToString());
            Assert.That(result["data"].Value<string>("reason"), Is.EqualTo("variation_glyph_may_populate_atlas"));
            Assert.That(result["data"].Value<string>("unicode"), Is.EqualTo(unicode));
            Assert.That(AllSceneObjects(), Is.EquivalentTo(objects));
            Assert.That(Undo.GetCurrentGroup(), Is.EqualTo(group));
            Assert.That(SharedFontState(font), Is.EqualTo(before));
        }

        [Test]
        public void StaticVariationSequenceCreatesWithoutAtlasMutation()
        {
            UseFont();
            string before = SharedFontState(font);
            RenderAndUndo(Send(WithProperty(Request("toggle"), "label", "a\uFE0F")));
            AssertSharedFontOnlyDerivedRatiosChanged(font, before);
        }

        [Test]
        public void UnusedDynamicGlobalFallbackDoesNotBlockOrMutate()
        {
            UseFont();
            Write(font, "atlasPopulationMode", Enum.Parse(fontType.GetProperty("atlasPopulationMode").PropertyType, "Dynamic"));
            CacheGlyphs(font, "\u2026");
            Write(font, "atlasPopulationMode", Enum.Parse(fontType.GetProperty("atlasPopulationMode").PropertyType, "Static"));
            var unused = NewDynamicFont();
            var slot = settingsType.GetField("m_fallbackFontAssets", BindingFlags.NonPublic | BindingFlags.Instance);
            Assert.That(slot, Is.Not.Null);
            var globals = (IList)Activator.CreateInstance(slot.FieldType);
            globals.Add(unused);
            slot.SetValue(settings, globals);
            string before = SharedFontState(unused);
            RenderAndUndo(Send(Request("toggle")));
            Assert.That(SharedFontState(unused), Is.EqualTo(before));
            Assert.That(ReferenceEquals(slot.GetValue(settings), globals), Is.True);
        }

        [Test]
        public void CachedDynamicFallbackGlyphCreatesWithoutSharedFontMutation()
        {
            UseFont();
            var fallback = NewDynamicFont();
            CacheGlyphs(fallback, "?\u2026");
            var table = (IList)Activator.CreateInstance(fontType.GetProperty("fallbackFontAssetTable").PropertyType);
            table.Add(fallback);
            Write(font, "fallbackFontAssetTable", table);
            string beforeRoot = SharedFontState(font),
                beforeFallback = SharedFontState(fallback);
            RenderAndUndo(Send(WithProperty(Request("toggle"), "label", "Ready?")));
            AssertSharedFontOnlyDerivedRatiosChanged(font, beforeRoot);
            AssertSharedFontOnlyDerivedRatiosChanged(fallback, beforeFallback);
        }

        [Test]
        public void MissingDynamicGlyphRefusesBeforeAllocationWithRemediation()
        {
            UseFont();
            Write(font, "atlasPopulationMode", Enum.Parse(fontType.GetProperty("atlasPopulationMode").PropertyType, "Dynamic"));
            CacheGlyphs(font, "\u2026");
            string before = SharedFontState(font);
            var objects = AllSceneObjects();
            int group = Undo.GetCurrentGroup();
            bool dirty = owned.isDirty;
            var result = Send(WithProperty(Request("toggle"), "label", "Ready?"));
            Assert.That(result.Value<bool>("success"), Is.False, result.ToString());
            Assert.That(result.Value<string>("message"), Is.EqualTo("compound_font_glyph_unavailable"));
            Assert.That(result["data"].Value<string>("reason"), Is.EqualTo("dynamic_font_missing_glyph"));
            Assert.That(result["data"].Value<string>("role"), Is.EqualTo("label"));
            Assert.That(result["data"].Value<string>("unicode"), Is.EqualTo("U+003F"));
            StringAssert.Contains("Populate", result["data"].Value<string>("remediation"));
            Assert.That(AllSceneObjects(), Is.EquivalentTo(objects));
            Assert.That(Undo.GetCurrentGroup(), Is.EqualTo(group));
            Assert.That(owned.isDirty, Is.EqualTo(dirty));
            Assert.That(SharedFontState(font), Is.EqualTo(before));
        }

        [Test]
        public void CachedFallbackDoesNotBypassMissingDynamicAncestor()
        {
            UseFont();
            var fallback = NewDynamicFont();
            CacheGlyphs(fallback, "?");
            Write(fallback, "atlasPopulationMode", Enum.Parse(fontType.GetProperty("atlasPopulationMode").PropertyType, "Static"));
            var table = (IList)Activator.CreateInstance(fontType.GetProperty("fallbackFontAssetTable").PropertyType);
            table.Add(fallback);
            Write(font, "fallbackFontAssetTable", table);
            Write(font, "atlasPopulationMode", Enum.Parse(fontType.GetProperty("atlasPopulationMode").PropertyType, "Dynamic"));
            CacheGlyphs(font, "\u2026");
            string beforeRoot = SharedFontState(font),
                beforeFallback = SharedFontState(fallback);
            var objects = AllSceneObjects();
            int group = Undo.GetCurrentGroup();
            var result = Send(WithProperty(Request("toggle"), "label", "Ready?"));
            Assert.That(result.Value<bool>("success"), Is.False, result.ToString());
            Assert.That(result["data"].Value<string>("reason"), Is.EqualTo("dynamic_font_missing_glyph"));
            Assert.That(result["data"].Value<string>("unicode"), Is.EqualTo("U+003F"));
            Assert.That(AllSceneObjects(), Is.EquivalentTo(objects));
            Assert.That(Undo.GetCurrentGroup(), Is.EqualTo(group));
            Assert.That(SharedFontState(font), Is.EqualTo(beforeRoot));
            Assert.That(SharedFontState(fallback), Is.EqualTo(beforeFallback));
        }

        [TestCase("ellipsis", "U+2026")]
        [TestCase("underline", "U+005F")]
        public void MissingDynamicSpecialGlyphRefusesBeforeAllocation(string special, string unicode)
        {
            UseFont();
            ScriptableObject selected;
            if (special == "ellipsis")
            {
                selected = font;
                Write(selected, "atlasPopulationMode", Enum.Parse(fontType.GetProperty("atlasPopulationMode").PropertyType, "Dynamic"));
            }
            else
            {
                selected = NewDynamicFont();
                CacheGlyphs(selected, "Toggle\u2026");
                settingsType.GetField("m_defaultFontAsset", BindingFlags.NonPublic | BindingFlags.Instance).SetValue(settings, selected);
            }
            string before = SharedFontState(selected);
            var objects = AllSceneObjects();
            int group = Undo.GetCurrentGroup();
            var result = Send(Request("toggle"));
            Assert.That(result.Value<bool>("success"), Is.False, result.ToString());
            Assert.That(result.Value<string>("message"), Is.EqualTo("compound_font_glyph_unavailable"));
            Assert.That(result["data"].Value<string>("role"), Is.EqualTo(special));
            Assert.That(result["data"].Value<string>("unicode"), Is.EqualTo(unicode));
            Assert.That(AllSceneObjects(), Is.EquivalentTo(objects));
            Assert.That(Undo.GetCurrentGroup(), Is.EqualTo(group));
            Assert.That(SharedFontState(selected), Is.EqualTo(before));
        }

        [Test]
        public void StaticDefaultMissingEllipsisRejectsReachableDynamicGlobalFallback()
        {
            UseFont();
            var fallback = NewDynamicFont();
            var slot = settingsType.GetField("m_fallbackFontAssets", BindingFlags.NonPublic | BindingFlags.Instance);
            var globals = (IList)Activator.CreateInstance(slot.FieldType);
            globals.Add(fallback);
            slot.SetValue(settings, globals);
            string beforeRoot = SharedFontState(font),
                beforeFallback = SharedFontState(fallback);
            var objects = AllSceneObjects();
            int group = Undo.GetCurrentGroup();
            var result = Send(Request("toggle"));
            Assert.That(result.Value<bool>("success"), Is.False, result.ToString());
            Assert.That(result["data"].Value<string>("role"), Is.EqualTo("ellipsis"));
            Assert.That(result["data"].Value<string>("unicode"), Is.EqualTo("U+2026"));
            Assert.That(AllSceneObjects(), Is.EquivalentTo(objects));
            Assert.That(Undo.GetCurrentGroup(), Is.EqualTo(group));
            Assert.That(SharedFontState(font), Is.EqualTo(beforeRoot));
            Assert.That(SharedFontState(fallback), Is.EqualTo(beforeFallback));
        }

        [TestCase("scroll_view")]
        [TestCase("toggle")]
        [TestCase("slider")]
        [TestCase("tmp_input")]
        public void ConstructedGraphReadbackAndSingleUndoRedo(string kind)
        {
            if (kind == "toggle" || kind == "tmp_input")
                UseFont();
            string parentBefore = EditorJsonUtility.ToJson(root.GetComponent<RectTransform>());
            var result = Send(Request(kind));
            var created = Created(result);
            AssertGraph(created, kind, result);
            Assert.That(EditorJsonUtility.ToJson(root.GetComponent<RectTransform>()), Is.EqualTo(parentBefore));
            var old = created.GetComponentsInChildren<Transform>(true).Select(t => t.gameObject).ToArray();
            Undo.PerformUndo();
            foreach (var item in old)
                Assert.That(item == null, Is.True, "The complete owned subtree must be undone.");
            Undo.PerformRedo();
            var restored = root.transform.Cast<Transform>().Single(t => t.name == "OwnedControl").gameObject;
            AssertGraph(restored, kind, null);
            Assert.That(restored.transform.parent, Is.EqualTo(root.transform));
        }

        private static void EmitWitness(string prefix, string json)
        {
            try
            {
                TestContext.Out.WriteLine(prefix + json);
            }
            catch (Exception error)
            {
                try
                {
                    Debug.Log(prefix + new JObject { ["diagnosticEmitError"] = error.GetType().FullName }.ToString(Newtonsoft.Json.Formatting.None));
                }
                catch (Exception)
                {
                    // Both diagnostic output surfaces failed. Missing evidence remains unaccepted;
                    // never replace the original test assertion or Undo exception with a logging failure.
                }
            }
        }

        private static string BoundedWitness(Func<JObject> capture)
        {
            try
            {
                string json = capture().ToString(Newtonsoft.Json.Formatting.None);
                int bytes = System.Text.Encoding.UTF8.GetByteCount(json);
                return bytes <= 16384 ? json : new JObject { ["diagnosticOversizeBytes"] = bytes }.ToString(Newtonsoft.Json.Formatting.None);
            }
            catch (Exception error)
            {
                return new JObject { ["diagnosticReadError"] = error.GetType().FullName }.ToString(Newtonsoft.Json.Formatting.None);
            }
        }

        // Diagnostic reads only: do not change native depth, culling, activation, Awake or font caches.
        private static object ObserveNativeField(object value, string name)
        {
            for (Type type = value.GetType(); type != null; type = type.BaseType)
            {
                var field = type.GetField(name, BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Instance | BindingFlags.DeclaredOnly);
                if (field != null)
                    return field.GetValue(value);
            }
            return "<missing>";
        }

        private static JToken ObserveWitnessValue(string field, Func<object> read)
        {
            try
            {
                object value = read();
                return value == null ? JValue.CreateNull() : JToken.FromObject(value);
            }
            catch (Exception error)
            {
                return new JObject { ["diagnosticField"] = field, ["diagnosticReadError"] = error.GetType().FullName };
            }
        }

        private string RaycastWitness(GameObject target, Vector2 screen) =>
            BoundedWitness(() =>
            {
                var canvas = root.GetComponent<Canvas>();
                var graphics = RequireAny("UnityEngine.UI.GraphicRegistry")
                    .GetMethod("GetRaycastableGraphicsForCanvas", BindingFlags.Public | BindingFlags.Static)
                    .Invoke(null, new object[] { canvas });
                int registryCount = (int)Read(graphics, "Count");
                var item = graphics.GetType().GetProperty("Item", new[] { typeof(int) });
                if (item == null || item.GetGetMethod() == null)
                    throw new MissingMemberException("Native GraphicRegistry Item[int] getter unavailable.");
                var rows = new JArray();
                for (int index = 0; index < Math.Min(registryCount, 32); index++)
                {
                    var graphic = (Component)item.GetValue(graphics, new object[] { index });
                    var rect = (RectTransform)graphic.transform;
                    var renderer = graphic.GetComponent<CanvasRenderer>();
                    rows.Add(
                        new JObject
                        {
                            ["name"] = graphic.gameObject.name,
                            ["type"] = graphic.GetType().FullName,
                            ["inTarget"] = graphic.gameObject == target || graphic.transform.IsChildOf(target.transform),
                            ["activeSelf"] = graphic.gameObject.activeSelf,
                            ["activeInHierarchy"] = graphic.gameObject.activeInHierarchy,
                            ["raycastTarget"] = (bool)Read(graphic, "raycastTarget"),
                            ["depth"] = ObserveWitnessValue("depth", () => renderer.absoluteDepth),
                            ["cull"] = ObserveWitnessValue("cull", () => renderer.cull),
                            ["contains"] = RectTransformUtility.RectangleContainsScreenPoint(rect, screen, canvas.worldCamera),
                        }
                    );
                }
                return new JObject
                {
                    ["scope"] = "actual before-handler raycast witness; no readiness inferred",
                    ["screen"] = new JArray(screen.x, screen.y),
                    ["screenSize"] = new JArray(Screen.width, Screen.height),
                    ["frame"] = Time.frameCount,
                    ["canvasActive"] = canvas.isActiveAndEnabled,
                    ["renderMode"] = canvas.renderMode.ToString(),
                    ["targetDisplay"] = canvas.targetDisplay,
                    ["registryCount"] = registryCount,
                    ["rowsBound"] = 32,
                    ["graphicsTruncated"] = registryCount > 32,
                    ["graphics"] = rows,
                };
            });

        private static string TextRedoWitness(Component text) =>
            BoundedWitness(() =>
            {
                var info = ObserveNativeField(text, "m_textInfo");
                var currentFont = (UnityEngine.Object)Read(text, "font");
                var lookup = currentFont == null ? null : ObserveNativeField(currentFont, "m_CharacterLookupDictionary") as IDictionary;
                object character = lookup != null && lookup.Contains((uint)0x51) ? lookup[(uint)0x51] : null;
                var characterFont = character == null ? null : Read(character, "textAsset") as UnityEngine.Object;
                var ancestors = new JArray();
                Transform parent = text.transform.parent;
                for (int depth = 0; parent != null && depth < 8; depth++, parent = parent.parent)
                {
                    var ancestor = parent.gameObject;
                    string name = ancestor.name;
                    ancestors.Add(
                        new JObject
                        {
                            ["name"] = name.Substring(0, Math.Min(name.Length, 128)),
                            ["instanceID"] = ancestor.GetInstanceIDCompat(),
                            ["activeSelf"] = ancestor.activeSelf,
                            ["activeInHierarchy"] = ancestor.activeInHierarchy,
                        }
                    );
                }
                return new JObject
                {
                    ["scope"] = "actual native TMP state; no lifecycle or cache mutation",
                    ["ancestors"] = ancestors,
                    ["ancestorBound"] = 8,
                    ["ancestorsTruncated"] = parent != null,
                    ["activeSelf"] = text.gameObject.activeSelf,
                    ["activeInHierarchy"] = text.gameObject.activeInHierarchy,
                    ["awake"] = JToken.FromObject(ObserveNativeField(text, "m_isAwake")),
                    ["textField"] = ObserveNativeField(text, "m_text") as string,
                    ["textBackingStringDirty"] = JToken.FromObject(ObserveNativeField(text, "m_IsTextBackingStringDirty")),
                    ["inputSource"] = ObserveNativeField(text, "m_inputSource").ToString(),
                    ["font"] = currentFont == null ? 0 : currentFont.GetInstanceIDCompat(),
                    ["fontLookupPresent"] = lookup != null,
                    ["sharedMaterials"] = new JArray(
                        (ObserveNativeField(text, "m_sharedMaterials") as UnityEngine.Object[] ?? Array.Empty<UnityEngine.Object>())
                            .Take(8)
                            .Select(item => item == null ? 0 : item.GetInstanceIDCompat())
                    ),
                    ["cachedQ"] = character != null,
                    ["cachedQFont"] = characterFont == null ? 0 : characterFont.GetInstanceIDCompat(),
                    ["materialCount"] = info == null || info is string ? -1 : (int)info.GetType().GetField("materialCount").GetValue(info),
                    ["submeshes"] = text.GetComponentsInChildren(Require("TMPro.TMP_SubMeshUI"), true).Length,
                };
            });

        [TestCase("toggle")]
        [TestCase("slider")]
        [TestCase("scroll_view")]
        [TestCase("tmp_input")]
        public void ConstructedControlHandlesRealInputEvents(string kind)
        {
            if (kind == "toggle" || kind == "tmp_input")
                UseFont();
            var go = Created(Send(Request(kind)));
            var peer = Created(Send(Request("slider", "Peer")));
            float peerValue = (float)Read(peer.GetComponent(Ui("Slider")), "value");
            var esGo = new GameObject("OwnedInputEventSystem", Require("UnityEngine.EventSystems.EventSystem"));
            GameObject renderCameraObject = null;
            Camera renderCamera = null;
            RenderTexture renderTarget = null;
            var previousRenderTarget = RenderTexture.active;
            try
            {
                var es = esGo.GetComponent(Require("UnityEngine.EventSystems.EventSystem"));
                renderCameraObject = new GameObject("UI16OwnedInputCamera", typeof(Camera));
                renderCamera = renderCameraObject.GetComponent<Camera>();
                renderCamera.enabled = false;
                renderCamera.orthographic = true;
                renderCamera.transform.position = new Vector3(0, 0, -10);
                renderCamera.nearClipPlane = 0.1f;
                renderCamera.farClipPlane = 100;
                renderCamera.clearFlags = CameraClearFlags.SolidColor;
                renderCamera.backgroundColor = Color.clear;
                renderCamera.allowHDR = renderCamera.allowMSAA = false;
                renderTarget = new RenderTexture(640, 480, 24);
                renderCamera.targetTexture = renderTarget;
                var canvas = root.GetComponent<Canvas>();
                canvas.renderMode = RenderMode.ScreenSpaceCamera;
                canvas.worldCamera = renderCamera;
                canvas.planeDistance = 1;
#if UNITY_2022_2_OR_NEWER
                canvas.updateRectTransformForStandalone = StandaloneRenderResize.Enabled;
#endif
                var raycaster = root.AddComponent(Ui("GraphicRaycaster"));
                Canvas.ForceUpdateCanvases();
                // A rebuild notification is not a draw: submit one actual owned-camera render.
                renderCamera.Render();
                Assert.That(
                    go.GetComponentsInChildren<CanvasRenderer>(true).Any(renderer => renderer.absoluteDepth >= 0),
                    Is.True,
                    "The constructed control must have a real native rendered depth before input raycasting."
                );
                Vector2 screen = RectTransformUtility.WorldToScreenPoint(
                    renderCamera,
                    kind == "toggle" ? go.transform.Find("Background").position : go.transform.position
                );
                Assert.That(
                    RectTransformUtility.RectangleContainsScreenPoint((RectTransform)go.transform, screen, renderCamera),
                    Is.True,
                    "The handler point must lie inside the real constructed rect."
                );
                var pointer = EventData("PointerEventData", es);
                Write(pointer, "position", screen);
                Write(pointer, "button", Enum.Parse(pointer.GetType().GetProperty("button").PropertyType, "Left"));
                var hits = (IList)Activator.CreateInstance(typeof(List<>).MakeGenericType(RequireAny("UnityEngine.EventSystems.RaycastResult")));
                Invoke(raycaster, "Raycast", pointer, hits);
                string raycastWitness = RaycastWitness(go, screen);
                EmitWitness("UI16_RAYCAST ", raycastWitness);
                Assert.That(hits.Count, Is.GreaterThan(0), raycastWitness);
                Assert.That(
                    hits.Cast<object>()
                        .Select(hit => (GameObject)Read(hit, "gameObject"))
                        .Any(hit => hit != null && (hit.transform.IsChildOf(go.transform) || hit == go)),
                    Is.True,
                    "Independent native raycast must hit this control before every handler witness, including TMP input."
                );
                object targetHit = hits.Cast<object>()
                    .First(hit =>
                    {
                        var hitObject = (GameObject)Read(hit, "gameObject");
                        return hitObject != null && (hitObject.transform.IsChildOf(go.transform) || hitObject == go);
                    });
                // Carry the actual raycaster result, so native handlers use its actual event camera.
                Write(pointer, "pointerCurrentRaycast", targetHit);
                Write(pointer, "pointerPressRaycast", targetHit);
                if (kind == "tmp_input")
                {
                    var input = go.GetComponent(Require("TMPro.TMP_InputField"));
                    int changed = 0;
                    AddStringListener(Read(input, "onValueChanged"), _ => changed++);
                    Invoke(input, "OnPointerClick", pointer);
                    Invoke(input, "ActivateInputField");
                    Invoke(input, "ProcessEvent", Event.KeyboardEvent("a"));
                    Assert.That((string)Read(input, "text"), Is.EqualTo("a"));
                    Assert.That(changed, Is.EqualTo(1));
                    var submit = Read(input, "onSubmit");
                    int events = 0;
                    AddStringListener(submit, _ => events++);
                    Invoke(input, "OnSubmit", EventData("BaseEventData", es));
                    Assert.That(events, Is.EqualTo(1));
                    Assert.That(Read(go.GetComponent(Require("TMPro.TMP_InputField")), "text"), Is.EqualTo("a"));
                }
                else if (kind == "toggle")
                {
                    var toggle = go.GetComponent(Ui("Toggle"));
                    int events = 0;
                    AddBoolListener(Read(toggle, "onValueChanged"), _ => events++);
                    Assert.That(Read(toggle, "isOn"), Is.EqualTo(false));
                    Invoke(toggle, "OnPointerClick", pointer);
                    Assert.That(Read(toggle, "isOn"), Is.EqualTo(true));
                    Assert.That(events, Is.EqualTo(1));
                }
                else if (kind == "slider")
                {
                    var slider = go.GetComponent(Ui("Slider"));
                    int events = 0;
                    AddFloatListener(Read(slider, "onValueChanged"), _ => events++);
                    var rt = (RectTransform)go.transform;
                    Vector2 right = RectTransformUtility.WorldToScreenPoint(renderCamera, rt.TransformPoint(new Vector3(rt.rect.xMax - 1, 0, 0)));
                    Vector2 left = RectTransformUtility.WorldToScreenPoint(renderCamera, rt.TransformPoint(new Vector3(rt.rect.xMin + 1, 0, 0)));
                    Write(pointer, "position", right);
                    Write(pointer, "pressPosition", right);
                    Invoke(slider, "OnPointerDown", pointer);
                    Assert.That((float)Read(slider, "value"), Is.GreaterThan(0.8f));
                    Assert.That(events, Is.GreaterThan(0));
                    int beforeDrag = events;
                    Write(pointer, "position", left);
                    Invoke(slider, "OnDrag", pointer);
                    Assert.That((float)Read(slider, "value"), Is.LessThan(0.2f));
                    Assert.That(events, Is.GreaterThan(beforeDrag), "Real drag must change the actual value and emit another event.");
                }
                else
                {
                    var scroll = go.GetComponent(Ui("ScrollRect"));
                    var content = (RectTransform)Read(scroll, "content");
                    var item = new GameObject("OwnedScrollItem", typeof(RectTransform));
                    item.transform.SetParent(content, false);
                    ((RectTransform)item.transform).sizeDelta = new Vector2(100, 1000);
                    Canvas.ForceUpdateCanvases();
                    Invoke(scroll, "Rebuild", Enum.Parse(RequireAny("UnityEngine.UI.CanvasUpdate"), "PostLayout"));
                    Assert.That(
                        content.rect.height,
                        Is.GreaterThan(((RectTransform)Read(scroll, "viewport")).rect.height),
                        "Real content layout must provide a nonzero scroll range."
                    );
                    int events = 0;
                    AddVectorListener(Read(scroll, "onValueChanged"), _ => events++);
                    Vector2 before = content.anchoredPosition;
                    Write(pointer, "scrollDelta", new Vector2(0, -4));
                    Invoke(scroll, "OnScroll", pointer);
                    FlushScrollEvents(scroll);
                    Assert.That(content.anchoredPosition, Is.Not.EqualTo(before));
                    Assert.That(events, Is.GreaterThan(0));
                    Vector2 beforeDrag = content.anchoredPosition;
                    int eventsBeforeDrag = events;
                    Write(pointer, "position", screen);
                    Write(pointer, "pressPosition", screen);
                    Invoke(scroll, "OnBeginDrag", pointer);
                    Write(pointer, "position", screen + new Vector2(0, 40));
                    Invoke(scroll, "OnDrag", pointer);
                    Invoke(scroll, "OnEndDrag", pointer);
                    FlushScrollEvents(scroll);
                    Assert.That(content.anchoredPosition, Is.Not.EqualTo(beforeDrag));
                    Assert.That(events, Is.GreaterThan(eventsBeforeDrag), "Drag must cause the native ScrollRect lifecycle to emit onValueChanged.");
                }
                Assert.That(Read(peer.GetComponent(Ui("Slider")), "value"), Is.EqualTo(peerValue), "An independent peer must stay unchanged.");
            }
            finally
            {
                try
                {
                    if (renderCamera != null)
                        renderCamera.targetTexture = null;
                }
                finally
                {
                    RenderTexture.active = previousRenderTarget;
                    try
                    {
                        if (renderCameraObject != null)
                            UnityEngine.Object.DestroyImmediate(renderCameraObject);
                    }
                    finally
                    {
                        try
                        {
                            if (renderTarget != null)
                                UnityEngine.Object.DestroyImmediate(renderTarget);
                        }
                        finally
                        {
                            UnityEngine.Object.DestroyImmediate(esGo);
                        }
                    }
                }
            }
        }

        [TestCase("after_root")]
        [TestCase("after_children")]
        [TestCase("after_reference_wiring")]
        [TestCase("after_new_event_system")]
        public void MidBuildFailureRemovesWholeOwnedTree(string stage)
        {
            var before = AllSceneObjects();
            var request = Request("slider");
            if (stage == "after_new_event_system")
            {
#if !ENABLE_LEGACY_INPUT_MANAGER
                Assert.That(Send(WithProperty(request, "eventSystem", "create_legacy_if_missing")).Value<bool>("success"), Is.False);
                Assert.That(AllSceneObjects(), Is.EquivalentTo(before));
                return;
#else
                Assert.That(LoadedEventSystems().Length, Is.Zero, "The newES failure control requires an isolated fresh scene set.");
                WithProperty(request, "eventSystem", "create_legacy_if_missing");
#endif
            }
            SetHook(
                "FaultForTests",
                (Action<string, GameObject>)(
                    (actual, _) =>
                    {
                        if (actual == stage)
                            throw new InvalidOperationException("owned_build_fault");
                    }
                )
            );
            try
            {
                var result = Send(request);
                Assert.That(result.Value<bool>("success"), Is.False);
                Assert.That(result["data"].Value<string>("rollback_status"), Is.EqualTo("complete"));
                Assert.That(result["data"]["errors"].Values<JObject>().Any(e => e.Value<string>("phase") == "build"), Is.True);
                Assert.That(AllSceneObjects(), Is.EquivalentTo(before));
            }
            finally
            {
                ResetHooks();
            }
        }

        [TestCase("none")]
        [TestCase("one_inactive")]
        [TestCase("multiple")]
        public void DefaultPreservesEventSystemsAndSelection(string state)
        {
            var added = new List<GameObject>();
            try
            {
                Assert.That(LoadedEventSystems().Length, Is.Zero, "Require an isolated EventSystem baseline.");
                int count =
                    state == "none" ? 0
                    : state == "one_inactive" ? 1
                    : 2;
                for (int i = 0; i < count; i++)
                {
                    var es = new GameObject("OwnedES", Require("UnityEngine.EventSystems.EventSystem"));
                    es.SetActive(false);
                    added.Add(es);
                }
                var before = LoadedEventSystems();
                var selection = Selection.activeObject;
                var result = Send(Request("slider"));
                Created(result);
                Assert.That(LoadedEventSystems(), Is.EquivalentTo(before));
                Assert.That(added.All(go => !go.activeSelf), Is.True);
                Assert.That(Selection.activeObject, Is.EqualTo(selection));
                Assert.That(result["data"].Value<string>("event_system"), Is.EqualTo("preserved"));
            }
            finally
            {
                foreach (var go in added)
                    UnityEngine.Object.DestroyImmediate(go);
            }
        }

        [TestCase(0)]
        [TestCase(1)]
        [TestCase(2)]
        public void ExplicitEventSystemOptionNeverReconfiguresExisting(int count)
        {
            Assert.That(LoadedEventSystems().Length, Is.Zero);
            var added = new List<GameObject>();
            try
            {
                for (int i = 0; i < count; i++)
                {
                    var es = new GameObject("OwnedES", Require("UnityEngine.EventSystems.EventSystem"));
                    es.SetActive(false);
                    added.Add(es);
                }
                var before = AllSceneObjects();
                var result = Send(WithProperty(Request("slider"), "eventSystem", "create_legacy_if_missing"));
#if ENABLE_LEGACY_INPUT_MANAGER
                if (count < 2)
                {
                    Created(result);
                    Assert.That(result["data"].Value<string>("event_system"), Is.EqualTo(count == 0 ? "created_legacy" : "reused_unchanged"));
                    Assert.That(LoadedEventSystems().Length, Is.EqualTo(1));
                    Assert.That(result["data"]["event_system_state"].Value<bool>("active_in_hierarchy"), Is.EqualTo(count == 0));
                    Assert.That(result["data"]["event_system_state"].Value<bool>("legacy_module_present"), Is.EqualTo(count == 0));
                }
                else
#endif
                {
                    Assert.That(result.Value<bool>("success"), Is.False);
                    Assert.That(AllSceneObjects(), Is.EquivalentTo(before));
                }
                Assert.That(added.All(go => !go.activeSelf), Is.True);
                foreach (var es in added)
                    Assert.That(es.GetComponents<Component>().Length, Is.EqualTo(2));
            }
            finally
            {
                foreach (var go in added)
                    if (go != null)
                        UnityEngine.Object.DestroyImmediate(go);
            }
        }

        [TestCase("type")]
        [TestCase("member")]
        [TestCase("font_missing")]
        [TestCase("emoji_capability")]
        public void UnsupportedDependencyFailsBeforeUndoOrAllocation(string fault)
        {
            UseFont();
            if (fault == "font_missing")
                settingsType.GetField("m_defaultFontAsset", BindingFlags.NonPublic | BindingFlags.Instance).SetValue(settings, null);
            if (fault == "type")
                SetHook("ResolveForTests", (Func<string, Type>)(name => name == "TMPro.TMP_InputField" ? null : UnityTypeResolver.ResolveAny(name)));
            if (fault == "member" || fault == "emoji_capability")
                SetHook(
                    "PropertyForTests",
                    (Func<Type, string, PropertyInfo>)(
                        (type, name) => name == (fault == "member" ? "textComponent" : "emojiFallbackSupport") ? null : type.GetProperty(name)
                    )
                );
            var before = AllSceneObjects();
            int group = Undo.GetCurrentGroup();
            bool dirty = owned.isDirty;
            try
            {
                Assert.That(Send(Request("tmp_input")).Value<bool>("success"), Is.False);
                Assert.That(AllSceneObjects(), Is.EquivalentTo(before));
                Assert.That(Undo.GetCurrentGroup(), Is.EqualTo(group));
                Assert.That(owned.isDirty, Is.EqualTo(dirty));
            }
            finally
            {
                ResetHooks();
            }
        }

        [TestCase("label")]
        [TestCase("text")]
        [TestCase("placeholder")]
        public void MarkupLookingTextIsLiteralWithFallbackRoutesDisabled(string role)
        {
            UseFont();
            string literal = "<font=Foreign><sprite=0>Owned</font>";
            var request = Request(role == "label" ? "toggle" : "tmp_input");
            WithProperty(request, role, literal);
            var result = Send(request);
            var go = Created(result);
            var text = Role(result, role).GetComponent(Require("TMPro.TextMeshProUGUI"));
            // TMP_InputField.UpdateLabel appends one caret-tracking sentinel to its display component.
            // Check the semantic input separately and never trim arbitrary characters from either value.
            if (role == "text")
                Assert.That(Read(go.GetComponent(Require("TMPro.TMP_InputField")), "text"), Is.EqualTo(literal));
            Assert.That(Read(text, "text"), Is.EqualTo(role == "text" ? literal + "\u200B" : literal));
            Assert.That(Read(text, "richText"), Is.EqualTo(false));
            Assert.That(Read(text, "emojiFallbackSupport"), Is.EqualTo(false));
            Invoke(text, "ForceMeshUpdate", true, true);
            Assert.That(Read(text, "text"), Is.EqualTo(role == "text" ? literal + "\u200B" : literal));
        }

        [Test]
        public void DynamicEmojiFallbackIsUntouchedBecauseEveryOwnedTextDisablesRoute()
        {
            UseFont();
            var factory = fontType
                .GetMethods(BindingFlags.Public | BindingFlags.Static)
                .Single(m => m.Name == "CreateFontAsset" && m.GetParameters().Length == 7 && m.GetParameters()[0].ParameterType == typeof(string));
            string path = Path.GetFullPath(Path.Combine(Application.dataPath, "..", "..", "font-input", "UI16.ttf"));
            var dynamicFont = (ScriptableObject)
                factory.Invoke(null, new object[] { path, 0, 90, 9, Enum.Parse(factory.GetParameters()[4].ParameterType, "SDFAA"), 1024, 1024 });
            fontObjects.Add(dynamicFont);
            var sprite = ScriptableObject.CreateInstance(RequireAny("TMPro.TMP_SpriteAsset"));
            try
            {
                Write(dynamicFont, "atlasPopulationMode", Enum.Parse(fontType.GetProperty("atlasPopulationMode").PropertyType, "Dynamic"));
                var slot = settingsType.GetField("m_EmojiFallbackTextAssets", BindingFlags.NonPublic | BindingFlags.Instance);
                Assert.That(slot, Is.Not.Null);
                var list = (IList)Activator.CreateInstance(slot.FieldType);
                list.Add(dynamicFont);
                list.Add(sprite);
                slot.SetValue(settings, list);
                string beforeFont = EditorJsonUtility.ToJson(dynamicFont),
                    beforeSprite = EditorJsonUtility.ToJson(sprite);
                var result = Send(WithProperty(Request("tmp_input"), "text", "1\uFE0F"));
                Created(result);
                foreach (var role in new[] { "text", "placeholder" })
                    Assert.That(Read(Role(result, role).GetComponent(Require("TMPro.TextMeshProUGUI")), "emojiFallbackSupport"), Is.EqualTo(false));
                var rendered = Role(result, "text").GetComponent(Require("TMPro.TextMeshProUGUI"));
                Invoke(rendered, "ForceMeshUpdate", true, true);
                Assert.That(ReferenceEquals(slot.GetValue(settings), list), Is.True);
                Assert.That(EditorJsonUtility.ToJson(dynamicFont), Is.EqualTo(beforeFont));
                Assert.That(EditorJsonUtility.ToJson(sprite), Is.EqualTo(beforeSprite));
                int beforeCharacters = ((IList)Read(dynamicFont, "characterTable")).Count;
                Assert.That(beforeCharacters, Is.Zero);
                Write(rendered, "emojiFallbackSupport", true); // Owned QA-only positive control of the otherwise disabled route.
                Invoke(rendered, "ForceMeshUpdate", true, true);
                Assert.That(
                    ((IList)Read(dynamicFont, "characterTable")).Count,
                    Is.GreaterThan(beforeCharacters),
                    "The supplied font and exact emoji fallback route must be effective before claiming its disabling is protective."
                );
            }
            finally
            {
                UnityEngine.Object.DestroyImmediate(sprite);
            }
        }

        [Test]
        public void EmojiSetterMismatchIsPostAllocationFailureWithCompleteOwnedRollback()
        {
            UseFont();
            var before = AllSceneObjects();
            int assigned = 0;
            SetHook(
                "AfterAssignmentForTests",
                (Action<Component, string>)(
                    (component, member) =>
                    {
                        if (member == "emojiFallbackSupport")
                        {
                            assigned++;
                            Write(component, member, true);
                        }
                    }
                )
            );
            try
            {
                var result = Send(Request("tmp_input"));
                Assert.That(assigned, Is.EqualTo(1));
                Assert.That(result.Value<bool>("success"), Is.False);
                Assert.That(result["data"].Value<bool>("allocation_started"), Is.True);
                Assert.That(result["data"].Value<string>("rollback_status"), Is.EqualTo("complete"));
                Assert.That(AllSceneObjects(), Is.EquivalentTo(before));
            }
            finally
            {
                ResetHooks();
            }
        }

        [Test]
        public void BuildRollbackAndFinalizationErrorsRemainDistinct()
        {
            var before = AllSceneObjects();
            SetHook(
                "FaultForTests",
                (Action<string, GameObject>)(
                    (stage, _) =>
                    {
                        if (stage == "after_children")
                            throw new InvalidOperationException("build");
                    }
                )
            );
            SetHook("BeforeRollbackForTests", (Action)(() => throw new IOException("rollback")));
            SetHook("BeforeFinalizeForTests", (Action)(() => throw new NotSupportedException("finalize")));
            try
            {
                var result = Send(Request("slider"));
                var errors = result["data"]["errors"].Values<JObject>().Select(e => e.Value<string>("phase")).ToArray();
                Assert.That(errors, Does.Contain("build").And.Contain("rollback").And.Contain("finalize"));
                Assert.That(
                    result["data"]["errors"].Values<JObject>().Single(e => e.Value<string>("phase") == "build").Value<string>("type"),
                    Is.EqualTo("InvalidOperationException")
                );
                Assert.That(
                    result["data"]["errors"].Values<JObject>().Single(e => e.Value<string>("phase") == "rollback").Value<string>("type"),
                    Is.EqualTo("IOException")
                );
                Assert.That(
                    result["data"]["errors"].Values<JObject>().Single(e => e.Value<string>("phase") == "finalize").Value<string>("type"),
                    Is.EqualTo("NotSupportedException")
                );
                Assert.That(result["data"].Value<string>("rollback_status"), Is.EqualTo("unknown"));
                Assert.That(AllSceneObjects(), Is.EquivalentTo(before), "Residue cleanup still runs after the rollback fault.");
            }
            finally
            {
                ResetHooks();
            }
        }

        [Test]
        public void PreexistingCallbackReparentedObjectIsNeverDestroyedAndRollbackIsUnknown()
        {
            var foreign = new GameObject("OwnedPreexistingControl", typeof(RectTransform));
            foreign.transform.SetParent(root.transform, false);
            var callback = root.AddComponent<CompoundReparentWitness>();
            callback.Protected = foreign;
            callback.Armed = true;
            SetHook(
                "FaultForTests",
                (Action<string, GameObject>)(
                    (stage, _) =>
                    {
                        if (stage == "after_root")
                            throw new InvalidOperationException("after_callback");
                    }
                )
            );
            try
            {
                var result = Send(Request("slider"));
                Assert.That(callback.Moved, Is.True, "The actual OnTransformChildrenChanged callback must reparent the existing object.");
                Assert.That(foreign != null, Is.True);
                Assert.That(ReferenceEquals(callback.Protected, foreign), Is.True);
                Assert.That(foreign.transform.parent, Is.EqualTo(root.transform));
                Assert.That(result["data"].Value<string>("rollback_status"), Is.EqualTo("unknown"));
                Assert.That(result["data"]["errors"].Values<JObject>().Any(e => e.Value<string>("phase") == "protect_preexisting"), Is.True);
            }
            finally
            {
                callback.Armed = false;
                ResetHooks();
                UnityEngine.Object.DestroyImmediate(callback);
                if (foreign != null)
                    UnityEngine.Object.DestroyImmediate(foreign);
            }
        }

        [Test]
        public void SnapshotObjectBoundRefusesBeforeUndoOrOwnedAllocation()
        {
            var holder = new GameObject("OwnedAdmissionPopulation");
            try
            {
                for (int i = 0; i < 16385; i++)
                {
                    var item = new GameObject("Bound");
                    item.transform.SetParent(holder.transform, false);
                }
                int group = Undo.GetCurrentGroup();
                int children = root.transform.childCount;
                Assert.That(Send(Request("slider")).Value<bool>("success"), Is.False);
                Assert.That(root.transform.childCount, Is.EqualTo(children));
                Assert.That(Undo.GetCurrentGroup(), Is.EqualTo(group));
            }
            finally
            {
                UnityEngine.Object.DestroyImmediate(holder);
            }
        }

        [Test]
        public void OtherSceneParentRefusesWithoutChangingActiveSceneOrUndo()
        {
            string path = folder + "/Other.unity";
            Assert.That(AssetDatabase.CopyAsset(AssetDatabase.GUIDToAssetPath(EmptySceneTemplateGuid), path), Is.True);
            var other = EditorSceneManager.OpenScene(path, OpenSceneMode.Additive);
            try
            {
                SceneManager.SetActiveScene(other);
                var foreignParent = new GameObject("OtherCanvas", typeof(RectTransform), typeof(Canvas));
                SceneManager.SetActiveScene(owned);
                var request = Request("slider");
                request["parent"] = foreignParent.GetInstanceIDCompat();
                int group = Undo.GetCurrentGroup();
                var before = AllSceneObjects();
                Assert.That(Send(request).Value<bool>("success"), Is.False);
                Assert.That(SceneManager.GetActiveScene(), Is.EqualTo(owned));
                Assert.That(Undo.GetCurrentGroup(), Is.EqualTo(group));
                Assert.That(AllSceneObjects(), Is.EquivalentTo(before));
            }
            finally
            {
                EditorSceneManager.CloseScene(other, true);
            }
        }

        [Test]
        public void FailureNeverDeletesExistingEventSystem()
        {
            var existing = new GameObject("OwnedExistingES", Require("UnityEngine.EventSystems.EventSystem"));
            existing.SetActive(false);
            SetHook(
                "FaultForTests",
                (Action<string, GameObject>)(
                    (stage, _) =>
                    {
                        if (stage == "after_children")
                            throw new InvalidOperationException("failure");
                    }
                )
            );
            try
            {
                var result = Send(Request("slider"));
                Assert.That(result.Value<bool>("success"), Is.False);
                Assert.That(existing != null, Is.True);
                Assert.That(existing.activeSelf, Is.False);
                Assert.That(existing.GetComponents<Component>().Length, Is.EqualTo(2));
            }
            finally
            {
                ResetHooks();
                UnityEngine.Object.DestroyImmediate(existing);
            }
        }

        [TestCase("LeftToRight")]
        [TestCase("RightToLeft")]
        [TestCase("BottomToTop")]
        [TestCase("TopToBottom")]
        public void SliderDirectionAndIntegralRangeReadBackExactly(string direction)
        {
            var request = Request("slider");
            WithProperty(request, "direction", direction);
            WithProperty(request, "minValue", 4);
            WithProperty(request, "maxValue", 12);
            WithProperty(request, "wholeNumbers", true);
            WithProperty(request, "value", 6);
            var result = Send(request);
            var go = Created(result);
            var slider = go.GetComponent(Ui("Slider"));
            Assert.That(Read(slider, "direction").ToString(), Is.EqualTo(direction));
            Assert.That(Read(slider, "value"), Is.EqualTo(6f));
            Assert.That(Read(slider, "minValue"), Is.EqualTo(4f));
            Assert.That(Read(slider, "maxValue"), Is.EqualTo(12f));
            Assert.That(result["data"]["values"].Value<string>("direction"), Is.EqualTo(direction));
            Assert.That(result["data"]["values"].Value<float>("value"), Is.EqualTo(6f));
        }

        [Test]
        public void ActualTmpFallbackSubmeshCannotEscapeRootUndoRedo()
        {
            UseFont();
            var characters = (IList)Read(font, "characterTable");
            for (int i = characters.Count - 1; i >= 0; i--)
                if ((uint)Read(characters[i], "unicode") == 0x51)
                    characters.RemoveAt(i);
            Invoke(font, "ReadFontAssetDefinition");
            var factory = fontType
                .GetMethods(BindingFlags.Public | BindingFlags.Static)
                .Single(m => m.Name == "CreateFontAsset" && m.GetParameters().Length == 7 && m.GetParameters()[0].ParameterType == typeof(string));
            var fallback = (ScriptableObject)
                factory.Invoke(
                    null,
                    new object[]
                    {
                        Path.GetFullPath(Path.Combine(Application.dataPath, "..", "..", "font-input", "UI16.ttf")),
                        0,
                        90,
                        9,
                        Enum.Parse(factory.GetParameters()[4].ParameterType, "SDFAA"),
                        1024,
                        1024,
                    }
                );
            fontObjects.Add(fallback);
            var args = new object[] { "Q", null, false };
            Assert.That(
                (bool)fontType.GetMethod("TryAddCharacters", new[] { typeof(string), typeof(string).MakeByRefType(), typeof(bool) }).Invoke(fallback, args),
                Is.True
            );
            Write(fallback, "atlasPopulationMode", Enum.Parse(fontType.GetProperty("atlasPopulationMode").PropertyType, "Static"));
            var table = (IList)Activator.CreateInstance(fontType.GetProperty("fallbackFontAssetTable").PropertyType);
            table.Add(fallback);
            Write(font, "fallbackFontAssetTable", table);
            var result = Send(WithProperty(Request("toggle"), "label", "Q"));
            var created = Created(result);
            var text = Role(result, "label").GetComponent(Require("TMPro.TextMeshProUGUI"));
            Invoke(text, "ForceMeshUpdate", true, true);
            var submeshes = created.GetComponentsInChildren(Require("TMPro.TMP_SubMeshUI"), true).Cast<Component>().Select(c => c.gameObject).ToArray();
            Assert.That(submeshes.Length, Is.GreaterThan(0), "Real fallback rendering must allocate an owned TMP submesh.");
            EmitWitness("UI16_BEFORE_UNDO ", TextRedoWitness(text));
            Undo.PerformUndo();
            foreach (var item in submeshes)
                Assert.That(item == null, Is.True);
            Assert.That(created == null, Is.True);
            Undo.PerformRedo();
            var restored = root.transform.Cast<Transform>().Single(t => t.name == "OwnedControl").gameObject;
            AssertGraph(restored, "toggle", null);
            var restoredText = restored.GetComponentsInChildren(Require("TMPro.TextMeshProUGUI"), true).Single();
            EmitWitness("UI16_AFTER_REDO_BEFORE_FORCE ", TextRedoWitness(restoredText));
            Invoke(restoredText, "ForceMeshUpdate", true, true);
            Assert.That(restored.GetComponentsInChildren(Require("TMPro.TMP_SubMeshUI"), true).Length, Is.GreaterThan(0), TextRedoWitness(restoredText));
        }

        [Test]
        public void FailedNewActionLeavesThePreviousUndoEntryAvailable()
        {
            Vector3 initial = root.transform.localPosition;
            var snapshots = new JArray();
            var undoEvents = new JArray();
            int undoEventCount = 0;
            string undoEventReadError = null;
            bool observerAttached = false;
            bool faultReached = false,
                rollbackReached = false,
                finalizeReached = false,
                beforeUndoReached = false,
                afterUndoReached = false;
            JObject observedResult = null;
            void Sample(string stage)
            {
                try
                {
                    if (snapshots.Count >= 7)
                        return;
                    string name = Undo.GetCurrentGroupName();
                    Vector3 position = root.transform.localPosition;
                    snapshots.Add(
                        new JObject
                        {
                            ["stage"] = stage,
                            ["currentGroup"] = Undo.GetCurrentGroup(),
                            ["currentName"] = name == null ? null : name.Substring(0, Math.Min(name.Length, 128)),
                            ["position"] = new JArray(position.x, position.y, position.z),
                        }
                    );
                }
                catch (Exception error)
                {
                    if (snapshots.Count < 7)
                        snapshots.Add(new JObject { ["stage"] = stage, ["diagnosticReadError"] = error.GetType().FullName });
                }
            }
#if UNITY_2022_2_OR_NEWER
            const bool undoEventMetadataAvailable = true;
            Undo.UndoRedoEventCallback observeUndo = (in UndoRedoInfo info) =>
            {
                try
                {
                    undoEventCount++;
                    if (undoEvents.Count < 2)
                        undoEvents.Add(
                            new JObject
                            {
                                ["name"] = info.undoName == null ? null : info.undoName.Substring(0, Math.Min(info.undoName.Length, 128)),
                                ["group"] = info.undoGroup,
                                ["isRedo"] = info.isRedo,
                            }
                        );
                }
                catch (Exception error)
                {
                    undoEventReadError = error.GetType().FullName;
                }
            };
#else
            const bool undoEventMetadataAvailable = false;
            Undo.UndoRedoCallback observeUndo = () => undoEventCount++;
#endif
            Undo.RecordObject(root.transform, "Owned prior change");
            ((RectTransform)root.transform).anchoredPosition3D += Vector3.one;
            Undo.FlushUndoRecordObjects();
            Sample("after_prior_flush");
            Undo.IncrementCurrentGroup();
            Sample("after_prior_increment");
            SetHook(
                "FaultForTests",
                (Action<string, GameObject>)(
                    (stage, _) =>
                    {
                        if (stage == "after_children")
                        {
                            faultReached = true;
                            Sample("at_after_children_fault");
                            throw new InvalidOperationException("new action");
                        }
                    }
                )
            );
            SetHook(
                "BeforeRollbackForTests",
                (Action)(
                    () =>
                    {
                        rollbackReached = true;
                        Sample("before_rollback_hook");
                    }
                )
            );
            SetHook(
                "BeforeFinalizeForTests",
                (Action)(
                    () =>
                    {
                        finalizeReached = true;
                        Sample("before_finalize_hook");
                    }
                )
            );
            try
            {
                var result = Send(Request("slider"));
                observedResult = result;
                Assert.That(result.Value<bool>("success"), Is.False);
                Assert.That(root.transform.localPosition, Is.EqualTo(initial + Vector3.one));
                beforeUndoReached = true;
                Sample("before_perform_undo");
#if UNITY_2022_2_OR_NEWER
                Undo.undoRedoEvent += observeUndo;
#else
                Undo.undoRedoPerformed += observeUndo;
#endif
                observerAttached = true;
                try
                {
                    Undo.PerformUndo();
                    afterUndoReached = true;
                    Sample("after_perform_undo");
                }
                finally
                {
                    if (observerAttached)
                    {
#if UNITY_2022_2_OR_NEWER
                        Undo.undoRedoEvent -= observeUndo;
#else
                        Undo.undoRedoPerformed -= observeUndo;
#endif
                        observerAttached = false;
                    }
                }
                Assert.That(root.transform.localPosition, Is.EqualTo(initial));
            }
            finally
            {
                try
                {
                    if (observerAttached)
                    {
#if UNITY_2022_2_OR_NEWER
                        Undo.undoRedoEvent -= observeUndo;
#else
                        Undo.undoRedoPerformed -= observeUndo;
#endif
                        observerAttached = false;
                    }
                    EmitWitness(
                        "UI16_PRIOR_UNDO ",
                        BoundedWitness(() =>
                            new JObject
                            {
                                ["scope"] = "actual public group snapshots; no saved Revert argument exposed",
                                ["samples"] = snapshots,
                                ["faultReached"] = faultReached,
                                ["rollbackReached"] = rollbackReached,
                                ["finalizeReached"] = finalizeReached,
                                ["beforeUndoReached"] = beforeUndoReached,
                                ["afterUndoReached"] = afterUndoReached,
                                ["undoEventCount"] = undoEventCount,
                                ["undoEventMetadataAvailable"] = undoEventMetadataAvailable,
                                ["undoEventsBound"] = 2,
                                ["undoEvents"] = undoEvents,
                                ["undoEventReadError"] = undoEventReadError,
                                ["rollbackStatus"] = observedResult?["data"]?["rollback_status"],
                                ["errorPhases"] = new JArray(
                                    (observedResult?["data"]?["errors"] as JArray ?? new JArray()).Take(8).Select(item => item["phase"])
                                ),
                            }
                        )
                    );
                }
                finally
                {
                    ResetHooks();
                }
            }
        }

        protected void AssertGraph(GameObject go, string kind, JObject result)
        {
            Assert.That(go.transform.parent, Is.EqualTo(root.transform));
            Assert.That(go.activeInHierarchy, Is.True, "The compound root must remain active in its hierarchy after creation and one Redo.");
            var rt = (RectTransform)go.transform;
            Assert.That(rt.sizeDelta.x, Is.GreaterThan(0));
            Assert.That(rt.sizeDelta.y, Is.GreaterThan(0));
            foreach (Transform child in go.GetComponentsInChildren<Transform>(true))
                Assert.That(child.gameObject.layer, Is.EqualTo(root.layer));
            if (kind == "toggle" || kind == "tmp_input")
                foreach (var text in go.GetComponentsInChildren(Require("TMPro.TextMeshProUGUI"), true))
                {
                    Assert.That(Read(text, "richText"), Is.EqualTo(false));
                    Assert.That(Read(text, "emojiFallbackSupport"), Is.EqualTo(false));
                    Assert.That(ReferenceEquals(Read(text, "font"), font), Is.True);
                }
            if (kind == "scroll_view")
            {
                var c = go.GetComponent(Ui("ScrollRect"));
                var content = (RectTransform)Read(c, "content");
                Assert.That(content.parent, Is.EqualTo((RectTransform)Read(c, "viewport")));
                Assert.That(((Component)Read(c, "viewport")).GetComponent(Ui("Mask")), Is.Not.Null);
                Assert.That(content.GetComponent(Ui("VerticalLayoutGroup")), Is.Not.Null);
                Assert.That(content.GetComponent(Ui("ContentSizeFitter")), Is.Not.Null);
                Assert.That(Read(c, "movementType").ToString(), Is.EqualTo("Clamped"));
            }
            if (kind == "toggle")
            {
                var c = go.GetComponent(Ui("Toggle"));
                Assert.That(((Component)Read(c, "graphic")).transform.name, Is.EqualTo("Checkmark"));
                Assert.That(((Component)Read(c, "targetGraphic")).transform.name, Is.EqualTo("Background"));
            }
            if (kind == "slider")
            {
                var c = go.GetComponent(Ui("Slider"));
                Assert.That(((RectTransform)Read(c, "fillRect")).name, Is.EqualTo("Fill"));
                Assert.That(((RectTransform)Read(c, "handleRect")).name, Is.EqualTo("Handle"));
            }
            if (kind == "tmp_input")
            {
                var c = go.GetComponent(Require("TMPro.TMP_InputField"));
                var text = (Component)Read(c, "textComponent");
                Assert.That(text.transform.parent, Is.EqualTo((RectTransform)Read(c, "textViewport")));
                var placeholder = (Component)Read(c, "placeholder");
                Assert.That(placeholder.transform.parent, Is.EqualTo(text.transform.parent));
                var background = ((Color)Read((Component)Read(c, "targetGraphic"), "color")).linear;
                float backgroundLuminance = 0.2126f * background.r + 0.7152f * background.g + 0.0722f * background.b;
                foreach (var foreground in new[] { text, placeholder })
                {
                    var color = ((Color)Read(foreground, "color")).linear;
                    Assert.That(color.a, Is.EqualTo(1f), "Default input text and placeholder must remain opaque.");
                    float luminance = 0.2126f * color.r + 0.7152f * color.g + 0.0722f * color.b;
                    float contrast = (Mathf.Max(luminance, backgroundLuminance) + 0.05f) / (Mathf.Min(luminance, backgroundLuminance) + 0.05f);
                    Assert.That(contrast, Is.GreaterThanOrEqualTo(4.5f), "Default input text and placeholder must contrast with their owned background.");
                }
                Assert.That(Read(c, "richText"), Is.EqualTo(false));
            }
            if (result != null)
                Assert.That(System.Text.Encoding.UTF8.GetByteCount(result.ToString(Newtonsoft.Json.Formatting.None)), Is.LessThanOrEqualTo(8192));
        }
    }

    [ExecuteAlways]
    public sealed class CompoundReparentWitness : MonoBehaviour
    {
        public GameObject Protected;
        public bool Armed,
            Moved;

        private void OnTransformChildrenChanged()
        {
            if (!Armed || Moved || Protected == null)
                return;
            var target = transform.Cast<Transform>().FirstOrDefault(t => t.name == "OwnedControl");
            if (target == null)
                return;
            Moved = true;
            Protected.transform.SetParent(target, false);
        }
    }

    [Parallelizable(ParallelScope.None)]
    [Explicit("Separate TMP runtime ownership probe only; actual create_control graph-through-normal-reload acceptance remains OPEN.")]
    public class UguiCompoundCaretPlayModeTests : CompoundOwnedFixture
    {
        private bool entered;
        private GameObject events;
        private Scene runtimePrevious;

        // Suppress the EditMode scene/font setup. All transient resources are acquired after entry and closed before exit.
        [SetUp]
        public override void SetUp() { }

        [TearDown]
        public override void TearDown() { }

        [UnityEngine.TestTools.UnitySetUp]
        public IEnumerator EnterOwnedRuntime()
        {
            yield return new UnityEngine.TestTools.EnterPlayMode();
            entered = true;
            Assert.That(Application.isPlaying, Is.True);
            runtimePrevious = SceneManager.GetActiveScene();
            owned = SceneManager.CreateScene("UI16Caret_" + Guid.NewGuid().ToString("N"));
            Assert.That(SceneManager.SetActiveScene(owned), Is.True);
            UseFont();
        }

        [UnityEngine.TestTools.UnityTest]
        public IEnumerator StandardTmpRuntimeCaretStaysOwnedAndDiesWithRoot()
        {
            Assert.That(Application.isPlaying, Is.True);
            Assert.That(LoadedEventSystems().Length, Is.Zero, "This owned probe never replaces an existing EventSystem.");
            Assert.That(
                Require("UnityEngine.EventSystems.EventSystem").GetProperty("current", BindingFlags.Public | BindingFlags.Static).GetValue(null),
                Is.Null
            );
            events = new GameObject("OwnedCaretEvents", Require("UnityEngine.EventSystems.EventSystem"));
            root = new GameObject("OwnedCaretCanvas", typeof(RectTransform), typeof(Canvas));
            root.GetComponent<Canvas>().renderMode = RenderMode.ScreenSpaceOverlay;
            var control = new GameObject("OwnedCaretInput", typeof(RectTransform));
            control.SetActive(false);
            control.transform.SetParent(root.transform, false);
            ((RectTransform)control.transform).sizeDelta = new Vector2(160, 36);
            var image = control.AddComponent(Ui("Image"));
            var input = control.AddComponent(Require("TMPro.TMP_InputField"));
            var viewport = new GameObject("Text Area", typeof(RectTransform));
            viewport.transform.SetParent(control.transform, false);
            viewport.AddComponent(Ui("RectMask2D"));
            var textRoot = new GameObject("Text", typeof(RectTransform));
            textRoot.transform.SetParent(viewport.transform, false);
            var text = textRoot.AddComponent(Require("TMPro.TextMeshProUGUI"));
            Write(text, "richText", false);
            Write(text, "emojiFallbackSupport", false);
            Write(text, "font", font);
            Write(text, "text", "Owned");
            Write(input, "richText", false);
            Write(input, "textViewport", (RectTransform)viewport.transform);
            Write(input, "textComponent", text);
            Write(input, "targetGraphic", image);
            Write(input, "fontAsset", font);
            control.SetActive(true);
            Canvas.ForceUpdateCanvases();
            Invoke(input, "ActivateInputField");
            yield return null;
            yield return null;
            var carets = control.GetComponentsInChildren(Require("TMPro.TMP_SelectionCaret"), true).Cast<Component>().ToArray();
            Assert.That(carets.Length, Is.GreaterThan(0), "An actual native PlayMode caret is required; an authored stand-in cannot satisfy this probe.");
            var identities = carets.Select(c => c.gameObject).ToArray();
            foreach (var item in identities)
                Assert.That(item.transform.IsChildOf(control.transform), Is.True);
            UnityEngine.Object.Destroy(control);
            yield return null;
            foreach (var item in identities)
                Assert.That(item == null, Is.True);
        }

        [UnityEngine.TestTools.UnityTearDown]
        public IEnumerator ExitOwnedRuntime()
        {
            var errors = new List<Exception>();
            Cleanup(
                () =>
                {
                    if (root != null)
                        UnityEngine.Object.DestroyImmediate(root);
                },
                errors
            );
            Cleanup(
                () =>
                {
                    if (events != null)
                        UnityEngine.Object.DestroyImmediate(events);
                },
                errors
            );
            Cleanup(CloseFontLease, errors);
            Cleanup(
                () =>
                {
                    if (runtimePrevious.IsValid() && runtimePrevious.isLoaded)
                        SceneManager.SetActiveScene(runtimePrevious);
                },
                errors
            );
            AsyncOperation unload = null;
            Cleanup(
                () =>
                {
                    if (owned.IsValid() && owned.isLoaded)
                        unload = SceneManager.UnloadSceneAsync(owned);
                },
                errors
            );
            if (unload != null)
                yield return unload;
            if (entered || Application.isPlaying)
            {
                entered = false;
                yield return new UnityEngine.TestTools.ExitPlayMode();
            }
            if (errors.Count > 0)
                throw new AggregateException("Owned runtime probe cleanup failed.", errors);
        }
    }

    // Independent fixture: never inherits the transient, mutating CompoundOwnedFixture.SetUp.
    [Serializable]
    [Parallelizable(ParallelScope.None)]
    [Explicit("Requires the reviewed UI16 lifecycle case, normal reload and the exact OFL caret font inputs.")]
    public class UguiCompoundActualCaretLifecycleTests
    {
        private const string WitnessAssembly = "MCPForUnityTests.UI16CaretRuntime";
        private const string WitnessName = WitnessAssembly + ".UguiCompoundCaretSceneWitness";
        private const string WitnessAsset = "Assets/Tests/UI16CaretRuntime/UguiCompoundCaretSceneWitness.cs";
        private const string WitnessGuid = "055c8578350054d29bcd5fbce6a3d6dd";
        private static readonly string DomainToken = Guid.NewGuid().ToString("N");

        // The installed TestFramework serializes public fields, not pre-yield locals.
        public string OwnedStateJson;
        public string ManifestPath;
        public string ManifestSha256;
        public string Phase;
        public string CleanupErrorsJson;
        private readonly List<UnityEngine.Object> transient = new List<UnityEngine.Object>();

        // Synchronous pre-entry failure witness only; never an ownership carrier across reload.
        private Exception lastPrepareFailure;
        private string manifestReadStage;

        [SetUp]
        public void ReadOnlyContinuationAdmission()
        {
            if (Application.isPlaying || !string.IsNullOrEmpty(Phase))
            {
                Need(!string.IsNullOrEmpty(OwnedStateJson), "missing_serialized_ownership");
                CheckState(State());
                if (Phase == "entering" || Phase == "playing" || Phase == "exiting")
                    ReadManifest();
            }
        }

        [UnityEngine.TestTools.UnityTest]
        public IEnumerator ActualToolGraphSurvivesNormalReloadAndOwnsItsRealCaret()
        {
            Begin("lifecycle");
            PrepareForTool(null, null);
            BuildAndSaveActualToolGraph();
            SealManifest();
            Phase = "entering";
            yield return new UnityEngine.TestTools.EnterPlayMode();
            {
                Phase = "playing";
                Need(Application.isPlaying, "real_playmode_required");
                RequireNormalReloadSettings();
                Need(State().Value<string>("domain") != DomainToken, "domain_reload_not_observed");
                Need(
                    State().Value<bool>("reloadEnabled") == EditorSettings.enterPlayModeOptionsEnabled
                        && State().Value<int>("reloadOptions") == (int)EditorSettings.enterPlayModeOptions,
                    "user_reload_settings_changed"
                );
                var manifest = ReadManifest();
                var scene = FindOwnedScene(manifest);
                var witness = scene.GetRootGameObjects().Single(go => go.name == manifest.Value<string>("witness")).GetComponent(WitnessType());
                Need((bool)Field(witness, "observed") && (int)Field(witness, "matchingEvents") == 1, "owned_scene_reload_not_observed");
                Need((string)Field(witness, "observedNonce") == manifest.Value<string>("nonce"), "wrong_scene_nonce");
                Need((string)Field(witness, "observedScenePath") == manifest.Value<string>("scene"), "wrong_scene_path");
                Need(JToken.DeepEquals(Signature(manifest), manifest["signature"]), "actual_graph_changed_across_reload");

                // Real post-entry refusal controls: neither may allocate, repair or invoke the tool again.
                var wrongGuid = (JObject)manifest.DeepClone();
                ((JObject)((JArray)wrongGuid["files"])[0])["guid"] = new string('0', 32);
                Assert.Throws<InvalidOperationException>(() => CheckPersistentIdentity(wrongGuid));
                string missing = ManifestPath + ".missing";
                Need(!File.Exists(missing), "failure_control_path_already_exists");
                Assert.Throws<InvalidOperationException>(() => ReadManifestAt(missing, ManifestSha256));
                Need(State().Value<int>("toolCalls") == 1, "no_replacement_tool_call");

                var control = ResolveRole(manifest, "root");
                var input = control.GetComponent(RequiredType("TMPro.TMP_InputField"));
                Invoke(input, "ActivateInputField");
                yield return null;
                yield return null;
                // These CLR references exist only within this post-reload PlayMode segment.
                var carets = ResolveRole(ReadManifest(), "root")
                    .GetComponentsInChildren(RequiredType("TMPro.TMP_SelectionCaret"), true)
                    .Cast<Component>()
                    .ToArray();
                Need(carets.Length > 0 && carets.Length <= 8, "real_native_caret_required");
                var actualCaretObjects = carets.Select(value => value.gameObject).ToArray();
                foreach (var caret in actualCaretObjects)
                {
                    Need(caret.transform.IsChildOf(ResolveRole(ReadManifest(), "root").transform), "caret_outside_tool_root");
                    Need(caret.hideFlags == HideFlags.DontSave, "unexpected_native_caret_flags");
                    var renderer = caret.GetComponent<CanvasRenderer>();
                    var mesh = (Mesh)RequiredType("TMPro.TMP_InputField").GetField("m_Mesh", BindingFlags.NonPublic | BindingFlags.Instance).GetValue(input);
                    Need(renderer != null && mesh != null && mesh.vertexCount > 0, "actual_native_caret_mesh_required");
                }
                WriteEvidence(
                    "after",
                    new JObject
                    {
                        ["signature"] = Signature(ReadManifest()),
                        ["caretCount"] = carets.Length,
                        ["toolCalls"] = State()["toolCalls"],
                        ["beforeDomainToken"] = State()["domain"],
                        ["afterDomainToken"] = DomainToken,
                        ["sceneEventCount"] = (int)Field(witness, "matchingEvents"),
                        ["sceneEventPath"] = (string)Field(witness, "observedScenePath"),
                        ["sceneEventNonce"] = (string)Field(witness, "observedNonce"),
                        ["reloadEnabled"] = EditorSettings.enterPlayModeOptionsEnabled,
                        ["reloadOptions"] = (int)EditorSettings.enterPlayModeOptions,
                    }
                );
                UnityEngine.Object.Destroy(ResolveRole(ReadManifest(), "root"));
                yield return null;
                foreach (var sameReference in actualCaretObjects)
                    Need(sameReference == null, "same_caret_reference_survived_root_death");
                Need(
                    !FindOwnedScene(State()).GetRootGameObjects().Single(go => go.name == State().Value<string>("canvas")).transform.Cast<Transform>().Any(),
                    "tool_descendants_remain"
                );
                Need(FindOwnedScene(State()).GetRootGameObjects().Any(go => go.name == State().Value<string>("events")), "fixture_eventsystem_changed");
                WriteEvidence(
                    "death",
                    new JObject
                    {
                        ["sameCaretReferencesDead"] = true,
                        ["toolDescendants"] = 0,
                        ["toolCalls"] = State()["toolCalls"],
                    }
                );
            }
        }

        [Test]
        public void OwnedPersistenceFailureBeforeToolCallCleansItsPartialAssets()
        {
            Begin("persist");
            var primary = Assert.Throws<InvalidOperationException>(() => PrepareForTool("after_font", null));
            Assert.That(primary.Message, Is.EqualTo("owned_persist_fault"));
            Need(State().Value<int>("toolCalls") == 0, "persist_failure_must_precede_tool_call");
            Need(JArray.Parse(CleanupErrorsJson).Count == 0, "partial_persist_cleanup_failed");
            Need(!AssetDatabase.IsValidFolder(State().Value<string>("folder")), "partial_owned_folder_remains");
        }

        [Test]
        public void CleanupFailureRetainsPrimaryAndStillAttemptsOwnedAssetDeletion()
        {
            Begin("cleanup");
            var combined = Assert.Throws<AggregateException>(() => PrepareForTool("after_font", "after_cache_cleanup"));
            var primary = lastPrepareFailure;
            var failures = JArray.Parse(CleanupErrorsJson);
            Need(failures.Count == 1 && failures[0].Value<string>("phase") == "injected_cleanup", "cleanup_fault_not_retained");
            Need(combined.InnerExceptions.Count == 2 && ReferenceEquals(combined.InnerExceptions[0], primary), "primary_error_replaced");
            Need(primary is InvalidOperationException && primary.Message == "owned_persist_fault", "wrong_primary_failure");
            Need(!AssetDatabase.IsValidFolder(State().Value<string>("folder")), "cleanup_fault_skipped_later_owned_deletion");
            // The asserted fault is retained in its own evidence, not promoted to a teardown error.
            WriteEvidence(
                "expected-failure",
                new JObject
                {
                    ["primary"] = primary.GetType().FullName,
                    ["cleanup"] = failures,
                    ["ownedAssetsRemoved"] = true,
                    ["toolCalls"] = 0,
                }
            );
            CleanupErrorsJson = "[]";
        }

        [UnityEngine.TestTools.UnityTearDown]
        public IEnumerator ExitAndCleanOnlyTrackedOwnership()
        {
            if (string.IsNullOrEmpty(OwnedStateJson))
            {
                if (Application.isPlaying)
                {
                    yield return new UnityEngine.TestTools.ExitPlayMode();
                    throw new InvalidOperationException("missing_reload_ownership_case_preserved_no_guessed_cleanup");
                }
                yield break;
            }
            if (string.IsNullOrEmpty(CleanupErrorsJson))
                CleanupErrorsJson = "[]";
            Capture(
                "runtime_roots",
                () =>
                {
                    if (!Application.isPlaying)
                        return;
                    var scene = FindOwnedScene(State());
                    AssertOnlyOwnedRoots(scene, State());
                    foreach (var root in scene.GetRootGameObjects())
                        UnityEngine.Object.Destroy(root);
                }
            );
            if (Application.isPlaying)
            {
                yield return null;
                Phase = "exiting";
                yield return new UnityEngine.TestTools.ExitPlayMode();
            }
            Phase = "cleaning";
            CleanupEditor(null);
            Capture(
                "cleanup_evidence",
                () =>
                    WriteEvidence(
                        "cleanup",
                        new JObject
                        {
                            ["primaryOutcome"] = TestContext.CurrentContext.Result.Outcome.Status.ToString(),
                            ["errors"] = JArray.Parse(CleanupErrorsJson),
                            ["phase"] = Phase,
                        }
                    )
            );
            string errors = CleanupErrorsJson;
            OwnedStateJson = ManifestPath = ManifestSha256 = Phase = CleanupErrorsJson = null;
            if (JArray.Parse(errors).Count != 0)
                throw new AggregateException(
                    "UI16 lifecycle cleanup incomplete; original test outcome retained in cleanup evidence.",
                    JArray.Parse(errors).Select(item => new InvalidOperationException(item.Value<string>("phase") + ":" + item.Value<string>("type")))
                );
        }

        private void Begin(string caseName)
        {
            lastPrepareFailure = null;
            Need(!Application.isPlaying && string.IsNullOrEmpty(OwnedStateJson), "fresh_editmode_lifecycle_required");
            RequireNormalReloadSettings();
            string nonce = Environment.GetEnvironmentVariable("UNITY_MCP_UI16_CARET_NONCE");
            Need(nonce != null && nonce.Length == 32 && nonce.All(c => c >= '0' && c <= '9' || c >= 'a' && c <= 'f'), "root_bound_nonce_required");
            string folder = "Assets/UI16Caret_" + nonce + "_" + caseName;
            NoLinks(Application.dataPath);
            Need(
                !AssetDatabase.IsValidFolder(folder)
                    && string.IsNullOrEmpty(AssetDatabase.AssetPathToGUID(folder))
                    && !Directory.Exists(DiskPath(folder))
                    && !File.Exists(DiskPath(folder))
                    && !File.Exists(DiskPath(folder) + ".meta"),
                "never_reuse_owned_folder"
            );
            Need(PrefabStageUtility.GetCurrentPrefabStage() == null && SceneManager.sceneCount <= 32, "normal_bounded_scene_context_required");
            var scenes = new JArray();
            for (int i = 0; i < SceneManager.sceneCount; i++)
            {
                Scene scene = SceneManager.GetSceneAt(i);
                Need(scene.isLoaded && scene.rootCount <= 32, "bounded_previous_scenes_required");
                scenes.Add(
                    new JObject
                    {
                        ["path"] = scene.path,
                        ["name"] = scene.name,
                        ["dirty"] = scene.isDirty,
                        ["roots"] = new JArray(scene.GetRootGameObjects().Select(go => go.name).OrderBy(name => name)),
                    }
                );
            }
            var active = SceneManager.GetActiveScene();
            SetState(
                new JObject
                {
                    ["nonce"] = nonce,
                    ["case"] = caseName,
                    ["folder"] = folder,
                    ["files"] = new JArray(),
                    ["folders"] = new JArray(),
                    ["previousScenes"] = scenes,
                    ["previousActivePath"] = active.path,
                    ["previousActiveName"] = active.name,
                    ["domain"] = DomainToken,
                    ["toolCalls"] = 0,
                    ["canvas"] = "Canvas_" + nonce,
                    ["events"] = "Events_" + nonce,
                    ["witness"] = "Witness_" + nonce,
                    ["sources"] = new JArray(),
                    ["reloadEnabled"] = EditorSettings.enterPlayModeOptionsEnabled,
                    ["reloadOptions"] = (int)EditorSettings.enterPlayModeOptions,
                }
            );
            var sourceState = State();
            foreach (string guid in new[] { "d672341ab38057469f9edc79cdc7c436", WitnessGuid, "6bc95e888a7a5a8a9a1649f6f1e78135" })
            {
                string source = AssetDatabase.GUIDToAssetPath(guid);
                Need(!string.IsNullOrEmpty(source), "fixture_source_GUID_required");
                ((JArray)sourceState["sources"]).Add(
                    new JObject
                    {
                        ["path"] = source,
                        ["guid"] = guid,
                        ["sha256"] = HashFile(source),
                    }
                );
            }
            SetState(sourceState);
            ExportRoot();
            Need(Selection.activeObject == null, "fresh_selection_required");
            Need(UnityEngine.Resources.FindObjectsOfTypeAll(RequiredType("TMPro.TMP_FontAsset")).Length == 0, "fresh_TMP_font_context_required");
            Need(
                SettingsSlot().GetValue(null) == null && UnityEngine.Resources.Load("TMP Settings", RequiredType("TMPro.TMP_Settings")) == null,
                "never_replace_existing_TMP_settings"
            );
            Need(!AssetDatabase.FindAssets("t:TMP_Settings").Any(), "no_ambiguous_resources_settings");
            Need(
                LoadedSceneObjects().All(go => go.GetComponent(RequiredType("UnityEngine.EventSystems.EventSystem")) == null),
                "never_replace_existing_eventsystem"
            );
            Need(RequiredType("UnityEngine.EventSystems.EventSystem").GetProperty("current").GetValue(null) == null, "fresh_event_selection_required");
            foreach (var registry in FontRegistries())
                Need(registry.Count == 0, "fresh_font_registry_required");
            Need(FontCallbacks().Count == 0, "fresh_font_callbacks_required");
            WitnessType();
        }

        private void PrepareForTool(string persistFault, string cleanupFault)
        {
            try
            {
                PrepareOwnedAssets(persistFault);
            }
            catch (Exception primary)
            {
                lastPrepareFailure = primary;
                CleanupErrorsJson = "[]";
                Exception pipelineFailure = null;
                try
                {
                    CleanupEditor(cleanupFault);
                }
                catch (Exception cleanup)
                {
                    pipelineFailure = cleanup;
                }
                var cleanupFailures = JArray
                    .Parse(CleanupErrorsJson)
                    .Select(item => (Exception)new InvalidOperationException(item.Value<string>("phase") + ":" + item.Value<string>("type")))
                    .ToList();
                if (pipelineFailure != null)
                    cleanupFailures.Add(pipelineFailure);
                if (cleanupFailures.Count != 0)
                    throw new AggregateException("Owned lifecycle preparation and cleanup failed.", new[] { primary }.Concat(cleanupFailures));
                throw;
            }
        }

        private void PrepareOwnedAssets(string fault)
        {
            var state = State();
            string path = state.Value<string>("folder");
            TrackFolder(path, AssetDatabase.CreateFolder("Assets", path.Substring("Assets/".Length)));
            TrackFolder(path + "/Resources", AssetDatabase.CreateFolder(path, "Resources"));
            string template = AssetDatabase.GUIDToAssetPath("d8f5ae9136f356e190abdc5c9be58fb6");
            Need(AssetDatabase.LoadAssetAtPath<SceneAsset>(template) != null, "saved_normal_template_required");
            string scenePath = path + "/Owned Scene.unity";
            FreshAsset(scenePath);
            Need(AssetDatabase.CopyAsset(template, scenePath), "owned_scene_copy_failed");
            TrackAsset(scenePath);
            state = State();
            state["scene"] = scenePath;
            SetState(state);
            Scene scene = EditorSceneManager.OpenScene(scenePath, OpenSceneMode.Additive);
            Need(!EditorSceneManager.IsPreviewScene(scene) && scene.rootCount == 0 && SelectActiveScene(scene), "owned_normal_empty_scene_required");
            string fontInput = Path.Combine(CaseRoot(), "font-input", "UI16-caret.ttf");
            string license = Path.Combine(CaseRoot(), "font-input", "UI16-caret-OFL.txt");
            CheckFile(fontInput, "e5b0af421ea2bfbc1ac8d251d647268087ae82786234c57f757d1f0b90fa8b49", 350200);
            CheckFile(license, "37f8552e9a874ec10710dc0ede6a9adf168e6609fbd02a507f35629373b85a48", 4469);
            // TMP font creation reads TMP_Settings before returning an owned font.
            Type settingsType = RequiredType("TMPro.TMP_Settings");
            var settings = ScriptableObject.CreateInstance(settingsType);
            transient.Add(settings);
            foreach (string name in new[] { "m_leadingCharacters", "m_followingCharacters" })
            {
                var text = new TextAsset("");
                transient.Add(text);
                settingsType.GetField(name, BindingFlags.NonPublic | BindingFlags.Instance).SetValue(settings, text);
            }
            foreach (string name in new[] { "m_fallbackFontAssets", "m_EmojiFallbackTextAssets" })
            {
                var field = settingsType.GetField(name, BindingFlags.NonPublic | BindingFlags.Instance);
                field.SetValue(settings, Activator.CreateInstance(field.FieldType));
            }
            settingsType.GetField("m_defaultSpriteAsset", BindingFlags.NonPublic | BindingFlags.Instance).SetValue(settings, null);
            settingsType
                .GetField("assetVersion", BindingFlags.NonPublic | BindingFlags.Instance)
                .SetValue(settings, settingsType.GetField("s_CurrentAssetVersion", BindingFlags.NonPublic | BindingFlags.Static).GetValue(null));
            string settingsPath = path + "/Resources/TMP Settings.asset";
            FreshAsset(settingsPath);
            AssetDatabase.CreateAsset(settings, settingsPath);
            TrackAsset(settingsPath);
            foreach (string name in new[] { "m_leadingCharacters", "m_followingCharacters" })
                AssetDatabase.AddObjectToAsset(
                    (TextAsset)settingsType.GetField(name, BindingFlags.NonPublic | BindingFlags.Instance).GetValue(settings),
                    settings
                );
            AssetDatabase.SaveAssetIfDirty(settings);
            Need(AssetDatabase.LoadAllAssetsAtPath(settingsPath).Length == 3, "exact_settings_subassets_required");
            Need(SettingsSlot().GetValue(null) == null, "foreign_settings_installed");
            SettingsSlot().SetValue(null, settings);
            state = State();
            state["settings"] = settingsPath;
            SetState(state);
            Type fontType = RequiredType("TMPro.TMP_FontAsset");
            MethodInfo create = fontType
                .GetMethods(BindingFlags.Public | BindingFlags.Static)
                .Single(method =>
                    method.Name == "CreateFontAsset" && method.GetParameters().Length == 7 && method.GetParameters()[0].ParameterType == typeof(string)
                );
            var font = (ScriptableObject)
                create.Invoke(null, new object[] { fontInput, 0, 90, 9, Enum.Parse(create.GetParameters()[4].ParameterType, "SDFAA"), 1024, 1024 });
            transient.Add(font);
            var add = fontType.GetMethod("TryAddCharacters", new[] { typeof(string), typeof(string).MakeByRefType(), typeof(bool) });
            object[] args = { " OwnedCaretEntertext0123456789", null, false };
            Need((bool)add.Invoke(font, args) && string.IsNullOrEmpty((string)args[1]), "owned_font_glyph_coverage_required");
            Write(font, "atlasPopulationMode", Enum.ToObject(fontType.GetProperty("atlasPopulationMode").PropertyType, 0));
            Write(font, "isMultiAtlasTexturesEnabled", false);
            var atlases = (Texture2D[])Read(font, "atlasTextures");
            var material = (Material)Read(font, "material");
            Need(
                atlases.Length == 1 && atlases[0] != null && atlases[0].width <= 1024 && atlases[0].height <= 1024 && material != null,
                "owned_single_atlas_required"
            );
            transient.Add(atlases[0]);
            transient.Add(material);
            foreach (var value in new UnityEngine.Object[] { font, atlases[0], material })
            {
                Need(!EditorUtility.IsPersistent(value), "never_persist_borrowed_font_object");
                value.hideFlags = HideFlags.None;
            }
            string fontPath = path + "/Font.asset";
            FreshAsset(fontPath);
            AssetDatabase.CreateAsset(font, fontPath);
            TrackAsset(fontPath);
            AssetDatabase.AddObjectToAsset(atlases[0], font);
            AssetDatabase.AddObjectToAsset(material, font);
            AssetDatabase.SaveAssetIfDirty(font);
            Need(AssetDatabase.LoadAllAssetsAtPath(fontPath).Length == 3, "exact_font_subassets_required");
            if (fault == "after_font")
                throw new InvalidOperationException("owned_persist_fault");
            state = State();
            settingsType.GetField("m_defaultFontAsset", BindingFlags.NonPublic | BindingFlags.Instance).SetValue(settings, font);
            AssetDatabase.SaveAssetIfDirty(settings);
            state["font"] = fontPath;
            state["settings"] = settingsPath;
            state["subassets"] = PersistentSubassets(state);
            SetState(state);
        }

        private void BuildAndSaveActualToolGraph()
        {
            var state = State();
            var scene = FindOwnedScene(state);
            Need(SelectActiveScene(scene), "owned_scene_not_active");
            var canvas = new GameObject(state.Value<string>("canvas"), typeof(RectTransform), typeof(Canvas));
            canvas.GetComponent<Canvas>().renderMode = RenderMode.ScreenSpaceOverlay;
            ((RectTransform)canvas.transform).sizeDelta = new Vector2(600, 400);
            canvas.AddComponent(RequiredType("UnityEngine.UI.GraphicRaycaster"));
            new GameObject(state.Value<string>("events"), RequiredType("UnityEngine.EventSystems.EventSystem"));
            var witness = new GameObject(state.Value<string>("witness")).AddComponent(WitnessType());
            SetField(witness, "nonce", state.Value<string>("nonce"));
            SetField(witness, "ownedScenePath", scene.path);
            var script = MonoScript.FromMonoBehaviour((MonoBehaviour)witness);
            Need(
                script != null
                    && AssetDatabase.GetAssetPath(script) == WitnessAsset
                    && AssetDatabase.AssetPathToGUID(WitnessAsset) == WitnessGuid
                    && script.GetClass() == WitnessType(),
                "runtime_MonoScript_identity_required"
            );
            var request = new JObject
            {
                ["action"] = "create_control",
                ["element_type"] = "tmp_input",
                ["parent"] = canvas.GetInstanceIDCompat(),
                ["name"] = "ActualToolInput_" + state.Value<string>("nonce"),
                ["properties"] = new JObject
                {
                    ["text"] = "OwnedCaret",
                    ["placeholder"] = "Enter text",
                    ["eventSystem"] = "preserve",
                },
            };
            state["toolCalls"] = 1;
            SetState(state); // Recorded before dispatch: never retry a partial call.
            var response = JObject.FromObject(ManageUGUI.HandleCommand(request));
            Need(response.Value<bool>("success"), "actual_create_control_failed");
            var roles = new JObject();
            var roleIds = (JObject)response["data"]["roles"];
            Need(roleIds.Count <= 8 && roleIds.ContainsKey("root"), "bounded_real_roles_required");
            var control = GameObjectLookup.FindById(roleIds.Value<int>("root"));
            foreach (var role in roleIds.Properties())
            {
                var go = GameObjectLookup.FindById(role.Value.Value<int>());
                Need(
                    go != null && go.scene.Equals(scene) && (ReferenceEquals(go, control) || go.transform.IsChildOf(control.transform)),
                    "real_role_outside_owned_root"
                );
                roles[role.Name] = RelativePath(canvas.transform, go.transform);
            }
            state = State();
            state["roles"] = roles;
            state["request"] = request;
            state["response"] = response;
            SetState(state);
            state["signature"] = Signature(state);
            SetState(state);
            Need(EditorSceneManager.SaveScene(scene, scene.path), "owned_scene_save_failed");
            AssertOnlyOwnedRoots(scene, state);
            WriteEvidence(
                "before",
                new JObject
                {
                    ["request"] = request,
                    ["response"] = response,
                    ["signature"] = state["signature"],
                }
            );
        }

        private void SealManifest()
        {
            var state = State();
            Need(
                ((JArray)state["files"]).Count == 3
                    && ((JArray)state["folders"]).Count == 2
                    && ((JArray)state["sources"]).Count == 3
                    && state.Value<int>("toolCalls") == 1,
                "complete_manifest_ownership_required"
            );
            foreach (var row in (JArray)state["files"])
            {
                row["sha256"] = HashFile(row.Value<string>("path"));
                row["bytes"] = new FileInfo(DiskPath(row.Value<string>("path"))).Length;
            }
            SetState(state);
            byte[] bytes = System.Text.Encoding.UTF8.GetBytes(OwnedStateJson);
            Need(bytes.Length <= 16384, "manifest_bound");
            ManifestPath = Path.Combine(ExportRoot(), state.Value<string>("case") + ".manifest.json");
            using (var stream = new FileStream(ManifestPath, FileMode.CreateNew, FileAccess.Write))
                stream.Write(bytes, 0, bytes.Length);
            ManifestSha256 = Hash(bytes);
            ReadManifest();
        }

        private JObject ReadManifest()
        {
            manifestReadStage = "entry";
            try
            {
                return ReadManifestAt(ManifestPath, ManifestSha256);
            }
            catch (Exception error)
            {
                ReportManifestReadFailure(error);
                throw;
            }
        }

        private void ReportManifestReadFailure(Exception error)
        {
            try
            {
                string json = new JObject
                {
                    ["stage"] = manifestReadStage,
                    ["type"] = error.GetType().FullName,
                    ["hasPath"] = !string.IsNullOrEmpty(ManifestPath),
                    ["hasDigest"] = !string.IsNullOrEmpty(ManifestSha256),
                    ["hasState"] = !string.IsNullOrEmpty(OwnedStateJson),
                    ["pathLength"] = ManifestPath?.Length ?? 0,
                    ["digestLength"] = ManifestSha256?.Length ?? 0,
                    ["stateLength"] = OwnedStateJson?.Length ?? 0,
                }.ToString(Newtonsoft.Json.Formatting.None);
                if (System.Text.Encoding.UTF8.GetByteCount(json) <= 2048)
                    TestContext.Out.WriteLine("UI16_MANIFEST_READ " + json);
            }
            catch (Exception)
            {
                // Diagnostic failure must never replace the original manifest exception.
            }
        }

        private JObject ReadManifestAt(string path, string digest)
        {
            manifestReadStage = "path_admission";
            Need(!string.IsNullOrEmpty(path) && Path.GetDirectoryName(Path.GetFullPath(path)) == ExportRoot() && File.Exists(path), "missing_owned_manifest");
            manifestReadStage = "manifest_links";
            NoLinks(path);
            manifestReadStage = "manifest_bytes";
            byte[] bytes = File.ReadAllBytes(path);
            manifestReadStage = "manifest_digest";
            Need(bytes.Length <= 16384 && Hash(bytes) == digest, "manifest_digest_mismatch");
            manifestReadStage = "manifest_parse";
            var result = JObject.Parse(System.Text.Encoding.UTF8.GetString(bytes));
            manifestReadStage = "manifest_state_shape";
            CheckState(result);
            manifestReadStage = "restored_state_equality";
            Need(result.ToString(Newtonsoft.Json.Formatting.None) == OwnedStateJson, "restored_manifest_state_mismatch");
            manifestReadStage = "persistent_entry";
            CheckPersistentIdentity(result);
            return result;
        }

        private void CheckPersistentIdentity(JObject state)
        {
            manifestReadStage = "persistent_state_shape";
            CheckState(state);
            foreach (var row in (JArray)state["files"])
            {
                string path = row.Value<string>("path");
                manifestReadStage = "persistent_file_guid";
                Need(AssetDatabase.AssetPathToGUID(path) == row.Value<string>("guid"), "owned_asset_GUID_mismatch");
                manifestReadStage = "persistent_file_hash";
                Need(HashFile(path) == row.Value<string>("sha256"), "owned_asset_bytes_changed");
            }
            manifestReadStage = "persistent_folders";
            foreach (var row in (JArray)state["folders"])
                Need(AssetDatabase.AssetPathToGUID(row.Value<string>("path")) == row.Value<string>("guid"), "owned_folder_GUID_mismatch");
            manifestReadStage = "persistent_sources";
            foreach (var row in (JArray)state["sources"])
                Need(
                    AssetDatabase.AssetPathToGUID(row.Value<string>("path")) == row.Value<string>("guid")
                        && HashFile(row.Value<string>("path")) == row.Value<string>("sha256"),
                    "fixture_source_changed_across_reload"
                );
            manifestReadStage = "persistent_subassets";
            Need(JToken.DeepEquals(PersistentSubassets(state), state["subassets"]), "owned_subasset_identity_changed");
        }

        private JObject Signature(JObject state)
        {
            var control = ResolveRole(state, "root");
            var input = control.GetComponent(RequiredType("TMPro.TMP_InputField"));
            var text = (Component)Read(input, "textComponent");
            var placeholder = (Component)Read(input, "placeholder");
            Need(ReferenceEquals(text, ResolveRole(state, "text").GetComponent(RequiredType("TMPro.TextMeshProUGUI"))), "actual_text_reference_changed");
            Need(
                ReferenceEquals(placeholder, ResolveRole(state, "placeholder").GetComponent(RequiredType("TMPro.TextMeshProUGUI"))),
                "actual_placeholder_reference_changed"
            );
            Need(ReferenceEquals(Read(input, "textViewport"), ResolveRole(state, "viewport").transform), "actual_viewport_reference_changed");
            var signature = new JObject
            {
                ["text"] = (string)Read(input, "text"),
                ["richText"] = (bool)Read(input, "richText"),
                ["characterLimit"] = (int)Read(input, "characterLimit"),
                ["roles"] = new JArray(),
            };
            Need(
                signature.Value<string>("text") == "OwnedCaret" && !signature.Value<bool>("richText") && signature.Value<int>("characterLimit") == 256,
                "tool_text_settings_changed"
            );
            signature["lineType"] = Read(input, "lineType").ToString();
            signature["contentType"] = Read(input, "contentType").ToString();
            Need(signature.Value<string>("lineType") == "SingleLine" && signature.Value<string>("contentType") == "Standard", "tool_input_variant_changed");
            var settings = AssetDatabase.LoadAssetAtPath<ScriptableObject>(state.Value<string>("settings"));
            Need(ReferenceEquals(SettingsSlot().GetValue(null), settings), "owned_settings_not_reacquired");
            Need(
                AssetDatabase.GetAssetPath(
                    settings.GetType().GetField("m_defaultFontAsset", BindingFlags.NonPublic | BindingFlags.Instance).GetValue(settings) as UnityEngine.Object
                ) == state.Value<string>("font"),
                "owned_default_font_changed"
            );
            foreach (var role in ((JObject)state["roles"]).Properties().OrderBy(value => value.Name))
            {
                var go = ResolveRole(state, role.Name);
                var rect = (RectTransform)go.transform;
                ((JArray)signature["roles"]).Add(
                    new JObject
                    {
                        ["role"] = role.Name,
                        ["path"] = role.Value,
                        ["size"] = new JArray(rect.sizeDelta.x, rect.sizeDelta.y),
                        ["anchorsMin"] = new JArray(rect.anchorMin.x, rect.anchorMin.y),
                        ["anchorsMax"] = new JArray(rect.anchorMax.x, rect.anchorMax.y),
                        ["position"] = new JArray(rect.anchoredPosition.x, rect.anchoredPosition.y),
                        ["active"] = go.activeSelf,
                    }
                );
            }
            foreach (var component in new[] { text, placeholder })
            {
                Need(!(bool)Read(component, "richText") && !(bool)Read(component, "emojiFallbackSupport"), "tool_literal_flags_changed");
                var font = (UnityEngine.Object)Read(component, "font");
                Need(AssetDatabase.GetAssetPath(font) == state.Value<string>("font"), "borrowed_or_replaced_font");
                var material = (UnityEngine.Object)Read(font, "material");
                var atlases = (Texture2D[])Read(font, "atlasTextures");
                Need(atlases.Length == 1, "atlas_count_changed");
                Need(
                    Convert.ToInt32(Read(font, "atlasPopulationMode")) == 0 && !(bool)Read(font, "isMultiAtlasTexturesEnabled"),
                    "owned_font_no_longer_static"
                );
                signature[component == text ? "textFont" : "placeholderFont"] = new JArray(
                    AssetIdentity(font),
                    AssetIdentity(material),
                    AssetIdentity(atlases[0]),
                    (string)Read(component, "text")
                );
            }
            return signature;
        }

        private static string AssetIdentity(UnityEngine.Object value)
        {
            string guid = null;
            long localId = 0;
            Need(value != null && AssetDatabase.TryGetGUIDAndLocalFileIdentifier(value, out guid, out localId), "persistent_asset_identity_required");
            return guid + ":" + localId.ToString(System.Globalization.CultureInfo.InvariantCulture);
        }

        private static JArray PersistentSubassets(JObject state)
        {
            var values = new List<JObject>();
            foreach (string key in new[] { "font", "settings" })
            {
                string path = state.Value<string>(key);
                Need(!string.IsNullOrEmpty(path), "owned_subasset_file_missing");
                var assets = AssetDatabase.LoadAllAssetsAtPath(path);
                Need(assets.Length == 3, "owned_subasset_count_changed");
                values.AddRange(
                    assets.Select(value => new JObject
                    {
                        ["file"] = path,
                        ["identity"] = AssetIdentity(value),
                        ["type"] = value.GetType().FullName,
                    })
                );
            }
            Need(values.Count == 6, "owned_subasset_bound");
            return new JArray(values.OrderBy(value => value.Value<string>("identity"), StringComparer.Ordinal));
        }

        private static GameObject ResolveRole(JObject state, string role)
        {
            Scene scene = FindOwnedScene(state);
            var canvas = scene.GetRootGameObjects().Single(go => go.name == state.Value<string>("canvas"));
            string path = ((JObject)state["roles"]).Value<string>(role);
            Need(
                path != null
                    && path.Length <= 512
                    && path.Split('/').Length <= 8
                    && !path.Split('/').Any(part => part == ".." || part == "." || part.Length == 0),
                "invalid_owned_role_path"
            );
            var result = canvas.transform.Find(path);
            Need(result != null, "owned_role_missing");
            return result.gameObject;
        }

        private static string RelativePath(Transform ancestor, Transform child)
        {
            var names = new List<string>();
            while (!ReferenceEquals(child, ancestor))
            {
                Need(child != null && names.Count < 8 && !child.name.Contains("/"), "bounded_owned_role_required");
                names.Add(child.name);
                child = child.parent;
            }
            names.Reverse();
            string result = string.Join("/", names);
            Need(result.Length <= 512, "role_path_bound");
            return result;
        }

        private void CleanupEditor(string fault)
        {
            if (Application.isPlaying)
            {
                AddError("editor_cleanup_while_playing", new InvalidOperationException());
                return;
            }
            var state = State();
            CheckState(state);
            if (((JArray)state["folders"]).Count == 0)
                return; // Admission failed before any owned allocation.
            Capture(
                "close_owned_scene",
                () =>
                {
                    string path = state.Value<string>("scene");
                    if (string.IsNullOrEmpty(path))
                        return;
                    var scene = SceneManager.GetSceneByPath(path);
                    if (scene.IsValid() && scene.isLoaded)
                    {
                        AssertOnlyOwnedRoots(scene, state);
                        Need(EditorSceneManager.CloseScene(scene, true), "owned_scene_close_failed");
                    }
                }
            );
            bool closed = string.IsNullOrEmpty(state.Value<string>("scene")) || !SceneManager.GetSceneByPath(state.Value<string>("scene")).isLoaded;
            if (!closed)
            {
                AddError("assets_retained_for_live_scene", new InvalidOperationException());
                return;
            }
            bool cacheSafe = false;
            Capture(
                "owned_cache_cleanup",
                () =>
                {
                    string fontPath = state.Value<string>("font") ?? state.Value<string>("folder") + "/Font.asset";
                    var fontRow = ((JArray)state["files"]).SingleOrDefault(row => row.Value<string>("path") == fontPath);
                    string fontGuid = AssetDatabase.AssetPathToGUID(fontPath);
                    Need(string.IsNullOrEmpty(fontGuid) || fontRow != null && fontRow.Value<string>("guid") == fontGuid, "foreign_font_GUID_preserved");
                    Type type = RequiredType("TMPro.TMP_FontAsset");
                    // Before persistence, use only the exact object returned to this fixture in this domain.
                    // Never recover an object by a recyclable ID or a global resource scan.
                    var font = string.IsNullOrEmpty(fontGuid)
                        ? transient
                            .Where(value => value != null && type.IsInstanceOfType(value) && !EditorUtility.IsPersistent(value))
                            .Cast<ScriptableObject>()
                            .SingleOrDefault()
                        : AssetDatabase.LoadAssetAtPath<ScriptableObject>(fontPath);
                    if (font != null)
                    {
                        foreach (var registry in FontRegistries())
                        {
                            Need(registry.Count <= 4096, "font_registry_bound");
                            foreach (object item in registry.Values)
                            {
                                object value =
                                    item is UnityEngine.Object
                                        ? item
                                        : item.GetType().GetField("fontAsset", BindingFlags.Public | BindingFlags.Instance).GetValue(item);
                                Need(ReferenceEquals(value, font), "foreign_font_cache_preserved");
                            }
                        }
                        var callbacks = FontCallbacks();
                        Need(callbacks.Count <= 4096, "callback_bound");
                        foreach (object weak in callbacks)
                        {
                            object[] target = { null };
                            Need(
                                (bool)weak.GetType().GetMethod("TryGetTarget").Invoke(weak, target) && ReferenceEquals(target[0], font),
                                "foreign_or_dead_font_callback_preserved"
                            );
                        }
                        var resource = type.Assembly.GetType("TMPro.TMP_ResourceManager", true);
                        resource.GetMethod("RemoveFontAsset", new[] { type }).Invoke(null, new object[] { font });
                        for (int i = callbacks.Count - 1; i >= 0; i--)
                        {
                            object[] target = { null };
                            if ((bool)callbacks[i].GetType().GetMethod("TryGetTarget").Invoke(callbacks[i], target) && ReferenceEquals(target[0], font))
                                callbacks.RemoveAt(i);
                        }
                        foreach (var registry in FontRegistries())
                            Need(registry.Count == 0, "owned_font_cache_remains");
                        Need(callbacks.Count == 0, "owned_font_callback_remains");
                    }
                    else
                    {
                        foreach (var registry in FontRegistries())
                            Need(registry.Count == 0, "untracked_font_registry_preserved");
                        Need(FontCallbacks().Count == 0, "untracked_font_callbacks_preserved");
                    }
                    object current = SettingsSlot().GetValue(null);
                    if (current != null)
                    {
                        string settingsPath = state.Value<string>("settings") ?? state.Value<string>("folder") + "/Resources/TMP Settings.asset";
                        var settingsRow = ((JArray)state["files"]).SingleOrDefault(row => row.Value<string>("path") == settingsPath);
                        Need(
                            settingsRow != null
                                && AssetDatabase.GetAssetPath(current as UnityEngine.Object) == settingsPath
                                && AssetDatabase.AssetPathToGUID(settingsPath) == settingsRow.Value<string>("guid"),
                            "foreign_TMP_settings_preserved"
                        );
                        var settings = AssetDatabase.LoadAssetAtPath<ScriptableObject>(settingsPath);
                        Need(ReferenceEquals(current, settings), "foreign_TMP_settings_preserved");
                        SettingsSlot().SetValue(null, null);
                    }
                    cacheSafe = true;
                }
            );
            if (!cacheSafe)
            {
                AddError("assets_retained_for_cache_uncertainty", new InvalidOperationException());
                return;
            }
            if (fault == "after_cache_cleanup")
                Capture("injected_cleanup", () => throw new InvalidOperationException("owned_cleanup_fault"));
            foreach (var row in ((JArray)state["files"]).Reverse().ToArray())
                Capture(
                    "delete_owned_asset",
                    () =>
                    {
                        string path = row.Value<string>("path");
                        string guid = AssetDatabase.AssetPathToGUID(path, AssetPathToGUIDOptions.OnlyExistingAssets);
                        if (string.IsNullOrEmpty(guid))
                            return;
                        Need(guid == row.Value<string>("guid"), "foreign_asset_GUID_preserved");
                        Need(AssetDatabase.DeleteAsset(path), "owned_asset_delete_failed");
                    }
                );
            foreach (var value in transient.AsEnumerable().Reverse())
                Capture(
                    "transient_cleanup",
                    () =>
                    {
                        if (value != null && !EditorUtility.IsPersistent(value))
                            UnityEngine.Object.DestroyImmediate(value);
                    }
                );
            transient.Clear();
            foreach (var row in ((JArray)state["folders"]).Reverse().ToArray())
                Capture(
                    "delete_owned_empty_folder",
                    () =>
                    {
                        string path = row.Value<string>("path");
                        if (!AssetDatabase.IsValidFolder(path))
                            return;
                        Need(
                            AssetDatabase.AssetPathToGUID(path) == row.Value<string>("guid") && Directory.GetFileSystemEntries(DiskPath(path)).Length == 0,
                            "foreign_folder_content_preserved"
                        );
                        Need(AssetDatabase.DeleteAsset(path), "owned_folder_delete_failed");
                    }
                );
            Capture(
                "previous_scene_restore",
                () =>
                {
                    var previous = Enumerable
                        .Range(0, SceneManager.sceneCount)
                        .Select(SceneManager.GetSceneAt)
                        .Single(scene => scene.path == state.Value<string>("previousActivePath") && scene.name == state.Value<string>("previousActiveName"));
                    bool previousActiveRestored = SelectActiveScene(previous);
                    string reason = "previous_active_scene_restore_failed";
                    if (!previousActiveRestored)
                        try
                        {
                            reason += ":valid=" + previous.IsValid();
                            reason += ";loaded=" + previous.isLoaded;
                            reason += ";preview=" + EditorSceneManager.IsPreviewScene(previous);
                            reason += ";active=" + SceneManager.GetActiveScene().Equals(previous);
                        }
                        catch (Exception error)
                        {
                            reason += ";flags_unavailable=" + error.GetType().Name;
                        }
                    Need(previousActiveRestored, reason);
                    var current = new JArray(
                        Enumerable
                            .Range(0, SceneManager.sceneCount)
                            .Select(SceneManager.GetSceneAt)
                            .Select(scene => new JObject
                            {
                                ["path"] = scene.path,
                                ["name"] = scene.name,
                                ["dirty"] = scene.isDirty,
                                ["roots"] = new JArray(scene.GetRootGameObjects().Select(go => go.name).OrderBy(name => name)),
                            })
                    );
                    Need(JToken.DeepEquals(current, state["previousScenes"]), "previous_scenes_changed");
                    Need(Selection.activeObject == null, "selection_changed");
                    Need(
                        state.Value<bool>("reloadEnabled") == EditorSettings.enterPlayModeOptionsEnabled
                            && state.Value<int>("reloadOptions") == (int)EditorSettings.enterPlayModeOptions,
                        "user_reload_settings_changed"
                    );
                }
            );
        }

        private void TrackAsset(string path)
        {
            string guid = AssetDatabase.AssetPathToGUID(path);
            Need(!string.IsNullOrEmpty(guid), "created_asset_GUID_required");
            var state = State();
            ((JArray)state["files"]).Add(new JObject { ["path"] = path, ["guid"] = guid });
            SetState(state);
        }

        private void TrackFolder(string path, string guid)
        {
            Need(!string.IsNullOrEmpty(guid) && AssetDatabase.GUIDToAssetPath(guid) == path, "created_folder_GUID_required");
            var state = State();
            ((JArray)state["folders"]).Add(new JObject { ["path"] = path, ["guid"] = guid });
            SetState(state);
        }

        private JObject State()
        {
            Need(!string.IsNullOrEmpty(OwnedStateJson), "missing_owned_state");
            return JObject.Parse(OwnedStateJson);
        }

        private void SetState(JObject state)
        {
            string encoded = state.ToString(Newtonsoft.Json.Formatting.None);
            Need(System.Text.Encoding.UTF8.GetByteCount(encoded) <= 16384, "ownership_state_bound");
            OwnedStateJson = encoded;
        }

        private void CheckState(JObject state)
        {
            string nonce = Environment.GetEnvironmentVariable("UNITY_MCP_UI16_CARET_NONCE");
            string kind = state.Value<string>("case");
            Need(nonce != null && state.Value<string>("nonce") == nonce && new[] { "lifecycle", "persist", "cleanup" }.Contains(kind), "wrong_owner_state");
            string folder = "Assets/UI16Caret_" + nonce + "_" + kind;
            Need(
                state.Value<string>("folder") == folder
                    && ((JArray)state["files"]).Count <= 3
                    && ((JArray)state["folders"]).Count <= 2
                    && state.Value<int>("toolCalls") <= 1,
                "owner_bounds"
            );
            foreach (var row in (JArray)state["files"])
                Need(
                    new[] { folder + "/Owned Scene.unity", folder + "/Font.asset", folder + "/Resources/TMP Settings.asset" }.Contains(
                        row.Value<string>("path")
                    ),
                    "unowned_asset_path"
                );
            foreach (var row in (JArray)state["folders"])
                Need(new[] { folder, folder + "/Resources" }.Contains(row.Value<string>("path")), "unowned_folder_path");
        }

        private static bool SelectActiveScene(Scene scene)
        {
            if (!scene.IsValid() || !scene.isLoaded || EditorSceneManager.IsPreviewScene(scene))
                return false;
            if (SceneManager.GetActiveScene() != scene)
                SceneManager.SetActiveScene(scene);
            return scene.IsValid() && scene.isLoaded && !EditorSceneManager.IsPreviewScene(scene) && SceneManager.GetActiveScene() == scene;
        }

        private static Scene FindOwnedScene(JObject state)
        {
            var scene = SceneManager.GetSceneByPath(state.Value<string>("scene"));
            Need(scene.IsValid() && scene.isLoaded, "owned_scene_missing");
            return scene;
        }

        private static void AssertOnlyOwnedRoots(Scene scene, JObject state)
        {
            Need(
                scene
                    .GetRootGameObjects()
                    .All(go => new[] { state.Value<string>("canvas"), state.Value<string>("events"), state.Value<string>("witness") }.Contains(go.name)),
                "foreign_scene_root_preserved"
            );
        }

        private static void RequireNormalReloadSettings()
        {
            Need(
                !EditorSettings.enterPlayModeOptionsEnabled || EditorSettings.enterPlayModeOptions == EnterPlayModeOptions.None,
                "normal_domain_and_scene_reload_required_no_settings_changes"
            );
        }

        private static Type WitnessType()
        {
            var assemblies = AppDomain.CurrentDomain.GetAssemblies();
            Need(assemblies.Length <= 512, "assembly_lookup_bound");
            var matches = assemblies.Where(assembly => assembly.GetName().Name == WitnessAssembly).ToArray();
            Need(matches.Length == 1, "exact_already_loaded_runtime_assembly_required");
            Type type = matches[0].GetType(WitnessName, true);
            Need(type.IsSealed && type.BaseType == typeof(MonoBehaviour) && type.Assembly == matches[0], "exact_runtime_witness_type_required");
            foreach (string field in new[] { "nonce", "ownedScenePath", "observedNonce", "observedScenePath" })
                Need(
                    type.GetField(field, BindingFlags.Public | BindingFlags.Instance | BindingFlags.DeclaredOnly)?.FieldType == typeof(string),
                    "exact_witness_string_contract"
                );
            Need(
                type.GetField("observed", BindingFlags.Public | BindingFlags.Instance | BindingFlags.DeclaredOnly)?.FieldType == typeof(bool)
                    && type.GetField("matchingEvents", BindingFlags.Public | BindingFlags.Instance | BindingFlags.DeclaredOnly)?.FieldType == typeof(int),
                "exact_witness_event_contract"
            );
            return type;
        }

        private static string CaseRoot() => Path.GetFullPath(Path.Combine(Application.dataPath, "..", ".."));

        private string ExportRoot()
        {
            string path = Environment.GetEnvironmentVariable("UNITY_MCP_UI16_CARET_EXPORT_ROOT");
            Need(
                path != null && Path.GetFullPath(path) == Path.Combine(CaseRoot(), "fixture-exports", "ui16-caret") && Directory.Exists(path),
                "exact_existing_fixture_export_root_required"
            );
            NoLinks(path);
            return Path.GetFullPath(path);
        }

        private static void NoLinks(string path)
        {
            FileSystemInfo cursor = Directory.Exists(path) ? (FileSystemInfo)new DirectoryInfo(Path.GetFullPath(path)) : new FileInfo(Path.GetFullPath(path));
            for (; cursor != null; cursor = cursor is DirectoryInfo directory ? directory.Parent : ((FileInfo)cursor).Directory)
                Need((cursor.Attributes & FileAttributes.ReparsePoint) == 0, "reparse_refused");
        }

        private void WriteEvidence(string suffix, JObject value)
        {
            byte[] bytes = System.Text.Encoding.UTF8.GetBytes(value.ToString(Newtonsoft.Json.Formatting.None));
            Need(bytes.Length <= 16384, "evidence_bound");
            string path = Path.Combine(ExportRoot(), State().Value<string>("case") + "." + suffix + ".json");
            using (var stream = new FileStream(path, FileMode.CreateNew, FileAccess.Write))
                stream.Write(bytes, 0, bytes.Length);
        }

        private static void CheckFile(string path, string hash, long bytes)
        {
            Need(File.Exists(path) && new FileInfo(path).Length == bytes, "exact_owned_input_required");
            NoLinks(path);
            Need(HashFile(path) == hash, "owned_input_digest_mismatch");
        }

        private static string DiskPath(string path) =>
            path.StartsWith("Assets/", StringComparison.Ordinal) ? Path.GetFullPath(Path.Combine(Application.dataPath, "..", path)) : Path.GetFullPath(path);

        private static void FreshAsset(string path) =>
            Need(
                string.IsNullOrEmpty(AssetDatabase.AssetPathToGUID(path))
                    && !File.Exists(DiskPath(path))
                    && !Directory.Exists(DiskPath(path))
                    && !File.Exists(DiskPath(path) + ".meta"),
                "existing_asset_preserved"
            );

        private static string HashFile(string path)
        {
            path = DiskPath(path);
            NoLinks(path);
            Need(new FileInfo(path).Length <= 16 * 1024 * 1024, "owned_file_read_bound");
            return Hash(File.ReadAllBytes(path));
        }

        private static string Hash(byte[] bytes)
        {
            using (var hash = System.Security.Cryptography.SHA256.Create())
                return BitConverter.ToString(hash.ComputeHash(bytes)).Replace("-", "").ToLowerInvariant();
        }

        private void Capture(string phase, Action action)
        {
            try
            {
                action();
            }
            catch (Exception error)
            {
                AddError(phase, error);
            }
        }

        private void AddError(string phase, Exception error)
        {
            var errors = JArray.Parse(CleanupErrorsJson ?? "[]");
            Need(errors.Count < 32, "cleanup_error_bound");
            string message;
            try
            {
                message = error.Message ?? "";
            }
            catch (Exception)
            {
                message = "<message unavailable>";
            }
            if (message.Length > 128)
                message = message.Substring(0, 128);
            errors.Add(
                new JObject
                {
                    ["phase"] = phase,
                    ["type"] = error.GetType().FullName,
                    ["message"] = message,
                }
            );
            CleanupErrorsJson = errors.ToString(Newtonsoft.Json.Formatting.None);
        }

        private static IEnumerable<GameObject> LoadedSceneObjects()
        {
            var queue = new Queue<Transform>();
            var result = new List<GameObject>();
            for (int index = 0; index < SceneManager.sceneCount; index++)
                foreach (var root in SceneManager.GetSceneAt(index).GetRootGameObjects())
                    queue.Enqueue(root.transform);
            while (queue.Count != 0)
            {
                var next = queue.Dequeue();
                Need(result.Count + queue.Count + next.childCount + 1 <= 16384, "fixture_scene_object_bound");
                result.Add(next.gameObject);
                for (int index = 0; index < next.childCount; index++)
                    queue.Enqueue(next.GetChild(index));
            }
            return result;
        }

        private static Type RequiredType(string name) =>
            UnityTypeResolver.ResolveAny(name) ?? throw new InvalidOperationException("required_type_missing:" + name);

        private static FieldInfo SettingsSlot() => RequiredType("TMPro.TMP_Settings").GetField("s_Instance", BindingFlags.NonPublic | BindingFlags.Static);

        private static IDictionary[] FontRegistries() =>
            new[] { "s_FontAssetReferences", "s_FontAssetNameReferenceLookup", "s_FontAssetFamilyNameAndStyleReferenceLookup" }
                .Select(name =>
                    (IDictionary)
                        RequiredType("TMPro.TMP_FontAsset")
                            .Assembly.GetType("TMPro.TMP_ResourceManager", true)
                            .GetField(name, BindingFlags.NonPublic | BindingFlags.Static)
                            .GetValue(null)
                )
                .ToArray();

        private static IList FontCallbacks() =>
            (IList)RequiredType("TMPro.TMP_FontAsset").GetField("s_CallbackInstances", BindingFlags.NonPublic | BindingFlags.Static).GetValue(null);

        private static object Read(object obj, string name) => obj.GetType().GetProperty(name).GetValue(obj);

        private static void Write(object obj, string name, object value) => obj.GetType().GetProperty(name).SetValue(obj, value);

        private static object Field(object obj, string name) =>
            obj.GetType().GetField(name, BindingFlags.Public | BindingFlags.Instance | BindingFlags.DeclaredOnly).GetValue(obj);

        private static void SetField(object obj, string name, object value) =>
            obj.GetType().GetField(name, BindingFlags.Public | BindingFlags.Instance | BindingFlags.DeclaredOnly).SetValue(obj, value);

        private static object Invoke(object obj, string name, params object[] args) =>
            obj.GetType()
                .GetMethods(BindingFlags.Public | BindingFlags.Instance)
                .Single(method =>
                    method.Name == name
                    && method.GetParameters().Length == args.Length
                    && method.GetParameters().Select((parameter, i) => args[i] == null || parameter.ParameterType.IsInstanceOfType(args[i])).All(value => value)
                )
                .Invoke(obj, args);

        private static void Need(bool value, string message)
        {
            if (!value)
                throw new InvalidOperationException(message);
        }
    }
}
