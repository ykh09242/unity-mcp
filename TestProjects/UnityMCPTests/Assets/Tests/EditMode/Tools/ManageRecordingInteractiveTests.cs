using System;
using System.Collections;
using System.IO;
using System.Reflection;
using MCPForUnity.Editor.Tools;
using MCPForUnity.Runtime.Helpers;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEditor;
using UnityEngine;
using UnityEngine.TestTools;

namespace MCPForUnityTests.EditMode.Tools
{
    /// <summary>Opt-in full-tool QA in an owned interactive .unity-ci project. Successful media is retained for external decoding.</summary>
    public class ManageRecordingInteractiveTests
    {
        private const string OptInVariable = "UNITY_MCP_RECORDING_INTERACTIVE_QA";
        private const double FrameTimeoutSeconds = 12;
        private string _jobId;
        private string _outputFolder;
        private string _outputDirectory;
        private GameObject _fixture;
        private EditorWindow _captureWindow;
        private JObject _lastSummary;
        private JObject _evidence;

        [UnityTest]
        [Category("InteractiveRecording")]
        public IEnumerator SceneViewPublicStartStatusStopCapturesRealViewportAndCleansCanceledJob()
        {
            RequireInteractiveScratch();
            InitializeEvidence("scene-view");
            var sceneView = EditorWindow.GetWindow<SceneView>();
            _captureWindow = sceneView;
            sceneView.Show();
            sceneView.Focus();
            _fixture = new GameObject("MCP Recording Interactive Scene Fixture") { hideFlags = HideFlags.HideAndDontSave };
            var cube = GameObject.CreatePrimitive(PrimitiveType.Cube);
            cube.name = "Recording viewport geometry";
            cube.transform.SetParent(_fixture.transform, false);
            sceneView.LookAtDirect(Vector3.zero, Quaternion.Euler(20, 30, 0), 3);
            sceneView.Repaint();
            yield return null;
            yield return null;

            var invalid = Call(
                new JObject
                {
                    ["action"] = "start",
                    ["capture_source"] = "scene_view",
                    ["width"] = 319,
                }
            );
            Assert.That(invalid.Value<bool>("success"), Is.False, invalid.ToString());
            _evidence["bounds_rejected"] = invalid;
            StartRecording("scene_view", 8, "scene-stopped.mp4");
            var duplicate = Call(new JObject { ["action"] = "start", ["capture_source"] = "scene_view" });
            Assert.That(duplicate.Value<bool>("success"), Is.False, "A second start must preserve the active job.");
            Assert.That(duplicate["data"].Value<string>("job_id"), Is.EqualTo(_jobId));
            _evidence["concurrent_start_rejected"] = duplicate;
            yield return WaitForFrames(3);
            SaveCapturedFrame("scene-viewport.png", false);
            var completed = CommandData("stop");
            Assert.That(completed.Value<string>("status"), Is.EqualTo("stopped"), completed.ToString());
            AssertPublished(completed, "scene-stopped.mp4");
            _evidence["stopped"] = completed;
            Assert.That(CommandData("status").Value<int>("frames_recorded"), Is.EqualTo(completed.Value<int>("frames_recorded")));
            AssertCaptureResourcesReleased();

            StartRecording("scene_view", 8, "scene-canceled-before-frame.mp4");
            var canceled = CommandData("stop");
            Assert.That(canceled.Value<string>("status"), Is.EqualTo("failed"), canceled.ToString());
            Assert.That(canceled.Value<int>("frames_recorded"), Is.Zero);
            Assert.That(canceled.Value<string>("error"), Does.Contain("No frames"));
            Assert.That(File.Exists(Path.Combine(_outputDirectory, "scene-canceled-before-frame.mp4")), Is.False);
            AssertCaptureResourcesReleased();
            _evidence["canceled_before_first_frame"] = canceled;
            WriteEvidence();
        }

        [UnityTest]
        [Category("InteractiveRecording")]
        public IEnumerator GameViewPublicRecordingCapturesOverlayCompletesAndDiscardsInterruptedVideo()
        {
            RequireInteractiveScratch();
            Assert.That(EditorApplication.isPlaying, Is.False, "Start this fixture in Edit Mode.");
            yield return new EnterPlayMode();
            InitializeEvidence("game-view");
            Type gameViewType = typeof(EditorWindow).Assembly.GetType("UnityEditor.GameView");
            Assert.That(gameViewType, Is.Not.Null);
            _captureWindow = EditorWindow.GetWindow(gameViewType);
            _captureWindow.Show();
            _captureWindow.Focus();
            BuildGameOverlayFixture();
            yield return null;
            yield return null;

            StartRecording("game_view", 3, "game-completed.mp4");
            yield return WaitForFrames(3);
            SaveCapturedFrame("game-overlay.png", true);
            yield return WaitForTerminal();
            Assert.That(_lastSummary.Value<string>("status"), Is.EqualTo("completed"), _lastSummary.ToString());
            AssertPublished(_lastSummary, "game-completed.mp4");
            _evidence["completed"] = _lastSummary.DeepClone();
            _evidence["expected_decoded_center_color"] = new JObject
            {
                ["r"] = 0,
                ["g"] = 1,
                ["b"] = 0,
            };
            AssertCaptureResourcesReleased();

            StartRecording("game_view", 20, "game-interrupted.mp4");
            yield return WaitForFrames(2);
            EditorApplication.isPaused = true;
            yield return WaitForTerminal();
            Assert.That(_lastSummary.Value<string>("status"), Is.EqualTo("interrupted"), _lastSummary.ToString());
            Assert.That(_lastSummary.Value<string>("reason"), Is.EqualTo("play_mode_paused_or_stopped"));
            Assert.That(_lastSummary.Value<string>("output_path"), Is.Null);
            Assert.That(File.Exists(Path.Combine(_outputDirectory, "game-interrupted.mp4")), Is.False);
            AssertCaptureResourcesReleased();
            _evidence["interrupted"] = _lastSummary.DeepClone();
            EditorApplication.isPaused = false;
            WriteEvidence();
        }

        [UnityTearDown]
        public IEnumerator CleanupOwnedFixture()
        {
            string project = Path.GetFullPath(Path.Combine(Application.dataPath, "..")).Replace('\\', '/');
            if (Environment.GetEnvironmentVariable(OptInVariable) != "1" || !project.Contains("/.unity-ci/"))
                yield break;
            if (!string.IsNullOrEmpty(_jobId))
                Call(new JObject { ["action"] = "stop", ["job_id"] = _jobId });
            _jobId = null;
            if (_fixture != null)
                UnityEngine.Object.DestroyImmediate(_fixture);
            _fixture = null;
            if (_evidence != null)
                WriteEvidence();
            if (EditorApplication.isPlaying)
            {
                EditorApplication.isPaused = false;
                yield return new ExitPlayMode();
            }
        }

        private static void RequireInteractiveScratch()
        {
            if (Environment.GetEnvironmentVariable(OptInVariable) != "1")
                Assert.Ignore("Opt-in interactive recording QA requires " + OptInVariable + "=1 in an owned .unity-ci project.");
            string project = Path.GetFullPath(Path.Combine(Application.dataPath, "..")).Replace('\\', '/');
            Assert.That(project, Does.Contain("/.unity-ci/"), "Interactive recording QA must never run in a user's Unity project.");
            Assert.That(project.EndsWith("/project", StringComparison.OrdinalIgnoreCase), Is.True);
            Assert.That(Application.isBatchMode, Is.False, "Launch without -batchmode; the public recording batch guard must stay enabled.");
            var capabilities = Call(new JObject { ["action"] = "capabilities" });
            Assert.That(capabilities.Value<bool>("success"), Is.True);
            Assert.That(capabilities["data"].Value<bool>("supported"), Is.True, capabilities.ToString());
        }

        private void InitializeEvidence(string source)
        {
            _outputFolder = "Captures/Recordings/interactive-qa-" + source + "-" + Guid.NewGuid().ToString("N");
            string projectRoot = Path.GetFullPath(Path.Combine(Application.dataPath, ".."));
            _outputDirectory = SafePathUtility.ResolveWithinRoot(projectRoot, _outputFolder);
            _evidence = new JObject
            {
                ["capture_source"] = source,
                ["unity_version"] = Application.unityVersion,
                ["batch_mode"] = Application.isBatchMode,
                ["capabilities"] = Call(new JObject { ["action"] = "capabilities" }),
                ["output_directory"] = _outputDirectory.Replace('\\', '/'),
            };
        }

        private void StartRecording(string source, double duration, string fileName)
        {
            var response = Call(
                new JObject
                {
                    ["action"] = "start",
                    ["capture_source"] = source,
                    ["duration_seconds"] = duration,
                    ["fps"] = 8,
                    ["width"] = 320,
                    ["height"] = 240,
                    ["output_folder"] = _outputFolder,
                    ["file_name"] = fileName,
                }
            );
            Assert.That(response.Value<bool>("success"), Is.True, response.ToString());
            var data = (JObject)response["data"];
            _jobId = data.Value<string>("job_id");
            Assert.That(_jobId, Is.Not.Null.And.Not.Empty);
            Assert.That(data.Value<string>("status"), Is.EqualTo("recording"));
            Assert.That(data.Value<int>("frames_recorded"), Is.Zero);
            _evidence["start_" + fileName] = data;
        }

        private IEnumerator WaitForFrames(int frames)
        {
            double deadline = EditorApplication.timeSinceStartup + FrameTimeoutSeconds;
            while (EditorApplication.timeSinceStartup < deadline)
            {
                RepaintCaptureWindow();
                _lastSummary = CommandData("status");
                if (_lastSummary.Value<int>("frames_recorded") >= frames)
                    yield break;
                Assert.That(_lastSummary.Value<bool>("terminal"), Is.False, "Recording stopped before the requested evidence frames: " + _lastSummary);
                yield return null;
            }
            Assert.Fail("Timed out waiting for actual rendered recording frames: " + _lastSummary);
        }

        private IEnumerator WaitForTerminal()
        {
            double deadline = EditorApplication.timeSinceStartup + FrameTimeoutSeconds;
            while (EditorApplication.timeSinceStartup < deadline)
            {
                RepaintCaptureWindow();
                _lastSummary = CommandData("status");
                if (_lastSummary.Value<bool>("terminal"))
                    yield break;
                yield return null;
            }
            Assert.Fail("Timed out waiting for recording cleanup/finalization: " + _lastSummary);
        }

        private void RepaintCaptureWindow()
        {
            if (_captureWindow == null)
                return;
            // Scene capture must schedule its own due repaints, including while the editor is otherwise idle.
            if (_captureWindow is SceneView)
                return;
            _captureWindow.Focus();
            _captureWindow.Repaint();
            EditorApplication.QueuePlayerLoopUpdate();
        }

        private JObject CommandData(string action)
        {
            var response = Call(new JObject { ["action"] = action, ["job_id"] = _jobId });
            Assert.That(response.Value<bool>("success"), Is.True, response.ToString());
            return (JObject)response["data"];
        }

        private static JObject Call(JObject request) => JObject.FromObject(ManageRecording.HandleCommand(request));

        private void BuildGameOverlayFixture()
        {
            _fixture = new GameObject("MCP Recording Interactive Game Fixture");
            var cameraObject = new GameObject("Recording fixture camera", typeof(Camera));
            cameraObject.transform.SetParent(_fixture.transform, false);
            var camera = cameraObject.GetComponent<Camera>();
            camera.depth = 100;
            camera.clearFlags = CameraClearFlags.SolidColor;
            camera.backgroundColor = Color.blue;
            var canvasObject = new GameObject("Recording fixture overlay canvas", typeof(Canvas));
            canvasObject.transform.SetParent(_fixture.transform, false);
            var canvas = canvasObject.GetComponent<Canvas>();
            canvas.renderMode = RenderMode.ScreenSpaceOverlay;
            canvas.sortingOrder = 32000;
            Type imageType = Type.GetType("UnityEngine.UI.Image, UnityEngine.UI");
            Assert.That(imageType, Is.Not.Null, "The isolated QA project must include com.unity.ugui for overlay capture evidence.");
            var imageObject = new GameObject("Green overlay marker", typeof(RectTransform), typeof(CanvasRenderer));
            imageObject.transform.SetParent(canvasObject.transform, false);
            var image = imageObject.AddComponent(imageType);
            imageType.GetProperty("color").SetValue(image, Color.green);
            var rect = imageObject.GetComponent<RectTransform>();
            rect.anchorMin = new Vector2(0.25f, 0.25f);
            rect.anchorMax = new Vector2(0.75f, 0.75f);
            rect.offsetMin = Vector2.zero;
            rect.offsetMax = Vector2.zero;
            Canvas.ForceUpdateCanvases();
        }

        private void SaveCapturedFrame(string fileName, bool expectGreenCenter)
        {
            var field = typeof(ManageRecording).GetField("_scaledTexture", BindingFlags.Static | BindingFlags.NonPublic);
            var texture = field.GetValue(null) as Texture2D;
            Assert.That(texture, Is.Not.Null, "The isolated capture viewport must differ from the requested 320x240 encoded resolution.");
            Color pixel = texture.GetPixel(texture.width / 2, texture.height / 2);
            _evidence["captured_center_color"] = new JObject
            {
                ["r"] = pixel.r,
                ["g"] = pixel.g,
                ["b"] = pixel.b,
            };
            if (expectGreenCenter)
            {
                Assert.That(pixel.g, Is.GreaterThan(0.6f), "Game View encoding must include the green Screen Space - Overlay marker.");
                Assert.That(pixel.r, Is.LessThan(0.3f));
                Assert.That(pixel.b, Is.LessThan(0.3f));
            }
            File.WriteAllBytes(SafePathUtility.ResolveWithinRoot(_outputDirectory, fileName), texture.EncodeToPNG());
        }

        private void AssertPublished(JObject summary, string fileName)
        {
            string expected = SafePathUtility.ResolveWithinRoot(_outputDirectory, fileName);
            Assert.That(summary.Value<string>("output_path"), Is.EqualTo(expected.Replace('\\', '/')));
            Assert.That(summary.Value<int>("frames_recorded"), Is.GreaterThanOrEqualTo(3));
            ManageRecordingTests.AssertFinalizedMp4(File.ReadAllBytes(expected));
        }

        private void AssertCaptureResourcesReleased()
        {
            foreach (string name in new[] { "_pump", "_scaledTexture", "_scaledRenderTexture", "_sceneView" })
                Assert.That(typeof(ManageRecording).GetField(name, BindingFlags.Static | BindingFlags.NonPublic).GetValue(null), Is.Null, name + " leaked.");
            Assert.That(typeof(ManageRecording).GetField("_sceneCaptureQueued", BindingFlags.Static | BindingFlags.NonPublic).GetValue(null), Is.False);
            Assert.That(Resources.FindObjectsOfTypeAll<RecordingFramePump>(), Is.Empty);
            Assert.That(Directory.GetDirectories(_outputDirectory, ".mcp-recording-*"), Is.Empty);
        }

        private void WriteEvidence()
        {
            Directory.CreateDirectory(_outputDirectory);
            File.WriteAllText(SafePathUtility.ResolveWithinRoot(_outputDirectory, "evidence.json"), _evidence.ToString(Formatting.Indented));
        }
    }
}
