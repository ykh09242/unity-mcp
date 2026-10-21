using System;
using System.Collections;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Security.Cryptography;
using System.Text;
using System.Threading;
using System.Threading.Tasks;
using MCPForUnity.Editor.Services.AssetGen;
using MCPForUnity.Editor.Services.AssetGen.Http;
using MCPForUnity.Editor.Services.AssetGen.Providers;
using MCPForUnity.Editor.Services.Transport;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using Unity.Collections;
using UnityEngine;
using UnityEngine.Rendering;
using UnityEngine.TestTools;
using Object = UnityEngine.Object;

namespace MCPForUnityTests.Editor.Performance
{
    // Optional isolated-project probe; never add this fixture to ordinary native CI selection.
    public class ServiceOptimizationProbes
    {
        private static readonly string Output =
            Environment.GetEnvironmentVariable("UNITY_MCP_OPTIMIZATION_OUTPUT") ?? Path.Combine(Application.temporaryCachePath, "McpServiceOptimizationProbes");
        private static readonly BindingFlags Instance = BindingFlags.Instance | BindingFlags.NonPublic | BindingFlags.Public;

        private static void Save(string name, object value)
        {
            Directory.CreateDirectory(Output);
            File.WriteAllText(Path.Combine(Output, name + ".json"), JsonConvert.SerializeObject(value, Formatting.Indented));
        }

        private static string Prepared(object value)
        {
            Type source = typeof(BoundedResponseJson);
            Type limited = source.GetNestedType("LimitedTextWriter", BindingFlags.NonPublic);
            using var output = (TextWriter)Activator.CreateInstance(limited, true);
            using var text = new JsonTextWriter(output) { AutoCompleteOnClose = false };
            var budget = new BoundedResponseJson.Budget();
            Type bounded = source.GetNestedType("TokenWriter", BindingFlags.NonPublic);
            using var writer = (JsonWriter)Activator.CreateInstance(bounded, Instance, null, new object[] { text, budget }, null);
            JsonSerializer.CreateDefault().Serialize(writer, value);
            budget.Check();
            writer.Flush();
            return (string)limited.GetMethod("Finish", Instance).Invoke(output, null);
        }

        [Test]
        public void Opt09PreparedEncoding()
        {
            var result = new List<object>();
            foreach (
                var shape in new[]
                {
                    ("small", 4, "status"),
                    ("ascii", 4000, new string('x', 64)),
                    ("unicode", 4000, string.Concat(Enumerable.Repeat("한글😀", 16))),
                    ("token", 4000, new string('x', 64)),
                }
            )
            {
                object payload = new
                {
                    success = true,
                    rows = Enumerable
                        .Range(0, shape.Item2)
                        .Select(i => new
                        {
                            id = i,
                            name = shape.Item3 + i,
                            values = new object[] { i, -0.0, 1.2345678901234567, null },
                        })
                        .ToArray(),
                };
                if (shape.Item1 == "token")
                    payload = JToken.FromObject(payload);
                string expected = BoundedResponseJson.Serialize(BoundedResponseJson.Freeze(payload));
                Assert.AreEqual(expected, Prepared(payload));
                var samples = new List<object>();
                foreach (char arm in "AABBAABBAABB")
                {
                    long bytes = GC.GetAllocatedBytesForCurrentThread();
                    var timer = Stopwatch.StartNew();
                    string json;
                    double preparationMs;
                    if (arm == 'A')
                    {
                        JToken frozen = BoundedResponseJson.Freeze(payload);
                        preparationMs = timer.Elapsed.TotalMilliseconds;
                        json = BoundedResponseJson.Serialize(frozen);
                    }
                    else
                    {
                        json = Prepared(payload);
                        preparationMs = timer.Elapsed.TotalMilliseconds;
                    }
                    timer.Stop();
                    long allocated = GC.GetAllocatedBytesForCurrentThread() - bytes;
                    Assert.AreEqual(expected, json);
                    samples.Add(
                        new
                        {
                            arm = arm.ToString(),
                            preparationMs,
                            totalMs = timer.Elapsed.TotalMilliseconds,
                            allocated,
                        }
                    );
                }
                var allocations = new List<object>();
                foreach (char arm in "AB")
                {
                    using var recorder = Unity.Profiling.ProfilerRecorder.StartNew(
                        Unity.Profiling.ProfilerCategory.Memory,
                        "GC.Alloc",
                        262144,
                        Unity.Profiling.ProfilerRecorderOptions.StartImmediately | Unity.Profiling.ProfilerRecorderOptions.CollectOnlyOnCurrentThread
                    );
                    string copy = arm == 'A' ? BoundedResponseJson.Serialize(BoundedResponseJson.Freeze(payload)) : Prepared(payload);
                    recorder.Stop();
                    Assert.IsTrue(recorder.Valid);
                    Assert.Less(recorder.Count, recorder.Capacity);
                    long bytes = 0;
                    for (int i = 0; i < recorder.Count; i++)
                        bytes += recorder.GetSample(i).Value;
                    allocations.Add(
                        new
                        {
                            arm = arm.ToString(),
                            events = recorder.Count,
                            sampledBytes = bytes,
                        }
                    );
                    Assert.AreEqual(expected, copy);
                }
                result.Add(
                    new
                    {
                        shape = shape.Item1,
                        rows = shape.Item2,
                        jsonBytes = Encoding.UTF8.GetByteCount(expected),
                        samples,
                        allocations,
                        getAllocatedBytesUnsupported = true,
                    }
                );
            }
            Save("opt09-prepared", result);
        }

        private static void MakeGlb(string path, int metadataBytes, int binaryBytes)
        {
            string json = "{\"asset\":{\"version\":\"2.0\"},\"extras\":\"" + new string('x', metadataBytes) + "\"}";
            byte[] metadata = Encoding.UTF8.GetBytes(json.PadRight((json.Length + 3) / 4 * 4));
            using var output = new FileStream(path, FileMode.CreateNew, FileAccess.Write, FileShare.None);
            using var writer = new BinaryWriter(output);
            writer.Write(0x46546C67u);
            writer.Write(2u);
            writer.Write((uint)(12 + 8 + metadata.Length + 8 + binaryBytes));
            writer.Write((uint)metadata.Length);
            writer.Write(0x4E4F534Au);
            writer.Write(metadata);
            writer.Write((uint)binaryBytes);
            writer.Write(0x004E4942u);
            byte[] block = new byte[64 * 1024];
            new System.Random(1701).NextBytes(block);
            for (int written = 0; written < binaryBytes; written += block.Length)
                writer.Write(block, 0, Math.Min(block.Length, binaryBytes - written));
        }

        [Test]
        public void Opt11ContentCacheLookup()
        {
            string root = Path.Combine(Application.temporaryCachePath, "Opt11-" + Guid.NewGuid().ToString("N"));
            Directory.CreateDirectory(root);
            var results = new List<object>();
            try
            {
                foreach (int metadataBytes in new[] { 512, 1024 * 1024 })
                {
                    string path = Path.Combine(root, metadataBytes + ".glb");
                    MakeGlb(path, metadataBytes, 16 * 1024 * 1024);
                    byte[] digest;
                    using (var input = File.OpenRead(path))
                    using (var sha = SHA256.Create())
                        digest = sha.ComputeHash(input);
                    using (var input = File.OpenRead(path))
                        Assert.AreEqual("glb", TripoAdapter.ValidateModelStream(input, "glb"));
                    var samples = new List<object>();
                    foreach (char arm in "AABBAABBAABB")
                    {
                        long allocation = GC.GetAllocatedBytesForCurrentThread();
                        var timer = Stopwatch.StartNew();
                        using (var input = File.OpenRead(path))
                        {
                            if (arm == 'A')
                                Assert.AreEqual("glb", TripoAdapter.ValidateModelStream(input, "glb"));
                            else
                            {
                                using var sha = SHA256.Create();
                                byte[] actual = sha.ComputeHash(input);
                                Assert.IsTrue(actual.SequenceEqual(digest));
                                // Best-case cache hit: no disk lookup or metadata clone cost.
                            }
                        }
                        timer.Stop();
                        samples.Add(
                            new
                            {
                                arm = arm.ToString(),
                                ms = timer.Elapsed.TotalMilliseconds,
                                allocated = GC.GetAllocatedBytesForCurrentThread() - allocation,
                            }
                        );
                    }
                    using (var file = new FileStream(path, FileMode.Open, FileAccess.ReadWrite, FileShare.None))
                    {
                        file.Position = file.Length - 1;
                        int old = file.ReadByte();
                        file.Position--;
                        file.WriteByte((byte)(old ^ 1));
                    }
                    using (var input = File.OpenRead(path))
                    using (var sha = SHA256.Create())
                        Assert.IsFalse(sha.ComputeHash(input).SequenceEqual(digest), "Same-length external mutation must invalidate the cache.");
                    results.Add(
                        new
                        {
                            metadataBytes,
                            binaryBytes = 16 * 1024 * 1024,
                            samples,
                        }
                    );
                }
            }
            finally
            {
                Directory.Delete(root, true);
            }
            Save("opt11-content-cache", results);
        }

        [Test]
        public async Task Opt11OwnedDigestPreparation()
        {
            string root = Path.Combine(Application.temporaryCachePath, "Opt11Digest-" + Guid.NewGuid().ToString("N"));
            Directory.CreateDirectory(root);
            var samples = new List<object>();
            try
            {
                byte[] bytes = new byte[16 * 1024 * 1024];
                new System.Random(1701).NextBytes(bytes);
                Buffer.BlockCopy(new byte[] { 137, 80, 78, 71, 13, 10, 26, 10 }, 0, bytes, 0, 8);
                for (int iteration = 0; iteration < 5; iteration++)
                {
                    using var body = new MemoryStream(bytes, false);
                    var download = await DownloadedArtifact.ReadAsync(body, bytes.Length, bytes.Length, root, CancellationToken.None);
                    string expected = download.Sha256;
                    var timer = Stopwatch.StartNew();
                    using var prepared = await PreparedArtifact.CreateAsync(
                        new HttpResult { DownloadedFile = download },
                        root,
                        "image",
                        "fal",
                        "png",
                        "png",
                        CancellationToken.None
                    );
                    timer.Stop();
                    Assert.AreEqual(expected, prepared.Hash);
                    Assert.AreEqual(bytes.Length, prepared.Length);
                    if (iteration > 0)
                        samples.Add(new { ms = timer.Elapsed.TotalMilliseconds });
                }
            }
            finally
            {
                Directory.Delete(root, true);
            }
            Save("opt11-owned-digest", new { bytes = 16 * 1024 * 1024, samples });
        }

        [Test]
        public async Task Opt12MixedArtifactQueue()
        {
            string root = Path.Combine(Application.temporaryCachePath, "Opt12-" + Guid.NewGuid().ToString("N"));
            Directory.CreateDirectory(root);
            var results = new List<object>();
            try
            {
                // Shared immutable source bytes keep measurement input identical; each HttpResult owns only its wrapper.
                byte[] large = new byte[32 * 1024 * 1024],
                    small = new byte[512 * 1024];
                new System.Random(1701).NextBytes(large);
                new System.Random(1702).NextBytes(small);
                byte[] signature = { 137, 80, 78, 71, 13, 10, 26, 10 };
                Buffer.BlockCopy(signature, 0, large, 0, 8);
                Buffer.BlockCopy(signature, 0, small, 0, 8);
                int[] sizes = { 1, 1, 0, 0, 1, 0, 0, 1, 0, 0, 0, 0 };
                using (await PreparedArtifact.CreateAsync(new HttpResult { Body = small }, root, "image", "fal", "png", "png", CancellationToken.None)) { }
                using var fifo = new SemaphoreSlim(2);
                var create =
                    (Func<HttpResult, string, string, string, string, string, CancellationToken, PreparedArtifact>)
                        typeof(PreparedArtifact)
                            .GetMethod("Create", BindingFlags.Static | BindingFlags.NonPublic)
                            .CreateDelegate(typeof(Func<HttpResult, string, string, string, string, string, CancellationToken, PreparedArtifact>));
                async Task<PreparedArtifact> PreviousFifo(HttpResult input)
                {
                    using (input)
                    {
                        await fifo.WaitAsync().ConfigureAwait(false);
                        try
                        {
                            return await Task.Run(() => create(input, root, "image", "fal", "png", "png", CancellationToken.None)).ConfigureAwait(false);
                        }
                        finally
                        {
                            fifo.Release();
                        }
                    }
                }
                foreach (char arm in "AABBAABB")
                {
                    int[] order = Enumerable.Range(0, sizes.Length).ToArray();
                    var timer = Stopwatch.StartNew();
                    double[] completed = new double[sizes.Length];
                    var tasks = new List<Task>();
                    foreach (int index in order)
                    {
                        async Task Run(int identity)
                        {
                            var input = new HttpResult { Body = sizes[identity] == 1 ? large : small };
                            using var output = await (
                                arm == 'A'
                                    ? PreviousFifo(input)
                                    : PreparedArtifact.CreateAsync(input, root, "image", "fal", "png", "png", CancellationToken.None)
                            ).ConfigureAwait(false);
                            Assert.AreEqual(sizes[identity] == 1 ? large.Length : small.Length, output.Length);
                            completed[identity] = timer.Elapsed.TotalMilliseconds;
                        }
                        tasks.Add(Run(index));
                    }
                    await Task.WhenAll(tasks);
                    timer.Stop();
                    Assert.IsTrue(completed.All(ms => ms > 0));
                    Assert.AreEqual(0, Directory.GetFiles(Path.Combine(root, "Temp/McpAssetPublication")).Length);
                    results.Add(
                        new
                        {
                            arm = arm.ToString(),
                            workers = 2,
                            totalMs = timer.Elapsed.TotalMilliseconds,
                            sizes,
                            completed,
                        }
                    );
                }
            }
            finally
            {
                Directory.Delete(root, true);
            }
            Save("opt12-mixed-queue", results);
        }

        [UnityTest]
        public IEnumerator Opt08OwnedReadbackPipeline()
        {
            Assert.IsTrue(SystemInfo.supportsAsyncGPUReadback, "This native backend cannot measure asynchronous readback.");
            var results = new List<object>();
            RenderTexture previous = RenderTexture.active;
            foreach (int size in new[] { 512, 2048 })
            {
                var pattern = new Texture2D(size, size, TextureFormat.RGBA32, false, true);
                var pixels = new Color32[size * size];
                for (int y = 0; y < size; y++)
                for (int x = 0; x < size; x++)
                    pixels[y * size + x] = new Color32((byte)(x * 251 / size), (byte)(y * 239 / size), (byte)((x / 13 + y / 17) % 256), (byte)(31 + x % 225));
                pattern.SetPixels32(pixels);
                pattern.Apply(false, false);
                var targets = new[]
                {
                    new RenderTexture(size, size, 0, RenderTextureFormat.ARGB32, RenderTextureReadWrite.Linear),
                    new RenderTexture(size, size, 0, RenderTextureFormat.ARGB32, RenderTextureReadWrite.Linear),
                };
                var cpu = new Texture2D(size, size, TextureFormat.RGBA32, false, true);
                var buffers = new[]
                {
                    new NativeArray<byte>(size * size * 4, Allocator.Persistent),
                    new NativeArray<byte>(size * size * 4, Allocator.Persistent),
                };
                byte[][] expected = new byte[8][];
                try
                {
                    foreach (var target in targets)
                    {
                        target.Create();
                        Graphics.Blit(pattern, target);
                    }
                    for (int frame = 0; frame < expected.Length; frame++)
                    {
                        Graphics.Blit(pattern, targets[0], Vector2.one, new Vector2(frame * .03125f, frame * .0625f));
                        RenderTexture.active = targets[0];
                        cpu.ReadPixels(new Rect(0, 0, size, size), 0, 0);
                        cpu.Apply(false, false);
                        expected[frame] = cpu.EncodeToPNG();
                    }
                    RenderTexture.active = previous;
                    foreach (int shots in new[] { 1, 8 })
                    foreach (char arm in "AABBAABB")
                    {
                        var frames = new byte[shots][];
                        var timer = Stopwatch.StartNew();
                        double maxSliceMs = 0,
                            totalWorkMs = 0;
                        long allocated = 0;
                        if (arm == 'A')
                        {
                            for (int shot = 0; shot < shots; shot++)
                            {
                                long before = GC.GetAllocatedBytesForCurrentThread();
                                var slice = Stopwatch.StartNew();
                                Graphics.Blit(pattern, targets[0], Vector2.one, new Vector2(shot * .03125f, shot * .0625f));
                                RenderTexture.active = targets[0];
                                cpu.ReadPixels(new Rect(0, 0, size, size), 0, 0);
                                cpu.Apply(false, false);
                                frames[shot] = cpu.EncodeToPNG();
                                RenderTexture.active = previous;
                                slice.Stop();
                                allocated += GC.GetAllocatedBytesForCurrentThread() - before;
                                maxSliceMs = Math.Max(maxSliceMs, slice.Elapsed.TotalMilliseconds);
                                totalWorkMs += slice.Elapsed.TotalMilliseconds;
                            }
                        }
                        else
                        {
                            int launched = 0,
                                finished = 0;
                            var pending = new bool[2];
                            var ready = new bool[2];
                            var errors = new bool[2];
                            var frameIds = new int[2];
                            while (finished < shots)
                            {
                                long before = GC.GetAllocatedBytesForCurrentThread();
                                var slice = Stopwatch.StartNew();
                                bool encodedThisSlice = false;
                                for (int slot = 0; slot < 2; slot++)
                                {
                                    if (pending[slot] && ready[slot] && !encodedThisSlice)
                                    {
                                        Assert.IsFalse(errors[slot]);
                                        cpu.LoadRawTextureData(buffers[slot]);
                                        frames[frameIds[slot]] = cpu.EncodeToPNG();
                                        pending[slot] = false;
                                        finished++;
                                        encodedThisSlice = true;
                                    }
                                    if (!pending[slot] && launched < shots)
                                    {
                                        frameIds[slot] = launched;
                                        Graphics.Blit(pattern, targets[slot], Vector2.one, new Vector2(launched * .03125f, launched * .0625f));
                                        int ownedSlot = slot;
                                        ready[slot] = false;
                                        errors[slot] = false;
                                        AsyncGPUReadback.RequestIntoNativeArray(
                                            ref buffers[slot],
                                            targets[slot],
                                            0,
                                            TextureFormat.RGBA32,
                                            request =>
                                            {
                                                errors[ownedSlot] = request.hasError;
                                                ready[ownedSlot] = true;
                                            }
                                        );
                                        pending[slot] = true;
                                        launched++;
                                    }
                                }
                                slice.Stop();
                                allocated += GC.GetAllocatedBytesForCurrentThread() - before;
                                maxSliceMs = Math.Max(maxSliceMs, slice.Elapsed.TotalMilliseconds);
                                totalWorkMs += slice.Elapsed.TotalMilliseconds;

                                if (finished < shots)
                                {
                                    UnityEditor.EditorApplication.QueuePlayerLoopUpdate();
                                    yield return null;
                                }
                                Assert.Less(timer.Elapsed.TotalSeconds, 60, "Readback probe did not finish");
                            }
                        }
                        timer.Stop();
                        for (int frame = 0; frame < shots; frame++)
                            CollectionAssert.AreEqual(expected[frame], frames[frame]);
                        results.Add(
                            new
                            {
                                size,
                                shots,
                                arm = arm.ToString(),
                                totalMs = timer.Elapsed.TotalMilliseconds,
                                totalWorkMs,
                                maxSliceMs,
                                allocated,
                                ownedPixelBytes = (long)size * size * 4 * (arm == 'A' ? 2 : 5),
                                pngBytes = expected.Sum(frame => frame.Length),
                                getAllocatedBytesUnsupported = true,
                                ownedPixelBytesAreEstimate = true,
                            }
                        );
                    }
                }
                finally
                {
                    AsyncGPUReadback.WaitAllRequests();
                    RenderTexture.active = previous;
                    foreach (var buffer in buffers)
                        buffer.Dispose();
                    foreach (var target in targets)
                    {
                        target.Release();
                        Object.DestroyImmediate(target);
                    }
                    Object.DestroyImmediate(cpu);
                    Object.DestroyImmediate(pattern);
                }
            }
            Save(
                "opt08-readback",
                new
                {
                    backend = SystemInfo.graphicsDeviceType.ToString(),
                    gpu = SystemInfo.graphicsDeviceName,
                    results,
                }
            );
        }
    }
}
