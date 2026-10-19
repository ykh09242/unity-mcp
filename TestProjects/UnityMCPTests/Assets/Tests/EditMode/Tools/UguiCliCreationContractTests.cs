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
using UnityEngine.TestTools;

namespace MCPForUnityTests.EditMode.Tools
{
    [Parallelizable(ParallelScope.None)]
    [Explicit("Requires a fresh owned NativeQA case, shared saved scene template and owned font-input/UI17.ttf.")]
    public class UguiCliCreationContractTests
    {
        private const string EmptySceneTemplateGuid = "d8f5ae9136f356e190abdc5c9be58fb6";
        private Scene previous,
            owned;
        private GameObject root;
        private ScriptableObject settings,
            font;
        private Type settingsType,
            fontType;
        private FieldInfo settingsSlot;
        private object originalSettings;
        private bool settingsInstalled;
        private IDictionary[] fontRegistries;
        private IList fontCallbacks;
        private object[] originalFontCallbacks;
        private Type resourceManager;
        private readonly List<UnityEngine.Object> fontObjects = new List<UnityEngine.Object>();
        private UnityEngine.Object previousSelection;
        private SceneSnapshot[] preservedScenes;
        private string folder,
            ownedFolderGuid;
        private int undoGroup;
        private bool ownsFolder,
            ownsScene,
            ownsUndoGroup,
            ownsSelection;

        [SetUp]
        public void SetUp()
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
                string prefix = "UI17_" + Guid.NewGuid().ToString("N");
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
                root = new GameObject("UI17Canvas", typeof(RectTransform), typeof(Canvas));
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
                    throw new AggregateException("Owned CLI fixture setup and cleanup failed.", setupError, cleanupError);
                }
                throw;
            }
        }

        [TearDown]
        public void TearDown()
        {
            var errors = new List<Exception>();
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

        private sealed class SceneSnapshot
        {
            public Scene Scene;
            public string Path;
            public bool Dirty,
                Loaded;
            public int[] RootIds;
        }

        private static int[] RootIds(Scene scene) => scene.GetRootGameObjects().Select(go => go.GetInstanceIDCompat()).OrderBy(id => id).ToArray();

        private void AssertPreservedScenes(bool expectOwnedScene)
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

        private static void Cleanup(Action action, List<Exception> errors)
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

        private static Type Ui(string name) => UnityTypeResolver.ResolveComponent("UnityEngine.UI." + name);

        private JObject Request(string kind, string name = "Duplicate")
        {
            var request = new JObject
            {
                ["action"] = "create_cli",
                ["element_type"] = kind,
                ["name"] = name,
            };
            if (kind != "canvas")
                request["parent"] = root.GetInstanceIDCompat();
            if (kind == "text" || kind == "button")
                request["text"] = "Launch";
            return request;
        }

        private static JObject Send(JObject request) => JObject.FromObject(ManageUGUI.HandleCommand(request));

        private static GameObject Created(JObject response)
        {
            Assert.That(response.Value<bool>("success"), Is.True, response.ToString());
            Assert.That(response["data"]["instanceID"].Type, Is.EqualTo(JTokenType.Integer));
            var go = GameObjectLookup.FindById(response["data"].Value<int>("instanceID"));
            Assert.That(go, Is.Not.Null);
            return go;
        }

        private static JObject LegacyRoot(string name, GameObject parent, Vector2? textPosition = null)
        {
            var input = new JObject { ["name"] = name };
            if (parent != null)
                input["parent"] = parent.GetInstanceIDCompat();
            if (textPosition.HasValue)
                input["position"] = new JArray(textPosition.Value.x, textPosition.Value.y, 0);
            var handler = typeof(ManageUGUI).Assembly.GetType("MCPForUnity.Editor.Tools.GameObjects.GameObjectCreate", true);
            var response = JObject.FromObject(handler.GetMethod("Handle", BindingFlags.Static | BindingFlags.NonPublic).Invoke(null, new object[] { input }));
            Assert.That(
                Created(response).transform.GetType(),
                Is.EqualTo(typeof(Transform)),
                "Generic creation starts with an ordinary Transform before UI components are added."
            );
            return response;
        }

        private JObject Legacy(
            string kind,
            string name,
            RenderMode mode = RenderMode.ScreenSpaceOverlay,
            string text = "Launch",
            Vector2? position = null,
            Sprite sprite = null
        )
        {
            // Independent positive control: the actual old construction sequence, with only
            // the broken text Vector3 arity corrected to reach the documented anchored setter.
            var response = LegacyRoot(name, kind == "canvas" ? null : root, kind == "text" ? position ?? Vector2.zero : (Vector2?)null);
            var go = Created(response);
            if (kind == "canvas")
            {
                var canvas = Undo.AddComponent<Canvas>(go);
                Undo.AddComponent(go, Ui("CanvasScaler"));
                Undo.AddComponent(go, Ui("GraphicRaycaster"));
                Undo.RecordObject(canvas, "Legacy Canvas mode control");
                canvas.renderMode = mode;
            }
            if (kind == "image" || kind == "button")
            {
                var image = Undo.AddComponent(go, Ui("Image"));
                if (sprite != null)
                {
                    Undo.RecordObject(image, "Legacy sprite control");
                    image.GetType().GetProperty("sprite").SetValue(image, sprite);
                }
            }
            if (kind == "button")
            {
                Undo.AddComponent(go, Ui("Button"));
                var label = Created(LegacyRoot(name + "_Label", go));
                var component = Undo.AddComponent(label, TmpType());
                component.GetType().GetProperty("text").SetValue(component, text);
            }
            if (kind == "text")
            {
                var component = Undo.AddComponent(go, TmpType());
                component.GetType().GetProperty("text").SetValue(component, text);
                ((RectTransform)go.transform).anchoredPosition = position ?? Vector2.zero;
            }
            return response;
        }

        private static JArray V2(Vector2 value) => new JArray(value.x, value.y);

        private static JArray V3(Vector3 value) => new JArray(value.x, value.y, value.z);

        private static JObject State(GameObject go)
        {
            var state = new JObject
            {
                ["name"] = go.name,
                ["types"] = new JArray(go.GetComponents<Component>().Select(value => value.GetType().FullName)),
                ["local_position"] = V3(go.transform.localPosition),
                ["local_scale"] = V3(go.transform.localScale),
                ["local_rotation"] = V3(go.transform.localEulerAngles),
                ["world_position"] = V3(go.transform.position),
            };
            if (go.transform is RectTransform rect)
                state["rect"] = new JObject
                {
                    ["anchorMin"] = V2(rect.anchorMin),
                    ["anchorMax"] = V2(rect.anchorMax),
                    ["pivot"] = V2(rect.pivot),
                    ["sizeDelta"] = V2(rect.sizeDelta),
                    ["offsetMin"] = V2(rect.offsetMin),
                    ["offsetMax"] = V2(rect.offsetMax),
                    ["anchoredPosition"] = V2(rect.anchoredPosition),
                };
            foreach (string typeName in new[] { "UnityEngine.UI.Image", "UnityEngine.UI.Button", "UnityEngine.UI.CanvasScaler", "TMPro.TextMeshProUGUI" })
            {
                Type type = UnityTypeResolver.ResolveComponent(typeName);
                Component component = type == null ? null : go.GetComponent(type);
                if (component == null)
                    continue;
                var values = new JObject();
                string[] names =
                    typeName.EndsWith("Image", StringComparison.Ordinal) ? new[] { "color", "raycastTarget", "type", "sprite", "preserveAspect" }
                    : typeName.EndsWith("Button", StringComparison.Ordinal) ? new[] { "interactable", "targetGraphic" }
                    : typeName.EndsWith("CanvasScaler", StringComparison.Ordinal)
                        ? new[] { "uiScaleMode", "scaleFactor", "referencePixelsPerUnit", "referenceResolution", "screenMatchMode", "matchWidthOrHeight" }
                    : new[] { "text", "font", "fontSize", "color", "raycastTarget", "alignment", "enableAutoSizing" };
                foreach (string name in names)
                {
                    object value = type.GetProperty(name).GetValue(component);
                    if (name == "targetGraphic")
                        values[name] = value is Component graphic && graphic.gameObject == go;
                    else if (value is UnityEngine.Object reference)
                        values[name] = reference == null ? JValue.CreateNull() : new JValue(reference.GetInstanceIDCompat());
                    else if (value is Vector2 vector)
                        values[name] = V2(vector);
                    else if (value is Color color)
                        values[name] = new JArray(color.r, color.g, color.b, color.a);
                    else
                        values[name] = value == null ? JValue.CreateNull() : JToken.FromObject(value);
                }
                state[typeName] = values;
            }
            if (go.TryGetComponent<Canvas>(out var canvas))
            {
                state["canvas_mode"] = canvas.renderMode.ToString();
                state["canvas_stored_mode"] = StoredCanvasMode(canvas).ToString();
            }
            state["children"] = new JArray(Enumerable.Range(0, go.transform.childCount).Select(index => State(go.transform.GetChild(index).gameObject)));
            return state;
        }

        private static RenderMode StoredCanvasMode(Canvas canvas)
        {
            using (var serialized = new SerializedObject(canvas))
            {
                var mode = serialized.FindProperty("m_RenderMode");
                Assert.That(mode, Is.Not.Null);
                Assert.That(mode.propertyType, Is.EqualTo(SerializedPropertyType.Enum));
                return (RenderMode)mode.intValue;
            }
        }

        private static void AssertSpriteReference(GameObject go, Sprite expected)
        {
            var image = go.GetComponent(Ui("Image"));
            var actual = (Sprite)image.GetType().GetProperty("sprite").GetValue(image);
            if (expected == null)
                Assert.That(actual == null, Is.True, "An absent sprite stays Unity-null across Undo and Redo.");
            else
            {
                Assert.That(actual != null, Is.True);
                Assert.That(actual == expected, Is.True, "The restored sprite references the same native asset.");
                Assert.That(actual.GetInstanceIDCompat(), Is.Not.Zero);
                Assert.That(actual.GetInstanceIDCompat(), Is.EqualTo(expected.GetInstanceIDCompat()));
            }
        }

        private static void SameInitialResponse(JObject actual, JObject legacy)
        {
            actual = (JObject)actual.DeepClone();
            legacy = (JObject)legacy.DeepClone();
            ((JObject)actual["data"]).Remove("instanceID");
            ((JObject)legacy["data"]).Remove("instanceID");
            Assert.That(JToken.DeepEquals(actual, legacy), Is.True, actual + "\n" + legacy);
        }

        private static void Journal(string key, JObject request, JObject response)
        {
            TestContext.WriteLine(
                "UI17_NATIVE_EXCHANGE:"
                    + new JObject
                    {
                        ["case"] = key,
                        ["request"] = request.DeepClone(),
                        ["response"] = response.DeepClone(),
                    }.ToString(Newtonsoft.Json.Formatting.None)
            );
        }

        private static JObject BoundedUndoDiagnostic(JToken value, int limit)
        {
            string text = value == null ? "null" : value.ToString(Newtonsoft.Json.Formatting.None);
            return new JObject
            {
                ["json"] = text.Length > limit ? text.Substring(0, limit) : text,
                ["characters"] = text.Length,
                ["truncated"] = text.Length > limit,
            };
        }

        private static JObject UndoDiagnosticVector(SerializedObject serialized, string name)
        {
            var property = serialized.FindProperty(name);
            return new JObject
            {
                ["type"] = property == null ? "missing" : property.propertyType.ToString(),
                ["value"] =
                    property != null && property.propertyType == SerializedPropertyType.Vector3 ? (JToken)V3(property.vector3Value)
                    : property != null && property.propertyType == SerializedPropertyType.Vector2 ? (JToken)V2(property.vector2Value)
                    : JValue.CreateNull(),
            };
        }

        private void WriteUndoDiscriminator(string scenario, string phase, GameObject target, JObject state)
        {
            var geometry = new JObject
            {
                ["screen"] = new JArray(Screen.width, Screen.height),
                ["batch_mode"] = Application.isBatchMode,
                ["graphics_device"] = SystemInfo.graphicsDeviceType.ToString(),
                ["frame"] = Time.frameCount,
                ["rendered_frame"] = Time.renderedFrameCount,
            };
            var details = new JObject();
            if (target != null && target.transform is RectTransform rect)
            {
                using (var serialized = new SerializedObject(rect))
                {
                    details["m_LocalPosition"] = UndoDiagnosticVector(serialized, "m_LocalPosition");
                    details["m_AnchoredPosition"] = UndoDiagnosticVector(serialized, "m_AnchoredPosition");
                    details["m_LocalScale"] = UndoDiagnosticVector(serialized, "m_LocalScale");
                }
                var driver = rect.drivenByObject;
                details["driver_type"] = driver == null ? JValue.CreateNull() : new JValue(driver.GetType().FullName);
                details["driver_on_target"] = driver is Component component && component.gameObject == target;
                // Unity6000 exposes this getter internally; do not add a compile-time dependency.
                var driven = typeof(RectTransform).GetProperty("drivenProperties", BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic);
                details["driven_property_available"] = driven != null;
                details["driven_properties"] = driven == null ? JValue.CreateNull() : JToken.FromObject(driven.GetValue(rect).ToString());
            }
            if (target != null && target.TryGetComponent<Canvas>(out var canvas))
            {
                Rect pixels = canvas.pixelRect;
                geometry["canvas_scale_factor"] = canvas.scaleFactor;
                geometry["pixel_rect"] = new JArray(pixels.x, pixels.y, pixels.width, pixels.height);
                geometry["target_display"] = canvas.targetDisplay;
            }
            TestContext.WriteLine(
                "UI17_UNDO_DISCRIMINATOR:"
                    + new JObject
                    {
                        ["scenario"] = scenario,
                        ["phase"] = phase,
                        ["instance_id"] = target == null ? (JToken)JValue.CreateNull() : new JValue(target.GetInstanceIDCompat()),
                        ["fixture_group"] = undoGroup,
                        ["current_group"] = Undo.GetCurrentGroup(),
                        ["state"] = BoundedUndoDiagnostic(state, 4096),
                        ["geometry"] = BoundedUndoDiagnostic(geometry, 512),
                        ["serialized_driven"] = BoundedUndoDiagnostic(details, 1024),
                    }.ToString(Newtonsoft.Json.Formatting.None)
            );
        }

        [Test]
        public void RectTransformPriorUndoBaselineWithoutConstruction()
        {
            Assert.That(ownsUndoGroup, Is.True);
            int entryGroup = Undo.GetCurrentGroup();
            Vector3 markerBefore = root.transform.localPosition;
            WriteUndoDiscriminator("prior_undo_baseline", "before_record", root, State(root));
            Undo.RecordObject(root.transform, "Previous owned edit before Canvas failure");
            ((RectTransform)root.transform).anchoredPosition3D += Vector3.one;
            Undo.FlushUndoRecordObjects();
            Undo.IncrementCurrentGroup();
            WriteUndoDiscriminator("prior_undo_baseline", "after_edit_flush", root, State(root));
            Undo.PerformUndo();
            Vector3 markerAfter = root.transform.localPosition;
            WriteUndoDiscriminator("prior_undo_baseline", "after_undo", root, State(root));
            TestContext.WriteLine(
                "UI17_UNDO_DISCRIMINATOR:"
                    + new JObject
                    {
                        ["scenario"] = "prior_undo_baseline",
                        ["phase"] = "comparison",
                        ["entry_group"] = entryGroup,
                        ["current_group"] = Undo.GetCurrentGroup(),
                        ["before"] = V3(markerBefore),
                        ["after"] = V3(markerAfter),
                        ["xy_equal"] = markerBefore.x == markerAfter.x && markerBefore.y == markerAfter.y,
                        ["z_equal"] = markerBefore.z == markerAfter.z,
                    }.ToString(Newtonsoft.Json.Formatting.None)
            );
            Assert.That(
                root.transform.localPosition,
                Is.EqualTo(markerBefore),
                "Creation-free prior Undo must restore the same SetUp-owned RectTransform marker."
            );
        }

        private void UndoRedo(JObject response, JObject expected)
        {
            int id = response["data"].Value<int>("instanceID");
            bool diagnoseOverlay = expected.Value<string>("canvas_stored_mode") == "ScreenSpaceOverlay";
            if (diagnoseOverlay)
                WriteUndoDiscriminator("current_overlay", "before_undo", GameObjectLookup.FindById(id), expected);
            Undo.PerformUndo();
            Assert.That(GameObjectLookup.FindById(id), Is.Null, "One Undo removes the whole new root and its label.");
            Undo.PerformRedo();
            var restored = GameObjectLookup.FindById(id);
            Assert.That(restored, Is.Not.Null);
            var actual = State(restored);
            Exception comparisonError = null;
            try
            {
                Assert.That(JToken.DeepEquals(actual, expected), Is.True, "Redo restores complete references and settings.");
            }
            catch (Exception error)
            {
                comparisonError = error;
                throw;
            }
            finally
            {
                if (diagnoseOverlay)
                {
                    try
                    {
                        WriteUndoDiscriminator("current_overlay", "after_redo", restored, actual);
                    }
                    catch (Exception observationError)
                    {
                        if (comparisonError != null)
                            throw new AggregateException("Overlay state comparison and readonly observation failed.", comparisonError, observationError);
                        throw;
                    }
                }
            }
        }

        [TestCase("WorldSpace")]
        public void CanvasDefaultsAndInitialOutputMatchLegacyWithOneUndoRedo(string renderMode)
        {
            var control = Legacy("canvas", "CanvasControl", (RenderMode)Enum.Parse(typeof(RenderMode), renderMode));
            var request = Request("canvas", "CanvasControl");
            request["render_mode"] = renderMode;
            var result = Send(request);
            var go = Created(result);
            SameInitialResponse(result, control);
            Assert.That(JToken.DeepEquals(State(go), State(Created(control))), Is.True);
            var canvas = go.GetComponent<Canvas>();
            var requestedMode = (RenderMode)Enum.Parse(typeof(RenderMode), renderMode);
            Assert.That(StoredCanvasMode(canvas), Is.EqualTo(requestedMode));
            Assert.That(canvas.worldCamera == null, Is.True, "CLI construction does not inject a camera.");
            Assert.That(canvas.renderMode, Is.EqualTo(Created(control).GetComponent<Canvas>().renderMode));
            Assert.That(
                canvas.renderMode,
                Is.EqualTo(requestedMode == RenderMode.ScreenSpaceCamera ? RenderMode.ScreenSpaceOverlay : requestedMode),
                "A stored ScreenSpaceCamera mode without a camera has the native Overlay fallback."
            );
            UndoRedo(result, State(go));
            if (renderMode == "ScreenSpaceOverlay")
                Journal("canvas_overlay", request, result);
        }

        [UnityTest]
        public IEnumerator OverlayCanvasDefaultsAndInitialOutputMatchLegacyWithOneUndoRedo()
        {
            return ScreenSpaceCanvasDefaultsAndInitialOutputMatchLegacyWithOneUndoRedo(RenderMode.ScreenSpaceOverlay);
        }

        [UnityTest]
        public IEnumerator CameraCanvasDefaultsAndInitialOutputMatchLegacyWithOneUndoRedo()
        {
            return ScreenSpaceCanvasDefaultsAndInitialOutputMatchLegacyWithOneUndoRedo(RenderMode.ScreenSpaceCamera);
        }

        private IEnumerator ScreenSpaceCanvasDefaultsAndInitialOutputMatchLegacyWithOneUndoRedo(RenderMode requestedMode)
        {
            int updateOrdinal = 0;
            int preWillRenderOrdinal = 0;
            void ObserveUpdate() => updateOrdinal++;
            void ObservePreWillRender() => preWillRenderOrdinal++;
            int Observe(string phase, GameObject target, JObject state, bool? equal = null)
            {
                WriteUndoDiscriminator("screen_space_canvas_acceptance", phase, target, state);
                int renderedFrame = Time.renderedFrameCount;
                TestContext.WriteLine(
                    "UI17_CANVAS_LIFECYCLE_ACCEPTANCE:"
                        + new JObject
                        {
                            ["phase"] = phase,
                            ["requested_mode"] = requestedMode.ToString(),
                            ["update_ordinal"] = updateOrdinal,
                            ["pre_will_render_ordinal"] = preWillRenderOrdinal,
                            ["frame"] = Time.frameCount,
                            ["rendered_frame"] = renderedFrame,
                            ["yield_count"] = phase == "after_one_yield" ? 1 : 0,
                            ["equal_to_before_undo"] = equal.HasValue ? new JValue(equal.Value) : JValue.CreateNull(),
                        }.ToString(Newtonsoft.Json.Formatting.None)
                );
                return renderedFrame;
            }
            try
            {
                EditorApplication.update += ObserveUpdate;
                Canvas.preWillRenderCanvases += ObservePreWillRender;
                string renderMode = requestedMode.ToString();
                var control = Legacy("canvas", "CanvasControl", requestedMode);
                var request = Request("canvas", "CanvasControl");
                request["render_mode"] = renderMode;
                var result = Send(request);
                var go = Created(result);
                SameInitialResponse(result, control);
                Assert.That(JToken.DeepEquals(State(go), State(Created(control))), Is.True);
                var canvas = go.GetComponent<Canvas>();
                Assert.That(StoredCanvasMode(canvas), Is.EqualTo(requestedMode));
                Assert.That(canvas.worldCamera == null, Is.True, "CLI construction does not inject a camera.");
                Assert.That(canvas.renderMode, Is.EqualTo(Created(control).GetComponent<Canvas>().renderMode));
                Assert.That(
                    canvas.renderMode,
                    Is.EqualTo(requestedMode == RenderMode.ScreenSpaceCamera ? RenderMode.ScreenSpaceOverlay : requestedMode),
                    "A stored ScreenSpaceCamera mode without a camera has the native Overlay fallback."
                );
                int id = result["data"].Value<int>("instanceID");
                var expected = State(go);
                Observe("before_undo", go, expected);
                Undo.PerformUndo();
                Assert.That(GameObjectLookup.FindById(id), Is.Null, "One Undo removes the whole new Canvas root.");
                Assert.That(go == null, Is.True, "Undo invalidates the previously held Canvas root.");
                Undo.PerformRedo();
                var restored = GameObjectLookup.FindById(id);
                Assert.That(restored, Is.Not.Null);
                Assert.That(restored.GetInstanceIDCompat(), Is.EqualTo(id));
                Assert.That(restored.scene, Is.EqualTo(owned));
                var immediate = State(restored);
                int immediateRenderedFrame = Observe("immediate_after_redo", restored, immediate, JToken.DeepEquals(immediate, expected));
                // Retain immediate object/component/reference/settings checks without altering either full snapshot.
                Assert.That(restored.name, Is.EqualTo(expected.Value<string>("name")));
                Assert.That(JToken.DeepEquals(immediate["types"], expected["types"]), Is.True);
                Assert.That(JToken.DeepEquals(immediate["children"], expected["children"]), Is.True);
                Assert.That(JToken.DeepEquals(immediate["UnityEngine.UI.CanvasScaler"], expected["UnityEngine.UI.CanvasScaler"]), Is.True);
                var restoredCanvas = restored.GetComponent<Canvas>();
                Assert.That(restoredCanvas, Is.Not.Null);
                Assert.That(StoredCanvasMode(restoredCanvas), Is.EqualTo(requestedMode));
                Assert.That(restoredCanvas.worldCamera == null, Is.True, "Redo does not inject a camera reference.");
                Assert.That(restoredCanvas.renderMode, Is.EqualTo((RenderMode)Enum.Parse(typeof(RenderMode), expected.Value<string>("canvas_mode"))));
                yield return null;
                var laterRoot = GameObjectLookup.FindById(id);
                Assert.That(laterRoot, Is.Not.Null);
                Assert.That(laterRoot.GetInstanceIDCompat(), Is.EqualTo(id));
                Assert.That(laterRoot.scene, Is.EqualTo(owned));
                var later = State(laterRoot);
                int laterRenderedFrame = Observe("after_one_yield", laterRoot, later, JToken.DeepEquals(later, expected));
                Assert.That(
                    laterRenderedFrame,
                    Is.GreaterThan(immediateRenderedFrame),
                    "No rendered-frame advancement after the single natural yield: Canvas lifecycle coverage is unavailable."
                );
                Assert.That(
                    JToken.DeepEquals(later, expected),
                    Is.True,
                    "After the observed native render transition, Redo restores the complete original Canvas state."
                );
                if (requestedMode == RenderMode.ScreenSpaceOverlay)
                    Journal("canvas_overlay", request, result);
            }
            finally
            {
                EditorApplication.update -= ObserveUpdate;
                Canvas.preWillRenderCanvases -= ObservePreWillRender;
            }
        }

        [Test]
        public void ImageDefaultsAndWorldPreservingParentingMatchLegacy()
        {
            var control = Legacy("image", "Duplicate");
            var request = Request("image");
            var result = Send(request);
            var go = Created(result);
            SameInitialResponse(result, control);
            Assert.That(JToken.DeepEquals(State(go), State(Created(control))), Is.True);
            Assert.That(go.transform.parent, Is.SameAs(root.transform));
            AssertSpriteReference(go, null);
            UndoRedo(result, State(go));
            AssertSpriteReference(Created(result), null);
            Assert.That(Created(control), Is.Not.Null, "An equal-name existing object is not compensated/deleted.");
        }

        [TestCase(false)]
        [TestCase(true)]
        public void TextAnchoredPositionMatchesDocumentedContractAndLegacyControl(bool explicitPosition)
        {
            UseFont();
            Vector2 position = explicitPosition ? new Vector2(12.5f, -7) : Vector2.zero;
            var control = Legacy("text", "Text", text: "Launch", position: position);
            var request = Request("text", "Text");
            if (explicitPosition)
                request["position"] = V2(position);
            var result = Send(request);
            var go = Created(result);
            SameInitialResponse(result, control);
            Assert.That(JToken.DeepEquals(State(go), State(Created(control))), Is.True);
            Assert.That(((RectTransform)go.transform).anchoredPosition, Is.EqualTo(position));
            UndoRedo(result, State(go));
            if (explicitPosition)
                Journal("text_position", request, result);
        }

        [Test]
        public void ButtonLabelDefaultsAndReferencesMatchLegacyAndRedo()
        {
            UseFont();
            var control = Legacy("button", "Duplicate");
            var request = Request("button");
            var result = Send(request);
            var go = Created(result);
            SameInitialResponse(result, control);
            Assert.That(JToken.DeepEquals(State(go), State(Created(control))), Is.True);
            Assert.That(go.transform.childCount, Is.EqualTo(1));
            Assert.That(go.transform.GetChild(0).name, Is.EqualTo("Duplicate_Label"));
            AssertSpriteReference(go, null);
            UndoRedo(result, State(go));
            AssertSpriteReference(Created(result), null);
            Journal("button", request, result);
        }

        private string SpriteAsset(int count)
        {
            string path = folder + "/Sprites.asset";
            var texture = new Texture2D(8, 8);
            AssetDatabase.CreateAsset(texture, path);
            for (int index = 0; index < count; index++)
            {
                var sprite = Sprite.Create(texture, new Rect(0, 0, 8, 8), new Vector2(.5f, .5f));
                sprite.name = "Part" + index;
                AssetDatabase.AddObjectToAsset(sprite, texture);
            }
            AssetDatabase.SaveAssetIfDirty(texture);
            return path;
        }

        [Test]
        public void ExactSpriteAndInitialOutputMatchLegacyAndUndo()
        {
            string path = SpriteAsset(1);
            var sprite = AssetDatabase.LoadAllAssetsAtPath(path).OfType<Sprite>().Single();
            var control = Legacy("image", "Image", sprite: sprite);
            var request = Request("image", "Image");
            request["sprite"] = path;
            var result = Send(request);
            var go = Created(result);
            SameInitialResponse(result, control);
            Assert.That(JToken.DeepEquals(State(go), State(Created(control))), Is.True);
            AssertSpriteReference(go, sprite);
            UndoRedo(result, State(go));
            AssertSpriteReference(Created(result), sprite);
            Journal("image_sprite", request, result);
        }

        [TestCase("missing")]
        [TestCase("ambiguous")]
        [TestCase("not_canvas")]
        [TestCase("asset")]
        public void InvalidParentLeavesNoHierarchyOrUndoEntry(string scenario)
        {
            var request = Request("image");
            if (scenario == "missing")
                request["parent"] = "UI17MissingParent";
            else if (scenario == "ambiguous")
            {
                var first = new GameObject("SameParent", typeof(RectTransform));
                first.transform.SetParent(root.transform, false);
                var second = new GameObject("SameParent", typeof(RectTransform));
                second.transform.SetParent(root.transform, false);
                request["parent"] = "SameParent";
            }
            else if (scenario == "not_canvas")
                request["parent"] = new GameObject("NotCanvas", typeof(RectTransform)).GetInstanceIDCompat();
            else
            {
                var source = new GameObject("OwnedPrefab", typeof(RectTransform), typeof(Canvas));
                string path = folder + "/Parent.prefab";
                PrefabUtility.SaveAsPrefabAsset(source, path);
                UnityEngine.Object.DestroyImmediate(source);
                request["parent"] = AssetDatabase.LoadAssetAtPath<GameObject>(path).GetInstanceIDCompat();
            }
            AssertRefusalBeforeUndo(request);
        }

        [TestCase("")]
        [TestCase("path/name")]
        [TestCase("bad\nname")]
        public void UnsafeNameRefusesBeforeHierarchyChanges(string name)
        {
            AssertRefusalBeforeUndo(Request("image", name));
        }

        [TestCase("unknown_shape")]
        [TestCase("extra_field")]
        [TestCase("wrong_kind_field")]
        [TestCase("three_coordinates")]
        [TestCase("nonfinite")]
        [TestCase("float_overflow")]
        public void ClosedShapeAndPositionValidationRefuseBeforeDependenciesOrAllocation(string scenario)
        {
            var request = Request("text");
            if (scenario == "unknown_shape")
                request["element_type"] = "panel";
            if (scenario == "extra_field")
                request["commands"] = new JArray();
            if (scenario == "wrong_kind_field")
                request["sprite"] = "Assets/Anything.asset";
            if (scenario == "three_coordinates")
                request["position"] = new JArray(1, 2, 3);
            if (scenario == "nonfinite")
                request["position"] = new JArray(double.NaN, 0);
            if (scenario == "float_overflow")
                request["position"] = new JArray(1e100, 0);
            AssertRefusalBeforeUndo(request);
        }

        [TestCase("text")]
        [TestCase("button")]
        public void MissingDefaultFontLeavesNoRootOrLabel(string kind)
        {
            InstallSettings();
            settingsType.GetField("m_defaultFontAsset", BindingFlags.NonPublic | BindingFlags.Instance).SetValue(settings, null);
            AssertRefusalBeforeUndo(Request(kind));
        }

        [TestCase(false)]
        [TestCase(true)]
        public void MissingOrAmbiguousSpriteLeavesNoResidual(bool ambiguous)
        {
            var request = Request("image");
            request["sprite"] = ambiguous ? SpriteAsset(2) : folder + "/Missing.asset";
            AssertRefusalBeforeUndo(request);
        }

        private void AssertRefusalBeforeUndo(JObject request)
        {
            var before = RootIds(owned);
            string state = State(root).ToString(Newtonsoft.Json.Formatting.None);
            int group = Undo.GetCurrentGroup();
            bool dirty = owned.isDirty;
            var response = Send(request);
            Assert.That(response.Value<bool>("success"), Is.False, response.ToString());
            Assert.That(RootIds(owned), Is.EqualTo(before));
            Assert.That(State(root).ToString(Newtonsoft.Json.Formatting.None), Is.EqualTo(state));
            Assert.That(Undo.GetCurrentGroup(), Is.EqualTo(group));
            Assert.That(owned.isDirty, Is.EqualTo(dirty));
        }

        [Test]
        public void CanvasConstructionFailureBeforeUndoRegistrationLeavesNoOwnedRootOrUndoEntry()
        {
            var before = RootIds(owned);
            var selected = Selection.activeObject;
            Vector3 markerBefore = root.transform.localPosition;
            Undo.RecordObject(root.transform, "Previous owned edit before Canvas failure");
            ((RectTransform)root.transform).anchoredPosition3D += Vector3.one;
            Undo.FlushUndoRecordObjects();
            Undo.IncrementCurrentGroup();
            var markerState = State(root);
            var helper = typeof(ManageUGUI).Assembly.GetType("MCPForUnity.Editor.Tools.UguiCliCreation", true);
            var builder = helper.GetMethod("CreateCanvas", BindingFlags.NonPublic | BindingFlags.Static);
            Assert.That(builder, Is.Not.Null);
            var transaction = typeof(ManageUGUI).GetMethod("Mutate", BindingFlags.NonPublic | BindingFlags.Static);
            // The existing reflection seam supplies a missing final component only after
            // the private builder has allocated its root, Canvas and scaler while inactive.
            Func<object> failing = () =>
                builder.Invoke(null, new object[] { "UI17OwnedCanvasFailure", RenderMode.ScreenSpaceOverlay, Ui("CanvasScaler"), null });
            var error = Assert.Throws<TargetInvocationException>(() => transaction.Invoke(null, new object[] { "Owned Canvas allocation failure", failing }));
            Assert.That(error.InnerException, Is.TypeOf<TargetInvocationException>());
            var constructionError = ((TargetInvocationException)error.InnerException).InnerException;
            Assert.That(constructionError, Is.TypeOf<InvalidOperationException>());
            Assert.That(constructionError.Message, Is.EqualTo("Required CLI GraphicRaycaster could not be added."));
            Assert.That(RootIds(owned), Is.EqualTo(before), "An unregistered allocated Canvas root is cleaned up by its exact owner.");
            Assert.That(Selection.activeObject, Is.EqualTo(selected));
            Assert.That(JToken.DeepEquals(State(root), markerState), Is.True);
            Undo.PerformUndo();
            Assert.That(root.transform.localPosition, Is.EqualTo(markerBefore), "Construction failure adds no committed Undo entry before the prior edit.");
        }

        [Test]
        public void BuilderFailureRollsBackActualRootAndLabelInSharedTransaction()
        {
            UseFont();
            var before = RootIds(owned);
            int beforeChildren = root.transform.childCount;
            Vector3 markerBefore = root.transform.localPosition;
            Undo.RecordObject(root.transform, "Previous owned edit");
            ((RectTransform)root.transform).anchoredPosition3D += Vector3.one;
            Undo.FlushUndoRecordObjects();
            Undo.IncrementCurrentGroup();
            var helper = typeof(ManageUGUI).Assembly.GetType("MCPForUnity.Editor.Tools.UguiCliCreation", true);
            var build =
                (Func<object>)helper.GetMethod("Prepare", BindingFlags.NonPublic | BindingFlags.Static).Invoke(null, new object[] { Request("button") });
            var transaction = typeof(ManageUGUI).GetMethod("Mutate", BindingFlags.NonPublic | BindingFlags.Static);
            int builtRoot = 0,
                builtLabel = 0;
            Func<object> failing = () =>
            {
                var response = JObject.FromObject(build());
                var created = Created(response);
                builtRoot = created.GetInstanceIDCompat();
                builtLabel = created.transform.GetChild(0).gameObject.GetInstanceIDCompat();
                Assert.That(root.transform.childCount, Is.EqualTo(beforeChildren + 1), "The real builder must allocate before the controlled failure.");
                throw new InvalidOperationException("Owned test failure after root and label construction.");
            };
            var error = Assert.Throws<TargetInvocationException>(() => transaction.Invoke(null, new object[] { "Owned CLI rollback control", failing }));
            Assert.That(error.InnerException, Is.TypeOf<InvalidOperationException>());
            Assert.That(builtRoot, Is.Not.Zero);
            Assert.That(builtLabel, Is.Not.Zero);
            Assert.That(GameObjectLookup.FindById(builtRoot), Is.Null);
            Assert.That(GameObjectLookup.FindById(builtLabel), Is.Null);
            Assert.That(root.transform.childCount, Is.EqualTo(beforeChildren));
            Assert.That(RootIds(owned), Is.EqualTo(before));
            Undo.PerformUndo();
            Assert.That(root.transform.localPosition, Is.EqualTo(markerBefore), "A failed transaction adds no committed Undo entry before the prior edit.");
        }

        private static Type TmpType() => UnityTypeResolver.ResolveComponent("TMPro.TextMeshProUGUI");

        private void InstallSettings()
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

        private void UseFont()
        {
            // Source reuse: UI08's accepted fresh-owned TMP fixture lease and factory.
            // A later QA case must supply its own compatible font; no OS/private font is copied.
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
            string path = Path.GetFullPath(Path.Combine(Application.dataPath, "..", "..", "font-input", "UI17.ttf"));
            Assert.That(File.Exists(path), Is.True, "Supply an owned compatible Latin font at case/font-input/UI17.ttf; missing input is setup failure.");
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
            font.name = "UI17OwnedFont" + Guid.NewGuid().ToString("N");
            var add = fontType.GetMethod("TryAddCharacters", new[] { typeof(string), typeof(string).MakeByRefType(), typeof(bool) });
            var args = new object[] { " ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_-", null, false };
            Assert.That((bool)add.Invoke(font, args), Is.True, "The supplied owned font must cover the fixture text.");
            fontType.GetProperty("atlasPopulationMode").SetValue(font, Enum.ToObject(fontType.GetProperty("atlasPopulationMode").PropertyType, 0));
            settingsType.GetField("m_defaultFontAsset", BindingFlags.NonPublic | BindingFlags.Instance).SetValue(settings, font);
        }

        private void CloseFontLease()
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
                                Assert.That(ReferenceEquals(current, font), Is.True, "Never remove a foreign font lookup.");
                        }
                        resourceManager.GetMethod("RemoveFontAsset", new[] { fontType }).Invoke(null, new object[] { font });
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
                            if ((bool)weak.GetType().GetMethod("TryGetTarget").Invoke(weak, value) && ReferenceEquals(value[0], font))
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
                throw new AggregateException("Owned CLI TMP lease cleanup failed.", errors);
        }
    }
}
