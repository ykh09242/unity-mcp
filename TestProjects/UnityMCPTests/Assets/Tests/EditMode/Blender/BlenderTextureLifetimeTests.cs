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
