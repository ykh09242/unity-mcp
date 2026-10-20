using MCPForUnity.Runtime.Helpers;
using NUnit.Framework;
using UnityEngine;
using UnityEngine.Rendering;

namespace MCPForUnityTests.Editor.Tools
{
    /// <summary>
    /// DownscaleTexture blitted through a RenderTexture created with the project-default
    /// read/write mode. In a Linear-colorspace project that is sRGB, so a linear-flagged
    /// capture picked up an sRGB encode on store and the inline preview came back washed
    /// out while the on-disk PNG stayed correct (issue #1328).
    /// </summary>
    [TestFixture]
    public class ScreenshotUtilityDownscaleTests
    {
        [Test]
        public void CameraPreviewMatchesTheExistingFullReadbackColorsAndRestoresTargets()
        {
            var owner = new GameObject("PreviewColorFixture");
            var camera = owner.AddComponent<Camera>();
            camera.enabled = false;
            camera.clearFlags = CameraClearFlags.SolidColor;
            camera.cullingMask = 0;
            camera.backgroundColor = new Color(0.12f, 0.3f, 0.75f, 0.5f);
            var originalTarget = RenderTexture.GetTemporary(192, 96, 24);
            var priorActive = RenderTexture.active;
            Texture2D full = null,
                expected = null,
                actual = null;
            try
            {
                camera.targetTexture = originalTarget;
                camera.Render();
                RenderTexture.active = originalTarget;
                full = new Texture2D(192, 96, TextureFormat.RGBA32, false);
                full.ReadPixels(new Rect(0, 0, 192, 96), 0, 0);
                full.Apply();
                expected = ScreenshotUtility.DownscaleTexture(full, 64);
                RenderTexture.active = priorActive;
                actual = ScreenshotUtility.RenderCameraToTexture(camera, 64);
                Assert.AreSame(originalTarget, camera.targetTexture);
                Assert.AreSame(priorActive, RenderTexture.active);
                Assert.AreEqual(64, actual.width);
                Assert.AreEqual(32, actual.height);
                var wanted = expected.GetPixels32();
                var got = actual.GetPixels32();
                for (int i = 0; i < wanted.Length; i++)
                {
                    Assert.AreEqual(wanted[i].r, got[i].r, 2);
                    Assert.AreEqual(wanted[i].g, got[i].g, 2);
                    Assert.AreEqual(wanted[i].b, got[i].b, 2);
                    Assert.AreEqual(wanted[i].a, got[i].a, 2);
                }
            }
            finally
            {
                camera.targetTexture = null;
                RenderTexture.active = priorActive;
                RenderTexture.ReleaseTemporary(originalTarget);
                if (full != null)
                    Object.DestroyImmediate(full);
                if (expected != null)
                    Object.DestroyImmediate(expected);
                if (actual != null)
                    Object.DestroyImmediate(actual);
                Object.DestroyImmediate(owner);
            }
        }

        [TestCase(TextureFormat.RGBA32)]
        [TestCase(TextureFormat.RGB24)]
        public void ContactSheetPreservesTilePixelsAndBannerAcrossSupportedFormats(TextureFormat format)
        {
            var tiles = new System.Collections.Generic.List<Texture2D>();
            var colors = new[] { new Color32(11, 33, 77, 255), new Color32(200, 80, 40, 255) };
            var decoded = new Texture2D(1, 1);
            try
            {
                foreach (var color in colors)
                {
                    var tile = new Texture2D(2, 2, format, false);
                    tile.SetPixels32(new[] { color, color, color, color });
                    tile.Apply();
                    tiles.Add(tile);
                }
                var sheet = ScreenshotUtility.ComposeContactSheet(tiles, null, 1);
                Assert.AreEqual(7, sheet.width);
                Assert.AreEqual(18, sheet.height);
                Assert.IsTrue(decoded.LoadImage(System.Convert.FromBase64String(sheet.base64)));
                var pixels = decoded.GetPixels32();
                Assert.AreEqual(colors[0], pixels[15 * 7 + 1]);
                Assert.AreEqual(colors[1], pixels[15 * 7 + 4]);
                Assert.AreEqual(new Color32(20, 20, 20, 220), pixels[1 * 7 + 1]);
                Assert.AreEqual(new Color32(30, 30, 30, 255), pixels[0]);
                Assert.IsTrue(tiles.TrueForAll(tile => tile == null));
            }
            finally
            {
                ScreenshotUtility.ReleaseCaptureTiles(tiles);
                Object.DestroyImmediate(decoded);
            }
        }

        [SetUp]
        public void SkipWithoutGraphics()
        {
            if (SystemInfo.graphicsDeviceType == GraphicsDeviceType.Null)
                Assert.Ignore("Requires a graphics device; Graphics.Blit is unavailable under -nographics.");
        }

        [Test]
        public void DownscaleTexture_LinearSource_PreservesPixelValues()
        {
            // The regression only reproduces in a Linear-colorspace project; in Gamma this
            // asserts the (already correct) identity round-trip.
            var source = new Texture2D(64, 64, TextureFormat.RGBA32, false, linear: true);
            var fill = new Color32(11, 11, 11, 255); // the dark value reported in #1328
            var pixels = new Color32[64 * 64];
            for (int i = 0; i < pixels.Length; i++)
                pixels[i] = fill;
            source.SetPixels32(pixels);
            source.Apply();

            Texture2D result = null;
            try
            {
                result = ScreenshotUtility.DownscaleTexture(source, 32);

                Assert.AreEqual(32, result.width);
                Assert.AreEqual(32, result.height);

                Color32 got = result.GetPixels32()[result.width * result.height / 2];
                Assert.AreEqual(fill.r, got.r, 2, $"Red shifted {fill.r} -> {got.r} (sRGB re-encode?)");
                Assert.AreEqual(fill.g, got.g, 2, $"Green shifted {fill.g} -> {got.g} (sRGB re-encode?)");
                Assert.AreEqual(fill.b, got.b, 2, $"Blue shifted {fill.b} -> {got.b} (sRGB re-encode?)");
            }
            finally
            {
                if (result != null)
                    Object.DestroyImmediate(result);
                Object.DestroyImmediate(source);
            }
        }

        [Test]
        public void DownscaleTexture_SrgbSource_PreservesPixelValues()
        {
            var source = new Texture2D(64, 64, TextureFormat.RGBA32, false, linear: false);
            var fill = new Color32(128, 64, 32, 255);
            var pixels = new Color32[64 * 64];
            for (int i = 0; i < pixels.Length; i++)
                pixels[i] = fill;
            source.SetPixels32(pixels);
            source.Apply();

            Texture2D result = null;
            try
            {
                result = ScreenshotUtility.DownscaleTexture(source, 16);

                Color32 got = result.GetPixels32()[result.width * result.height / 2];
                Assert.AreEqual(fill.r, got.r, 2);
                Assert.AreEqual(fill.g, got.g, 2);
                Assert.AreEqual(fill.b, got.b, 2);
            }
            finally
            {
                if (result != null)
                    Object.DestroyImmediate(result);
                Object.DestroyImmediate(source);
            }
        }

        [Test]
        public void DownscaleTexture_NeverUpscales()
        {
            var source = new Texture2D(8, 8, TextureFormat.RGBA32, false, linear: true);
            source.Apply();

            Texture2D result = null;
            try
            {
                result = ScreenshotUtility.DownscaleTexture(source, 512);
                Assert.AreEqual(8, result.width);
                Assert.AreEqual(8, result.height);
            }
            finally
            {
                if (result != null)
                    Object.DestroyImmediate(result);
                Object.DestroyImmediate(source);
            }
        }
    }
}
