using System.Collections;
using System.IO;
using System.Reflection;
using NUnit.Framework;
using UnityEngine;
using UnityEngine.Rendering;
using UnityEngine.TestTools;
using MCPForUnity.Runtime.Helpers;

namespace MCPForUnityTests.Editor.Helpers
{
    public class ScreenshotCapturerTests
    {
        [TearDown]
        public void TearDown()
        {
            foreach (var capturer in UnityEngine.Resources.FindObjectsOfTypeAll<ScreenshotCapturer>())
            {
                if (capturer != null)
                    Object.DestroyImmediate(capturer.gameObject);
            }
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
                File.WriteAllBytes(prepared.FullPath, winner);
                var encode = typeof(ScreenshotUtility).GetMethod(
                    "EncodeAndSaveComposited", BindingFlags.Static | BindingFlags.NonPublic);
                Assert.IsNotNull(encode);
                object[] args = { texture, prepared, false, 0, unique, null };
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

            var capturer = ScreenshotCapturer.Begin(1, tex =>
            {
                received = tex;
                called = true;
            }, timeoutSeconds: 0.15f);

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
                var task = ScreenshotUtility.CaptureCompositedAsync(
                    "batch_mode", includeImage: true, maxResolution: 64, folderOverride: folder);

                // Batch mode renders no frames, so waiting for one would only sit out the timeout.
                Assert.IsTrue(task.IsCompleted, "the call must not wait for an end of frame in batch mode");
                var result = task.Result;
                StringAssert.StartsWith("Batch mode renders no frames", result.FallbackReason);
                // The camera the response reports must be the one that rendered, as the reason says.
                Assert.IsNotNull(result.FallbackCameraName);
                StringAssert.Contains($"render of camera '{result.FallbackCameraName}'", result.FallbackReason);
                Assert.IsNotNull(result.ImageBase64, "the caller still gets an image");
                Assert.AreEqual(0, UnityEngine.Resources.FindObjectsOfTypeAll<ScreenshotCapturer>().Length,
                    "no capturer may start when no frame can come");
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
