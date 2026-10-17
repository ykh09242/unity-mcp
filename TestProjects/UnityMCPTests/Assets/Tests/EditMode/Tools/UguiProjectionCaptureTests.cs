using System;
using System.Collections;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Editor.Tools;
using MCPForUnity.Runtime.Helpers;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEditor;
using UnityEngine;
using UnityEngine.TestTools;

namespace MCPForUnityTests.EditMode.Tools
{
    [Parallelizable(ParallelScope.None)]
    public class UguiProjectionCaptureTests
    {
        private readonly List<UnityEngine.Object> owned = new List<UnityEngine.Object>();
        private GameObject root;
        private Camera camera;
        private RenderTexture target;
        private int pixelChecks;
        private int renderCallbacks;
        private Camera.CameraCallback renderCallback;

        private void Build(bool backbuffer = false)
        {
            renderCallbacks = 0;
            pixelChecks = 0;
            var cameraObject = new GameObject("UI07CaptureCamera", typeof(Camera));
            owned.Add(cameraObject);
            camera = cameraObject.GetComponent<Camera>();
            renderCallback = observed =>
            {
                if (observed == camera)
                    renderCallbacks++;
            };
            Camera.onPreCull += renderCallback;
            camera.enabled = backbuffer;
            camera.clearFlags = CameraClearFlags.SolidColor;
            camera.backgroundColor = Color.black;
            camera.allowHDR = false;
            camera.allowMSAA = false;
            camera.cullingMask = 1 << 30;
            camera.nearClipPlane = 0.3f;
            camera.farClipPlane = 30;
            camera.fieldOfView = 60;
            if (!backbuffer)
            {
                target = new RenderTexture(320, 240, 24, RenderTextureFormat.ARGB32);
                owned.Add(target);
                Assert.IsTrue(target.Create(), "Owned graphics prerequisite.");
                camera.targetTexture = target;
                camera.aspect = 4f / 3f;
            }
            root = new GameObject("UI07CaptureCanvas", typeof(RectTransform), typeof(Canvas));
            owned.Add(root);
            root.layer = 30;
            root.GetComponent<Canvas>().renderMode = RenderMode.WorldSpace;
            ((RectTransform)root.transform).sizeDelta = new Vector2(2, 2);
            root.transform.position = new Vector3(0, 0, 5);
            var quad = new GameObject("ColoredQuad", typeof(RectTransform));
            quad.layer = 30;
            quad.transform.SetParent(root.transform, false);
            ((RectTransform)quad.transform).sizeDelta = new Vector2(1.6f, 0.8f);
            var imageType = UnityTypeResolver.ResolveComponent("UnityEngine.UI.Image");
            Assert.IsNotNull(imageType, "The mandatory pixel fixture requires installed uGUI.");
            var image = quad.AddComponent(imageType);
            imageType.GetProperty("color").SetValue(image, Color.red);
            Canvas.ForceUpdateCanvases();
        }

        [TearDown]
        public void TearDown()
        {
            try
            {
                foreach (var value in owned.AsEnumerable().Reverse())
                    if (value != null)
                        UnityEngine.Object.DestroyImmediate(value);
            }
            finally
            {
                if (renderCallback != null)
                    Camera.onPreCull -= renderCallback;
                renderCallback = null;
                owned.Clear();
            }
        }

        [UnityTearDown]
        public IEnumerator LeaveOwnedPlayMode()
        {
            if (Application.isPlaying)
            {
                TearDown();
                yield return new ExitPlayMode();
            }
        }

        private JObject Diagnose()
        {
            var r = JObject.FromObject(
                ManageUGUI.HandleCommand(
                    new JObject
                    {
                        ["action"] = "diagnose_projection",
                        ["target"] = root.GetInstanceIDCompat(),
                        ["camera"] = camera.gameObject.GetInstanceIDCompat(),
                    }
                )
            );
            Assert.IsTrue(r.Value<bool>("success"), r.ToString());
            return (JObject)r["data"];
        }

        private JObject Snapshot()
        {
            var r = JObject.FromObject(CaptureUISnapshot.HandleCommand(new JObject { ["framework"] = "ugui", ["target"] = root.GetInstanceIDCompat() }));
            Assert.IsTrue(r.Value<bool>("success"), r.ToString());
            return (JObject)r["data"];
        }

        private static string ExportDirectory()
        {
            string configured = Environment.GetEnvironmentVariable("UNITY_MCP_UI07_EXPORT_ROOT");
            if (configured == null)
                return Path.GetTempPath(); // Preserve ordinary non-QA test behavior.
            string project = Path.GetFullPath(Path.GetDirectoryName(Application.dataPath));
            string expected = Path.GetFullPath(Path.Combine(project, "..", "fixture-exports"));
            string actual = Path.GetFullPath(configured);
            Assert.IsTrue(string.Equals(expected, actual, StringComparison.OrdinalIgnoreCase), "Exact owned fixture export directory required.");
            Assert.IsTrue(
                string.Equals(project, Path.GetFullPath(Environment.GetEnvironmentVariable("UNITY_MCP_NATIVE_QA_PROJECT")), StringComparison.OrdinalIgnoreCase),
                "Exact native QA project binding required."
            );
            Assert.IsTrue(Directory.Exists(actual), "The fresh wrapper must precreate the owned fixture export directory.");
            for (string current = actual; current != null; current = Path.GetDirectoryName(current))
                if (Directory.Exists(current))
                    Assert.IsFalse((File.GetAttributes(current) & FileAttributes.ReparsePoint) != 0, "Linked fixture export directory refused.");
            return actual;
        }

        private static void Export(string name, JObject data) =>
            File.WriteAllText(Path.Combine(ExportDirectory(), "ui07-" + name + ".json"), data.ToString(Newtonsoft.Json.Formatting.None));

        private Texture2D ReadTarget(string exportName = null)
        {
            var previous = RenderTexture.active;
            var image = new Texture2D(target.width, target.height, TextureFormat.RGBA32, false);
            owned.Add(image);
            try
            {
                camera.Render();
                RenderTexture.active = target;
                image.ReadPixels(new Rect(0, 0, target.width, target.height), 0, 0);
                image.Apply();
            }
            finally
            {
                RenderTexture.active = previous;
            }
            if (exportName != null)
                File.WriteAllBytes(Path.Combine(ExportDirectory(), "ui07-" + exportName + ".png"), image.EncodeToPNG());
            return image;
        }

        private static Rect? RedBounds(Texture2D image)
        {
            Assert.LessOrEqual(image.width * image.height, 512 * 512);
            var pixels = image.GetPixels32();
            int minX = image.width,
                minY = image.height,
                maxX = -1,
                maxY = -1;
            for (int y = 0; y < image.height; y++)
            for (int x = 0; x < image.width; x++)
            {
                var c = pixels[y * image.width + x];
                if (!IsRed(c))
                    continue;
                minX = Math.Min(minX, x);
                minY = Math.Min(minY, y);
                maxX = Math.Max(maxX, x);
                maxY = Math.Max(maxY, y);
            }
            return maxX < 0 ? (Rect?)null : Rect.MinMaxRect(minX, minY, maxX + 1, maxY + 1);
        }

        private Rect ComparePixels(JObject data, Texture2D image)
        {
            var actual = RedBounds(image);
            Assert.IsTrue(actual.HasValue, "Mandatory colored render control must be nonempty.");
            var node = data["nodes"].Single(n => n.Value<string>("name") == "ColoredQuad");
            var b = node["clipped_target_pixel_bounds"].Values<double>().ToArray();
            Assert.AreEqual(4, b.Length);
            Assert.That(actual.Value.xMin, Is.EqualTo(b[0]).Within(2));
            Assert.That(actual.Value.yMin, Is.EqualTo(b[1]).Within(2));
            Assert.That(actual.Value.width, Is.EqualTo(b[2]).Within(2));
            Assert.That(actual.Value.height, Is.EqualTo(b[3]).Within(2));
            CheckPolygonPixels(data, node, image);
            return actual.Value;
        }

        private static bool IsRed(Color32 pixel) => pixel.r >= 180 && pixel.g <= 50 && pixel.b <= 50;

        private void CheckPolygonPixels(JObject data, JToken node, Texture2D image)
        {
            var viewport = data["context"]["output"]["viewport_pixels"].Values<double>().ToArray();
            var polygon = node["clipped_viewport_polygon"]
                .Select(p => new[] { viewport[0] + p[0].Value<double>() * viewport[2], viewport[1] + p[1].Value<double>() * viewport[3] })
                .ToArray();
            Assert.That(polygon.Length, Is.InRange(3, 10));
            var pixels = image.GetPixels32();
            int interior = 0,
                exterior = 0,
                excluded = 0,
                mismatches = 0;
            var firstMismatches = new JArray();
            for (int y = 0; y < image.height; y++)
            for (int x = 0; x < image.width; x++)
            {
                double px = x + 0.5,
                    py = y + 0.5,
                    distance = double.PositiveInfinity;
                bool inside = false;
                for (int i = 0; i < polygon.Length; i++)
                {
                    var a = polygon[i];
                    var b = polygon[(i + 1) % polygon.Length];
                    double dx = b[0] - a[0],
                        dy = b[1] - a[1],
                        length = dx * dx + dy * dy;
                    double t = length == 0 ? 0 : Math.Max(0, Math.Min(1, ((px - a[0]) * dx + (py - a[1]) * dy) / length));
                    double ox = px - a[0] - t * dx,
                        oy = py - a[1] - t * dy;
                    distance = Math.Min(distance, ox * ox + oy * oy);
                    if ((a[1] > py) != (b[1] > py) && px < (b[0] - a[0]) * (py - a[1]) / (b[1] - a[1]) + a[0])
                        inside = !inside;
                }
                if (distance < 4)
                {
                    excluded++;
                    continue;
                }
                if (inside)
                    interior++;
                else
                    exterior++;
                bool red = IsRed(pixels[y * image.width + x]);
                if (inside != red)
                {
                    mismatches++;
                    if (firstMismatches.Count < 16)
                        firstMismatches.Add(
                            new JObject
                            {
                                ["x"] = x,
                                ["y"] = y,
                                ["expected_red"] = inside,
                                ["actual_red"] = red,
                            }
                        );
                }
            }
            var proof = new JObject
            {
                ["test"] = TestContext.CurrentContext.Test.FullName,
                ["sequence"] = pixelChecks++,
                ["width"] = image.width,
                ["height"] = image.height,
                ["edge_exclusion_pixels"] = 2,
                ["interior_checked"] = interior,
                ["exterior_checked"] = exterior,
                ["edge_excluded"] = excluded,
                ["mismatches"] = mismatches,
                ["first_mismatches"] = firstMismatches,
                ["target_polygon"] = JArray.FromObject(polygon),
                ["node"] = node.DeepClone(),
                ["output"] = data["context"]["output"].DeepClone(),
            };
            File.WriteAllText(Path.Combine(ExportDirectory(), "ui07pixel-" + Guid.NewGuid().ToString("N") + ".json"), proof.ToString());
            Assert.Greater(interior, 0, "Actual capture must contain representative interior samples at least two pixels from edges.");
            Assert.AreEqual(0, mismatches, proof.ToString());
        }

        [Test]
        public void PixelOracleRejectsDifferentPolygonWithSameBoundsAfterActualRenderControl()
        {
            Build();
            var data = Diagnose();
            var image = ReadTarget();
            ComparePixels(data, image);
            var node = data["nodes"].Single(n => n.Value<string>("name") == "ColoredQuad");
            var original = node["clipped_viewport_polygon"].Select(p => p.DeepClone()).ToArray();
            Assert.AreEqual(4, original.Length);
            node["clipped_viewport_polygon"] = new JArray(original[0], original[1], original[2]);
            Assert.Throws<AssertionException>(
                () => ComparePixels(data, image),
                "A different polygon sharing the old AABB must fail the independent raster oracle."
            );
        }

        [Test]
        public void DiagnosisInvokesNeitherArmedRenderNorCanvasLayoutCallbacks()
        {
            Build();
            int layouts = 0;
            Canvas.WillRenderCanvases callback = () => layouts++;
            Canvas.willRenderCanvases += callback;
            try
            {
                Canvas.ForceUpdateCanvases();
                Assert.Greater(layouts, 0, "Canvas callback positive control.");
                Assert.IsTrue(RedBounds(ReadTarget()).HasValue, "Actual render prerequisite.");
                Assert.Greater(renderCallbacks, 0, "Current camera callback positive control.");
                layouts = 0;
                renderCallbacks = 0;
                var d = Diagnose();
                Assert.AreEqual(0, layouts, "Diagnosis must not force Canvas layout/render callbacks.");
                Assert.AreEqual(0, renderCallbacks, "Diagnosis must not render.");
                Export("callback-invariant", d);
            }
            finally
            {
                Canvas.willRenderCanvases -= callback;
            }
        }

        [TestCase(false)]
        [TestCase(true)]
        public void CameraMotionChangesActualPixelsWhileWorldSnapshotStaysFixed(bool orthographic)
        {
            Build();
            camera.orthographic = orthographic;
            camera.orthographicSize = 3;
            Assert.IsTrue(RedBounds(ReadTarget()).HasValue, "Independent render prerequisite.");
            Assert.Greater(renderCallbacks, 0, "Callback positive control.");
            renderCallbacks = 0;
            var before = Diagnose();
            Assert.AreEqual(0, renderCallbacks, "Diagnosis must not render.");
            var snapshotBefore = Snapshot();
            var pixelsBefore = ComparePixels(before, ReadTarget(orthographic ? "capture-ortho-before" : "capture-perspective-before"));
            camera.transform.position = new Vector3(0.4f, 0, 0);
            camera.transform.rotation = Quaternion.Euler(0, 3, 0);
            var after = Diagnose();
            var pixelsAfter = ComparePixels(after, ReadTarget(orthographic ? "capture-ortho-after" : "capture-perspective-after"));
            Assert.Less(pixelsAfter.center.x, pixelsBefore.center.x);
            var snapshotAfter = Snapshot();
            Assert.IsTrue(JToken.DeepEquals(snapshotBefore, snapshotAfter), "Camera-only changes do not reproject world-XY snapshots.");
            Export(orthographic ? "capture-ortho-before" : "capture-perspective-before", before);
            Export(orthographic ? "capture-ortho-after" : "capture-perspective-after", after);
            Export(orthographic ? "snapshot-ortho-before" : "snapshot-perspective-before", snapshotBefore);
            Export(orthographic ? "snapshot-ortho-after" : "snapshot-perspective-after", snapshotAfter);
        }

        [Test]
        public void NonFullTextureViewportWithholdsPixelsAfterIndependentRenderControl()
        {
            Build();
            ComparePixels(Diagnose(), ReadTarget()); // Preserve the independent full-viewport raster oracle.
            camera.rect = new Rect(0.1f, 0.2f, 0.4f, 0.5f);
            var image = ReadTarget("capture-ignored-viewport");
            var actual = RedBounds(image);
            Assert.IsTrue(actual.HasValue, "Independent non-full viewport render remains nonempty.");
            var d = Diagnose();
            var output = d["context"]["output"];
            Export(
                "capture-nonfull-native",
                new JObject
                {
                    ["camera_rect"] = JArray.FromObject(new[] { camera.rect.x, camera.rect.y, camera.rect.width, camera.rect.height }),
                    ["camera_pixel_rect"] = JArray.FromObject(
                        new[] { camera.pixelRect.x, camera.pixelRect.y, camera.pixelRect.width, camera.pixelRect.height }
                    ),
                    ["actual_red_bounds"] = JArray.FromObject(new[] { actual.Value.x, actual.Value.y, actual.Value.width, actual.Value.height }),
                    ["payload"] = d.DeepClone(),
                }
            );
            Assert.AreEqual("partial", d.Value<string>("status"));
            Assert.AreEqual("unavailable", output.Value<string>("pixel_status"));
            Assert.AreEqual(JTokenType.Null, output["viewport_pixels"].Type);
            Assert.AreEqual(JTokenType.Null, output["camera_rect_ignored"].Type);
            var node = d["nodes"].Single(n => n.Value<string>("name") == "ColoredQuad");
            Assert.That(node["clipped_viewport_polygon"].Count(), Is.InRange(3, 10));
            Assert.AreEqual("unavailable", node.Value<string>("pixel_status"));
            Assert.AreEqual(JTokenType.Null, node["clipped_target_pixel_bounds"].Type);
            Export("capture-ignored-viewport", d);
        }

        [TestCase("side")]
        [TestCase("near")]
        public void PartialQuadMatchesActualClippedColorRegion(string kind)
        {
            Build();
            Assert.IsTrue(RedBounds(ReadTarget()).HasValue, "Unclipped render prerequisite.");
            if (kind == "side")
                root.transform.position = new Vector3(3.4f, 0, 5);
            else
            {
                root.transform.position = new Vector3(0, 0, 0.35f);
                root.transform.rotation = Quaternion.Euler(0, 65, 0);
            }
            var d = Diagnose();
            Assert.AreEqual("partial", d["nodes"].Single(n => n.Value<string>("name") == "ColoredQuad").Value<string>("frustum_relation"));
            ComparePixels(d, ReadTarget("capture-partial-" + kind));
            Export("capture-partial-" + kind, d);
        }

        [Test]
        public void FullyBehindQuadHasNoColoredPixelsAfterPositiveControl()
        {
            Build();
            Assert.IsTrue(RedBounds(ReadTarget()).HasValue, "Front-facing render prerequisite.");
            root.transform.position = new Vector3(0, 0, -5);
            var d = Diagnose();
            Assert.AreEqual("behind", d["nodes"].Single(n => n.Value<string>("name") == "ColoredQuad").Value<string>("camera_plane_relation"));
            Assert.IsFalse(RedBounds(ReadTarget("capture-behind")).HasValue);
            Export("capture-behind", d);
        }

        [UnityTest]
        [Explicit("Mandatory backbuffer proof: exact owned graphical wrapper review and root launch release required; never run by the batch/default filter.")]
        public IEnumerator BackbufferSubviewportMatchesActualCompositedFrame()
        {
            Assert.IsFalse(Application.isBatchMode, "WaitForEndOfFrame is unavailable in Editor batch mode.");
            Assert.AreEqual(
                "UnityEditor.GameView",
                EditorWindow.focusedWindow?.GetType().FullName,
                "Owned focused Game View prerequisite; the fixture does not alter saved layouts/preferences."
            );
            yield return new EnterPlayMode();
            Build(backbuffer: true);
            Assert.AreEqual(
                1,
                UnityFindObjectsCompat.FindAll<Camera>().Count(c => c.isActiveAndEnabled && c.gameObject.scene.IsValid()),
                "The reviewed owned surface must contain only this enabled scene camera."
            );
            camera.rect = new Rect(0.15f, 0.2f, 0.6f, 0.5f);
            yield return null;
            Assert.That(Screen.width, Is.InRange(1, 512));
            Assert.That(Screen.height, Is.InRange(1, 512));
            var d = Diagnose();
            Assert.AreEqual("display", d["context"]["output"].Value<string>("kind"));
            yield return new WaitForEndOfFrame();
            var frame = ScreenCapture.CaptureScreenshotAsTexture();
            Assert.IsNotNull(frame, "Actual composited frame prerequisite.");
            owned.Add(frame);
            Assert.AreEqual(Screen.width, frame.width);
            Assert.AreEqual(Screen.height, frame.height);
            ComparePixels(d, frame);
            File.WriteAllBytes(Path.Combine(ExportDirectory(), "ui07-capture-backbuffer-viewport.png"), frame.EncodeToPNG());
            Export("capture-backbuffer-viewport", d);
        }
    }
}
