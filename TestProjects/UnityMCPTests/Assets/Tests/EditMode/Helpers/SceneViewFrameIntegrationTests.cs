using System;
using System.Collections;
using System.IO;
using System.Threading;
using System.Threading.Tasks;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Editor.Tools.Capabilities;
using MCPForUnity.Runtime.Helpers;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEditor;
using UnityEngine;
using UnityEngine.TestTools;

namespace MCPForUnityTests.Editor.Helpers
{
    /// <summary>Opt-in real frame readback in an explicitly owned interactive QA project.</summary>
    public class SceneViewFrameIntegrationTests
    {
        private SceneView view;
        private int drawCount;
        private GameObject negative;
        private GameObject positive;
        private string outputFolder;
        private string projectRoot;
        private readonly CancellationTokenSource lifetime = new();

        [UnityTest]
        [Timeout(420000)]
        public IEnumerator PublicCapturesAwaitTheirOwnFrameAndCanceledCapturesNeverWrite()
        {
            if (Environment.GetEnvironmentVariable("UNITY_MCP_SCENE_FRAME_QA") != "1")
                Assert.Ignore("Requires an explicitly owned interactive Scene View QA project.");
            projectRoot = Path.GetFullPath(Path.Combine(Application.dataPath, "..")).Replace('\\', '/');
            string ownedRoot = Environment.GetEnvironmentVariable("UNITY_MCP_NATIVE_QA_PROJECT");
            Assert.That(string.IsNullOrEmpty(ownedRoot), Is.False);
            Assert.That(projectRoot, Is.EqualTo(Path.GetFullPath(ownedRoot).Replace('\\', '/')).IgnoreCase);
            Assert.That(projectRoot.Contains("/.work/") || projectRoot.Contains("/.unity-ci/"), Is.True);
            Assert.That(Guid.TryParseExact(Environment.GetEnvironmentVariable("UNITY_MCP_NATIVE_QA_NONCE"), "N", out _), Is.True);
            Assert.That(Application.isBatchMode, Is.False, "Keep the production batch-mode guard enabled.");

            outputFolder = "Captures/SceneFrameQa/" + Guid.NewGuid().ToString("N");
            Debug.Log("Scene frame QA context: " + SynchronizationContext.Current?.GetType().FullName);
            negative = new GameObject("Scene frame negative") { hideFlags = HideFlags.HideAndDontSave };
            positive = new GameObject("Scene frame positive") { hideFlags = HideFlags.HideAndDontSave };
            negative.transform.position = Vector3.left * 20;
            positive.transform.position = Vector3.right * 20;
            view = ScriptableObject.CreateInstance<SceneView>();
            view.titleContent = new GUIContent("Owned scene frame QA");
            view.position = new Rect(80, 80, 640, 480);
            view.Show();
            view.Focus();
            SceneView.duringSceneGui += DrawExpectedColor;
            yield return null;

            using var activeCancellation = CancellationTokenSource.CreateLinkedTokenSource(lifetime.Token);
            Task<object> activeCanceled = Capture("canceled-active.png", negative, activeCancellation.Token);
            Assert.That(activeCanceled.IsCompleted, Is.False, "Capture must wait for a repaint instead of blocking or reading stale pixels.");
            activeCancellation.Cancel();
            Debug.Log("Scene frame QA active cancellation requested; task=" + activeCanceled.Status);
            yield return WaitFor(activeCanceled);
            Assert.That(activeCanceled.IsCanceled, Is.True);
            string outputDirectory = Path.Combine(projectRoot, outputFolder);
            Assert.That(Directory.Exists(outputDirectory) ? Directory.GetFiles(outputDirectory, "*.png").Length : 0, Is.Zero);

            Debug.Log("Scene frame QA active cancellation observed.");
            Task<object> red = Capture("negative.png", negative, lifetime.Token);
            using var queuedCancellation = CancellationTokenSource.CreateLinkedTokenSource(lifetime.Token);
            Task<object> canceledQueued = Capture("canceled-queued.png", positive, queuedCancellation.Token);
            Task<object> green = Capture("positive.png", positive, lifetime.Token);
            Assert.That(red.IsCompleted || green.IsCompleted, Is.False, "Concurrent callers must await their individual rendered frames.");
            queuedCancellation.Cancel();
            yield return WaitFor(red);
            yield return WaitFor(canceledQueued);
            yield return WaitFor(green);
            Assert.That(canceledQueued.IsCanceled, Is.True);
            Assert.That(File.Exists(Path.Combine(projectRoot, outputFolder, "canceled-queued.png")), Is.False);

            JObject redData = RequireCapture(red);
            JObject greenData = RequireCapture(green);
            Assert.That(Path.GetFileName(redData.Value<string>("fullPath")), Is.EqualTo("negative.png"));
            Assert.That(Path.GetFileName(greenData.Value<string>("fullPath")), Is.EqualTo("positive.png"));
            Assert.That(Directory.GetFiles(outputDirectory, "*.png").Length, Is.EqualTo(2), "Only the two completed captures may publish any PNG.");
            Color redPixel = ReadCenter(redData.Value<string>("fullPath"));
            Color greenPixel = ReadCenter(greenData.Value<string>("fullPath"));
            Assert.That(redPixel.r, Is.GreaterThan(0.8f));
            Assert.That(redPixel.g, Is.LessThan(0.2f));
            Assert.That(greenPixel.g, Is.GreaterThan(0.8f));
            Assert.That(greenPixel.r, Is.LessThan(0.2f));

            var evidence = new JObject
            {
                ["unity_version"] = Application.unityVersion,
                ["batch_mode"] = Application.isBatchMode,
                ["red"] = redData["path"],
                ["green"] = greenData["path"],
                ["red_pixel"] = new JArray(redPixel.r, redPixel.g, redPixel.b),
                ["green_pixel"] = new JArray(greenPixel.r, greenPixel.g, greenPixel.b),
                ["active_cancellation"] = activeCanceled.IsCanceled,
                ["queued_cancellation"] = canceledQueued.IsCanceled,
            };
            File.WriteAllText(Path.Combine(projectRoot, outputFolder, "evidence.json"), evidence.ToString());
            TestContext.WriteLine("Scene frame evidence: " + outputFolder);
        }

        private Task<object> Capture(string name, GameObject target, CancellationToken token) =>
            CaptureCamera.HandleCommand(
                new JObject
                {
                    ["action"] = "screenshot",
                    ["capture_source"] = "scene_view",
                    ["view_target"] = target.GetInstanceIDCompat(),
                    ["fileName"] = name,
                    ["output_folder"] = outputFolder,
                    ["timeout_seconds"] = 300,
                },
                token
            );

        private void DrawExpectedColor(SceneView drawingView)
        {
            if (drawingView != view || Event.current.type != EventType.Repaint)
                return;
            drawCount++;
            Handles.BeginGUI();
            try
            {
                EditorGUI.DrawRect(new Rect(80, 80, view.position.width - 160, view.position.height - 160), view.pivot.x < 0 ? Color.red : Color.green);
            }
            finally
            {
                Handles.EndGUI();
            }
        }

        private IEnumerator WaitFor(Task task)
        {
            double deadline = EditorApplication.timeSinceStartup + 360;
            int ticks = 0;
            while (!task.IsCompleted)
            {
                Assert.That(
                    EditorApplication.timeSinceStartup,
                    Is.LessThan(deadline),
                    "Owned QA watchdog expired while waiting for the product's finite budget."
                );
                if (++ticks == 30)
                {
                    int observations = 0;
                    foreach (Delegate callback in EditorApplication.update?.GetInvocationList() ?? Array.Empty<Delegate>())
                        if (callback.Target?.GetType().Name == "SceneViewRepaintWait")
                            observations++;
                    Debug.Log(
                        $"Scene frame QA wait: task={task.Status}, observationHooks={observations}, draws={drawCount}, active={SceneView.lastActiveSceneView == view}, focus={view.hasFocus}, panel={view.rootVisualElement.panel != null}, context={SynchronizationContext.Current?.GetType().FullName}"
                    );
                }
                yield return null;
            }
        }

        private static JObject RequireCapture(Task<object> task)
        {
            Assert.That(task.IsFaulted || task.IsCanceled, Is.False, task.Exception?.ToString());
            JObject response = JObject.FromObject(task.Result);
            Assert.That(response.Value<bool>("success"), Is.True, response.ToString());
            return (JObject)response["data"];
        }

        private static Color ReadCenter(string path)
        {
            var image = new Texture2D(2, 2, TextureFormat.RGB24, false);
            try
            {
                Assert.That(image.LoadImage(File.ReadAllBytes(path)), Is.True);
                return image.GetPixel(image.width / 2, image.height / 2);
            }
            finally
            {
                UnityEngine.Object.DestroyImmediate(image);
            }
        }

        [TearDown]
        public void Cleanup()
        {
            lifetime.Cancel();
            SceneView.duringSceneGui -= DrawExpectedColor;
            if (view != null)
                view.Close();
            if (negative != null)
                UnityEngine.Object.DestroyImmediate(negative);
            if (positive != null)
                UnityEngine.Object.DestroyImmediate(positive);
            lifetime.Dispose();
        }
    }
}
