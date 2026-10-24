using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Reflection;
using MCPForUnity.Editor.Tools;
using NUnit.Framework;
using UnityEngine;
using UnityEngine.Profiling;

namespace MCPForUnityTests.EditMode.Tools
{
    [Parallelizable(ParallelScope.None)]
    public class TextureGenerationCostTests
    {
        private static readonly List<Color32> Palette = new List<Color32>
        {
            new Color32(0, 1, 127, 0),
            new Color32(255, 254, 128, 127),
            new Color32(19, 57, 213, 255),
        };

        private static Action<Texture2D> Generator(string kind, bool baseline)
        {
            Type type = baseline ? typeof(FrozenTextureGeneration) : typeof(ManageTexture);
            string method =
                kind == "linear" ? "ApplyLinearGradient"
                : kind == "radial" ? "ApplyRadialGradient"
                : kind == "noise" ? "ApplyPerlinNoise"
                : "ApplyPatternToTexture";
            MethodInfo info = type.GetMethod(method, BindingFlags.Static | BindingFlags.NonPublic);
            if (kind == "linear")
            {
                var call = (Action<Texture2D, List<Color32>, float>)Delegate.CreateDelegate(typeof(Action<Texture2D, List<Color32>, float>), info);
                return texture => call(texture, Palette, -37.5f);
            }
            if (kind == "radial")
            {
                var call = (Action<Texture2D, List<Color32>>)Delegate.CreateDelegate(typeof(Action<Texture2D, List<Color32>>), info);
                return texture => call(texture, Palette);
            }
            if (kind == "noise")
            {
                var call = (Action<Texture2D, List<Color32>, float, int>)Delegate.CreateDelegate(typeof(Action<Texture2D, List<Color32>, float, int>), info);
                return texture => call(texture, Palette, 0.071f, 1);
            }
            var pattern = (Action<Texture2D, string, List<Color32>, int>)Delegate.CreateDelegate(typeof(Action<Texture2D, string, List<Color32>, int>), info);
            return texture => pattern(texture, kind, Palette, 3);
        }

        [TestCase(1, 1)]
        [TestCase(1, 17)]
        [TestCase(19, 1)]
        [TestCase(17, 29)]
        public void EveryGeneratorPreservesExactPixelsAndPng(int width, int height)
        {
            foreach (
                string kind in new[]
                {
                    "checkerboard",
                    "stripes",
                    "stripes_v",
                    "stripes_h",
                    "stripes_diag",
                    "dots",
                    "grid",
                    "brick",
                    "unknown",
                    "linear",
                    "radial",
                    "noise",
                }
            )
                Compare(kind, width, height, false);
        }

        [Test]
        [Explicit("Isolated paired native performance adoption gate; not an ordinary CI timing assertion.")]
        public void GenerationMustImprovePairedNativeFillCost()
        {
            double baseline = 0;
            double candidate = 0;
            for (int index = 0; index < 5; index++)
            {
                var times = Compare("checkerboard", 2048, 2048, index % 2 != 0);
                baseline += times[0];
                candidate += times[1];
            }
            Assert.Less(candidate, baseline * 0.9, "Adoption gate: paired native generation must reduce fill cost by at least 10%.");
        }

        [TestCase(2048, "checkerboard")]
        [TestCase(4096, "checkerboard")]
        [TestCase(8192, "checkerboard")]
        [TestCase(2048, "linear")]
        [TestCase(4096, "radial")]
        [TestCase(8192, "noise")]
        [Explicit("Serialized native performance measurements, including 8K allocation and encoding.")]
        // The opt-in 8K baseline performs repeated native writes and encodes; allow its full benchmark workload.
        [Timeout(2640000)]
        public void LargeGenerationPairedMeasurements(int size, string kind)
        {
            for (int index = 0; index < 3; index++)
                Compare(kind, size, size, index % 2 != 0);
        }

        private static double[] Compare(string kind, int width, int height, bool reverse)
        {
            var originalRandom = UnityEngine.Random.state;
            var textures = new Texture2D[2];
            var times = new double[2];
            try
            {
                for (int pass = 0; pass < 2; pass++)
                {
                    int index = reverse ? 1 - pass : pass;
                    textures[index] = new Texture2D(width, height, TextureFormat.RGBA32, false);
                    Action<Texture2D> generate = Generator(kind, index == 0);
                    // Warm delegate/JIT work on a small independent texture, then restore the seed.
                    var warm = new Texture2D(3, 3, TextureFormat.RGBA32, false);
                    try
                    {
                        generate(warm);
                    }
                    finally
                    {
                        UnityEngine.Object.DestroyImmediate(warm);
                    }
                    UnityEngine.Random.InitState(104729);
                    var watch = new Stopwatch();
                    using var process = Process.GetCurrentProcess();
                    var cpuBefore = process.TotalProcessorTime;
                    watch.Start();
                    generate(textures[index]);
                    watch.Stop();
                    double cpu = (process.TotalProcessorTime - cpuBefore).TotalMilliseconds;
                    times[index] = watch.Elapsed.TotalMilliseconds;
                    long allocationEvents = -1;
                    try
                    {
                        // A separately seeded repeat excludes instrumentation from CPU timings.
                        UnityEngine.Random.InitState(104729);
                        allocationEvents = ManagedAllocation.Count(() => generate(textures[index]));
                    }
                    catch (InvalidOperationException) { /* The native runner can lack GC.Alloc instrumentation. */ }
                    watch.Restart();
                    textures[index].Apply();
                    watch.Stop();
                    TestContext.WriteLine(
                        $"BPA13 kind={kind} width={width} height={height} variant={(index == 0 ? "baseline" : "candidate")} fill_ms={times[index]:F6} cpu_ms={cpu:F6} apply_ms={watch.Elapsed.TotalMilliseconds:F6} gc_events={allocationEvents} texture_native_bytes={Profiler.GetRuntimeMemorySizeLong(textures[index])} observed_total_native_bytes={Profiler.GetTotalAllocatedMemoryLong()} observed_managed_bytes={GC.GetTotalMemory(false)} process_peak_working_set_bytes={process.PeakWorkingSet64}"
                    );
                }
                ComparePixels(textures[0], textures[1], kind);
                byte[][] encoded = new byte[2][];
                for (int index = 0; index < 2; index++)
                {
                    var watch = Stopwatch.StartNew();
                    encoded[index] = textures[index].EncodeToPNG();
                    watch.Stop();
                    TestContext.WriteLine(
                        $"BPA13 encode kind={kind} width={width} variant={index} encode_ms={watch.Elapsed.TotalMilliseconds:F6} bytes={encoded[index].Length}"
                    );
                }
                Assert.AreEqual(encoded[0].Length, encoded[1].Length);
                for (int index = 0; index < encoded[0].Length; index++)
                    if (encoded[0][index] != encoded[1][index])
                        Assert.Fail($"{kind} PNG differs at byte {index}.");
                return times;
            }
            finally
            {
                UnityEngine.Random.state = originalRandom;
                foreach (var texture in textures)
                    if (texture != null)
                        UnityEngine.Object.DestroyImmediate(texture);
            }
        }

        private static void ComparePixels(Texture2D baseline, Texture2D candidate, string kind)
        {
            // Views are confined to comparison and never survive into Apply/encode/destruction.
            var before = baseline.GetPixelData<Color32>(0);
            var after = candidate.GetPixelData<Color32>(0);
            Assert.AreEqual(before.Length, after.Length);
            for (int index = 0; index < before.Length; index++)
                if (
                    before[index].r != after[index].r
                    || before[index].g != after[index].g
                    || before[index].b != after[index].b
                    || before[index].a != after[index].a
                )
                    Assert.Fail($"{kind} differs at pixel {index}: {before[index]} != {after[index]}");
        }
    }

    // Frozen beta generation algorithm: only the four pixel writers and their color helpers.
    // Keep independent from production so exact rounding/noise and paired costs remain comparable.
    internal static class FrozenTextureGeneration
    {
        internal static void ApplyPatternToTexture(Texture2D texture, string pattern, List<Color32> palette, int patternSize)
        {
            if (palette == null || palette.Count == 0)
            {
                palette = new List<Color32> { new Color32(255, 255, 255, 255), new Color32(0, 0, 0, 255) };
            }

            int width = texture.width;
            int height = texture.height;
            if (width > 0 && height > 0)
                pattern = pattern.ToLower();

            for (int y = 0; y < height; y++)
            {
                for (int x = 0; x < width; x++)
                {
                    Color32 color = GetPatternColor(x, y, pattern, palette, patternSize, width, height);
                    texture.SetPixel(x, y, color);
                }
            }
        }

        internal static Color32 GetPatternColor(int x, int y, string pattern, List<Color32> palette, int size, int width, int height)
        {
            int colorIndex = 0;

            switch (pattern)
            {
                case "checkerboard":
                    colorIndex = ((x / size) + (y / size)) % 2;
                    break;

                case "stripes":
                case "stripes_v":
                    colorIndex = (x / size) % palette.Count;
                    break;

                case "stripes_h":
                    colorIndex = (y / size) % palette.Count;
                    break;

                case "stripes_diag":
                    colorIndex = ((x + y) / size) % palette.Count;
                    break;

                case "dots":
                    long cx = (x % ((long)size * 2)) - size;
                    long cy = (y % ((long)size * 2)) - size;
                    bool inDot = (cx * cx + cy * cy) < ((long)size * size / 4);
                    colorIndex = inDot ? 1 : 0;
                    break;

                case "grid":
                    bool onGridLine = (x % size == 0) || (y % size == 0);
                    colorIndex = onGridLine ? 1 : 0;
                    break;

                case "brick":
                    int row = y / size;
                    int offset = (row % 2) * (size / 2);
                    bool onBorder = ((x + offset) % size == 0) || (y % size == 0);
                    colorIndex = onBorder ? 1 : 0;
                    break;

                default:
                    colorIndex = 0;
                    break;
            }

            return palette[Mathf.Clamp(colorIndex, 0, palette.Count - 1)];
        }

        // --- Gradient Helpers ---

        internal static void ApplyLinearGradient(Texture2D texture, List<Color32> palette, float angle)
        {
            int width = texture.width;
            int height = texture.height;
            float radians = angle * Mathf.Deg2Rad;
            Vector2 dir = new Vector2(Mathf.Cos(radians), Mathf.Sin(radians));
            float denomX = Mathf.Max(1, width - 1);
            float denomY = Mathf.Max(1, height - 1);

            for (int y = 0; y < height; y++)
            {
                for (int x = 0; x < width; x++)
                {
                    float nx = x / denomX;
                    float ny = y / denomY;
                    float t = Vector2.Dot(new Vector2(nx, ny), dir);
                    t = Mathf.Clamp01((t + 1f) / 2f);

                    Color32 color = LerpPalette(palette, t);
                    texture.SetPixel(x, y, color);
                }
            }
        }

        internal static void ApplyRadialGradient(Texture2D texture, List<Color32> palette)
        {
            int width = texture.width;
            int height = texture.height;
            float cx = width / 2f;
            float cy = height / 2f;
            float maxDist = Mathf.Sqrt(cx * cx + cy * cy);

            for (int y = 0; y < height; y++)
            {
                for (int x = 0; x < width; x++)
                {
                    float dx = x - cx;
                    float dy = y - cy;
                    float dist = Mathf.Sqrt(dx * dx + dy * dy);
                    float t = Mathf.Clamp01(dist / maxDist);

                    Color32 color = LerpPalette(palette, t);
                    texture.SetPixel(x, y, color);
                }
            }
        }

        internal static Color32 LerpPalette(List<Color32> palette, float t)
        {
            if (palette.Count == 1)
                return palette[0];
            if (t <= 0)
                return palette[0];
            if (t >= 1)
                return palette[palette.Count - 1];

            float scaledT = t * (palette.Count - 1);
            int index = Mathf.FloorToInt(scaledT);
            float localT = scaledT - index;

            if (index >= palette.Count - 1)
                return palette[palette.Count - 1];

            Color c1 = palette[index];
            Color c2 = palette[index + 1];
            return Color.Lerp(c1, c2, localT);
        }

        // --- Noise Helpers ---

        internal static void ApplyPerlinNoise(Texture2D texture, List<Color32> palette, float scale, int octaves)
        {
            int width = texture.width;
            int height = texture.height;

            // Random offset to ensure different patterns
            float offsetX = UnityEngine.Random.Range(0f, 1000f);
            float offsetY = UnityEngine.Random.Range(0f, 1000f);

            for (int y = 0; y < height; y++)
            {
                for (int x = 0; x < width; x++)
                {
                    float noiseValue = 0f;
                    float amplitude = 1f;
                    float frequency = 1f;
                    float maxValue = 0f;

                    for (int o = 0; o < octaves; o++)
                    {
                        float sampleX = (x + offsetX) * scale * frequency;
                        float sampleY = (y + offsetY) * scale * frequency;
                        noiseValue += Mathf.PerlinNoise(sampleX, sampleY) * amplitude;
                        maxValue += amplitude;
                        amplitude *= 0.5f;
                        frequency *= 2f;
                    }

                    float t = Mathf.Clamp01(noiseValue / maxValue);
                    Color32 color = LerpPalette(palette, t);
                    texture.SetPixel(x, y, color);
                }
            }
        }
    }
}
