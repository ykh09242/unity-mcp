using System;
using System.IO;
using System.Reflection;
using MCPForUnity.Editor.Tools;
using MCPForUnity.Editor.Tools.Recording;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEngine;
using UnityEngine.Rendering;

namespace MCPForUnityTests.EditMode.Tools
{
    /// <summary>Baseline-compatible raster and real MP4 fixtures. No scene or package mutation.</summary>
    public class RecordingAspectModeTests
    {
        private Texture2D _source;

        [TearDown]
        public void ReleaseOwnedTextures()
        {
            typeof(ManageRecording).GetMethod("Cleanup", BindingFlags.NonPublic | BindingFlags.Static).Invoke(null, null);
            if (_source != null)
                UnityEngine.Object.DestroyImmediate(_source);
        }

        [TestCase("stretch")]
        [TestCase("fit")]
        [TestCase("crop")]
        public void PortraitCheckerboardAndCirclePreserveRequestedGeometry(string mode)
        {
            // Given a 9:16 checkerboard, centered circle and distinctive outer horizontal bands.
            RequireGraphics();
            _source = MakePortrait();
            var options = Options(mode);
            var previous = RenderTexture.active;
            // When the production recording resize seam renders a 16:9 frame.
            var output = Scale(options);
            // Then fit pads in black, crop drops the bands, and stretch keeps its original distortion.
            AssertGeometry(output, mode);
            Assert.That(RenderTexture.active, Is.EqualTo(previous));
        }

        [TestCase("fit")]
        [TestCase("crop")]
        [Category("NativeMediaEncoder")]
        public void AspectFramesPublishTimestampedFinalMp4(string mode)
        {
            RequireGraphics();
            if (Application.platform != RuntimePlatform.WindowsEditor && Application.platform != RuntimePlatform.OSXEditor)
                Assert.Ignore("Native MP4 requires Windows or macOS Editor.");
            _source = MakePortrait();
            var options = Options(mode);
            string projectRoot = Path.GetFullPath(Path.Combine(Application.dataPath, ".."));
            string folder = "Captures/Recordings/aspect-qa-" + Guid.NewGuid().ToString("N");
            string output = RecordingOptions.ResolveOutputPath(projectRoot, folder, mode + ".mp4", "aspect");
            var job = new RecordingJob("aspect", options, projectRoot, output, 100, (path, config) => new UnityRecordingEncoder(path, config));
            try
            {
                // Given real source pixels and the normal encoder/job staging lifecycle.
                var frame = Scale(options);
                AssertGeometry(frame, mode);
                // When four deliberately irregular capture timestamps are encoded and finalized.
                foreach (double elapsed in new[] { 0.0, 0.12, 0.37, 0.61 })
                    job.AddFrame(frame, _source.width, _source.height, 100 + elapsed);
                job.Tick(101);
                // Then retain actual output for independent decode/timestamp validation by the runner.
                Assert.That(job.Status, Is.EqualTo("completed"), job.Snapshot(101).ToString());
                Assert.That(job.Frames, Is.EqualTo(4));
                Assert.That(job.Snapshot(101).Value<double>("last_frame_seconds"), Is.EqualTo(0.61).Within(0.000001));
                ManageRecordingTests.AssertFinalizedMp4(File.ReadAllBytes(output));
                File.WriteAllBytes(Path.Combine(Path.GetDirectoryName(output), "expected.png"), frame.EncodeToPNG());
                File.WriteAllText(Path.Combine(Path.GetDirectoryName(output), "evidence.json"), job.Snapshot(101).ToString());
                Assert.That(Directory.GetDirectories(Path.GetDirectoryName(output)), Is.Empty);
                TestContext.WriteLine("PLAY19_MP4=" + output);
            }
            finally
            {
                job.Finish("interrupted", "fixture_cleanup", 101);
            }
        }

        private static RecordingOptions Options(string mode)
        {
            // Reflection deliberately allows this fixture to compile against the unmodified baseline.
            var constructor = typeof(RecordingOptions).GetConstructor(
                BindingFlags.Instance | BindingFlags.NonPublic,
                null,
                new[] { typeof(string), typeof(int), typeof(int), typeof(int), typeof(double), typeof(string) },
                null
            );
            return constructor == null
                ? new RecordingOptions("scene_view", 320, 180, 10, 1)
                : (RecordingOptions)constructor.Invoke(new object[] { "scene_view", 320, 180, 10, 1.0, mode });
        }

        private Texture2D Scale(RecordingOptions options) =>
            (Texture2D)
                typeof(ManageRecording).GetMethod("ScaleFrame", BindingFlags.NonPublic | BindingFlags.Static).Invoke(null, new object[] { _source, options });

        private static void RequireGraphics()
        {
            Assert.That(SystemInfo.graphicsDeviceType, Is.Not.EqualTo(GraphicsDeviceType.Null), "Run this fixture with graphics enabled (no -nographics).");
        }

        private static Texture2D MakePortrait()
        {
            var texture = new Texture2D(180, 320, TextureFormat.RGBA32, false);
            var pixels = new Color32[180 * 320];
            for (int y = 0; y < 320; y++)
            for (int x = 0; x < 180; x++)
            {
                Color32 color = ((x / 20 + y / 20) % 2 == 0) ? new Color32(220, 220, 220, 255) : new Color32(100, 100, 100, 255);
                if (y < 24 || y >= 296)
                    color = new Color32(0, 0, 255, 255);
                if ((x - 90) * (x - 90) + (y - 160) * (y - 160) <= 30 * 30)
                    color = new Color32(255, 0, 0, 255);
                pixels[y * 180 + x] = color;
            }
            texture.SetPixels32(pixels);
            texture.Apply(false, false);
            return texture;
        }

        private static void AssertGeometry(Texture2D output, string mode)
        {
            Assert.That(output.width, Is.EqualTo(320));
            Assert.That(output.height, Is.EqualTo(180));
            int minX = 320,
                maxX = -1,
                minY = 180,
                maxY = -1;
            for (int y = 0; y < 180; y++)
            for (int x = 0; x < 320; x++)
            {
                Color color = output.GetPixel(x, y);
                if (color.r > 0.7f && color.g < 0.15f && color.b < 0.15f)
                {
                    minX = Math.Min(x, minX);
                    maxX = Math.Max(x, maxX);
                    minY = Math.Min(y, minY);
                    maxY = Math.Max(y, maxY);
                }
            }
            int diameterX = maxX - minX + 1,
                diameterY = maxY - minY + 1;
            Assert.That(diameterX, Is.GreaterThan(20), "The center circle must reach the encoded frame.");
            if (mode == "stretch")
                Assert.That((float)diameterX / diameterY, Is.GreaterThan(2.8f));
            else
                Assert.That(diameterX, Is.EqualTo(diameterY).Within(2), "Aspect preservation keeps the circle round.");
            Color edge = output.GetPixel(10, 90);
            Color band = output.GetPixel(160, 4);
            if (mode == "fit")
            {
                Assert.That(Math.Max(edge.r, Math.Max(edge.g, edge.b)), Is.LessThan(0.02f), "Portrait fit requires black side padding.");
                Assert.That(band.b, Is.GreaterThan(0.7f), "Fit retains the full source's outer bands.");
            }
            else
            {
                Assert.That(Math.Max(edge.r, Math.Max(edge.g, edge.b)), Is.GreaterThan(0.1f));
                if (mode == "crop")
                    Assert.That(band.b - band.r, Is.LessThan(0.1f), "Centered crop excludes the source's outer blue bands.");
            }
        }
    }
}
