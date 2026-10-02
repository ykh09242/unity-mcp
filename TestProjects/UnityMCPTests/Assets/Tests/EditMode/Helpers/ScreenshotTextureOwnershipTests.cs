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
                var flip = typeof(EditorWindowScreenshotUtility).GetMethod(
                    "FlipTextureVertically", BindingFlags.Static | BindingFlags.NonPublic);
                Assert.IsNotNull(flip);
                flip.Invoke(null, new object[] { texture });
                var flipped = texture.GetPixels32();
                for (int row = 0; row < height; row++)
                    for (int column = 0; column < width; column++)
                        Assert.AreEqual(pixels[(height - 1 - row) * width + column],
                            flipped[row * width + column]);
                Assert.IsTrue(texture.isReadable);
            }
            finally { Object.DestroyImmediate(texture); }
        }

        [Test]
        public void NullDownscale_RejectsWithoutChangingActiveRenderTarget()
        {
            var prior = RenderTexture.active;
            Assert.Throws<System.ArgumentNullException>(() => ScreenshotUtility.DownscaleTexture(null, 2));
            Assert.AreSame(prior, RenderTexture.active);
        }
    }
}
