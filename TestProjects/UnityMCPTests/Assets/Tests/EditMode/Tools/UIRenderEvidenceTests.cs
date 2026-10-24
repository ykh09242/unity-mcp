using System;
using System.Collections;
using System.Collections.Generic;
using System.IO;
using System.Reflection;
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
    // These tests require a real graphics-enabled Editor. No texture/capture completion
    // injection: assertions compare the saved native capture with independently decoded PNGs.
    public class UIRenderEvidenceTests
    {
        private const BindingFlags StaticPrivate = BindingFlags.Static | BindingFlags.NonPublic;
        private Fixture fixture;
        private bool ownsSlot;

        private sealed class Fixture : IDisposable
        {
            internal readonly GameObject Object;
            internal readonly UIDocument Document;
            internal readonly PanelSettings Panel;
            internal readonly string Folder = "Temp/UIRenderEvidence-" + Guid.NewGuid().ToString("N");
            internal string FullFolder => Path.GetFullPath(Path.Combine(Path.GetDirectoryName(Application.dataPath), Folder));

            internal Fixture(Color clear)
            {
                Panel = ScriptableObject.CreateInstance<PanelSettings>();
                Panel.name = "UI12EvidencePanel-" + Guid.NewGuid().ToString("N");
                Panel.scaleMode = PanelScaleMode.ConstantPixelSize;
                Panel.scale = 1;
                Panel.clearColor = true;
                Panel.colorClearValue = clear;
                Object = new GameObject("UI12Evidence-" + Guid.NewGuid().ToString("N"));
                Document = Object.AddComponent<UIDocument>();
                Document.panelSettings = Panel;
                var root = Document.rootVisualElement;
                Assert.IsNotNull(root);
                root.style.width = 64;
                root.style.height = 64;
                root.style.backgroundColor = Color.clear;
            }

            internal JObject Request(string name, int warmup = 4) =>
                new JObject
                {
                    ["action"] = "render_ui",
                    ["target"] = Object.GetInstanceIDCompat().ToString(),
                    ["component_type"] = "ui_document",
                    ["width"] = 64,
                    ["height"] = 64,
                    ["warmup_updates"] = warmup,
                    ["file_name"] = name,
                    ["output_folder"] = Folder,
                };

            public void Dispose()
            {
                // Release only this fixture's cached target, preserving all other panel caches.
                var cache = (Dictionary<int, RenderTexture>)typeof(ManageUI).GetField("s_panelRTs", StaticPrivate).GetValue(null);
                int id = Panel.GetInstanceIDCompat();
                if (cache.TryGetValue(id, out var target))
                    typeof(ManageUI)
                        .GetMethod("FinishPanelRender", StaticPrivate | BindingFlags.Public)
                        .Invoke(null, new object[] { id, Panel, target, false, true });
                UnityEngine.Object.DestroyImmediate(Object);
                UnityEngine.Object.DestroyImmediate(Panel);
                // PNGs and JSON receipts intentionally remain in this unique Temp folder for
                // the isolated native runner to collect. No project asset or existing output is removed.
            }
        }

        [SetUp]
        public void SetUp()
        {
            if (Read<bool>("s_pendingCaptureStarted") || Read<bool>("s_pendingCaptureDone") || Read<Texture2D>("s_pendingCaptureTex") != null)
                Assert.Ignore("An unrelated capture owns the slot.");
            ownsSlot = true;
        }

        [TearDown]
        public void TearDown() => CleanupOwnedState();

        private void CleanupOwnedState()
        {
            if (ownsSlot)
                Invoke("CleanupPendingCapture");
            fixture?.Dispose();
            fixture = null;
            ownsSlot = false;
        }

        [UnityTearDown]
        public IEnumerator ExitPlay()
        {
            CleanupOwnedState();
            if (EditorApplication.isPlaying)
                yield return new ExitPlayMode();
        }

        [Test]
        public void RenderTargetCollisionPreservesSentinelAcrossAllCleanupPaths()
        {
            fixture = new Fixture(Color.clear);
            int panelId = fixture.Panel.GetInstanceIDCompat();
            string sentinelPath = $"Assets/UI/RT_MCP_UI_Render_{panelId}.renderTexture";
            bool createdFolder = !AssetDatabase.IsValidFolder("Assets/UI");
            if (createdFolder)
                Assert.IsNotEmpty(AssetDatabase.CreateFolder("Assets", "UI"));
            Assert.IsNull(AssetDatabase.LoadMainAssetAtPath(sentinelPath), "The fixture must never replace a pre-existing asset.");
            var sentinel = new RenderTexture(7, 9, 0) { name = "UserRenderTargetSentinel" };
            AssetDatabase.CreateAsset(sentinel, sentinelPath);
            AssetDatabase.SaveAssetIfDirty(sentinel);
            Assert.IsTrue(AssetDatabase.TryGetGUIDAndLocalFileIdentifier(sentinel, out string guid, out long localId));
            byte[] originalBytes = File.ReadAllBytes(sentinelPath);
            var clock = typeof(ManageUI).GetField("s_renderCacheClock", StaticPrivate);
            object previousClock = clock.GetValue(null);
            double now = 1000;
            try
            {
                clock.SetValue(null, (Func<double>)(() => now));
                var cold = Send(fixture.Request("collision-cold.png", 0));
                Assert.IsTrue(cold.Value<bool>("success"), cold.ToString());
                AssertSentinel();
                var cache = (Dictionary<int, RenderTexture>)typeof(ManageUI).GetField("s_panelRTs", StaticPrivate).GetValue(null);
                var first = cache[panelId];
                string firstPath = AssetDatabase.GetAssetPath(first);
                Assert.That(firstPath, Does.StartWith("Assets/UI/"));
                Assert.AreNotEqual(sentinelPath, firstPath);

                var resize = fixture.Request("collision-resize.png", 0);
                resize["width"] = 32;
                var resized = Send(resize);
                Assert.IsTrue(resized.Value<bool>("success"), resized.ToString());
                Assert.IsTrue(first == null, "Resizing releases the previous owned target.");
                Assert.AreNotSame(first, cache[panelId]);
                Assert.AreEqual(32, cache[panelId].width);
                AssertSentinel();

                var warmup = fixture.Request("collision-cancel.png");
                warmup["width"] = 32;
                var started = Send(warmup);
                Assert.IsTrue(started.Value<bool>("success"), started.ToString());
                warmup["capture_id"] = started["data"]["capture_id"];
                warmup["cancel_capture"] = true;
                Assert.IsTrue(Send(warmup).Value<bool>("success"));
                AssertSentinel();
                var idleTarget = cache[panelId];
                string idlePath = AssetDatabase.GetAssetPath(idleTarget);
                now += 61;
                Invoke("CleanupIdlePanelTargets");
                Assert.IsFalse(cache.ContainsKey(panelId));
                Assert.IsTrue(idleTarget == null);
                Assert.IsNull(AssetDatabase.LoadMainAssetAtPath(idlePath));
                AssertSentinel();

                Assert.IsTrue(Send(fixture.Request("collision-global.png", 0)).Value<bool>("success"));
                var globalTarget = cache[panelId];
                string globalPath = AssetDatabase.GetAssetPath(globalTarget);
                Invoke("CleanupRenderTextures");
                Assert.IsTrue(globalTarget == null);
                Assert.IsFalse(cache.ContainsKey(panelId));
                Assert.IsNull(AssetDatabase.LoadMainAssetAtPath(globalPath));
                AssertSentinel();
            }
            finally
            {
                clock.SetValue(null, previousClock);
                CleanupOwnedState();
                AssetDatabase.DeleteAsset(sentinelPath);
                if (createdFolder && Directory.GetFileSystemEntries("Assets/UI").Length == 0)
                    AssetDatabase.DeleteAsset("Assets/UI");
            }

            void AssertSentinel()
            {
                var current = AssetDatabase.LoadAssetAtPath<RenderTexture>(sentinelPath);
                Assert.IsTrue(current == sentinel, "An existing asset must retain its object identity.");
                Assert.IsTrue(AssetDatabase.TryGetGUIDAndLocalFileIdentifier(current, out string actualGuid, out long actualId));
                Assert.AreEqual(guid, actualGuid);
                Assert.AreEqual(localId, actualId);
                CollectionAssert.AreEqual(originalBytes, File.ReadAllBytes(sentinelPath));
                Assert.AreEqual(7, current.width);
                Assert.AreEqual(9, current.height);
            }
        }

        [TestCase(false)]
        [TestCase(true)]
        public void ReplacingOwnedRenderAssetPreservesReplacementDuringCleanup(bool idle)
        {
            fixture = new Fixture(Color.clear);
            var clock = typeof(ManageUI).GetField("s_renderCacheClock", StaticPrivate);
            object previousClock = clock.GetValue(null);
            double now = 1000;
            string path = null;
            try
            {
                clock.SetValue(null, (Func<double>)(() => now));
                var response = Send(fixture.Request("replace-owned.png", 0));
                Assert.IsTrue(response.Value<bool>("success"), response.ToString());
                int panelId = fixture.Panel.GetInstanceIDCompat();
                var cache = (Dictionary<int, RenderTexture>)typeof(ManageUI).GetField("s_panelRTs", StaticPrivate).GetValue(null);
                var previous = cache[panelId];
                path = AssetDatabase.GetAssetPath(previous);
                var replacement = new RenderTexture(7, 9, 0) { name = "ExternalReplacement" };
                AssetDatabase.CreateAsset(replacement, path);
                AssetDatabase.SaveAssetIfDirty(replacement);
                byte[] bytes = File.ReadAllBytes(path);
                Assert.IsTrue(AssetDatabase.TryGetGUIDAndLocalFileIdentifier(replacement, out string guid, out long localId));
                Assert.IsTrue(previous == null, "The previous cached native object is destroyed by replacement.");
                now += 61;
                Invoke(idle ? "CleanupIdlePanelTargets" : "CleanupRenderTextures");
                Assert.IsFalse(cache.ContainsKey(panelId));
                Assert.IsTrue(AssetDatabase.LoadAssetAtPath<RenderTexture>(path) == replacement);
                Assert.IsTrue(AssetDatabase.TryGetGUIDAndLocalFileIdentifier(replacement, out string actualGuid, out long actualId));
                Assert.AreEqual(guid, actualGuid);
                Assert.AreEqual(localId, actualId);
                CollectionAssert.AreEqual(bytes, File.ReadAllBytes(path));
            }
            finally
            {
                clock.SetValue(null, previousClock);
                CleanupOwnedState();
                if (path != null)
                    AssetDatabase.DeleteAsset(path);
            }
        }

        [TestCase(false)]
        [TestCase(true)]
        public void UnidentifiedRenderTargetRollbackReleasesOnlyExactOwnedAsset(bool replaced)
        {
            fixture = new Fixture(Color.clear);
            int panelId = fixture.Panel.GetInstanceIDCompat();
            string path = $"Assets/UI/RT_Rollback_{Guid.NewGuid():N}.renderTexture";
            bool createdFolder = !AssetDatabase.IsValidFolder("Assets/UI");
            if (createdFolder)
                Assert.IsNotEmpty(AssetDatabase.CreateFolder("Assets", "UI"));
            var pendingAssets =
                (Dictionary<int, (RenderTexture target, string path, string guid, long localId)>)
                    typeof(ManageUI).GetField("s_panelRTAssets", StaticPrivate).GetValue(null);
            try
            {
                var target = new RenderTexture(16, 16, 0);
                target.Create();
                AssetDatabase.CreateAsset(target, path);
                pendingAssets[panelId] = (target, path, null, 0);
                RenderTexture replacement = null;
                byte[] bytes = null;
                if (replaced)
                {
                    replacement = new RenderTexture(7, 9, 0) { name = "ReplacementDuringCreation" };
                    AssetDatabase.CreateAsset(replacement, path);
                    AssetDatabase.SaveAssetIfDirty(replacement);
                    bytes = File.ReadAllBytes(path);
                }
                Invoke("ReleasePanelTarget", panelId, target);
                Assert.IsFalse(pendingAssets.ContainsKey(panelId));
                if (replaced)
                {
                    Assert.IsTrue(AssetDatabase.LoadAssetAtPath<RenderTexture>(path) == replacement);
                    CollectionAssert.AreEqual(bytes, File.ReadAllBytes(path));
                }
                else
                {
                    Assert.IsTrue(target == null, "A failed creation must release its native target even before GUID identification completes.");
                    Assert.IsNull(AssetDatabase.LoadMainAssetAtPath(path));
                }
            }
            finally
            {
                pendingAssets.Remove(panelId);
                CleanupOwnedState();
                AssetDatabase.DeleteAsset(path);
                if (createdFolder && Directory.GetFileSystemEntries("Assets/UI").Length == 0)
                    AssetDatabase.DeleteAsset("Assets/UI");
            }
        }

        [UnityTest]
        public IEnumerator Transparent64CaptureIsSuccessfulAndMatchesEveryDecodedPixel()
        {
            fixture = new Fixture(Color.clear);
            JObject result = null;
            yield return Capture(fixture.Request("transparent.png"), value => result = value);
            AssertEvidence(result, false, 64, 64, 0);
            Assert.AreEqual("bounded_editor_updates_elapsed", result["data"].Value<string>("readiness"));
            Assert.AreEqual(4, result["data"].Value<int>("warmup_updates_observed"));
            Assert.IsNull(fixture.Panel.targetTexture, "Deferred readback restores the previous panel target.");
            Assert.AreEqual(1, Directory.GetFiles(fixture.FullFolder, "*.png").Length);
        }

        [UnityTest]
        public IEnumerator TinyOpaqueElementBetweenOldSamplePositionsIsDetected()
        {
            fixture = new Fixture(Color.clear);
            var tiny = new VisualElement { name = "off-sample-one-pixel" };
            tiny.style.position = Position.Absolute;
            tiny.style.left = 2;
            tiny.style.top = 2;
            tiny.style.width = 1;
            tiny.style.height = 1;
            tiny.style.backgroundColor = Color.red;
            fixture.Document.rootVisualElement.Add(tiny);
            JObject result = null;
            yield return Capture(fixture.Request("tiny.png"), value => result = value);
            var pixels = AssertEvidence(result, false, 64, 64, 1);
            int oldStep = pixels.Length / 100;
            for (int i = 0; i < pixels.Length; i += oldStep)
                Assert.AreEqual(0, pixels[i].a, "The fixture must demonstrate a pixel missed by the previous sparse sampler.");
            Assert.AreEqual(1, Directory.GetFiles(fixture.FullFolder, "*.png").Length);
        }

        [UnityTest]
        public IEnumerator OpaquePanelBackgroundDoesNotClaimUIPresence()
        {
            fixture = new Fixture(Color.black);
            Assert.AreEqual(0, fixture.Document.rootVisualElement.childCount);
            JObject result = null;
            yield return Capture(fixture.Request("opaque-background.png"), value => result = value);
            AssertEvidence(result, false, 64, 64, 4096);
            Assert.AreEqual("not_determined", result["data"]["content_check"].Value<string>("ui_presence"));
        }

        [UnityTest]
        public IEnumerator ColdDefaultAndSingleBoundedWarmupReportDifferentReadiness()
        {
            fixture = new Fixture(Color.clear);
            fixture.Document.rootVisualElement.style.backgroundColor = Color.green;
            var cold = Send(fixture.Request("cold.png", 0));
            AssertEvidence(cold, false, 64, 64, null);
            Assert.AreEqual("target_just_assigned", cold["data"].Value<string>("readiness"));
            Assert.AreEqual(0, cold["data"].Value<int>("warmup_updates_observed"));
            JObject warm = null;
            yield return Capture(fixture.Request("warm.png"), value => warm = value);
            AssertEvidence(warm, false, 64, 64, 4096);
            Assert.AreEqual("bounded_editor_updates_elapsed", warm["data"].Value<string>("readiness"));
            Assert.AreEqual(4, warm["data"].Value<int>("warmup_updates_observed"));
            Assert.AreEqual(2, Directory.GetFiles(fixture.FullFolder, "*.png").Length, "One PNG per requested capture, no hidden retry outputs.");
        }

        [Test]
        public void WarmupRejectsForeignOwnersAndChangedSettingsBeforeCancellation()
        {
            fixture = new Fixture(Color.clear);
            var start = Send(fixture.Request("cancelled.png"));
            Assert.IsTrue(start.Value<bool>("success"), start.ToString());
            var target = fixture.Panel.targetTexture;
            Assert.IsNotNull(target);
            Assert.IsFalse(Send(fixture.Request("foreign.png")).Value<bool>("success"));
            var foreign = Poll(start);
            foreign["capture_id"] = "foreign";
            foreign["cancel_capture"] = true;
            Assert.IsFalse(Send(foreign).Value<bool>("success"));
            var changed = Poll(start);
            changed["warmup_updates"] = 1;
            Assert.IsFalse(Send(changed).Value<bool>("success"));
            Assert.AreSame(target, fixture.Panel.targetTexture);
            var cancel = Poll(start);
            cancel["cancel_capture"] = true;
            Assert.IsTrue(Send(cancel).Value<bool>("success"));
            Assert.IsNull(fixture.Panel.targetTexture);
            Assert.IsNull(Read<object>("s_pendingPanelCapture"));
            Assert.IsFalse(Directory.Exists(fixture.FullFolder));
        }

        [Test]
        public void ExpiryRestoresPanelWithoutWritingAndModeCleanupInvalidatesOwner()
        {
            fixture = new Fixture(Color.clear);
            var expired = Send(fixture.Request("expired.png"));
            typeof(ManageUI).GetField("s_pendingCaptureExpiresAt", StaticPrivate).SetValue(null, EditorApplication.timeSinceStartup - 1);
            Invoke("ExpirePendingCapture");
            Assert.IsNull(fixture.Panel.targetTexture);
            Assert.IsFalse(Send(Poll(expired)).Value<bool>("success"));
            var cancelled = Send(fixture.Request("transition.png"));
            Invoke("OnPlayModeStateChanged", PlayModeStateChange.ExitingEditMode);
            Assert.IsNull(fixture.Panel.targetTexture);
            Assert.IsFalse(Send(Poll(cancelled)).Value<bool>("success"));
            Assert.IsNull(Read<object>("s_pendingPanelCapture"));
            Assert.IsFalse(Directory.Exists(fixture.FullFolder));
        }

        [UnityTest]
        public IEnumerator ExternalTargetReplacementFailsOwnedWarmupWithoutOverwritingReplacement()
        {
            fixture = new Fixture(Color.clear);
            var started = Send(fixture.Request("replaced.png"));
            var replacement = new RenderTexture(8, 8, 0);
            try
            {
                fixture.Panel.targetTexture = replacement;
                JObject result = null;
                yield return PollUntilComplete(started, value => result = value);
                Assert.IsFalse(result.Value<bool>("success"));
                StringAssert.Contains("binding changed", result.Value<string>("error"));
                Assert.AreSame(replacement, fixture.Panel.targetTexture);
                Assert.IsFalse(Directory.Exists(fixture.FullFolder));
            }
            finally
            {
                fixture.Panel.targetTexture = null;
                UnityEngine.Object.DestroyImmediate(replacement);
            }
        }

        [UnityTest]
        public IEnumerator ResizedOwnedTargetCannotBypassTheRequestedPixelBudget()
        {
            fixture = new Fixture(Color.clear);
            var started = Send(fixture.Request("resized.png"));
            var target = fixture.Panel.targetTexture;
            Assert.IsNotNull(target);
            target.Release();
            target.width = 65;
            target.Create();
            JObject result = null;
            yield return PollUntilComplete(started, value => result = value);
            Assert.IsFalse(result.Value<bool>("success"));
            StringAssert.Contains("binding changed", result.Value<string>("error"));
            Assert.IsNull(fixture.Panel.targetTexture);
            Assert.IsFalse(Directory.Exists(fixture.FullFolder));
        }

        [UnityTest]
        public IEnumerator PlayCaptureCountsOneRealFullGameViewPngWithoutInferringUI()
        {
            yield return new EnterPlayMode();
            fixture = new Fixture(Color.clear);
            fixture.Panel.clearColor = false;
            var cameraObject = new GameObject("UI12OwnedBackgroundCamera");
            try
            {
                var camera = cameraObject.AddComponent<Camera>();
                camera.clearFlags = CameraClearFlags.SolidColor;
                camera.backgroundColor = Color.blue;
                camera.cullingMask = 0;
                camera.depth = 1000;
                yield return null;
                var rejected = Send(fixture.Request("must-not-start.png", 1));
                Assert.IsFalse(rejected.Value<bool>("success"));
                Assert.IsNull(Read<object>("s_pendingCaptureRequest"));
                JObject result = null;
                yield return Capture(fixture.Request("full-game-view.png", 0), value => result = value);
                Assert.IsTrue(result.Value<bool>("success"), result.ToString());
                int width = result["data"].Value<int>("width");
                int height = result["data"].Value<int>("height");
                AssertEvidence(result, true, width, height, width * height);
                Assert.AreEqual("end_of_frame_capture", result["data"].Value<string>("readiness"));
                Assert.AreEqual(1, Directory.GetFiles(fixture.FullFolder, "*.png").Length);
                Assert.IsNull(Read<object>("s_pendingCaptureRequest"));
            }
            finally
            {
                UnityEngine.Object.DestroyImmediate(cameraObject);
            }
        }

        private static IEnumerator Capture(JObject request, Action<JObject> completed)
        {
            var start = Send(request);
            Assert.IsTrue(start.Value<bool>("success"), start.ToString());
            Assert.IsTrue(start["data"].Value<bool>("pending"), start.ToString());
            yield return PollUntilComplete(start, completed);
        }

        private static IEnumerator PollUntilComplete(JObject start, Action<JObject> completed)
        {
            double deadline = EditorApplication.timeSinceStartup + 8;
            for (int poll = 0; poll < 240 && EditorApplication.timeSinceStartup < deadline; poll++)
            {
                yield return null;
                var result = Send(Poll(start));
                if (result["data"]?.Value<bool?>("pending") == true)
                    continue;
                completed(result);
                yield break;
            }
            Assert.Fail("The bounded capture did not finish within 240 polls / 8 seconds.");
        }

        private Color32[] AssertEvidence(JObject response, bool fullGameView, int width, int height, int? expectedNonzero)
        {
            Assert.IsTrue(response.Value<bool>("success"), response.ToString());
            var data = response["data"];
            Assert.AreEqual(fullGameView ? "game_view" : "panel_render_texture", data.Value<string>("capture_source"));
            Assert.AreEqual(fullGameView, data.Value<bool>("full_game_view"));
            Assert.AreEqual(width, data.Value<int>("width"));
            Assert.AreEqual(height, data.Value<int>("height"));
            Assert.AreEqual("not_observed", data.Value<string>("ui_repaint_completion"));
            string path = data.Value<string>("fullPath");
            Assert.AreEqual(fixture.FullFolder, Path.GetDirectoryName(Path.GetFullPath(path)));
            var decoded = new Texture2D(2, 2, TextureFormat.RGBA32, false);
            Color32[] pixels;
            try
            {
                Assert.IsTrue(decoded.LoadImage(File.ReadAllBytes(path)));
                Assert.AreEqual(width, decoded.width);
                Assert.AreEqual(height, decoded.height);
                pixels = decoded.GetPixels32();
            }
            finally
            {
                UnityEngine.Object.DestroyImmediate(decoded);
            }
            int count = 0;
            foreach (var pixel in pixels)
                if (pixel.a != 0)
                    count++;
            var evidence = data["content_check"];
            Assert.AreEqual("full_alpha_scan", evidence.Value<string>("method"));
            Assert.AreEqual("image_alpha_occupancy", evidence.Value<string>("meaning"));
            Assert.AreEqual("full_resolution_saved_png", evidence.Value<string>("pixel_basis"));
            Assert.AreEqual("not_determined", evidence.Value<string>("ui_presence"));
            Assert.AreEqual(pixels.Length, evidence.Value<int>("pixels_examined"));
            Assert.AreEqual(count, evidence.Value<int>("nonzero_alpha_pixels"));
            Assert.AreEqual(count != 0, data.Value<bool>("hasContent"));
            Assert.AreEqual((double)count / pixels.Length, evidence.Value<double>("alpha_occupancy"), 1e-12);
            if (expectedNonzero.HasValue)
                Assert.AreEqual(expectedNonzero.Value, count, "The real renderer must exercise the intended fixture, not merely return matching bookkeeping.");
            File.WriteAllText(
                Path.ChangeExtension(path, ".evidence.json"),
                new JObject
                {
                    ["native_response"] = response.DeepClone(),
                    ["decoded_pixels"] = pixels.Length,
                    ["decoded_nonzero_alpha_pixels"] = count,
                    ["expected_nonzero_alpha_pixels"] = expectedNonzero.HasValue ? new JValue(expectedNonzero.Value) : JValue.CreateNull(),
                    ["fixture"] = TestContext.CurrentContext.Test.Name,
                }.ToString()
            );
            return pixels;
        }

        private static JObject Send(JObject request) => JObject.FromObject(ManageUI.HandleCommand(request));

        private static JObject Poll(JObject start) => new JObject { ["action"] = "render_ui", ["capture_id"] = start["data"]["capture_id"].DeepClone() };

        private static T Read<T>(string name) => (T)typeof(ManageUI).GetField(name, StaticPrivate).GetValue(null);

        private static object Invoke(string name, params object[] args) => typeof(ManageUI).GetMethod(name, StaticPrivate).Invoke(null, args);
    }
}
