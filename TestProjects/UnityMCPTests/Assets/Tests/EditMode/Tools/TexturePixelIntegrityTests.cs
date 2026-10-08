using MCPForUnity.Editor.Helpers;
using MCPForUnity.Editor.Tools;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEngine;
using UnityEngine.Rendering;

namespace MCPForUnityTests.EditMode.Tools
{
    public class TexturePixelIntegrityTests
    {
        private Texture2D _texture;

        [SetUp]
        public void SetUp()
        {
            if (SystemInfo.graphicsDeviceType == GraphicsDeviceType.Null)
                Assert.Ignore("Texture pixel tests require a graphics device.");
            _texture = new Texture2D(2, 2, TextureFormat.RGBA32, false);
            TextureOps.FillTexture(_texture, new Color32(20, 30, 40, 255));
        }

        [TearDown]
        public void TearDown()
        {
            if (_texture != null)
                Object.DestroyImmediate(_texture);
        }

        [TestCase("[[1,2,3],false]")]
        [TestCase("[[1,2,3],['bad',2,3]]")]
        [TestCase("[[1,2,3],[NaN,2,3]]")]
        [TestCase("[[1,2,3],[Infinity,2,3]]")]
        [TestCase("[[1,2,3]]")]
        [TestCase("'base64:AQI='")]
        [TestCase("'base64:!!!!'")]
        [TestCase("{}")]
        [TestCase("null")]
        public void InvalidPayloadPreservesEveryPixel(string json)
        {
            var before = _texture.GetPixels32();
            Assert.Catch(() => TextureOps.ApplyPixelDataToRegion(_texture, JToken.Parse(json), 0, 0, 2, 1));
            CollectionAssert.AreEqual(before, _texture.GetPixels32());
        }

        [Test]
        public void NegativeOriginUsesTheCorrectSourcePixel()
        {
            TextureOps.ApplyPixelDataToRegion(_texture, JArray.Parse("[[1,2,3],[4,5,6]]"), -1, 0, 2, 1);
            var after = _texture.GetPixels32();
            Assert.AreEqual(new Color32(4, 5, 6, 255), after[0]);
            Assert.AreEqual(new Color32(20, 30, 40, 255), after[1]);
        }

        [Test]
        public void FullyOutsideRegionPreservesEveryPixel()
        {
            var before = _texture.GetPixels32();
            TextureOps.ApplyPixelDataToRegion(_texture, JArray.Parse("[[1,2,3]]"), int.MaxValue, 0, 1, 1);
            CollectionAssert.AreEqual(before, _texture.GetPixels32());
        }

        [Test]
        public void OverflowingRequestedCountRejectsBeforeAnyPixelChanges()
        {
            var before = _texture.GetPixels32();
            Assert.Catch(() => TextureOps.ApplyPixelDataToRegion(_texture, JArray.Parse("[[1,2,3]]"), 0, 0, int.MaxValue, int.MaxValue));
            CollectionAssert.AreEqual(before, _texture.GetPixels32());
        }

        [TestCase("'AQIDBA=='")]
        [TestCase("'base64:AQIDBA=='")]
        [TestCase("[['1','2','3','4']]")]
        public void CreatePixelHelperAcceptsExistingArrayAndBase64Forms(string json)
        {
            TextureOps.ApplyPixelData(_texture, JToken.Parse(json), 1, 1);
            Assert.AreEqual(new Color32(1, 2, 3, 4), _texture.GetPixels32()[0]);
        }

        [TestCase(-1, -1)]
        [TestCase(0, 0)]
        [TestCase(1, 1)]
        [TestCase(-1, 1)]
        [TestCase(1, -1)]
        [TestCase(2, 0)]
        [TestCase(0, 2)]
        [TestCase(int.MinValue, int.MinValue)]
        [TestCase(int.MaxValue, int.MaxValue)]
        public void ClippedArrayAndBase64PreserveRowOrderAndOutsidePixels(int x, int y)
        {
            var colors = new JArray();
            var bytes = new byte[3 * 3 * 4];
            for (int i = 0; i < 9; i++)
            {
                byte red = (byte)(i + 1);
                colors.Add(new JArray(red, 10, 20, 255));
                bytes[i * 4] = red;
                bytes[i * 4 + 1] = 10;
                bytes[i * 4 + 2] = 20;
                bytes[i * 4 + 3] = 255;
            }
            foreach (var payload in new JToken[] { colors, new JValue("base64:" + System.Convert.ToBase64String(bytes)) })
            {
                TextureOps.FillTexture(_texture, new Color32(20, 30, 40, 255));
                TextureOps.ApplyPixelDataToRegion(_texture, payload, x, y, 3, 3);
                var after = _texture.GetPixels32();
                for (int py = 0; py < 2; py++)
                {
                    for (int px = 0; px < 2; px++)
                    {
                        long sourceX = (long)px - x,
                            sourceY = (long)py - y;
                        var expected =
                            sourceX >= 0 && sourceX < 3 && sourceY >= 0 && sourceY < 3
                                ? new Color32((byte)(sourceY * 3 + sourceX + 1), 10, 20, 255)
                                : new Color32(20, 30, 40, 255);
                        Assert.AreEqual(expected, after[py * 2 + px]);
                    }
                }
            }
        }

        [TestCase(-1)]
        [TestCase(int.MaxValue)]
        public void InvalidInvisibleColorsStillRejectBeforeAnyPixelChanges(int x)
        {
            var before = _texture.GetPixels32();
            Assert.Catch(() => TextureOps.ApplyPixelDataToRegion(_texture, JArray.Parse("[['bad',2,3],[4,5,6]]"), x, 0, 2, 1));
            CollectionAssert.AreEqual(before, _texture.GetPixels32());
        }

        [TestCase(TextureFormat.RGB24)]
        [TestCase(TextureFormat.ARGB32)]
        [TestCase(TextureFormat.RGBA32)]
        public void PixelRegionsWorkWithLoadedPngAndJpegFormats(TextureFormat format)
        {
            var texture = new Texture2D(2, 2, format, false);
            try
            {
                foreach (
                    var payload in new JToken[]
                    {
                        JArray.Parse("[[1,2,3,255],[4,5,6,255]]"),
                        new JValue("base64:" + System.Convert.ToBase64String(new byte[] { 1, 2, 3, 255, 4, 5, 6, 255 })),
                    }
                )
                {
                    TextureOps.ApplyPixelDataToRegion(texture, payload, 1, 0, 1, 2);
                    var after = texture.GetPixels32();
                    Assert.AreEqual(new Color32(1, 2, 3, 255), after[1]);
                    Assert.AreEqual(new Color32(4, 5, 6, 255), after[3]);
                }
            }
            finally
            {
                Object.DestroyImmediate(texture);
            }
        }

        [TestCase("en-US", "CHECKERBOARD")]
        [TestCase("en-US", "cHeCkErBoArD")]
        [TestCase("en-US", "STRIPES_H")]
        [TestCase("en-US", "UNKNOWN")]
        [TestCase("tr-TR", "STRIPES")]
        [TestCase("tr-TR", "GRID")]
        public void PatternApplicationPreservesCurrentCultureCasingAndUnknownFallback(string culture, string pattern)
        {
            var previous = System.Globalization.CultureInfo.CurrentCulture;
            try
            {
                System.Globalization.CultureInfo.CurrentCulture = System.Globalization.CultureInfo.GetCultureInfo(culture);
                var method = typeof(ManageTexture).GetMethod(
                    "ApplyPatternToTexture",
                    System.Reflection.BindingFlags.NonPublic | System.Reflection.BindingFlags.Static
                );
                Assert.IsNotNull(method);
                var palette = new System.Collections.Generic.List<Color32> { new Color32(20, 30, 40, 255), new Color32(255, 0, 0, 255) };
                method.Invoke(null, new object[] { _texture, pattern.ToLower(), palette, 1 });
                var expected = _texture.GetPixels32();

                method.Invoke(null, new object[] { _texture, pattern, palette, 1 });

                CollectionAssert.AreEqual(expected, _texture.GetPixels32());
            }
            finally
            {
                System.Globalization.CultureInfo.CurrentCulture = previous;
            }
        }

        [Test]
        public void NullPatternApplicationStillRejectsBeforeChangingPixels()
        {
            var before = _texture.GetPixels32();
            var method = typeof(ManageTexture).GetMethod(
                "ApplyPatternToTexture",
                System.Reflection.BindingFlags.NonPublic | System.Reflection.BindingFlags.Static
            );
            Assert.IsNotNull(method);
            var palette = new System.Collections.Generic.List<Color32> { new Color32(255, 0, 0, 255) };

            var error = Assert.Throws<System.Reflection.TargetInvocationException>(() => method.Invoke(null, new object[] { _texture, null, palette, 1 }));

            Assert.IsInstanceOf<System.NullReferenceException>(error.InnerException);
            CollectionAssert.AreEqual(before, _texture.GetPixels32());
        }

        [TestCase(32768)]
        [TestCase(46341)]
        [TestCase(int.MaxValue)]
        public void LargeDotSizesDoNotOverflowDistanceCalculations(int size)
        {
            var method = typeof(ManageTexture).GetMethod("GetPatternColor", System.Reflection.BindingFlags.NonPublic | System.Reflection.BindingFlags.Static);
            var background = new Color32(20, 30, 40, 255);
            var palette = new System.Collections.Generic.List<Color32> { background, new Color32(255, 0, 0, 255) };
            var actual = method.Invoke(null, new object[] { 0, 0, "dots", palette, size, 2, 2 });
            Assert.AreEqual(background, actual);
        }
    }
}
