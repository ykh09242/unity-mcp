using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Security.Cryptography;
using System.Threading;
using System.Threading.Tasks;
using MCPForUnity.Editor.Services.AssetGen;
using MCPForUnity.Editor.Services.AssetGen.Http;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEngine;

namespace MCPForUnityTests.Editor.AssetGen
{
    public sealed class ArtifactSchedulingMeasurementTests
    {
        // Exact pre-change queue implementation, only for equal-work measurements.
        internal sealed class PreviousQueue
        {
            private readonly object gate = new();
            private readonly LinkedList<Waiter> waiting = new();
            private readonly int workers;
            private readonly Func<double> clock;
            private int active;

            internal PreviousQueue(int workers = 2, Func<double> clock = null)
            {
                if (workers < 1)
                    throw new ArgumentOutOfRangeException(nameof(workers));
                this.workers = workers;
                this.clock = clock ?? (() => (double)Stopwatch.GetTimestamp() / Stopwatch.Frequency);
            }

            private sealed class Waiter
            {
                internal long Bytes;
                internal double QueuedAt;
                internal LinkedListNode<Waiter> Node;
                internal readonly TaskCompletionSource<IDisposable> Completion = new(TaskCreationOptions.RunContinuationsAsynchronously);
            }

            private sealed class Lease : IDisposable
            {
                private PreviousQueue owner;

                internal Lease(PreviousQueue owner) => this.owner = owner;

                public void Dispose() => Interlocked.Exchange(ref owner, null)?.Release();
            }

            internal async Task<IDisposable> AcquireAsync(long bytes, CancellationToken token)
            {
                token.ThrowIfCancellationRequested();
                Waiter waiter;
                lock (gate)
                {
                    if (active < workers)
                    {
                        active++;
                        return new Lease(this);
                    }
                    waiter = new Waiter { Bytes = Math.Max(0, bytes), QueuedAt = clock() };
                    waiter.Node = waiting.AddLast(waiter);
                }
                using (token.Register(() => Cancel(waiter, token)))
                    return await waiter.Completion.Task.ConfigureAwait(false);
            }

            private void Cancel(Waiter waiter, CancellationToken token)
            {
                lock (gate)
                {
                    if (waiter.Node.List == null)
                        return; // Already granted; the worker checks cancellation before touching input.
                    waiting.Remove(waiter.Node);
                    waiter.Completion.TrySetCanceled(token);
                }
            }

            private void Release()
            {
                lock (gate)
                {
                    active--;
                    if (waiting.Count == 0)
                        return;
                    Waiter next = waiting.First.Value;
                    // After two seconds, the oldest waiter takes precedence. This is
                    // scheduling age, not a timeout or a bound on supported work.
                    if (clock() - next.QueuedAt < 2.0)
                        for (LinkedListNode<Waiter> node = waiting.First.Next; node != null; node = node.Next)
                            if (node.Value.Bytes < next.Bytes)
                                next = node.Value;
                    waiting.Remove(next.Node);
                    active++;
                    next.Completion.SetResult(new Lease(this));
                }
            }
        }

        [Test, Explicit("Controller-owned equal artifact scheduling measurements."), Timeout(720000)]
        public async Task CompleteMixedBurstsAndSustainedArrivalsPreserveAllArtifacts()
        {
            string root = Path.Combine(Application.temporaryCachePath, "McpFairQueue-" + Guid.NewGuid().ToString("N"));
            Directory.CreateDirectory(root);
            var create =
                (Func<HttpResult, string, string, string, string, string, CancellationToken, PreparedArtifact>)
                    typeof(PreparedArtifact)
                        .GetMethod("Create", BindingFlags.Static | BindingFlags.NonPublic)
                        .CreateDelegate(typeof(Func<HttpResult, string, string, string, string, string, CancellationToken, PreparedArtifact>));
            try
            {
                foreach (
                    var shape in new (string name, int mib, int smallKiB, int largeCount, int smallCount, bool sustained)[]
                    {
                        (name: "132MiB", mib: 32, smallKiB: 512, largeCount: 4, smallCount: 8, sustained: false),
                        ("264MiB", 64, 1024, 4, 8, false),
                        ("sustained536MiB", 64, 1024, 8, 24, true),
                    }
                )
                {
                    byte[] large = new byte[shape.mib * 1024 * 1024];
                    byte[] small = new byte[shape.smallKiB * 1024];
                    new System.Random(1701).NextBytes(large);
                    new System.Random(1702).NextBytes(small);
                    // Measures byte scheduling/copying without inventing malformed image headers.
                    // No native importer runs; opaque FBX payloads exercise the artifact worker queue.
                    string Hash(byte[] value)
                    {
                        using var sha = SHA256.Create();
                        return BitConverter.ToString(sha.ComputeHash(value)).Replace("-", "").ToLowerInvariant();
                    }
                    string largeHash = Hash(large),
                        smallHash = Hash(small);
                    for (int round = 0; round < 6; round++)
                    {
                        bool candidate = round % 2 == 1;
                        var previous = new PreviousQueue();
                        var current = new ArtifactPreparationQueue(2);
                        var clock = Stopwatch.StartNew();
                        var records = new JObject[shape.largeCount + shape.smallCount];
                        var tasks = new List<Task>();
                        async Task Run(int id, bool isLarge)
                        {
                            byte[] body = isLarge ? large : small;
                            double submitted = clock.Elapsed.TotalMilliseconds;
                            using var input = new HttpResult { Body = body };
                            using var lease = await (
                                candidate
                                    ? current.AcquireAsync(body.Length, CancellationToken.None)
                                    : previous.AcquireAsync(body.Length, CancellationToken.None)
                            ).ConfigureAwait(false);
                            double started = clock.Elapsed.TotalMilliseconds;
                            using var output = await Task.Run(() => create(input, root, "model", "fal", "fbx", "fbx", CancellationToken.None))
                                .ConfigureAwait(false);
                            Assert.AreEqual(body.Length, output.Length);
                            Assert.AreEqual(isLarge ? largeHash : smallHash, output.Hash);
                            records[id] = new JObject
                            {
                                ["large"] = isLarge,
                                ["bytes"] = body.Length,
                                ["submittedMs"] = submitted,
                                ["startedMs"] = started,
                                ["completedMs"] = clock.Elapsed.TotalMilliseconds,
                                ["queuedMs"] = started - submitted,
                                ["latencyMs"] = clock.Elapsed.TotalMilliseconds - submitted,
                            };
                        }
                        // Match the original mixed burst; sustained jobs continue arriving while workers run.
                        int[] burst = { 1, 1, 0, 0, 1, 0, 0, 1, 0, 0, 0, 0 };
                        for (int i = 0; i < records.Length; i++)
                        {
                            bool isLarge = i < burst.Length ? burst[i] == 1 : (i - burst.Length) % 5 == 0;
                            tasks.Add(Run(i, isLarge));
                            if (shape.sustained && i >= 2)
                                await Task.Delay(500);
                        }
                        await Task.WhenAll(tasks);
                        clock.Stop();
                        Assert.IsTrue(records.All(row => row != null));
                        Assert.AreEqual(shape.largeCount, records.Count(row => row.Value<bool>("large")));
                        if (shape.sustained)
                        {
                            double finalSubmission = records.Max(row => row.Value<double>("submittedMs"));
                            int overlappedGrants = records.Count(row => row.Value<double>("queuedMs") >= 1 && row.Value<double>("startedMs") < finalSubmission);
                            Assert.GreaterOrEqual(overlappedGrants, 2, "New requests must arrive across actual queued grants.");
                            Assert.Greater(finalSubmission, records.Min(row => row.Value<double>("completedMs")));
                        }
                        Assert.AreEqual(0, Directory.GetFiles(Path.Combine(root, "Temp/McpAssetPublication")).Length);
                        TestContext.Out.WriteLine(
                            "ARTIFACT_SCHEDULING "
                                + new JObject
                                {
                                    ["shape"] = shape.name,
                                    ["candidate"] = candidate,
                                    ["round"] = round,
                                    ["workers"] = 2,
                                    ["totalMs"] = clock.Elapsed.TotalMilliseconds,
                                    ["jobs"] = new JArray(records),
                                    ["stagingFilesAfter"] = 0,
                                }.ToString(Newtonsoft.Json.Formatting.None)
                        );
                    }
                }
            }
            finally
            {
                Directory.Delete(root, true);
            }
        }
    }
}
