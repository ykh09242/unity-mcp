using System.Collections;
using System.Collections.Generic;
using MCPForUnity.Runtime.Helpers;
using NUnit.Framework;
using UnityEngine;
using UnityEngine.Rendering;
using UnityEngine.TestTools;

namespace MCPForUnityTests.PlayMode
{
    public class ScreenshotAllocationTests
    {
        [UnityTest]
        public IEnumerator PreviewBatchDoesNotRetainFullSizeCpuTexturesUntilTheFrameEnds()
        {
            if (SystemInfo.graphicsDeviceType == GraphicsDeviceType.Null)
                Assert.Ignore("Camera preview requires a graphics device.");
            var owner = new GameObject("OwnedPreviewAllocationCamera");
            var camera = owner.AddComponent<Camera>();
            camera.enabled = false;
            camera.clearFlags = CameraClearFlags.SolidColor;
            camera.cullingMask = 0;
            var source = RenderTexture.GetTemporary(512, 256, 24);
            camera.targetTexture = source;
            var before = new HashSet<int>();
            foreach (var texture in Resources.FindObjectsOfTypeAll<Texture2D>())
                before.Add(texture.GetInstanceID());
            var previews = new List<Texture2D>();
            try
            {
                for (int i = 0; i < 4; i++)
                    previews.Add(ScreenshotUtility.RenderCameraToTexture(camera, 64));
                foreach (var texture in Resources.FindObjectsOfTypeAll<Texture2D>())
                    if (!before.Contains(texture.GetInstanceID()))
                        Assert.IsFalse(texture.width == 512 && texture.height == 256, "A preview must never allocate a full-resolution CPU readback texture.");
                Assert.AreEqual(4, previews.Count);
                Assert.IsTrue(previews.TrueForAll(texture => texture.width == 64 && texture.height == 32));
                Assert.AreSame(source, camera.targetTexture);
            }
            finally
            {
                camera.targetTexture = null;
                RenderTexture.ReleaseTemporary(source);
                foreach (var texture in previews)
                    if (texture != null)
                        Object.Destroy(texture);
                Object.Destroy(owner);
            }
            yield return null;
        }
    }
}
