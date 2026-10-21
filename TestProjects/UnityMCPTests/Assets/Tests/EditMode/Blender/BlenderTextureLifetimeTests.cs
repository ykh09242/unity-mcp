using System.Collections.Generic;
using System.Linq;
using System.Reflection;
using MCPForUnity.Editor.Tools.Blender;
using NUnit.Framework;
using UnityEngine;

namespace MCPForUnityTests.Editor.Blender
{
    [Parallelizable(ParallelScope.None)]
    public class BlenderTextureLifetimeTests
    {
        [TestCase(TextureFormat.RGBA32)]
        [TestCase(TextureFormat.RGB24)]
        public void ResamplePreservesBilinearPixelsWithoutAnImageSizedFloatArray(TextureFormat format)
        {
            var source = new Texture2D(512, 512, format, false);
            var expected = new Texture2D(256, 256, TextureFormat.RGBA32, false);
            Texture2D destination = null;
            try
            {
                var sourceColors = new Color32[512 * 512];
                for (int i = 0; i < sourceColors.Length; i++)
                    sourceColors[i] = new Color32((byte)i, (byte)(i / 512), 97, (byte)(i % 233));
                source.SetPixels32(sourceColors);
                source.Apply();
                var reference = new Color[256 * 256];
                for (int y = 0; y < 256; y++)
                for (int x = 0; x < 256; x++)
                    reference[y * 256 + x] = source.GetPixelBilinear((x + 0.5f) / 256, (y + 0.5f) / 256);
                expected.SetPixels(reference);
                expected.Apply();
                destination = Scale(source, 256);
                Object.DestroyImmediate(destination);
                var scale = (System.Func<Texture2D, int, Texture2D>)
                    System.Delegate.CreateDelegate(
                        typeof(System.Func<Texture2D, int, Texture2D>),
                        typeof(BlenderBridgeTool).GetMethod("ScaleToHeight", BindingFlags.Static | BindingFlags.NonPublic)
                    );
                Assert.Greater(ManagedAllocation.Count(() => System.GC.KeepAlive(new byte[65536])), 0);
                int baseline = ManagedAllocation.Count(() => destination = new Texture2D(256, 256, TextureFormat.RGBA32, false));
                Object.DestroyImmediate(destination);
                int allocated = ManagedAllocation.Count(() => destination = scale(source, 256));
                Assert.That(allocated, Is.LessThanOrEqualTo(baseline), "Resampling should only allocate the destination wrapper.");
                CollectionAssert.AreEqual(expected.GetPixels32(), destination.GetPixels32());
            }
            finally
            {
                if (destination != null)
                    Object.DestroyImmediate(destination);
                Object.DestroyImmediate(expected);
                Object.DestroyImmediate(source);
            }
        }

        [TestCase(TextureFormat.RGB24)]
        [TestCase(TextureFormat.RGBA32)]
        public void CompositionPreservesRowOrderAlphaAndOwnedTextures(TextureFormat format)
        {
            string folder = System.IO.Path.GetFullPath(
                System.IO.Path.Combine(Application.dataPath, "../Temp/McpComposite_" + System.Guid.NewGuid().ToString("N"))
            );
            var left = new Texture2D(2, 2, format, false);
            var right = new Texture2D(1, 2, TextureFormat.RGBA32, false);
            var output = new Texture2D(2, 2, TextureFormat.RGBA32, false);
            try
            {
                left.SetPixels32(new[] { new Color32(1, 2, 3, 4), new Color32(5, 6, 7, 8), new Color32(9, 10, 11, 12), new Color32(13, 14, 15, 16) });
                right.SetPixels32(new[] { new Color32(17, 18, 19, 20), new Color32(21, 22, 23, 24) });
                left.Apply();
                right.Apply();
                System.IO.Directory.CreateDirectory(folder);
                string l = System.IO.Path.Combine(folder, "left.png"),
                    r = System.IO.Path.Combine(folder, "right.png"),
                    o = System.IO.Path.Combine(folder, "out.png");
                System.IO.File.WriteAllBytes(l, left.EncodeToPNG());
                System.IO.File.WriteAllBytes(r, right.EncodeToPNG());
                var before = SnapshotTextures();
                Assert.AreEqual((3, 2), BlenderBridgeTool.CompositeSideBySide(l, r, o));
                CollectionAssert.AreEquivalent(before, SnapshotTextures());
                Assert.IsTrue(ImageConversion.LoadImage(output, System.IO.File.ReadAllBytes(o)));
                var lp = left.GetPixels32();
                var rp = right.GetPixels32();
                CollectionAssert.AreEqual(new[] { lp[0], lp[1], rp[0], lp[2], lp[3], rp[1] }, output.GetPixels32());
            }
            finally
            {
                Object.DestroyImmediate(left);
                Object.DestroyImmediate(right);
                Object.DestroyImmediate(output);
                if (System.IO.Directory.Exists(folder))
                    System.IO.Directory.Delete(folder, true);
            }
        }

        [Test]
        public void FailedResampleReleasesItsUnreturnedDestination()
        {
            var source = new Texture2D(8, 8, TextureFormat.RGBA32, false);
            HashSet<Texture2D> before = null;
            try
            {
                source.Apply(false, true);
                Assert.IsFalse(source.isReadable);
                before = SnapshotTextures();
                for (int i = 0; i < 3; i++)
                {
                    var error = Assert.Throws<TargetInvocationException>(() => Scale(source, 4));
                    Assert.IsInstanceOf<UnityException>(error.InnerException);
                }
                CollectionAssert.AreEquivalent(before, SnapshotTextures());
                Assert.IsTrue(source != null, "Source ownership stays with the caller.");
            }
            finally
            {
                // Also clean failed-baseline destinations so a red test cannot pollute later tests.
                if (before != null)
                    foreach (var texture in UnityEngine.Resources.FindObjectsOfTypeAll<Texture2D>().Where(texture => !before.Contains(texture)))
                        Object.DestroyImmediate(texture);
                Object.DestroyImmediate(source);
            }
        }

        [Test]
        public void SameHeightReturnsTheCallerOwnedSource()
        {
            var source = new Texture2D(8, 8, TextureFormat.RGBA32, false);
            try
            {
                Assert.AreSame(source, Scale(source, 8));
            }
            finally
            {
                Object.DestroyImmediate(source);
            }
        }

        [Test]
        public void SuccessfulResampleTransfersDestinationOwnership()
        {
            var source = new Texture2D(8, 8, TextureFormat.RGBA32, false);
            Texture2D destination = null;
            try
            {
                destination = Scale(source, 4);
                Assert.AreNotSame(source, destination);
                Assert.AreEqual(4, destination.width);
                Assert.AreEqual(4, destination.height);
                Assert.AreEqual(8, source.height);
            }
            finally
            {
                if (destination != null)
                    Object.DestroyImmediate(destination);
                Object.DestroyImmediate(source);
            }
        }

        private static Texture2D Scale(Texture2D source, int height) =>
            (Texture2D)
                typeof(BlenderBridgeTool)
                    .GetMethod("ScaleToHeight", BindingFlags.Static | BindingFlags.NonPublic)
                    .Invoke(null, new object[] { source, height });

        private static HashSet<Texture2D> SnapshotTextures() => new HashSet<Texture2D>(UnityEngine.Resources.FindObjectsOfTypeAll<Texture2D>());
    }
}
