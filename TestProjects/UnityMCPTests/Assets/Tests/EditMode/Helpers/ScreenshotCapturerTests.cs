using System.Collections;
using System.IO;
using System.Reflection;
using System.Threading;
using System.Threading.Tasks;
using MCPForUnity.Editor.Tools;
using MCPForUnity.Runtime.Helpers;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEditor;
using UnityEngine;
using UnityEngine.Rendering;
using UnityEngine.TestTools;

namespace MCPForUnityTests.Editor.Helpers
{
    [Parallelizable(ParallelScope.None)]
    public class ScreenshotCapturerTests
    {
        private static readonly BindingFlags StaticPrivate = BindingFlags.Static | BindingFlags.NonPublic;
        private static SemaphoreSlim CaptureGate => (SemaphoreSlim)typeof(ScreenshotUtility).GetField("CompositedCaptureGate", StaticPrivate).GetValue(null);

        [UnityTearDown]
        public IEnumerator LeavePlayMode()
        {
            if (Application.isPlaying)
                yield return new ExitPlayMode();
        }

        [TearDown]
        public void TearDown()
        {
            foreach (var capturer in UnityEngine.Resources.FindObjectsOfTypeAll<ScreenshotCapturer>())
            {
                if (capturer != null)
                    Object.DestroyImmediate(capturer.gameObject);
            }
        }

        [TestCase(float.NaN)]
        [TestCase(float.PositiveInfinity)]
        [TestCase(float.NegativeInfinity)]
        [TestCase(0f)]
        [TestCase(-1f)]
        [TestCase(86401f)]
        public void BeginRejectsInvalidTimeoutBeforeCreatingHiddenObject(float timeout)
        {
            Assert.Throws<System.ArgumentOutOfRangeException>(() => ScreenshotCapturer.Begin(1, _ => { }, timeout));
            Assert.AreEqual(0, UnityEngine.Resources.FindObjectsOfTypeAll<ScreenshotCapturer>().Length);
        }

        [TestCase(0)]
        [TestCase(5)]
        [TestCase(int.MaxValue)]
        public void CaptureCompositedAsync_RejectsInvalidSupersizeBeforeStartingCapture(int superSize)
        {
            var task = ScreenshotUtility.CaptureCompositedAsync(superSize: superSize);
            Assert.IsTrue(task.IsFaulted, "Invalid dimensions must fail before waiting for a frame.");
            Assert.That(task.Exception.GetBaseException(), Is.TypeOf<System.ArgumentException>());
            StringAssert.Contains("superSize must", task.Exception.GetBaseException().Message);
            Assert.AreEqual(0, UnityEngine.Resources.FindObjectsOfTypeAll<ScreenshotCapturer>().Length);
        }

        [TestCase(-1)]
        [TestCase(8193)]
        public void CaptureCompositedAsync_RejectsInvalidResolutionBeforeStartingCapture(int maxResolution)
        {
            var task = ScreenshotUtility.CaptureCompositedAsync(maxResolution: maxResolution);
            Assert.IsTrue(task.IsFaulted, "Invalid resolution must fail before waiting for a frame.");
            Assert.That(task.Exception.GetBaseException(), Is.TypeOf<System.ArgumentException>());
            StringAssert.Contains("maxResolution must", task.Exception.GetBaseException().Message);
            Assert.AreEqual(0, UnityEngine.Resources.FindObjectsOfTypeAll<ScreenshotCapturer>().Length);
        }

        [TestCase(true)]
        [TestCase(false)]
        public void EncodeComposited_RespectsUniqueFilePolicyAtWriteTime(bool unique)
        {
            if (SystemInfo.graphicsDeviceType == GraphicsDeviceType.Null)
                Assert.Ignore("Texture allocation requires a graphics device.");

            string folder = "Temp/ScreenshotWriteTests-" + System.Guid.NewGuid().ToString("N");
            var prepared = ScreenshotUtility.PrepareCaptureResult("capture", 1, unique, folder, false);
            var texture = new Texture2D(1, 1, TextureFormat.RGBA32, false);
            try
            {
                texture.SetPixel(0, 0, Color.red);
                byte[] expected = texture.EncodeToPNG();
                byte[] winner = { 1, 2, 3 };
                Directory.CreateDirectory(Path.GetDirectoryName(prepared.FullPath));
                File.WriteAllBytes(prepared.FullPath, winner);
                var encode = typeof(ScreenshotUtility).GetMethod("EncodeAndSaveComposited", BindingFlags.Static | BindingFlags.NonPublic);
                Assert.IsNotNull(encode);
                object[] args = { texture, prepared, false, 0, unique, null, CancellationToken.None };
                if (unique)
                {
                    var error = Assert.Throws<TargetInvocationException>(() => encode.Invoke(null, args));
                    Assert.That(error.InnerException, Is.InstanceOf<IOException>());
                    CollectionAssert.AreEqual(winner, File.ReadAllBytes(prepared.FullPath));
                }
                else
                {
                    var result = (ScreenshotCaptureResult)encode.Invoke(null, args);
                    Assert.AreEqual(prepared.FullPath, result.FullPath);
                    CollectionAssert.AreEqual(expected, File.ReadAllBytes(prepared.FullPath));
                }
            }
            finally
            {
                Object.DestroyImmediate(texture);
                Directory.Delete(Path.GetDirectoryName(prepared.FullPath), true);
            }
        }

        [Test]
        public void LegacyPublicCaptureSignaturesRemainAvailable()
        {
            var settings = new[] { typeof(string), typeof(int), typeof(bool), typeof(bool), typeof(int), typeof(string) };
            Assert.IsNotNull(typeof(ScreenshotUtility).GetMethod("CaptureCompositedAsync", settings));
            Assert.IsNotNull(
                typeof(ScreenshotUtility).GetMethod(
                    "CaptureFromCameraToProjectFolder",
                    new[] { typeof(Camera), typeof(string), typeof(int), typeof(bool), typeof(bool), typeof(int), typeof(string) }
                )
            );
            Assert.IsNotNull(typeof(ScreenshotCapturer).GetMethod("Begin", new[] { typeof(int), typeof(System.Action<Texture2D>), typeof(float) }));
            Assert.IsNotNull(typeof(ScreenshotCapturer).GetMethod("Begin", new[] { typeof(int), typeof(System.Action<Texture2D, bool>), typeof(float) }));
        }

        [Test]
        public void AlreadyCanceledCaptureDoesNotAdmitOrCreateWork()
        {
            using var cancel = new CancellationTokenSource();
            cancel.Cancel();
            var task = ScreenshotUtility.CaptureCompositedAsync(cancellationToken: cancel.Token);
            Assert.IsTrue(task.IsCanceled);
            Assert.AreEqual(1, CaptureGate.CurrentCount);
            Assert.Throws<System.OperationCanceledException>(() => ScreenshotCapturer.Begin(1, _ => { }, cancellationToken: cancel.Token));
            Assert.AreEqual(0, UnityEngine.Resources.FindObjectsOfTypeAll<ScreenshotCapturer>().Length);
        }

        [UnityTest]
        public IEnumerator CancelWhileWaitingForAdmissionDoesNotReleaseAnotherOwnersPermit()
        {
            var gate = CaptureGate;
            Assert.IsTrue(gate.Wait(0));
            using var cancel = new CancellationTokenSource();
            try
            {
                var capture = ScreenshotUtility.CaptureCompositedAsync(cancellationToken: cancel.Token);
                Assert.IsFalse(capture.IsCompleted);
                var signal = Task.Run(() => cancel.Cancel());
                float deadline = Time.realtimeSinceStartup + 2f;
                while ((!signal.IsCompleted || !capture.IsCompleted) && Time.realtimeSinceStartup < deadline)
                    yield return null;
                Assert.IsTrue(capture.IsCanceled, "Cancellation must abort admission without waiting for the 120 second timeout.");
                Assert.AreEqual(0, gate.CurrentCount, "The canceled waiter never owned this permit.");
                Assert.AreEqual(0, UnityEngine.Resources.FindObjectsOfTypeAll<ScreenshotCapturer>().Length);
            }
            finally
            {
                cancel.Cancel();
                gate.Release();
            }
            Assert.AreEqual(1, gate.CurrentCount);
        }

        [UnityTest]
        public IEnumerator CancelActualHelperWithoutFramesSettlesAfterOwnedCapturerCleanup()
        {
            using var cancel = new CancellationTokenSource();
            string folder = "Temp/ScreenshotCancelTests-" + System.Guid.NewGuid().ToString("N");
            string fullFolder = ScreenshotUtility.ResolveFolderAbsolute(folder);
            var method = typeof(ScreenshotUtility).GetMethod("CaptureCompositedAsyncUngated", StaticPrivate);
            var capture = (Task<ScreenshotCaptureResult>)method.Invoke(null, new object[] { "cancel", 1, true, true, 32, folder, cancel.Token });
            var owners = UnityEngine.Resources.FindObjectsOfTypeAll<ScreenshotCapturer>();
            Assert.AreEqual(1, owners.Length);
            var owner = owners[0];
            var settlement = (Task)typeof(ScreenshotCapturer).GetProperty("Settlement", BindingFlags.Instance | BindingFlags.NonPublic).GetValue(owner);
            var signal = Task.Run(() => cancel.Cancel());
            // Do not pump Unity while the worker signals: observing the token must not call
            // Unity or settle ownership from the cancellation thread.
            Assert.IsTrue(signal.Wait(2000));
            Assert.IsFalse(capture.IsCompleted);
            Assert.IsFalse(settlement.IsCompleted);
            Assert.IsTrue(owner != null);
            typeof(ScreenshotCapturer).GetMethod("TickTimeout", BindingFlags.Instance | BindingFlags.NonPublic).Invoke(owner, null);
            Assert.IsTrue(owner == null, "The actual main-thread tick must destroy its hidden GameObject.");
            Assert.IsTrue(settlement.IsCompleted);
            float deadline = Time.realtimeSinceStartup + 2f;
            while (!capture.IsCompleted && Time.realtimeSinceStartup < deadline)
                yield return null;
            Assert.IsTrue(capture.IsCanceled);
            Assert.IsFalse(Directory.Exists(fullFolder), "Cancellation before the callback must not write an image or create an output folder.");
            Assert.AreEqual(0, UnityEngine.Resources.FindObjectsOfTypeAll<ScreenshotCapturer>().Length);
        }

        [UnityTest]
        public IEnumerator CancelPublicCaptureReleasesAdmissionForTheNextCapture()
        {
            if (Application.isBatchMode)
                Assert.Ignore("Actual compositor admission requires an interactive Editor; batch fallback is covered separately.");
            using var firstCancel = new CancellationTokenSource();
            var first = ScreenshotUtility.CaptureCompositedAsync(cancellationToken: firstCancel.Token);
            Assert.AreEqual(0, CaptureGate.CurrentCount);
            firstCancel.Cancel();
            float deadline = Time.realtimeSinceStartup + 2f;
            while (!first.IsCompleted && Time.realtimeSinceStartup < deadline)
                yield return null;
            Assert.IsTrue(first.IsCanceled);
            Assert.AreEqual(0, UnityEngine.Resources.FindObjectsOfTypeAll<ScreenshotCapturer>().Length);
            Assert.AreEqual(1, CaptureGate.CurrentCount);
            using var secondCancel = new CancellationTokenSource();
            var second = ScreenshotUtility.CaptureCompositedAsync(cancellationToken: secondCancel.Token);
            Assert.AreEqual(0, CaptureGate.CurrentCount, "The next real helper call must acquire the single permit.");
            secondCancel.Cancel();
            deadline = Time.realtimeSinceStartup + 2f;
            while (!second.IsCompleted && Time.realtimeSinceStartup < deadline)
                yield return null;
            Assert.IsTrue(second.IsCanceled);
            Assert.AreEqual(1, CaptureGate.CurrentCount);
        }

        [UnityTest]
        public IEnumerator PlayModeToolCancellationBeforeWritePreventsImageAndAssetImport()
        {
            if (SystemInfo.graphicsDeviceType == GraphicsDeviceType.Null)
                Assert.Ignore("The actual screenshot tool requires a graphics device.");
            if (GraphicsSettings.currentRenderPipeline != null)
                Assert.Ignore("The fallback cancellation checkpoint uses the built-in Camera.onPostRender callback.");
            yield return new EnterPlayMode();
            // Create the callback closure after the domain reload. An event lambda captured
            // directly in this iterator would leave its display class behind during reload.
            yield return RunPlayModeToolCancellationBeforeWrite();
            yield return new ExitPlayMode();
        }

        private static IEnumerator RunPlayModeToolCancellationBeforeWrite()
        {
            using var cancel = new CancellationTokenSource();
            string folder = "Assets/ScreenshotCancelTests-" + System.Guid.NewGuid().ToString("N");
            string fullFolder = ScreenshotUtility.ResolveFolderAbsolute(folder);
            var cameraObject = new GameObject("OwnedCanceledScreenshotCamera") { tag = "MainCamera" };
            var camera = cameraObject.AddComponent<Camera>();
            camera.enabled = false;
            camera.clearFlags = CameraClearFlags.SolidColor;
            camera.cullingMask = 0;
            var target = RenderTexture.GetTemporary(16, 16, 24);
            camera.targetTexture = target;
            int rendered = 0;
            Camera.CameraCallback afterRender = renderedCamera =>
            {
                rendered++;
                cancel.Cancel();
            };
            Camera.onPostRender += afterRender;
            try
            {
                var capture = ManageScene.HandleCommand(
                    new JObject
                    {
                        ["action"] = "screenshot",
                        ["include_image"] = true,
                        ["file_name"] = "cancel",
                        ["output_folder"] = folder,
                    },
                    cancel.Token
                );
                if (!Application.isBatchMode)
                {
                    // Deliver a frame in the same tick as cancellation, exercising the real
                    // tool/helper completion race without depending on Game View focus.
                    var owners = UnityEngine.Resources.FindObjectsOfTypeAll<ScreenshotCapturer>();
                    Assert.AreEqual(1, owners.Length);
                    cancel.Cancel();
                    var texture = new Texture2D(2, 2, TextureFormat.RGBA32, false);
                    typeof(ScreenshotCapturer)
                        .GetMethod("Complete", BindingFlags.Instance | BindingFlags.NonPublic)
                        .Invoke(owners[0], new object[] { texture, false });
                    yield return null; // Flush texture destruction scheduled by the play-mode callback.
                    Assert.IsTrue(texture == null, "The canceled helper must release a captured texture it cannot publish.");
                }
                float deadline = Time.realtimeSinceStartup + 2f;
                while (!capture.IsCompleted && Time.realtimeSinceStartup < deadline)
                    yield return null;
                Assert.IsTrue(capture.IsCanceled, "The handler must propagate its request token through the composited helper.");
                if (Application.isBatchMode)
                    Assert.AreEqual(1, rendered, "Cancel from the actual fallback render before the write boundary.");
                Assert.IsFalse(Directory.Exists(fullFolder));
                Assert.IsEmpty(AssetDatabase.AssetPathToGUID(folder + "/cancel.png"), "A canceled image must not be imported.");
                Assert.AreEqual(0, UnityEngine.Resources.FindObjectsOfTypeAll<ScreenshotCapturer>().Length);
                Assert.AreEqual(1, CaptureGate.CurrentCount);
                Assert.AreSame(target, camera.targetTexture, "Cancellation must restore the user's camera target.");
            }
            finally
            {
                Camera.onPostRender -= afterRender;
                camera.targetTexture = null;
                RenderTexture.ReleaseTemporary(target);
                Object.DestroyImmediate(cameraObject);
                if (Directory.Exists(fullFolder))
                    AssetDatabase.DeleteAsset(folder);
            }
        }

        [UnityTest]
        public IEnumerator Begin_DoesNotLeakCapturerWhenFrameNeverCompletes()
        {
            bool called = false;
            var capturer = ScreenshotCapturer.Begin(1, _ => called = true, timeoutSeconds: 0.15f);
            Assert.IsNotNull(capturer, "Begin should return the live capturer.");

            float deadline = Time.realtimeSinceStartup + 2f;
            while (!called && Time.realtimeSinceStartup < deadline)
                yield return null;

            Assert.IsTrue(called, "Capturer must complete even if WaitForEndOfFrame never resumes.");
            yield return null;

            Assert.IsTrue(capturer == null, "Hidden __MCP_ScreenshotCapturer__ must destroy itself after completion.");
            Assert.AreEqual(0, UnityEngine.Resources.FindObjectsOfTypeAll<ScreenshotCapturer>().Length);
        }

        [UnityTest]
        public IEnumerator Destroy_StillCompletesPendingCallback()
        {
            // Outside play mode Unity sends this component no OnDestroy, so after an outside
            // destroy it is the editor-update timeout that completes the waiter, and it must
            // do so without touching the destroyed object.
            bool called = false;
            Texture2D received = null;

            var capturer = ScreenshotCapturer.Begin(
                1,
                tex =>
                {
                    received = tex;
                    called = true;
                },
                timeoutSeconds: 0.15f
            );

            Assert.IsNotNull(capturer);
            Object.DestroyImmediate(capturer.gameObject);

            float deadline = Time.realtimeSinceStartup + 2f;
            while (!called && Time.realtimeSinceStartup < deadline)
                yield return null;

            Assert.IsTrue(called, "Destroying the capturer must complete the waiter so MCP commands cannot hang.");
            Assert.IsNull(received);
            Assert.AreEqual(0, UnityEngine.Resources.FindObjectsOfTypeAll<ScreenshotCapturer>().Length);
        }

        [Test]
        public void CaptureCompositedAsync_InBatchMode_RendersACameraAtOnceAndSaysWhy()
        {
            if (!Application.isBatchMode)
                Assert.Ignore("Covers the batch-mode path; run the suite with -batchmode.");
            if (SystemInfo.graphicsDeviceType == GraphicsDeviceType.Null)
                Assert.Ignore("Requires a graphics device for the camera render; unavailable under -nographics.");

            var cameraObject = new GameObject("__ScreenshotBatchModeTestCamera");
            cameraObject.AddComponent<Camera>();
            string folder = "Temp/ScreenshotCapturerTests-" + System.Guid.NewGuid().ToString("N");
            try
            {
                var task = ScreenshotUtility.CaptureCompositedAsync("batch_mode", includeImage: true, maxResolution: 64, folderOverride: folder);

                // Batch mode renders no frames, so waiting for one would only sit out the timeout.
                Assert.IsTrue(task.IsCompleted, "the call must not wait for an end of frame in batch mode");
                var result = task.Result;
                StringAssert.StartsWith("Batch mode renders no frames", result.FallbackReason);
                // The camera the response reports must be the one that rendered, as the reason says.
                Assert.IsNotNull(result.FallbackCameraName);
                StringAssert.Contains($"render of camera '{result.FallbackCameraName}'", result.FallbackReason);
                Assert.IsNotNull(result.ImageBase64, "the caller still gets an image");
                Assert.AreEqual(0, UnityEngine.Resources.FindObjectsOfTypeAll<ScreenshotCapturer>().Length, "no capturer may start when no frame can come");
            }
            finally
            {
                Object.DestroyImmediate(cameraObject);
                string absolute = ScreenshotUtility.ResolveFolderAbsolute(folder);
                if (Directory.Exists(absolute))
                    Directory.Delete(absolute, true);
            }
        }
    }
}
