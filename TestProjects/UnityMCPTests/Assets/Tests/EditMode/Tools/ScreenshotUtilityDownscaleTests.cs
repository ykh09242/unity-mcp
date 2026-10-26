using System;
using System.IO;
using System.Linq;
using System.Security.Cryptography;
using System.Threading;
using MCPForUnity.Editor.Tools;
using MCPForUnity.Editor.Tools.Cameras;
using MCPForUnity.Runtime.Helpers;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEngine;
using UnityEngine.Rendering;
using Object = UnityEngine.Object;

namespace MCPForUnityTests.Editor.Tools
{
    /// <summary>
    /// DownscaleTexture blitted through a RenderTexture created with the project-default
    /// read/write mode. In a Linear-colorspace project that is sRGB, so a linear-flagged
    /// capture picked up an sRGB encode on store and the inline preview came back washed
    /// out while the on-disk PNG stayed correct (issue #1328).
    /// </summary>
    [TestFixture]
    [Parallelizable(ParallelScope.None)]
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

        [TestCase(192, 96, 64)]
        [TestCase(2560, 1440, 160)]
        [TestCase(2048, 1024, 2048)]
        public void OwnedBatchRendersOnlyOutputPixelsAndPreservesFramingAndColors(int width, int height, int maxResolution)
        {
            var owner = new GameObject("OwnedBatchMeasurement");
            var camera = owner.AddComponent<Camera>();
            camera.enabled = false;
            camera.clearFlags = CameraClearFlags.SolidColor;
            camera.cullingMask = 0;
            camera.backgroundColor = new Color(0.12f, 0.3f, 0.75f, 0.5f);
            camera.aspect = 1.7f;
            camera.fieldOfView = 47f;
            var projection = Matrix4x4.Perspective(43f, 1.7f, 0.2f, 123f);
            projection.m02 = 0.1f;
            camera.projectionMatrix = projection;
            var originalTarget = RenderTexture.GetTemporary(width, height, 24);
            camera.targetTexture = originalTarget;
            var priorActive = RenderTexture.active;
            Texture2D expected = null,
                actual = null;
            int observedWidth = 0,
                observedHeight = 0;
            Matrix4x4 observedProjection = default;
            void Observe(Camera rendering)
            {
                if (rendering != camera)
                    return;
                observedWidth = rendering.targetTexture.width;
                observedHeight = rendering.targetTexture.height;
                observedProjection = rendering.projectionMatrix;
            }
            Camera.onPreRender += Observe;
            bool allocationCounterAvailable = false;
            string allocationCounterUnavailableReason = null;
            long? ReadAllocationCounter()
            {
                if (!allocationCounterAvailable)
                    return null;
                try
                {
                    return GC.GetAllocatedBytesForCurrentThread();
                }
                catch (Exception error) when (error is NotSupportedException || error is NotImplementedException)
                {
                    allocationCounterAvailable = false;
                    allocationCounterUnavailableReason = error.GetType().Name;
                    return null;
                }
            }
            long? AllocationDelta(long? before, long? after)
            {
                if (!before.HasValue || !after.HasValue)
                    return null;
                if (after.Value >= before.Value)
                    return after.Value - before.Value;
                allocationCounterAvailable = false;
                allocationCounterUnavailableReason = "Counter was not monotonic on the measurement thread.";
                return null;
            }
            try
            {
                try
                {
                    long calibration = GC.GetAllocatedBytesForCurrentThread();
                    GC.KeepAlive(new byte[4096]);
                    allocationCounterAvailable = GC.GetAllocatedBytesForCurrentThread() >= calibration + 4096;
                    if (!allocationCounterAvailable)
                        allocationCounterUnavailableReason = "Counter did not observe the calibration allocation.";
                }
                catch (Exception error) when (error is NotSupportedException || error is NotImplementedException)
                {
                    allocationCounterUnavailableReason = error.GetType().Name;
                }
                long? before = ReadAllocationCounter();
                long started = System.Diagnostics.Stopwatch.GetTimestamp();
                expected = ScreenshotUtility.RenderCameraToTexture(camera, maxResolution);
                double baselineMs = (System.Diagnostics.Stopwatch.GetTimestamp() - started) * 1000.0 / System.Diagnostics.Stopwatch.Frequency;
                long? baselineBytes = AllocationDelta(before, ReadAllocationCounter());
                Assert.AreEqual(width, observedWidth);
                Assert.AreEqual(height, observedHeight);
                var budget = new ScreenshotUtility.CaptureBatchBudget();
                before = ReadAllocationCounter();
                started = System.Diagnostics.Stopwatch.GetTimestamp();
                actual = ScreenshotUtility.RenderOwnedCameraTileToTexture(camera, maxResolution, budget);
                double batchMs = (System.Diagnostics.Stopwatch.GetTimestamp() - started) * 1000.0 / System.Diagnostics.Stopwatch.Frequency;
                long? batchBytes = AllocationDelta(before, ReadAllocationCounter());
                Assert.AreEqual(actual.width, observedWidth);
                Assert.AreEqual(actual.height, observedHeight);
                Assert.AreEqual((long)actual.width * actual.height, budget.RenderedPixels);
                Assert.AreEqual(expected.width, actual.width);
                Assert.AreEqual(expected.height, actual.height);
                Assert.AreEqual(projection, observedProjection);
                Assert.AreEqual(projection, camera.projectionMatrix);
                Assert.AreEqual(1.7f, camera.aspect);
                Assert.AreEqual(47f, camera.fieldOfView);
                Assert.AreSame(originalTarget, camera.targetTexture);
                Assert.AreSame(priorActive, RenderTexture.active);
                var wanted = expected.GetPixels32();
                var got = actual.GetPixels32();
                int maxDelta = 0;
                for (int i = 0; i < wanted.Length; i++)
                {
                    maxDelta = Math.Max(maxDelta, Math.Abs(wanted[i].r - got[i].r));
                    maxDelta = Math.Max(maxDelta, Math.Abs(wanted[i].g - got[i].g));
                    maxDelta = Math.Max(maxDelta, Math.Abs(wanted[i].b - got[i].b));
                    maxDelta = Math.Max(maxDelta, Math.Abs(wanted[i].a - got[i].a));
                }
                Assert.LessOrEqual(maxDelta, 2, "Solid-color output must retain the existing color-space behavior.");
                byte[] png = actual.EncodeToPNG();
                Assert.AreEqual(actual.width, (png[16] << 24) | (png[17] << 16) | (png[18] << 8) | png[19]);
                Assert.AreEqual(actual.height, (png[20] << 24) | (png[21] << 16) | (png[22] << 8) | png[23]);
                string evidence = Path.Combine(
                    Path.GetDirectoryName(Application.dataPath),
                    "Library",
                    "MCPForUnity",
                    "CaptureBudgetTests",
                    Guid.NewGuid().ToString("N")
                );
                Directory.CreateDirectory(evidence);
                string baselinePath = Path.Combine(evidence, "baseline.png");
                string batchPath = Path.Combine(evidence, "batch.png");
                File.WriteAllBytes(baselinePath, expected.EncodeToPNG());
                File.WriteAllBytes(batchPath, png);
                using var sha = SHA256.Create();
                var measurement = new JObject
                {
                    ["source_width"] = width,
                    ["source_height"] = height,
                    ["render_width"] = observedWidth,
                    ["render_height"] = observedHeight,
                    ["tile_width"] = actual.width,
                    ["tile_height"] = actual.height,
                    ["png_width"] = actual.width,
                    ["png_height"] = actual.height,
                    ["full_render_pixels"] = (long)width * height,
                    ["batch_render_pixels"] = budget.RenderedPixels,
                    ["baseline_ms"] = baselineMs,
                    ["batch_ms"] = batchMs,
                    ["baseline_managed_bytes"] = baselineBytes,
                    ["batch_managed_bytes"] = batchBytes,
                    ["allocation_counter_available"] = allocationCounterAvailable,
                    ["allocation_counter_unavailable_reason"] = allocationCounterUnavailableReason,
                    ["max_color_delta"] = maxDelta,
                    ["graphics_type"] = SystemInfo.graphicsDeviceType.ToString(),
                    ["baseline_png"] = baselinePath,
                    ["batch_png"] = batchPath,
                    ["batch_sha256"] = BitConverter.ToString(sha.ComputeHash(png)).Replace("-", "").ToLowerInvariant(),
                };
                // Retained solely as native QA evidence; the runner may remove this exact nonce directory after review.
                File.WriteAllText(Path.Combine(evidence, "measurement.json"), measurement.ToString());
                TestContext.WriteLine("CAPTURE_BUDGET_MEASUREMENT " + measurement.ToString(Newtonsoft.Json.Formatting.None));
                if (width >= 2048)
                {
                    const int warmupsPerHelper = 2;
                    const int pairCount = 8;
                    var seriesBudget = new ScreenshotUtility.CaptureBatchBudget();
                    var samples = new JArray();
                    var texturesBefore = UnityEngine.Resources.FindObjectsOfTypeAll<Texture2D>().Select(t => t.GetInstanceIDCompat()).ToArray();
                    Color32 referenceColor = actual.GetPixel(actual.width / 2, actual.height / 2);
                    JObject CaptureSample(bool ownedBatch, int pair, int position, bool record)
                    {
                        Texture2D captured = null;
                        double milliseconds = 0;
                        long? allocatedBytes = null;
                        int renderWidth = 0,
                            renderHeight = 0,
                            outputWidth = 0,
                            outputHeight = 0;
                        try
                        {
                            long? allocatedBefore = ReadAllocationCounter();
                            long start = System.Diagnostics.Stopwatch.GetTimestamp();
                            captured = ownedBatch
                                ? ScreenshotUtility.RenderOwnedCameraTileToTexture(camera, maxResolution, seriesBudget)
                                : ScreenshotUtility.RenderCameraToTexture(camera, maxResolution);
                            milliseconds = (System.Diagnostics.Stopwatch.GetTimestamp() - start) * 1000.0 / System.Diagnostics.Stopwatch.Frequency;
                            allocatedBytes = AllocationDelta(allocatedBefore, ReadAllocationCounter());
                            renderWidth = observedWidth;
                            renderHeight = observedHeight;
                            outputWidth = captured.width;
                            outputHeight = captured.height;
                            Assert.AreEqual(ownedBatch ? actual.width : width, renderWidth);
                            Assert.AreEqual(ownedBatch ? actual.height : height, renderHeight);
                            Assert.AreEqual(actual.width, outputWidth);
                            Assert.AreEqual(actual.height, outputHeight);
                            Assert.AreEqual(projection, observedProjection);
                            Assert.AreEqual(projection, camera.projectionMatrix);
                            Assert.AreEqual(1.7f, camera.aspect);
                            Assert.AreEqual(47f, camera.fieldOfView);
                            Assert.AreSame(originalTarget, camera.targetTexture);
                            Assert.AreSame(priorActive, RenderTexture.active);
                            Color32 pixel = captured.GetPixel(outputWidth / 2, outputHeight / 2);
                            Assert.AreEqual(referenceColor.r, pixel.r, 2);
                            Assert.AreEqual(referenceColor.g, pixel.g, 2);
                            Assert.AreEqual(referenceColor.b, pixel.b, 2);
                            Assert.AreEqual(referenceColor.a, pixel.a, 2);
                        }
                        finally
                        {
                            if (captured != null)
                                Object.DestroyImmediate(captured);
                        }
                        Assert.IsTrue(captured == null, "Each timed CPU readback texture must be released before the next capture.");
                        if (!record)
                            return null;
                        return new JObject
                        {
                            ["pair"] = pair,
                            ["position"] = position,
                            ["mode"] = ownedBatch ? "batch" : "baseline",
                            ["elapsed_ms"] = milliseconds,
                            ["managed_bytes"] = allocatedBytes,
                            ["allocation_counter_available"] = allocatedBytes.HasValue,
                            ["render_width"] = renderWidth,
                            ["render_height"] = renderHeight,
                            ["render_pixels"] = (long)renderWidth * renderHeight,
                            ["output_width"] = outputWidth,
                            ["output_height"] = outputHeight,
                            ["targets_restored"] = true,
                            ["readback_texture_destroyed"] = true,
                        };
                    }
                    // Warm both paths and alternate the first helper. Exclude these captures from samples and medians.
                    for (int warmup = 0; warmup < warmupsPerHelper; warmup++)
                    for (int position = 0; position < 2; position++)
                        CaptureSample((warmup + position) % 2 != 0, -1, position, false);
                    for (int pair = 0; pair < pairCount; pair++)
                    for (int position = 0; position < 2; position++)
                        samples.Add(CaptureSample((pair + position) % 2 != 0, pair, position, true));
                    double Median(string mode, string field)
                    {
                        double[] values = samples
                            .Where(sample => sample.Value<string>("mode") == mode)
                            .Select(sample => sample.Value<double>(field))
                            .OrderBy(value => value)
                            .ToArray();
                        return (values[values.Length / 2 - 1] + values[values.Length / 2]) / 2;
                    }
                    CollectionAssert.AreEquivalent(
                        texturesBefore,
                        UnityEngine.Resources.FindObjectsOfTypeAll<Texture2D>().Select(t => t.GetInstanceIDCompat()).ToArray()
                    );
                    Assert.AreEqual(warmupsPerHelper + pairCount, seriesBudget.Shots);
                    Assert.AreEqual((long)actual.width * actual.height * (warmupsPerHelper + pairCount), seriesBudget.RenderedPixels);
                    bool allAllocationSamplesAvailable = samples.All(sample => sample.Value<bool>("allocation_counter_available"));
                    var pairedMeasurement = new JObject
                    {
                        ["schema_version"] = 1,
                        ["source_width"] = width,
                        ["source_height"] = height,
                        ["output_width"] = actual.width,
                        ["output_height"] = actual.height,
                        ["graphics_type"] = SystemInfo.graphicsDeviceType.ToString(),
                        ["warmup_captures_per_helper"] = warmupsPerHelper,
                        ["warmups_excluded"] = true,
                        ["pair_count"] = pairCount,
                        ["baseline_first_pairs"] = pairCount / 2,
                        ["batch_first_pairs"] = pairCount / 2,
                        ["timed_scope"] = "One synchronous product helper; excludes counter reads, pixel assertions, destruction, PNG encoding and JSON.",
                        ["baseline_median_ms"] = Median("baseline", "elapsed_ms"),
                        ["batch_median_ms"] = Median("batch", "elapsed_ms"),
                        ["allocation_counter"] = "GC.GetAllocatedBytesForCurrentThread",
                        ["allocation_counter_calibration_bytes"] = 4096,
                        ["allocation_counter_available"] = allAllocationSamplesAvailable,
                        ["allocation_counter_unavailable_reason"] = allocationCounterUnavailableReason,
                        ["baseline_median_managed_bytes"] = allAllocationSamplesAvailable
                            ? new JValue(Median("baseline", "managed_bytes"))
                            : JValue.CreateNull(),
                        ["batch_median_managed_bytes"] = allAllocationSamplesAvailable ? new JValue(Median("batch", "managed_bytes")) : JValue.CreateNull(),
                        ["texture_inventory_restored"] = true,
                        ["samples"] = samples,
                        ["baseline_png"] = baselinePath,
                        ["batch_png"] = batchPath,
                        ["batch_sha256"] = measurement["batch_sha256"].DeepClone(),
                    };
                    File.WriteAllText(Path.Combine(evidence, "paired-measurement.json"), pairedMeasurement.ToString());
                    TestContext.WriteLine("CAPTURE_BUDGET_PAIRED_MEASUREMENT " + pairedMeasurement.ToString(Newtonsoft.Json.Formatting.None));
                }
            }
            finally
            {
                Camera.onPreRender -= Observe;
                camera.targetTexture = null;
                RenderTexture.active = priorActive;
                RenderTexture.ReleaseTemporary(originalTarget);
                if (expected != null)
                    Object.DestroyImmediate(expected);
                if (actual != null)
                    Object.DestroyImmediate(actual);
                Object.DestroyImmediate(owner);
            }
        }

        [Test]
        public void OwnedBatchPreservesAsymmetricGeometryFramingAtOutputResolution()
        {
            var scene = UnityEditor.SceneManagement.EditorSceneManager.NewPreviewScene();
            var owner = new GameObject("OwnedBatchGeometryCamera");
            UnityEngine.SceneManagement.SceneManager.MoveGameObjectToScene(owner, scene);
            var camera = owner.AddComponent<Camera>();
            camera.scene = scene;
            camera.enabled = false;
            camera.clearFlags = CameraClearFlags.SolidColor;
            camera.backgroundColor = new Color(0.04f, 0.06f, 0.1f, 1f);
            camera.cullingMask = 1 << 31;
            camera.aspect = 1.7f;
            var projection = Matrix4x4.Perspective(43f, 1.7f, 0.2f, 123f);
            projection.m02 = 0.12f;
            projection.m12 = -0.07f;
            camera.projectionMatrix = projection;
            var priorActive = RenderTexture.active;
            var target = RenderTexture.GetTemporary(513, 289, 24);
            camera.targetTexture = target;
            var meshes = new System.Collections.Generic.List<Mesh>();
            var materials = new System.Collections.Generic.List<Material>();
            Texture2D baseline = null,
                batch = null;
            void AddGeometry(string name, Vector3 position, Color color, bool triangle)
            {
                Shader shader = Shader.Find("Unlit/Color") ?? Shader.Find("Sprites/Default");
                Assert.IsNotNull(shader, "The native fixture requires an existing built-in unlit shader.");
                var shape = new GameObject(name);
                UnityEngine.SceneManagement.SceneManager.MoveGameObjectToScene(shape, scene);
                shape.layer = 31;
                shape.transform.position = position;
                var mesh = new Mesh { name = name + "Mesh" };
                meshes.Add(mesh);
                mesh.vertices = triangle
                    ? new[] { new Vector3(-0.55f, -0.4f, 0), new Vector3(-0.25f, 0.55f, 0), new Vector3(0.5f, -0.2f, 0) }
                    : new[] { new Vector3(-0.3f, -0.45f, 0), new Vector3(-0.3f, 0.35f, 0), new Vector3(0.4f, 0.35f, 0), new Vector3(0.4f, -0.45f, 0) };
                mesh.triangles = triangle ? new[] { 0, 1, 2, 2, 1, 0 } : new[] { 0, 1, 2, 0, 2, 3, 2, 1, 0, 3, 2, 0 };
                mesh.RecalculateBounds();
                shape.AddComponent<MeshFilter>().sharedMesh = mesh;
                var material = new Material(shader) { color = color };
                materials.Add(material);
                shape.AddComponent<MeshRenderer>().sharedMaterial = material;
            }
            (Vector2 center, RectInt bounds, int count) Silhouette(Texture2D image, bool red)
            {
                var pixels = image.GetPixels32();
                int minX = image.width,
                    minY = image.height,
                    maxX = -1,
                    maxY = -1,
                    count = 0;
                double sumX = 0,
                    sumY = 0;
                for (int i = 0; i < pixels.Length; i++)
                {
                    Color32 color = pixels[i];
                    bool selected = red ? color.r > 140 && color.g < 80 && color.b < 80 : color.g > 140 && color.r < 80 && color.b < 80;
                    if (!selected)
                        continue;
                    int x = i % image.width,
                        y = i / image.width;
                    minX = Math.Min(minX, x);
                    minY = Math.Min(minY, y);
                    maxX = Math.Max(maxX, x);
                    maxY = Math.Max(maxY, y);
                    sumX += x;
                    sumY += y;
                    count++;
                }
                Assert.Greater(count, 12, "Each asymmetric foreground shape must be visible.");
                return (new Vector2((float)(sumX / count), (float)(sumY / count)), new RectInt(minX, minY, maxX - minX + 1, maxY - minY + 1), count);
            }
            try
            {
                AddGeometry("OwnedRedTriangle", new Vector3(-0.8f, 0.35f, 4f), Color.red, true);
                AddGeometry("OwnedGreenQuad", new Vector3(0.65f, -0.3f, 3.5f), Color.green, false);
                baseline = ScreenshotUtility.RenderCameraToTexture(camera, 96);
                batch = ScreenshotUtility.RenderOwnedCameraTileToTexture(camera, 96, new ScreenshotUtility.CaptureBatchBudget());
                Assert.AreEqual(baseline.width, batch.width);
                Assert.AreEqual(baseline.height, batch.height);
                var metrics = new JObject();
                foreach (bool red in new[] { true, false })
                {
                    var expected = Silhouette(baseline, red);
                    var actual = Silhouette(batch, red);
                    float centroidDistance = Vector2.Distance(expected.center, actual.center);
                    Assert.LessOrEqual(centroidDistance, 0.75f, "Framing must preserve each foreground centroid within one output pixel.");
                    Assert.LessOrEqual(Math.Abs(expected.bounds.xMin - actual.bounds.xMin), 1);
                    Assert.LessOrEqual(Math.Abs(expected.bounds.yMin - actual.bounds.yMin), 1);
                    Assert.LessOrEqual(Math.Abs(expected.bounds.xMax - actual.bounds.xMax), 1);
                    Assert.LessOrEqual(Math.Abs(expected.bounds.yMax - actual.bounds.yMax), 1);
                    Assert.LessOrEqual(Math.Abs(expected.count - actual.count), Math.Max(12, expected.count * 0.15));
                    metrics[red ? "red_triangle" : "green_quad"] = new JObject
                    {
                        ["centroid_distance"] = centroidDistance,
                        ["baseline_pixels"] = expected.count,
                        ["batch_pixels"] = actual.count,
                        ["baseline_bounds"] = expected.bounds.ToString(),
                        ["batch_bounds"] = actual.bounds.ToString(),
                    };
                }
                Assert.AreSame(target, camera.targetTexture);
                Assert.AreSame(priorActive, RenderTexture.active);
                Assert.AreEqual(projection, camera.projectionMatrix);
                string evidence = Path.Combine(
                    Path.GetDirectoryName(Application.dataPath),
                    "Library",
                    "MCPForUnity",
                    "CaptureBudgetTests",
                    Guid.NewGuid().ToString("N")
                );
                Directory.CreateDirectory(evidence);
                string baselinePath = Path.Combine(evidence, "geometry-baseline.png");
                string batchPath = Path.Combine(evidence, "geometry-batch.png");
                File.WriteAllBytes(baselinePath, baseline.EncodeToPNG());
                byte[] png = batch.EncodeToPNG();
                File.WriteAllBytes(batchPath, png);
                using var sha = SHA256.Create();
                metrics["baseline_png"] = baselinePath;
                metrics["batch_png"] = batchPath;
                metrics["batch_sha256"] = BitConverter.ToString(sha.ComputeHash(png)).Replace("-", "").ToLowerInvariant();
                metrics["graphics_type"] = SystemInfo.graphicsDeviceType.ToString();
                File.WriteAllText(Path.Combine(evidence, "geometry.json"), metrics.ToString());
                TestContext.WriteLine("CAPTURE_BUDGET_GEOMETRY " + metrics.ToString(Newtonsoft.Json.Formatting.None));
            }
            finally
            {
                camera.targetTexture = null;
                RenderTexture.active = priorActive;
                RenderTexture.ReleaseTemporary(target);
                if (baseline != null)
                    Object.DestroyImmediate(baseline);
                if (batch != null)
                    Object.DestroyImmediate(batch);
                UnityEditor.SceneManagement.EditorSceneManager.ClosePreviewScene(scene);
                foreach (var material in materials)
                    Object.DestroyImmediate(material);
                foreach (var mesh in meshes)
                    Object.DestroyImmediate(mesh);
            }
        }

        [TestCase(false)]
        [TestCase(true)]
        public void PublicOrdinaryBatchReturnsDecodableContactSheet(bool surround)
        {
            var command = new JObject
            {
                ["action"] = "screenshot",
                ["batch"] = surround ? "surround" : "orbit",
                ["viewTarget"] = new JArray(0, 0, 0),
                ["maxResolution"] = 32,
                ["orbitAngles"] = 2,
                ["orbitElevations"] = new JArray(0),
            };
            var response = JObject.FromObject(ManageScene.HandleCommand(command));
            Assert.IsTrue(response.Value<bool>("success"), response.ToString());
            var data = response["data"];
            Assert.AreEqual(surround ? 6 : 2, ((JArray)data["shots"]).Count);
            var decoded = new Texture2D(1, 1);
            try
            {
                Assert.IsTrue(decoded.LoadImage(Convert.FromBase64String(data.Value<string>("imageBase64"))));
                Assert.AreEqual(data.Value<int>("imageWidth"), decoded.width);
                Assert.AreEqual(data.Value<int>("imageHeight"), decoded.height);
            }
            finally
            {
                Object.DestroyImmediate(decoded);
            }
        }

        [TestCase(false)]
        [TestCase(true)]
        public void OwnedBatchRestoresTargetsAndReleasesReadbackOnCancellationOrTargetDrift(bool cancelAfterRender)
        {
            var owner = new GameObject("OwnedBatchFailure");
            var camera = owner.AddComponent<Camera>();
            camera.enabled = false;
            camera.cullingMask = 0;
            camera.clearFlags = CameraClearFlags.SolidColor;
            var originalTarget = RenderTexture.GetTemporary(192, 96, 24);
            camera.targetTexture = originalTarget;
            var projection = camera.projectionMatrix;
            var priorActive = RenderTexture.active;
            using var cancel = new CancellationTokenSource();
            var budget = new ScreenshotUtility.CaptureBatchBudget(cancel.Token);
            var before = UnityEngine.Resources.FindObjectsOfTypeAll<Texture2D>().Select(t => t.GetInstanceIDCompat()).ToArray();
            void Change(Camera rendering)
            {
                if (rendering != camera)
                    return;
                if (cancelAfterRender)
                    cancel.Cancel();
                else
                    rendering.targetTexture = originalTarget;
            }
            Camera.onPostRender += Change;
            try
            {
                if (cancelAfterRender)
                    Assert.Throws<OperationCanceledException>(() => ScreenshotUtility.RenderOwnedCameraTileToTexture(camera, 64, budget));
                else
                    Assert.Throws<InvalidOperationException>(() => ScreenshotUtility.RenderOwnedCameraTileToTexture(camera, 64, budget));
                Assert.AreSame(originalTarget, camera.targetTexture);
                Assert.AreSame(priorActive, RenderTexture.active);
                Assert.AreEqual(projection, camera.projectionMatrix);
                CollectionAssert.AreEquivalent(before, UnityEngine.Resources.FindObjectsOfTypeAll<Texture2D>().Select(t => t.GetInstanceIDCompat()).ToArray());
            }
            finally
            {
                Camera.onPostRender -= Change;
                camera.targetTexture = null;
                RenderTexture.active = priorActive;
                RenderTexture.ReleaseTemporary(originalTarget);
                Object.DestroyImmediate(owner);
            }
        }

        [Test]
        public void GenericCameraBudgetRejectsActualFullRenderBeforeAllocation()
        {
            var owner = new GameObject("OwnedBudgetRefusal");
            var camera = owner.AddComponent<Camera>();
            camera.enabled = false;
            var target = RenderTexture.GetTemporary(64, 32, 24);
            camera.targetTexture = target;
            var budget = new ScreenshotUtility.CaptureBatchBudget();
            budget.AdmitFrame(8192, 4096, 1, 1);
            int renders = 0;
            void Observe(Camera rendering)
            {
                if (rendering == camera)
                    renders++;
            }
            Camera.onPreRender += Observe;
            var textures = UnityEngine.Resources.FindObjectsOfTypeAll<RenderTexture>().Select(t => t.GetInstanceIDCompat()).ToArray();
            try
            {
                Assert.Throws<ArgumentException>(() => ScreenshotUtility.RenderCameraToTexture(camera, 16, budget));
                Assert.AreEqual(0, renders);
                Assert.AreSame(target, camera.targetTexture);
                CollectionAssert.AreEquivalent(
                    textures,
                    UnityEngine.Resources.FindObjectsOfTypeAll<RenderTexture>().Select(t => t.GetInstanceIDCompat()).ToArray()
                );
            }
            finally
            {
                Camera.onPreRender -= Observe;
                camera.targetTexture = null;
                RenderTexture.ReleaseTemporary(target);
                Object.DestroyImmediate(owner);
            }
        }

        [TestCase(false)]
        [TestCase(true)]
        public void PublicBatchCancellationStopsAfterOneRenderAndDestroysOwnedCamera(bool cameraTool)
        {
            using var cancel = new CancellationTokenSource();
            var textures = UnityEngine.Resources.FindObjectsOfTypeAll<Texture2D>().Select(t => t.GetInstanceIDCompat()).ToArray();
            int renders = 0;
            Camera persistent = Camera.main;
            string persistentState = persistent != null ? UnityEditor.EditorJsonUtility.ToJson(persistent) : null;
            Matrix4x4 persistentProjection = persistent != null ? persistent.projectionMatrix : default;
            void Cancel(Camera camera)
            {
                if (!camera.name.StartsWith("__MCP_", StringComparison.Ordinal))
                    return;
                renders++;
                cancel.Cancel();
            }
            Camera.onPostRender += Cancel;
            try
            {
                var command = new JObject
                {
                    ["action"] = cameraTool ? "screenshot_multiview" : "screenshot",
                    ["batch"] = "orbit",
                    ["viewTarget"] = new JArray(0, 0, 0),
                    ["maxResolution"] = 32,
                };
                Assert.Throws<OperationCanceledException>(() =>
                {
                    if (cameraTool)
                        ManageCamera.HandleCommand(command, cancel.Token);
                    else
                        ManageScene.HandleCommand(command, cancel.Token);
                });
                Assert.AreEqual(1, renders);
                if (persistent != null)
                {
                    Assert.AreEqual(persistentState, UnityEditor.EditorJsonUtility.ToJson(persistent));
                    Assert.AreEqual(persistentProjection, persistent.projectionMatrix);
                }
                Assert.IsFalse(
                    UnityEngine
                        .Resources.FindObjectsOfTypeAll<Camera>()
                        .Any(c => c.name == "__MCP_OrbitCapture_Temp_Camera__" || c.name == "__MCP_MultiAngle_Temp_Camera__")
                );
                CollectionAssert.AreEquivalent(
                    textures,
                    UnityEngine.Resources.FindObjectsOfTypeAll<Texture2D>().Select(t => t.GetInstanceIDCompat()).ToArray()
                );
            }
            finally
            {
                Camera.onPostRender -= Cancel;
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
