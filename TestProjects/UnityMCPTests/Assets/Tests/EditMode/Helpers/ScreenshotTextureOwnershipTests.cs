using System.Reflection;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Runtime.Helpers;
using NUnit.Framework;
using UnityEngine;
using UnityEngine.Rendering;

namespace MCPForUnityTests.EditMode.Helpers
{
    public class ScreenshotTextureOwnershipTests
    {
        [TestCase(2, 2)]
        [TestCase(2, 3)]
        [TestCase(1, 1)]
        public void ViewportFlip_UsesCpuPixelsWithoutAnIntermediateUpload(int width, int height)
        {
            if (SystemInfo.graphicsDeviceType == GraphicsDeviceType.Null)
                Assert.Ignore("Texture upload requires a graphics device.");
            var texture = new Texture2D(width, height, TextureFormat.RGBA32, false);
            try
            {
                var pixels = new Color32[width * height];
                for (int index = 0; index < pixels.Length; index++)
                    pixels[index] = new Color32((byte)(index + 1), 0, 0, 255);
                texture.SetPixels32(pixels); // intentionally no Apply before the CPU flip
                var flip = typeof(EditorWindowScreenshotUtility).GetMethod("FlipTextureVertically", BindingFlags.Static | BindingFlags.NonPublic);
                Assert.IsNotNull(flip);
                flip.Invoke(null, new object[] { texture });
                var flipped = texture.GetPixels32();
                for (int row = 0; row < height; row++)
                for (int column = 0; column < width; column++)
                    Assert.AreEqual(pixels[(height - 1 - row) * width + column], flipped[row * width + column]);
                Assert.IsTrue(texture.isReadable);
            }
            finally
            {
                Object.DestroyImmediate(texture);
            }
        }

        [Test]
        public void NullDownscale_RejectsWithoutChangingActiveRenderTarget()
        {
            var prior = RenderTexture.active;
            Assert.Throws<System.ArgumentNullException>(() => ScreenshotUtility.DownscaleTexture(null, 2));
            Assert.AreSame(prior, RenderTexture.active);
        }

        [TestCase(8193, 1)]
        [TestCase(8192, 8192)]
        [TestCase(int.MaxValue, int.MaxValue)]
        [TestCase(0, 1)]
        public void FrameBudgetRejectsOversizedOrOverflowingDimensions(int width, int height)
        {
            Assert.Throws<System.ArgumentException>(() => ScreenshotUtility.ValidateFrameDimensions(width, height));
        }

        [TestCase(7680, 4320)]
        [TestCase(8192, 4096)]
        public void FrameBudgetAcceptsUseful8KDimensionsWithoutAllocatingTextures(int width, int height)
        {
            Assert.DoesNotThrow(() => ScreenshotUtility.ValidateFrameDimensions(width, height));
        }

        [Test]
        public void SupersizeRejectsBeforeCameraRenderingOrOutputPreparation()
        {
            var go = new GameObject("__MCP_Budget_Camera__");
            try
            {
                var camera = go.AddComponent<Camera>();
                var error = Assert.Throws<System.ArgumentException>(() => ScreenshotUtility.CaptureFromCameraToProjectFolder(camera, superSize: int.MaxValue));
                StringAssert.Contains("superSize must", error.Message);
            }
            finally
            {
                Object.DestroyImmediate(go);
            }
        }

        [Test]
        public void ContactSheetPaddingRejectionReleasesOwnedTiles()
        {
            if (SystemInfo.graphicsDeviceType == GraphicsDeviceType.Null)
                Assert.Ignore("Texture allocation requires a graphics device.");
            var tile = new Texture2D(1, 1, TextureFormat.RGBA32, false);
            var tiles = new System.Collections.Generic.List<Texture2D> { tile };
            var prior = RenderTexture.active;
            try
            {
                Assert.Throws<System.ArgumentException>(() =>
                    ScreenshotUtility.ComposeContactSheet(tiles, new System.Collections.Generic.List<string> { "fixture" }, int.MaxValue)
                );
                Assert.IsTrue(tile == null, "Rejected composition must release its owned input texture.");
                Assert.AreSame(prior, RenderTexture.active);
            }
            finally
            {
                if (tile != null)
                    Object.DestroyImmediate(tile);
            }
        }

        [TestCase(8193, 1)]
        [TestCase(8192, 8192)]
        [TestCase(int.MaxValue, int.MaxValue)]
        [TestCase(0, 1)]
        public void SceneViewViewportBudgetRejectsBeforeResolvingEngineHost(int width, int height)
        {
            var capture = typeof(MCPForUnity.Editor.Helpers.EditorWindowScreenshotUtility).GetMethod(
                "CaptureViewRect",
                System.Reflection.BindingFlags.Static | System.Reflection.BindingFlags.NonPublic
            );
            Assert.IsNotNull(capture);
            var error = Assert.Throws<System.Reflection.TargetInvocationException>(() =>
                capture.Invoke(null, new object[] { null, new Rect(0, 0, width, height) })
            );
            Assert.IsInstanceOf<System.ArgumentException>(error.InnerException);
        }
    }
}
