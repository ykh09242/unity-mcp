using MCPForUnity.Editor.Helpers;
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
            if (_texture != null) Object.DestroyImmediate(_texture);
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
            Assert.Catch(() => TextureOps.ApplyPixelDataToRegion(
                _texture, JToken.Parse(json), 0, 0, 2, 1));
            CollectionAssert.AreEqual(before, _texture.GetPixels32());
        }

        [Test]
        public void NegativeOriginUsesTheCorrectSourcePixel()
        {
            TextureOps.ApplyPixelDataToRegion(_texture,
                JArray.Parse("[[1,2,3],[4,5,6]]"), -1, 0, 2, 1);
            var after = _texture.GetPixels32();
            Assert.AreEqual(new Color32(4, 5, 6, 255), after[0]);
            Assert.AreEqual(new Color32(20, 30, 40, 255), after[1]);
        }

        [Test]
        public void FullyOutsideRegionPreservesEveryPixel()
        {
            var before = _texture.GetPixels32();
            TextureOps.ApplyPixelDataToRegion(_texture,
                JArray.Parse("[[1,2,3]]"), int.MaxValue, 0, 1, 1);
            CollectionAssert.AreEqual(before, _texture.GetPixels32());
        }

        [Test]
        public void OverflowingRequestedCountRejectsBeforeAnyPixelChanges()
        {
            var before = _texture.GetPixels32();
            Assert.Catch(() => TextureOps.ApplyPixelDataToRegion(_texture,
                JArray.Parse("[[1,2,3]]"), 0, 0, int.MaxValue, int.MaxValue));
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
    }
}
