using System;
using System.Collections;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Threading;
using MCPForUnity.Editor.Windows.PlayScenarios;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEngine.TestTools;

namespace MCPForUnityTests.Editor.Windows
{
    /// <summary>The maintained Python harness owns both processes and all external lifetime policy.</summary>
    public sealed class PlayScenarioPlayerImportOverlapTests
    {
        private sealed class Probe
        {
            internal int Attempts;
            internal bool BackgroundReads = true;
            internal bool MainThreadDelivery = true;
            internal int Successful;
            internal int Unavailable;
            internal Exception Error;
            internal ReadResult Latest;
            internal readonly JArray Observations = new JArray();
        }

        private sealed class ReadResult
        {
            internal PlayScenarioSoakEvidence Evidence;
            internal long Started;
            internal long Finished;
        }

        private static long Now => DateTimeOffset.UtcNow.ToUnixTimeMilliseconds();

        private static void Publish(string file, JObject value)
        {
            string temporary = file + ".tmp";
            using (var stream = new FileStream(temporary, FileMode.CreateNew, FileAccess.Write, FileShare.None))
            using (var writer = new StreamWriter(stream))
                writer.Write(value.ToString(Formatting.None));
            File.Move(temporary, file);
        }

        private static ReadResult Read(string file, CancellationToken cancellation, Probe probe, int mainThread)
        {
            Interlocked.Increment(ref probe.Attempts);
            if (Thread.CurrentThread.ManagedThreadId == mainThread)
                probe.BackgroundReads = false;
            long started = Now;
            return new ReadResult
            {
                Evidence = PlayScenarioSoakEvidence.Read(file, cancellation),
                Started = started,
                Finished = Now,
            };
        }

        private static void Apply(object value, Exception error, Probe probe, int mainThread)
        {
            probe.MainThreadDelivery &= Thread.CurrentThread.ManagedThreadId == mainThread;
            probe.Error = error;
            if (error != null)
            {
                probe.Unavailable++;
                return;
            }
            var read = (ReadResult)value;
            probe.Latest = read;
            probe.Successful++;
            int count = read.Evidence.Rows.Count;
            if (probe.Observations.Count != 0 && count <= (int)probe.Observations.Last["rows"])
                return;
            Assert.Less(probe.Observations.Count, 1001, "Import prefix observations must remain bounded.");
            probe.Observations.Add(
                new JObject
                {
                    ["started_unix_ms"] = read.Started,
                    ["finished_unix_ms"] = read.Finished,
                    ["process_id"] = read.Evidence.Summary["process_id"],
                    ["job_id"] = read.Evidence.Summary["job_id"],
                    ["rows"] = count,
                }
            );
        }

        [UnityTest]
        public IEnumerator ConcurrentPlayerImports()
        {
            string root = Environment.GetEnvironmentVariable("MCP_PLAYER_IMPORT_CASE_ROOT");
            if (string.IsNullOrEmpty(root))
                Assert.Ignore("Run through the maintained Player E2E harness to own the Editor and Player lifetimes.");
            root = Path.GetFullPath(root);
            string caseName = Path.GetFileName(root);
            Assert.That(caseName, Is.EqualTo("soak-import-overlap").Or.EqualTo("soak-import-cancel"));
            string output = Path.Combine(root, "editor-import");
            JObject config = JObject.Parse(File.ReadAllText(Path.Combine(output, "config.json")));
            string nonce = (string)config["nonce"];
            Assert.AreEqual(32, nonce.Length);
            int mainThread = Thread.CurrentThread.ManagedThreadId;
            int editorPid;
            using (Process current = Process.GetCurrentProcess())
                editorPid = current.Id;
            var elapsed = Stopwatch.StartNew();
            var probe = new Probe();
            var worker = new PlayScenarioImportWorker();
            string source = null;
            try
            {
                Publish(
                    Path.Combine(output, "ready.json"),
                    new JObject
                    {
                        ["nonce"] = nonce,
                        ["editor_process_id"] = editorPid,
                        ["ready_unix_ms"] = Now,
                    }
                );
                while (!File.Exists(Path.Combine(output, "finished.json")))
                {
                    Assert.Less(elapsed.Elapsed.TotalSeconds, 270, "Owned Player did not finish before import deadline.");
                    if (source == null && Directory.Exists(Path.Combine(root, "cli-output")))
                    {
                        string[] children = Directory.EnumerateDirectories(Path.Combine(root, "cli-output")).Take(2).ToArray();
                        Assert.LessOrEqual(children.Length, 1, "Only one native Player output is allowed.");
                        if (children.Length == 1)
                            source = Path.Combine(children[0], "soak.json");
                    }
                    if (source == null || !File.Exists(source))
                    {
                        yield return null;
                        continue;
                    }
                    string file = source;
                    worker.RequestRead(file, token => Read(file, token, probe, mainThread), (value, error) => Apply(value, error, probe, mainThread));
                    while (worker.IsBusy)
                        yield return null;
                    if (probe.Error != null && !PlayScenarioImportWorker.IsTransient(probe.Error))
                        Assert.Fail("Concurrent import rejected stable evidence: " + probe.Error);
                    yield return null;
                }
                Assert.IsNotNull(source, "Player never published an importable checkpoint.");
                // This is a new read after the CLI has reaped its Player, never an in-flight snapshot.
                string finalFile = source;
                worker.RequestRead(finalFile, token => Read(finalFile, token, probe, mainThread), (value, error) => Apply(value, error, probe, mainThread));
                while (worker.IsBusy)
                    yield return null;
                Assert.IsNull(probe.Error, "Final immutable evidence must import successfully.");
                Assert.That(probe.Latest.Evidence.Completeness, Does.StartWith("Complete:"));
                Assert.GreaterOrEqual(probe.Successful, 10);
                Assert.IsTrue(probe.BackgroundReads && probe.MainThreadDelivery);
            }
            finally
            {
                worker.Dispose();
            }
            Assert.IsFalse(worker.IsBusy || worker.IsPumping, "Disposed import worker retained work or Editor hooks.");
            Publish(
                Path.Combine(output, "receipt.json"),
                new JObject
                {
                    ["schema_version"] = 1,
                    ["nonce"] = nonce,
                    ["case"] = caseName,
                    ["editor_process_id"] = editorPid,
                    ["completed_unix_ms"] = Now,
                    ["read_attempts"] = probe.Attempts,
                    ["successful_imports"] = probe.Successful,
                    ["unavailable_imports"] = probe.Unavailable,
                    ["background_reads"] = probe.BackgroundReads,
                    ["main_thread_delivery"] = probe.MainThreadDelivery,
                    ["worker_disposed"] = worker.IsDisposed,
                    ["pump_released"] = !worker.IsPumping,
                    ["observations"] = probe.Observations,
                    ["final_summary"] = probe.Latest.Evidence.Summary.DeepClone(),
                    ["final_complete"] = true,
                }
            );
        }
    }
}
