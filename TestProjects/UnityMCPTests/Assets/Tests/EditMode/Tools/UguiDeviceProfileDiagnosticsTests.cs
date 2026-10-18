using System;
using System.Collections.Generic;
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
    [Parallelizable(ParallelScope.None)]
    public class UguiDeviceProfileDiagnosticsTests
    {
        private const string EmptySceneTemplateGuid = "d8f5ae9136f356e190abdc5c9be58fb6";
        private Scene previous,
            owned;
        private GameObject root,
            target;
        private Canvas canvas;
        private Component scaler;
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
            ownsFolder = ownsScene = ownsUndoGroup = ownsSelection = false;
            previous = owned = default;
            preservedScenes = null;
            ownedFolderGuid = null;
            try
            {
                if (Ui("CanvasScaler") == null || Ui("Image") == null)
                    Assert.Ignore("Optional uGUI is unavailable; this is not a passing native device-profile check.");
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
                string prefix = "UI14_" + Guid.NewGuid().ToString("N");
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
                root = new GameObject("Canvas", typeof(RectTransform), typeof(Canvas));
                canvas = root.GetComponent<Canvas>();
                canvas.renderMode = RenderMode.ScreenSpaceOverlay;
                scaler = root.AddComponent(Ui("CanvasScaler"));
                target = new GameObject("Target", typeof(RectTransform));
                target.transform.SetParent(root.transform, false);
                var rect = (RectTransform)target.transform;
                rect.anchorMin = rect.anchorMax = rect.pivot = new Vector2(0.5f, 0.5f);
                rect.sizeDelta = new Vector2(48, 48);
                target.AddComponent(Ui("Image"));
                Canvas.ForceUpdateCanvases();
            }
            catch (Exception setupError)
            {
                try
                {
                    TearDown();
                }
                catch (Exception cleanupError)
                {
                    throw new AggregateException("Owned device-profile fixture setup and cleanup failed.", setupError, cleanupError);
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
                Scene retained = current.Single(scene => scene == original.Scene);
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

        [Test]
        public void PhysicalEstimateDistinguishesDpiWithoutChangingPixelGeometry()
        {
            // Given: caller asks whether a 48px raycast rectangle meets 7mm.
            var request = Request(Virtual(800, 600, 160), Virtual(800, 600, 320));
            request["minimum_touch_target_mm"] = 7;
            Scene beforeScene = SceneManager.GetActiveScene();
            string before = State();
            // When
            var data = Call(request);
            TestContext.WriteLine("UI14_NATIVE_REQUEST:" + request.ToString(Newtonsoft.Json.Formatting.None));
            TestContext.WriteLine("UI14_NATIVE_REPORT:" + data.ToString(Newtonsoft.Json.Formatting.None));
            // Then
            var a = Node(data, 0);
            var b = Node(data, 1);
            Assert.That(a["pixel_bounds"].ToString(), Is.EqualTo(b["pixel_bounds"].ToString()));
            Assert.That(a["width_mm"].Value<double>(), Is.EqualTo(7.62).Within(0.00001));
            Assert.That(b["width_mm"].Value<double>(), Is.EqualTo(3.81).Within(0.00001));
            Assert.That(a["touch_target"].Value<string>(), Is.EqualTo("meets_caller_threshold"));
            Assert.That(b["touch_target"].Value<string>(), Is.EqualTo("below_caller_threshold"));
            Assert.That(SceneManager.GetActiveScene(), Is.EqualTo(beforeScene));
            Assert.That(State(), Is.EqualTo(before));
        }

        [Test]
        public void PhysicalSizeAndSpritePreferredLayoutMatchOwnedNativeCanvas()
        {
            // Given: actual installed CanvasScaler drives an owned source before the measured call.
            if (Screen.width < 64 || Screen.width > 8192 || Screen.height < 64 || Screen.height > 8192)
                Assert.Ignore("Current Editor output is outside virtual profile bounds.");
            SetEnum(scaler, "uiScaleMode", "ConstantPhysicalSize");
            SetEnum(scaler, "physicalUnit", "Points");
            Set(scaler, "referencePixelsPerUnit", 123f);
            Set(scaler, "defaultSpriteDPI", 96f);
            Set(scaler, "fallbackScreenDPI", 160f);
            var texture = new Texture2D(100, 60);
            var sprite = Sprite.Create(texture, new Rect(0, 0, 100, 60), Vector2.zero, 100);
            try
            {
                Set(target.GetComponent(Ui("Image")), "sprite", sprite);
                var fitter = target.AddComponent(Ui("ContentSizeFitter"));
                SetEnum(fitter, "horizontalFit", "PreferredSize");
                SetEnum(fitter, "verticalFit", "PreferredSize");
                // Fixture setup may run source layout. The diagnostic below must not.
                scaler.GetType().GetMethod("Handle", BindingFlags.NonPublic | BindingFlags.Instance).Invoke(scaler, null);
                Rebuild((RectTransform)root.transform);
                Canvas.ForceUpdateCanvases();
                float dpi = Screen.dpi > 0 ? Screen.dpi : 160;
                float expectedWidth = ((RectTransform)target.transform).rect.width * canvas.scaleFactor;
                float expectedPpu = canvas.referencePixelsPerUnit;
                Scene beforeScene = SceneManager.GetActiveScene();
                string before = State();
                // When
                var data = Call(Request(Virtual(Screen.width, Screen.height, dpi)));
                // Then: independent installed native scaler and Image preferred-size results.
                var report = data["profiles"][0];
                Assert.That(report["scale_factor"].Value<float>(), Is.EqualTo(canvas.scaleFactor).Within(0.00001));
                Assert.That(report["reference_pixels_per_unit"].Value<float>(), Is.EqualTo(expectedPpu).Within(0.00001));
                Assert.That(Node(data, 0)["pixel_bounds"][2].Value<float>(), Is.EqualTo(expectedWidth).Within(0.01));
                Assert.That(SceneManager.GetActiveScene(), Is.EqualTo(beforeScene));
                Assert.That(State(), Is.EqualTo(before));
            }
            finally
            {
                UnityEngine.Object.DestroyImmediate(sprite);
                UnityEngine.Object.DestroyImmediate(texture);
            }
        }

        [Test]
        public void FractionalProfileRetainsNativeFloatDomainEvidenceForSdkReplay()
        {
            // Given: fractional request values whose wire decimals need not equal expanded Single doubles.
            var rect = (RectTransform)target.transform;
            rect.anchorMin = rect.anchorMax = rect.pivot = Vector2.zero;
            rect.anchoredPosition = new Vector2(0.1f, 0.1f);
            rect.sizeDelta = new Vector2(0.2f, 0.2f);
            var profile = Virtual(64, 64, 160.125);
            profile["safe_area"] = new JArray(0.1, 0.1, 0.3, 0.3);
            var request = Request(profile);
            request["minimum_touch_target_mm"] = 0.1;
            Scene beforeScene = SceneManager.GetActiveScene();
            string before = State();
            // When
            var data = Call(request);
            // Then: both SDK modes must later decode this actual, unmodified native report.
            Assert.That(data["status"].Value<string>(), Is.EqualTo("complete"));
            Assert.That(SceneManager.GetActiveScene(), Is.EqualTo(beforeScene));
            Assert.That(State(), Is.EqualTo(before));
            TestContext.WriteLine("UI14_NATIVE_FRACTIONAL_REQUEST:" + request.ToString(Newtonsoft.Json.Formatting.None));
            TestContext.WriteLine("UI14_NATIVE_FRACTIONAL_REPORT:" + data.ToString(Newtonsoft.Json.Formatting.None));
        }

        [TestCase("missing")]
        [TestCase("early_unique")]
        [TestCase("late_unique")]
        [TestCase("late_path")]
        public void StringSelectorExhaustionNeverPublishesAnUnprovenTarget(string selectorCase)
        {
            // Given: many owned objects, and a selector requiring a complete uniqueness scan.
            string unique = "UI14_selector_" + Guid.NewGuid().ToString("N");
            root.name = unique + "_root";
            target.name = unique;
            for (int i = 0; i < 256; i++)
            {
                var filler = new GameObject("filler_" + i);
                filler.transform.SetParent(root.transform, false);
            }
            if (selectorCase == "late_unique" || selectorCase == "late_path")
                target.transform.SetAsLastSibling();
            var request = Request(Virtual(800, 600, 160));
            request["target"] =
                selectorCase == "missing" ? unique + "_absent"
                : selectorCase == "late_path" ? root.name + "/" + unique
                : unique;
            request["max_evaluations"] = 48;
            Scene beforeScene = SceneManager.GetActiveScene();
            string before = State();
            // When
            var data = Call(request);
            // Then: neither a missing nor an early/late unique match bypasses the shared bound.
            Assert.That(data["status"].Value<string>(), Is.EqualTo("partial"));
            Assert.That(data["budget"]["evaluations"].Value<int>(), Is.LessThanOrEqualTo(48));
            Assert.That(data["budget"]["reasons"].Values<string>(), Does.Contain("max_evaluations"));
            Assert.That(((JArray)data["profiles"]).Count, Is.Zero);
            Assert.That(data["projection"].Type, Is.EqualTo(JTokenType.Null));
            Assert.That(SceneManager.GetActiveScene(), Is.EqualTo(beforeScene));
            Assert.That(State(), Is.EqualTo(before));
        }

        [Test]
        public void ExplicitCameraStringSelectorSharesTheWholeRequestBudget()
        {
            // Given: a valid target ID followed by a late unique camera name in a large owned hierarchy.
            string name = "UI14_camera_" + Guid.NewGuid().ToString("N");
            for (int i = 0; i < 256; i++)
            {
                var filler = new GameObject("camera_filler_" + i);
                filler.transform.SetParent(root.transform, false);
            }
            var cameraObject = new GameObject(name, typeof(Camera));
            canvas.renderMode = RenderMode.ScreenSpaceCamera;
            canvas.worldCamera = cameraObject.GetComponent<Camera>();
            Canvas.ForceUpdateCanvases();
            var request = Request(Virtual(800, 600, 160));
            request["camera"] = name;
            request["max_evaluations"] = 64;
            Scene beforeScene = SceneManager.GetActiveScene();
            string before = State();
            // When
            var data = Call(request);
            // Then
            Assert.That(data["status"].Value<string>(), Is.EqualTo("partial"));
            Assert.That(data["budget"]["evaluations"].Value<int>(), Is.LessThanOrEqualTo(64));
            Assert.That(((JArray)data["profiles"]).Count, Is.Zero);
            Assert.That(data["projection"].Type, Is.EqualTo(JTokenType.Null));
            Assert.That(SceneManager.GetActiveScene(), Is.EqualTo(beforeScene));
            Assert.That(State(), Is.EqualTo(before));
        }

        [Test]
        public void InsetSafeAreaChangesGeometricCandidateWithoutMovingSource()
        {
            // Given: the centered 48px target intersects a safe-area edge in output coordinates.
            var full = Virtual(800, 600, 160);
            var inset = Virtual(800, 600, 160);
            inset["safe_area"] = new JArray(400, 0, 400, 600);
            Scene beforeScene = SceneManager.GetActiveScene();
            string before = State();
            // When
            var data = Call(Request(full, inset));
            // Then
            Assert.That(Node(data, 0)["safe_area_relation"].Value<string>(), Is.EqualTo("inside_geometric_safe_area"));
            Assert.That(Node(data, 1)["safe_area_relation"].Value<string>(), Is.EqualTo("possible_unsafe_region"));
            Assert.That(Node(data, 1)["unsafe_region_area_px2"].Value<double>(), Is.EqualTo(1152).Within(0.01));
            Assert.That(SceneManager.GetActiveScene(), Is.EqualTo(beforeScene));
            Assert.That(State(), Is.EqualTo(before));
        }

        [TestCase(1e-300)]
        [TestCase(1e308)]
        public void InvalidDpiRejectsBeforeMissingTargetLookup(double dpi)
        {
            // Given: malformed numeric input and a deliberately nonexistent target.
            var p = Virtual(800, 600, 160);
            p["dpi"] = dpi;
            var request = Request(p);
            request["target"] = "missing_UI14_target";
            // When
            var response = JObject.FromObject(ManageUGUI.HandleCommand(request));
            // Then
            Assert.That(response["success"].Value<bool>(), Is.False);
            Assert.That(response["error"].Value<string>(), Is.EqualTo("invalid_device_profile_request"));
        }

        [Test]
        public void EightProfilesShareOneWorkBudgetAndNinthRejects()
        {
            // Given: a bounded whole-request budget that cannot process eight profiles fully.
            var request = Request(Enumerable.Range(0, 8).Select(_ => Virtual(800, 600, 160)).ToArray());
            request["max_evaluations"] = 25;
            Scene beforeScene = SceneManager.GetActiveScene();
            string before = State();
            // When
            var data = Call(request);
            // Then
            Assert.That(data["status"].Value<string>(), Is.EqualTo("partial"));
            Assert.That(data["budget"]["evaluations"].Value<int>(), Is.LessThanOrEqualTo(25));
            Assert.That(data["budget"]["reasons"].Values<string>(), Does.Contain("max_evaluations"));
            Assert.That(SceneManager.GetActiveScene(), Is.EqualTo(beforeScene));
            Assert.That(State(), Is.EqualTo(before));
        }

        [Test]
        public void NinthProfileRejectsBeforeMissingTargetLookup()
        {
            // Given
            var request = Request(Enumerable.Range(0, 9).Select(_ => Virtual(800, 600, 160)).ToArray());
            request["target"] = "missing_UI14_target";
            // When
            var response = JObject.FromObject(ManageUGUI.HandleCommand(request));
            // Then
            Assert.That(response["error"].Value<string>(), Is.EqualTo("invalid_device_profile_request"));
        }

        [Test]
        public void EightProfilesAreRetainedWhenTheSharedBudgetSuffices()
        {
            // Given
            var request = Request(Enumerable.Range(0, 8).Select(_ => Virtual(800, 600, 160)).ToArray());
            Scene beforeScene = SceneManager.GetActiveScene();
            string before = State();
            // When
            var data = Call(request);
            // Then
            Assert.That(((JArray)data["profiles"]).Count, Is.EqualTo(8));
            Assert.That(data["status"].Value<string>(), Is.EqualTo("complete"));
            Assert.That(SceneManager.GetActiveScene(), Is.EqualTo(beforeScene));
            Assert.That(State(), Is.EqualTo(before));
        }

        [Test]
        public void TinyCharacterBudgetReturnsPartialControlReceiptWithoutNodes()
        {
            // Given
            var request = Request(Virtual(800, 600, 160));
            request["max_characters"] = 1;
            // When
            var data = Call(request);
            // Then
            Assert.That(data["status"].Value<string>(), Is.EqualTo("partial"));
            Assert.That(data["budget"]["characters"].Value<int>(), Is.Zero);
            Assert.That(((JArray)data["profiles"][0]["nodes"]).Count, Is.Zero);
        }

        [TestCase(0f)]
        [TestCase(-1f)]
        [TestCase(float.PositiveInfinity)]
        public void CapturedUnknownDpiDoesNotAcquireMeasuredDpiFromScaler(float dpi)
        {
            // Given: captured values enter the same private normalization boundary as Screen.
            Type helper = typeof(ManageUGUI).Assembly.GetType("MCPForUnity.Editor.Tools.UguiDeviceProfileDiagnostics");
            Type profileType = helper.GetNestedType("Profile", BindingFlags.NonPublic);
            object profile = Activator.CreateInstance(profileType, true);
            SetField(profile, "Source", "observed");
            SetField(profile, "Width", 800);
            SetField(profile, "Height", 600);
            SetField(profile, "Dpi", dpi);
            SetField(profile, "Safe", new Rect(0, 0, 800, 600));
            // When
            helper
                .GetMethod("NormalizeObservation", BindingFlags.NonPublic | BindingFlags.Static)
                .Invoke(null, new[] { profile, (object)ScreenOrientation.LandscapeLeft });
            // Then: finite valid geometry survives but physical measurement is unavailable.
            Assert.That(profileType.GetField("Available", BindingFlags.NonPublic | BindingFlags.Instance).GetValue(profile), Is.True);
            Assert.That(profileType.GetField("Dpi", BindingFlags.NonPublic | BindingFlags.Instance).GetValue(profile), Is.EqualTo(0f));
        }

        [Test]
        public void CapturedEmptySafeAreaIsUnavailableWithoutFullscreenSubstitution()
        {
            // Given: no Screen mutation is needed to exercise the captured-value boundary.
            Type helper = typeof(ManageUGUI).Assembly.GetType("MCPForUnity.Editor.Tools.UguiDeviceProfileDiagnostics");
            Type profileType = helper.GetNestedType("Profile", BindingFlags.NonPublic);
            object profile = Activator.CreateInstance(profileType, true);
            SetField(profile, "Source", "observed");
            SetField(profile, "Width", 800);
            SetField(profile, "Height", 600);
            SetField(profile, "Dpi", 160f);
            SetField(profile, "Safe", new Rect(0, 0, 0, 600));
            // When
            helper
                .GetMethod("NormalizeObservation", BindingFlags.NonPublic | BindingFlags.Static)
                .Invoke(null, new[] { profile, (object)ScreenOrientation.LandscapeLeft });
            // Then
            Assert.That(profileType.GetField("Available", BindingFlags.NonPublic | BindingFlags.Instance).GetValue(profile), Is.False);
            Assert.That(profileType.GetField("Safe", BindingFlags.NonPublic | BindingFlags.Instance).GetValue(profile), Is.EqualTo(new Rect(0, 0, 0, 600)));
        }

        [Test]
        public void RotatedTargetRetainsAabbQualification()
        {
            // Given
            target.transform.localRotation = Quaternion.Euler(0, 0, 45);
            Scene beforeScene = SceneManager.GetActiveScene();
            string before = State();
            // When
            var data = Call(Request(Virtual(800, 600, 160)));
            // Then
            Assert.That(Node(data, 0)["pixel_bounds"][2].Value<double>(), Is.GreaterThan(48));
            Assert.That(data["policy"]["measurement"].Value<string>(), Is.EqualTo("dpi_geometry_estimate"));
            Assert.That(SceneManager.GetActiveScene(), Is.EqualTo(beforeScene));
            Assert.That(State(), Is.EqualTo(before));
        }

        [Test]
        public void RenderTextureRejectsObservedScreenEvenWithMatchingDimensions()
        {
            // Given: an actual owned output whose size happens to equal Screen.
            var cameraObject = new GameObject("Camera", typeof(Camera));
            var camera = cameraObject.GetComponent<Camera>();
            var rt = new RenderTexture(Math.Max(64, Screen.width), Math.Max(64, Screen.height), 0);
            try
            {
                rt.Create();
                camera.targetTexture = rt;
                canvas.renderMode = RenderMode.ScreenSpaceCamera;
                canvas.worldCamera = camera;
                Canvas.ForceUpdateCanvases();
                Scene beforeScene = SceneManager.GetActiveScene();
                string before = State();
                // When
                var data = Call(Request(new JObject { ["source"] = "observed" }));
                // Then
                Assert.That(data["profiles"][0]["status"].Value<string>(), Is.EqualTo("unavailable"));
                Assert.That(((JArray)data["profiles"][0]["nodes"]).Count, Is.Zero);
                Assert.That(SceneManager.GetActiveScene(), Is.EqualTo(beforeScene));
                Assert.That(State(), Is.EqualTo(before));
            }
            finally
            {
                camera.targetTexture = null;
                rt.Release();
                UnityEngine.Object.DestroyImmediate(rt);
            }
        }

        [Test]
        public void CameraSubviewportAssessesCurrentCornersOnceInOutputCoordinates()
        {
            // Given: current camera corner projection includes subviewport offset.
            if (Screen.width < 64 || Screen.width > 8192 || Screen.height < 64 || Screen.height > 8192)
                Assert.Ignore("Current Editor output is outside profile bounds.");
            var cameraObject = new GameObject("Camera", typeof(Camera));
            var camera = cameraObject.GetComponent<Camera>();
            camera.rect = new Rect(0.25f, 0.25f, 0.5f, 0.5f);
            canvas.renderMode = RenderMode.ScreenSpaceCamera;
            canvas.worldCamera = camera;
            canvas.scaleFactor = 2;
            Canvas.ForceUpdateCanvases();
            var p = Virtual(Screen.width, Screen.height, 160);
            p["safe_area"] = new JArray(Screen.width / 2f, 0, Screen.width / 2f, Screen.height);
            Scene beforeScene = SceneManager.GetActiveScene();
            string before = State();
            // When
            var data = Call(Request(p));
            // Then
            var projected = data["projection"]["nodes"].Single(n => n["instance_id"].Value<int>() == target.GetInstanceIDCompat());
            Assert.That(Node(data, 0)["raycast_rectangle_candidate"].Value<bool>(), Is.True);
            var actualBounds = Node(data, 0)["pixel_bounds"] as JArray;
            var projectedBounds = projected["clipped_target_pixel_bounds"] as JArray;
            Assert.That(actualBounds, Is.Not.Null);
            Assert.That(projectedBounds, Is.Not.Null);
            Assert.That(actualBounds.Count, Is.EqualTo(4));
            Assert.That(projectedBounds.Count, Is.EqualTo(4));
            for (int i = 0; i < 4; i++)
                Assert.That(actualBounds[i].Value<float>(), Is.EqualTo(projectedBounds[i].Value<float>()), $"Pixel bounds component {i}");
            Assert.That(data["projection"]["evidence"]["profile"].Type, Is.EqualTo(JTokenType.Null));
            Assert.That(SceneManager.GetActiveScene(), Is.EqualTo(beforeScene));
            Assert.That(State(), Is.EqualTo(before));
        }

        [Test]
        public void CustomIgnorerGetterIsNeverCalled()
        {
#if MCP_UI14_UGUI_WITNESS
            // Given: the actual interface getter is armed only after component setup.
            var witness = target.AddComponent<Ui14ArmedIgnorer>();
            witness.Armed = true;
            Scene beforeScene = SceneManager.GetActiveScene();
            string before = State();
            // When
            var data = Call(Request(Virtual(800, 600, 160)));
            // Then: omission must withhold pass findings and execute zero user callbacks.
            Assert.That(witness.Calls, Is.Zero);
            Assert.That(data["profiles"][0]["reasons"].Values<string>(), Does.Contain("unsupported_layout_provider"));
            Assert.That(Node(data, 0)["touch_target"].Value<string>(), Is.EqualTo("unknown"));
            Assert.That(Node(data, 0)["safe_area_relation"].Value<string>(), Is.EqualTo("unknown"));
            Assert.That(SceneManager.GetActiveScene(), Is.EqualTo(beforeScene));
            Assert.That(State(), Is.EqualTo(before));
#else
            Assert.Ignore("Mandatory custom-interface witness was not compiled. Enable the owned native projection overlay; absence is not acceptance.");
#endif
        }

        private string State()
        {
            var screen = new JObject
            {
                ["width"] = Screen.width,
                ["height"] = Screen.height,
                ["dpi"] = Screen.dpi,
                ["safe"] = Screen.safeArea.ToString("R"),
                ["orientation"] = Screen.orientation.ToString(),
                ["selection"] = Selection.activeObject == null ? 0 : Selection.activeObject.GetInstanceIDCompat(),
                ["scene_dirty"] = owned.isDirty,
                ["scene_count"] = SceneManager.sceneCount,
            };
            var objects = new JArray();
            foreach (var component in root.GetComponentsInChildren<Component>(true).Where(c => c != null))
                objects.Add(
                    new JObject
                    {
                        ["id"] = component.GetInstanceIDCompat(),
                        ["dirty"] = EditorUtility.IsDirty(component),
                        ["serialized"] = EditorJsonUtility.ToJson(component),
                    }
                );
            screen["objects"] = objects;
            return screen.ToString();
        }

        private JObject Request(params JObject[] profiles) =>
            new JObject
            {
                ["action"] = "diagnose_device_profiles",
                ["target"] = target.GetInstanceIDCompat(),
                ["device_profiles"] = new JArray(profiles),
            };

        private static JObject Virtual(int width, int height, double dpi) =>
            new JObject
            {
                ["source"] = "virtual",
                ["width"] = width,
                ["height"] = height,
                ["dpi"] = dpi,
                ["safe_area"] = new JArray(0, 0, width, height),
                ["orientation"] = "landscape_left",
            };

        private static JObject Call(JObject request)
        {
            var response = JObject.FromObject(ManageUGUI.HandleCommand(request));
            Assert.That(response["success"].Value<bool>(), Is.True, response.ToString());
            return (JObject)response["data"];
        }

        private JObject Node(JObject data, int index) =>
            (JObject)data["profiles"][index]["nodes"].Single(n => n["instance_id"].Value<int>() == target.GetInstanceIDCompat());

        private static Type Ui(string name) => UnityTypeResolver.ResolveComponent("UnityEngine.UI." + name);

        private static void Set(Component component, string member, object value) => component.GetType().GetProperty(member).SetValue(component, value);

        private static void SetField(object value, string member, object field) =>
            value.GetType().GetField(member, BindingFlags.NonPublic | BindingFlags.Instance).SetValue(value, field);

        private static void SetEnum(Component component, string member, string value)
        {
            var property = component.GetType().GetProperty(member);
            property.SetValue(component, Enum.Parse(property.PropertyType, value));
        }

        private static void Rebuild(RectTransform rect) =>
            Ui("Image")
                .Assembly.GetType("UnityEngine.UI.LayoutRebuilder")
                .GetMethod("ForceRebuildLayoutImmediate", BindingFlags.Public | BindingFlags.Static)
                .Invoke(null, new object[] { rect });
    }

#if MCP_UI14_UGUI_WITNESS
    // Native-only owned projection adds the installed uGUI reference and this define.
    // No shipping production assembly setting or optional package installation is required.
    public sealed class Ui14ArmedIgnorer : MonoBehaviour, UnityEngine.UI.ILayoutIgnorer
    {
        [NonSerialized]
        public bool Armed;

        [NonSerialized]
        public int Calls;
        public bool ignoreLayout
        {
            get
            {
                if (Armed)
                {
                    Calls++;
                    throw new InvalidOperationException("UI14 custom getter executed");
                }
                return false;
            }
        }
    }
#endif
}
