using System;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Text;
using MCPForUnity.Editor.Tools;
using MCPForUnity.Runtime;
using Newtonsoft.Json.Linq;
using NUnit.Framework;

namespace MCPForUnityTests.EditMode.Tools
{
    public sealed class ReferenceGraphRuntimeSpoolTests
    {
        private string root,
            location;
        private object capture;
        private Type captureType;

        [SetUp]
        public void SetUp()
        {
            root = Path.Combine(Path.GetTempPath(), "McpNativeSpool_" + Guid.NewGuid().ToString("N"));
            string editor = Path.Combine(root, Guid.NewGuid().ToString("N"));
            Directory.CreateDirectory(editor);
            location = Path.Combine(editor, Guid.NewGuid().ToString("N") + Guid.NewGuid().ToString("N"));
            captureType = typeof(ManageReferenceGraph).Assembly.GetType("MCPForUnity.Editor.Tools.ReferenceGraphRuntimeCapture+Capture");
            captureType
                .DeclaringType.GetMethod("HandleAt", BindingFlags.Static | BindingFlags.NonPublic)
                .Invoke(
                    null,
                    new object[]
                    {
                        new JObject { ["action"] = "runtime_start", ["token"] = Path.GetFileName(location) },
                        location,
                    }
                );
            capture = ActiveCaptures()[Path.GetFileName(location)];
        }

        [TearDown]
        public void TearDown()
        {
            captureType.GetMethod("Close", BindingFlags.Instance | BindingFlags.NonPublic).Invoke(capture, new object[] { "test_complete" });
            ActiveCaptures().Remove(Path.GetFileName(location));
            captureType.DeclaringType.GetMethod("UnsubscribeIfIdle", BindingFlags.Static | BindingFlags.NonPublic).Invoke(null, null);
            // This directory is a unique test-owned fixture, never the native
            // profile spool or a pre-existing retained capture.
            if (Directory.Exists(root))
                Directory.Delete(root, true);
        }

        private JObject Metadata() => JObject.Parse(File.ReadAllText(location + ".json"));

        private JObject Status() => (JObject)captureType.GetMethod("Status", BindingFlags.Instance | BindingFlags.NonPublic).Invoke(capture, null);

        private JObject Read() =>
            (JObject)captureType.GetMethod("Read", BindingFlags.Instance | BindingFlags.NonPublic).Invoke(capture, new object[] { 0L, 128 });

        private void Drain() => captureType.GetMethod("Drain", BindingFlags.Instance | BindingFlags.NonPublic).Invoke(capture, null);

        private System.Collections.IDictionary ActiveCaptures() =>
            (System.Collections.IDictionary)captureType.DeclaringType.GetField("Active", BindingFlags.Static | BindingFlags.NonPublic).GetValue(null);

        private JObject Stop() =>
            (JObject)
                captureType
                    .DeclaringType.GetMethod("HandleAt", BindingFlags.Static | BindingFlags.NonPublic)
                    .Invoke(
                        null,
                        new object[]
                        {
                            new JObject { ["action"] = "runtime_stop", ["token"] = Path.GetFileName(location) },
                            location,
                        }
                    );

        private void UpdateCaptures() => captureType.DeclaringType.GetMethod("DrainCaptures", BindingFlags.Static | BindingFlags.NonPublic).Invoke(null, null);

        private JObject HandleAt(string action, string path) =>
            (JObject)
                captureType
                    .DeclaringType.GetMethod("HandleAt", BindingFlags.Static | BindingFlags.NonPublic)
                    .Invoke(
                        null,
                        new object[]
                        {
                            new JObject { ["action"] = action, ["token"] = Path.GetFileName(path) },
                            path,
                        }
                    );

        [Test]
        public void ReleasePrunesEmptyOwnerDirectory()
        {
            Assert.IsTrue(HandleAt("runtime_release", location).Value<bool>("released"));
            Assert.IsFalse(Directory.Exists(Path.GetDirectoryName(location)), "The last released capture must not retain an empty owner.");
            Assert.IsTrue(HandleAt("runtime_release", location).Value<bool>("released"));
            Assert.IsFalse(Directory.Exists(Path.GetDirectoryName(location)));
        }

        [Test]
        public void StartPrunesPreexistingEmptyOwners()
        {
            string[] empty = Enumerable.Range(0, 3).Select(_ => Path.Combine(root, Guid.NewGuid().ToString("N"))).ToArray();
            foreach (string directory in empty)
                Directory.CreateDirectory(directory);
            string next = Path.Combine(root, Guid.NewGuid().ToString("N"), Guid.NewGuid().ToString("N") + Guid.NewGuid().ToString("N"));
            try
            {
                Assert.AreEqual("active", HandleAt("runtime_start", next).Value<string>("state"));
                foreach (string directory in empty)
                    Assert.IsFalse(Directory.Exists(directory), "Start must reclaim bounded old empty owners.");
                Assert.IsTrue(File.Exists(location + ".events"));
            }
            finally
            {
                HandleAt("runtime_release", next);
            }
        }

        [Test]
        public void RepeatedOwnerReleaseFitsReducedInventoryLimit()
        {
            for (int i = 0; i < 12; i++)
            {
                string next = Path.Combine(root, Guid.NewGuid().ToString("N"), Guid.NewGuid().ToString("N") + Guid.NewGuid().ToString("N"));
                try
                {
                    Assert.AreEqual("active", HandleAt("runtime_start", next).Value<string>("state"));
                }
                finally
                {
                    HandleAt("runtime_release", next);
                }
                Assert.IsFalse(Directory.Exists(Path.GetDirectoryName(next)));
                lock (ReferenceGraphRuntimeSpool.Gate)
                    using (ReferenceGraphRuntimeSpool.Lock(Path.Combine(root, ".spool.lock")))
                        Assert.IsNull(ReferenceGraphRuntimeSpool.Pressure(location, 0, true, 8));
            }
        }

        [Test]
        public void OldEmptySweepIsBoundedAndPreservesUnknownContents()
        {
            string unknown = Path.Combine(root, "unknown-owner");
            Directory.CreateDirectory(unknown);
            string nonempty = Path.Combine(root, Guid.NewGuid().ToString("N"));
            Directory.CreateDirectory(nonempty);
            File.WriteAllText(Path.Combine(nonempty, "unknown-content"), "retained");
            string[] empty = Enumerable.Range(0, 19).Select(_ => Path.Combine(root, Guid.NewGuid().ToString("N"))).ToArray();
            foreach (string directory in empty)
                Directory.CreateDirectory(directory);
            lock (ReferenceGraphRuntimeSpool.Gate)
                using (ReferenceGraphRuntimeSpool.Lock(Path.Combine(root, ".spool.lock")))
                {
                    ReferenceGraphRuntimeSpool.PruneEmptyOwners(root, 8);
                    Assert.GreaterOrEqual(empty.Count(Directory.Exists), 11, "A sweep must inspect at most its entry budget.");
                    for (int pass = 0; pass < 8 && empty.Any(Directory.Exists); pass++)
                        ReferenceGraphRuntimeSpool.PruneEmptyOwners(root, 8);
                }
            Assert.IsFalse(empty.Any(Directory.Exists));
            Assert.IsTrue(Directory.Exists(unknown));
            Assert.AreEqual("retained", File.ReadAllText(Path.Combine(nonempty, "unknown-content")));
            Assert.IsTrue(File.Exists(location + ".events"));
        }

        [Test]
        public void ReleasingOneCapturePreservesConcurrentSameOwnerCapture()
        {
            string next = Path.Combine(Path.GetDirectoryName(location), Guid.NewGuid().ToString("N") + Guid.NewGuid().ToString("N"));
            try
            {
                Assert.AreEqual("active", HandleAt("runtime_start", next).Value<string>("state"));
                HandleAt("runtime_release", location);
                Assert.IsTrue(Directory.Exists(Path.GetDirectoryName(next)));
                Assert.IsTrue(File.Exists(next + ".lease"));
                Assert.IsTrue(File.Exists(next + ".events"));
                Assert.AreEqual("active", HandleAt("runtime_start", next).Value<string>("state"));
            }
            finally
            {
                HandleAt("runtime_release", next);
            }
            Assert.IsFalse(Directory.Exists(Path.GetDirectoryName(next)));
        }

        [Test, Platform(Include = "Win")]
        public void ContendedRootPreventsReleaseAndOldEmptySweep()
        {
            string empty = Path.Combine(root, Guid.NewGuid().ToString("N"));
            Directory.CreateDirectory(empty);
            string next = Path.Combine(empty, Guid.NewGuid().ToString("N") + Guid.NewGuid().ToString("N"));
            using (HoldRoot())
            {
                Assert.Throws<TargetInvocationException>(() => HandleAt("runtime_release", location));
                Assert.Throws<TargetInvocationException>(() => HandleAt("runtime_start", next));
                Assert.IsTrue(File.Exists(location + ".events"));
                Assert.IsTrue(Directory.Exists(empty));
            }
            Assert.IsTrue(HandleAt("runtime_release", location).Value<bool>("released"));
            try
            {
                Assert.AreEqual("active", HandleAt("runtime_start", next).Value<string>("state"));
                Assert.IsTrue(File.Exists(next + ".events"));
            }
            finally
            {
                HandleAt("runtime_release", next);
            }
        }

        [Test, Platform(Include = "Win")]
        public void RecoveredContentionStopFinishesMoreThanOneBatchAcrossUpdates()
        {
            using (HoldRoot())
                for (int i = 0; i < 33; i++)
                    ReferenceGraphRuntime.Load("custom", "queued/" + i);
            JObject stopping = Stop();
            Assert.AreEqual("finishing", stopping.Value<string>("state"));
            Assert.AreEqual(32, stopping.Value<long>("count"));
            Assert.AreEqual(1, stopping.Value<int>("pending_count"));
            Assert.AreEqual(0, stopping.Value<long>("unwritten_count"));
            Assert.IsTrue(ActiveCaptures().Contains(Path.GetFileName(location)), "Finishing work must retain its owning capture.");
            Assert.IsFalse(ReferenceGraphRuntime.Enabled, "Stop must retire new observations before draining accepted work.");
            ReferenceGraphRuntime.Load("custom", "late");
            captureType
                .GetMethod("Record", BindingFlags.Instance | BindingFlags.NonPublic)
                .Invoke(capture, new object[] { new ReferenceGraphRuntimeEvent("load", "custom", "late-callback", "", null, "") });
            UpdateCaptures();
            Assert.AreEqual("stopped", Metadata().Value<string>("state"));
            Assert.AreEqual(33, Metadata().Value<long>("count"));
            Assert.AreEqual(0, Metadata().Value<long>("unwritten_count"));
            Assert.IsFalse(ActiveCaptures().Contains(Path.GetFileName(location)));
            JObject read = Read();
            for (int i = 0; i < 33; i++)
            {
                Assert.AreEqual("queued/" + i, read["events"][i].Value<string>("key"));
                Assert.AreEqual(i + 1, read["events"][i].Value<long>("sequence"));
            }
        }

        [Test, Platform(Include = "Win")]
        public void ContendedStopRemainsFinishingUntilRecoveryAndRepeatedStopIsIdempotent()
        {
            using (HoldRoot())
            {
                for (int i = 0; i < 65; i++)
                    ReferenceGraphRuntime.Load("custom", "waiting/" + i);
                JObject first = Stop();
                Assert.AreEqual("finishing", first.Value<string>("state"));
                Assert.AreEqual(65, first.Value<int>("pending_count"));
                Assert.AreEqual(0, first.Value<long>("count"));
                Assert.IsTrue(first.Value<bool>("storage_waiting"));
                Assert.IsFalse(ReferenceGraphRuntime.Enabled);
                bool acquired = ReferenceGraphRuntimeSpool.TryLock(location + ".lease", out FileStream competing);
                competing?.Dispose();
                Assert.IsFalse(acquired, "Pending accepted work must retain its cross-process capture lease.");
                JObject again = Stop();
                Assert.AreEqual(65, again.Value<int>("pending_count"));
                UpdateCaptures();
                Assert.AreEqual("finishing", Status().Value<string>("state"));
                Assert.AreEqual(65, Status().Value<int>("pending_count"));
            }
            JObject unchanged = Stop();
            Assert.AreEqual("finishing", unchanged.Value<string>("state"));
            Assert.AreEqual(0, unchanged.Value<long>("count"));
            Assert.AreEqual(65, unchanged.Value<int>("pending_count"));
            for (int update = 0; update < 3; update++)
            {
                UpdateCaptures();
                Assert.AreEqual(Math.Min((update + 1) * 32, 65), Status().Value<long>("count"));
            }
            Assert.AreEqual("stopped", Metadata().Value<string>("state"));
            Assert.AreEqual(65, Stop().Value<long>("count"));
            Assert.AreEqual(0, Metadata().Value<long>("unwritten_count"));
            Assert.IsTrue(ReferenceGraphRuntimeSpool.TryLock(location + ".lease", out FileStream settled));
            settled.Dispose();
        }

        [Test, Platform(Include = "Win")]
        public void ReleaseCancelsFinishingWorkAndRemovesOnlyItsOwnedCapture()
        {
            string retained = Path.Combine(Path.GetDirectoryName(location), new string('e', 64) + ".events");
            File.WriteAllText(retained, "retained evidence");
            using (HoldRoot())
            {
                for (int i = 0; i < 65; i++)
                    ReferenceGraphRuntime.Load("custom", "release/" + i);
                Assert.AreEqual("finishing", Stop().Value<string>("state"));
            }
            JObject released = (JObject)
                captureType
                    .DeclaringType.GetMethod("HandleAt", BindingFlags.Static | BindingFlags.NonPublic)
                    .Invoke(
                        null,
                        new object[]
                        {
                            new JObject { ["action"] = "runtime_release", ["token"] = Path.GetFileName(location) },
                            location,
                        }
                    );
            Assert.IsTrue(released.Value<bool>("released"));
            Assert.IsFalse(ReferenceGraphRuntime.Enabled);
            Assert.IsFalse(ActiveCaptures().Contains(Path.GetFileName(location)));
            foreach (string extension in new[] { ".json", ".json.pending", ".events", ".lease" })
                Assert.IsFalse(File.Exists(location + extension));
            UpdateCaptures();
            Assert.IsFalse(File.Exists(location + ".events"));
            Assert.AreEqual("retained evidence", File.ReadAllText(retained));
        }

        [Test, Platform(Include = "Win")]
        public void OrphanedFinishingReceiptReportsInterruptedUnconfirmedWork()
        {
            JObject persisted;
            using (HoldRoot())
            {
                for (int i = 0; i < 33; i++)
                    ReferenceGraphRuntime.Load("custom", "orphaned/" + i);
                Stop();
                persisted = Metadata();
                captureType.GetMethod("Close", BindingFlags.Instance | BindingFlags.NonPublic).Invoke(capture, new object[] { "test_complete" });
            }
            ActiveCaptures().Remove(Path.GetFileName(location));
            // Simulate a process disappearing after persisting finishing metadata.
            File.WriteAllText(location + ".json", persisted.ToString());
            JObject interrupted = Stop();
            Assert.AreEqual("interrupted", interrupted.Value<string>("state"));
            Assert.AreEqual(0, interrupted.Value<int>("pending_count"));
            Assert.AreEqual(0, interrupted.Value<long>("pending_bytes"));
            Assert.IsFalse(interrupted.Value<bool>("storage_waiting"));
            Assert.AreEqual(33, interrupted.Value<long>("unwritten_count"));
            Assert.IsFalse(ReferenceGraphRuntime.Enabled);
        }

        [Test, Platform(Include = "Win")]
        public void ForcedShutdownDuringFinishingReportsTheUndrainedQueueAndRetiresOwnership()
        {
            using (HoldRoot())
            {
                for (int i = 0; i < 33; i++)
                    ReferenceGraphRuntime.Load("custom", "forced/" + i);
                Assert.AreEqual("finishing", Stop().Value<string>("state"));
                captureType.DeclaringType.GetMethod("CloseAll", BindingFlags.Static | BindingFlags.NonPublic).Invoke(null, new object[] { "domain_reload" });
                JObject forced = Metadata();
                Assert.AreEqual("storage_backpressure", forced.Value<string>("state"));
                Assert.AreEqual("pending_events_on_domain_reload", forced.Value<string>("stop_reason"));
                Assert.AreEqual(33, forced.Value<long>("unwritten_count"));
                Assert.AreEqual(0, forced.Value<int>("pending_count"));
                Assert.IsFalse(ReferenceGraphRuntime.Enabled);
                Assert.IsFalse(ActiveCaptures().Contains(Path.GetFileName(location)));
            }
            byte[] closed = Events();
            UpdateCaptures();
            ReferenceGraphRuntime.Load("custom", "late");
            CollectionAssert.AreEqual(closed, Events());
        }

        private sealed class CountingWriter : BinaryWriter
        {
            internal int Flushes;
            internal bool FailFlush;

            internal CountingWriter(Stream stream)
                : base(stream, Encoding.UTF8) { }

            public override void Flush()
            {
                Flushes++;
                if (FailFlush)
                    throw new IOException("Injected batch flush failure.");
                base.Flush();
            }
        }

        private CountingWriter CountFlushes()
        {
            FieldInfo field = captureType.GetField("writer", BindingFlags.Instance | BindingFlags.NonPublic);
            var original = (BinaryWriter)field.GetValue(capture);
            original.Flush();
            var counted = new CountingWriter(original.BaseStream);
            field.SetValue(capture, counted);
            return counted;
        }

        [Test]
        public void SmallObserverBurstWaitsForOneUpdateBatch()
        {
            CountingWriter counted = CountFlushes();
            for (int i = 0; i < 17; i++)
                ReferenceGraphRuntime.Load("custom", "queued/" + i);
            Assert.AreEqual(0, counted.Flushes, "Ordinary small observations must share a later census/flush.");
            Assert.AreEqual(17, Status().Value<int>("pending_count"));
            Assert.AreEqual(0, Events().Length);
            Drain();
            Assert.AreEqual(1, counted.Flushes, "One update batch must flush once, not once per event.");
            Assert.AreEqual(17, Status().Value<long>("count"));
            Assert.AreEqual(0, Status().Value<int>("pending_count"));
            JObject read = Read();
            for (int i = 0; i < 17; i++)
            {
                Assert.AreEqual("queued/" + i, read["events"][i].Value<string>("key"));
                Assert.AreEqual(i + 1, read["events"][i].Value<long>("sequence"));
            }
        }

        [Test]
        public void SustainedObserverBurstUsesBoundedFallbackBatchesAndStopKeepsTheRemainder()
        {
            CountingWriter counted = CountFlushes();
            for (int i = 0; i < 97; i++)
                ReferenceGraphRuntime.Load("custom", "burst/" + i);
            Assert.AreEqual(3, counted.Flushes, "Uncontended sustained bursts must census/flush once per 32 observations.");
            Assert.AreEqual(96, Status().Value<long>("count"));
            Assert.AreEqual(1, Status().Value<int>("pending_count"));
            captureType.GetMethod("Close", BindingFlags.Instance | BindingFlags.NonPublic).Invoke(capture, new object[] { "stopped" });
            JObject stopped = Metadata();
            Assert.AreEqual("stopped", stopped.Value<string>("state"));
            Assert.AreEqual(97, stopped.Value<long>("count"));
            Assert.AreEqual(0, stopped.Value<long>("unwritten_count"));
            Assert.IsFalse(ReferenceGraphRuntime.Enabled);
            JObject read = Read();
            for (int i = 0; i < 97; i++)
            {
                Assert.AreEqual("burst/" + i, read["events"][i].Value<string>("key"));
                Assert.AreEqual(i + 1, read["events"][i].Value<long>("sequence"));
            }
            byte[] closed = Events();
            ReferenceGraphRuntime.Load("custom", "after-stop");
            Drain();
            CollectionAssert.AreEqual(closed, Events(), "Retired captures must not retain observer/update work.");
        }

        [Test]
        public void FailedBatchFlushRetiresObserverAndReportsEveryUnconfirmedObservation()
        {
            CountingWriter counted = CountFlushes();
            for (int i = 0; i < 17; i++)
                ReferenceGraphRuntime.Load("custom", "unconfirmed/" + i);
            counted.FailFlush = true;
            Drain();
            JObject stopped = Metadata();
            Assert.AreEqual("observer_failed", stopped.Value<string>("state"));
            Assert.AreEqual("IOException", stopped.Value<string>("error"));
            Assert.AreEqual(0, stopped.Value<long>("count"));
            Assert.AreEqual(17, stopped.Value<long>("unwritten_count"));
            Assert.AreEqual(0, stopped.Value<int>("pending_count"));
            Assert.IsFalse(ReferenceGraphRuntime.Enabled);
            Drain();
            Assert.AreEqual(17, Metadata().Value<long>("unwritten_count"));
        }

        [TestCase("domain_reload")]
        [TestCase("editor_quit")]
        [TestCase("play_transition")]
        public void LifecycleCloseFlushesThePendingBatchAndRetiresObserver(string reason)
        {
            for (int i = 0; i < 17; i++)
                ReferenceGraphRuntime.Load("custom", "closing/" + i);
            captureType.GetMethod("Close", BindingFlags.Instance | BindingFlags.NonPublic).Invoke(capture, new object[] { reason });
            Assert.AreEqual(reason, Metadata().Value<string>("state"));
            Assert.AreEqual(17, Metadata().Value<long>("count"));
            Assert.AreEqual(0, Metadata().Value<long>("unwritten_count"));
            Assert.IsFalse(ReferenceGraphRuntime.Enabled);
            byte[] closed = Events();
            ReferenceGraphRuntime.Load("custom", "late");
            Drain();
            CollectionAssert.AreEqual(closed, Events());
        }

        [Test]
        public void ByteThresholdFallbackPreservesLargePendingEventsAtImmediateStop()
        {
            CountingWriter counted = CountFlushes();
            string payload = new string('x', 600 * 1024);
            ReferenceGraphRuntime.Load("custom", payload);
            Assert.AreEqual(0, counted.Flushes);
            ReferenceGraphRuntime.Load("custom", payload);
            Assert.AreEqual(1, counted.Flushes);
            Assert.AreEqual(1, Status().Value<long>("count"));
            Assert.AreEqual(1, Status().Value<int>("pending_count"));
            captureType.GetMethod("Close", BindingFlags.Instance | BindingFlags.NonPublic).Invoke(capture, new object[] { "stopped" });
            Assert.AreEqual(2, Metadata().Value<long>("count"));
            Assert.AreEqual(0, Metadata().Value<long>("unwritten_count"));
        }

        [Test, Explicit("Controlled retained-spool timing; reports measured observer and drain cost without a frame-time claim.")]
        public void RetainedSpoolObserverAndDrainMeasurements()
        {
            for (int i = 0; i < 200; i++)
            {
                string token = i.ToString("x64");
                File.WriteAllText(Path.Combine(Path.GetDirectoryName(location), token + ".json"), "{}");
                File.WriteAllBytes(Path.Combine(Path.GetDirectoryName(location), token + ".events"), new byte[4096]);
            }
            CountingWriter counted = CountFlushes();
            var observer = Stopwatch.StartNew();
            for (int i = 0; i < 97; i++)
                ReferenceGraphRuntime.Load("custom", "measured/" + i);
            observer.Stop();
            int observerFlushes = counted.Flushes;
            var drain = Stopwatch.StartNew();
            Drain();
            drain.Stop();
            Console.WriteLine(
                $"R11 retained_captures=200 observations=97 observer_flushes={observerFlushes} observer_ms={observer.Elapsed.TotalMilliseconds:F3} update_flushes={counted.Flushes - observerFlushes} update_ms={drain.Elapsed.TotalMilliseconds:F3}"
            );
            Assert.AreEqual(97, Status().Value<long>("count"));
            Assert.AreEqual(0, Status().Value<int>("pending_count"));
        }

        private byte[] Events()
        {
            using (var stream = new FileStream(location + ".events", FileMode.Open, FileAccess.Read, FileShare.ReadWrite))
            using (var buffer = new MemoryStream())
            {
                stream.CopyTo(buffer);
                return buffer.ToArray();
            }
        }

        private FileStream HoldRoot()
        {
            var holder = new FileStream(Path.Combine(root, ".spool.lock"), FileMode.OpenOrCreate, FileAccess.ReadWrite, FileShare.ReadWrite);
            holder.Lock(0, 1);
            return holder;
        }

        [Test]
        public void OversizedEventStopsWithReasonAndPreservesPreviousRecord()
        {
            ReferenceGraphRuntime.Load("custom", "retained");
            ReferenceGraphRuntime.Load("custom", new string('x', 1024 * 1024));
            JObject metadata = Metadata();
            Assert.AreEqual("storage_limit", metadata.Value<string>("state"));
            Assert.AreEqual("max_record_bytes", metadata.Value<string>("stop_reason"));
            Assert.AreEqual(1, metadata.Value<long>("count"));
            Assert.AreEqual("retained", Read()["events"][0].Value<string>("key"));
            Assert.IsFalse(ReferenceGraphRuntime.Enabled);
        }

        [Test]
        public void CaptureLimitStopsBeforeAppendingAndKeepsExistingLength()
        {
            var writer = (BinaryWriter)captureType.GetField("writer", BindingFlags.Instance | BindingFlags.NonPublic).GetValue(capture);
            long limit = Metadata().Value<long>("max_capture_bytes");
            writer.BaseStream.SetLength(limit - 4);
            writer.BaseStream.Position = limit - 4;
            ReferenceGraphRuntime.Load("custom", "refused");
            Assert.AreEqual("max_capture_bytes", Metadata().Value<string>("stop_reason"));
            Assert.AreEqual(limit - 4, new FileInfo(location + ".events").Length);
            Assert.AreEqual(0, Metadata().Value<long>("count"));
        }

        [Test]
        public void RetainedRootPressureStopsWithoutDeletingOtherCapture()
        {
            string retained = Path.Combine(Path.GetDirectoryName(location), new string('e', 64) + ".events");
            long total = Metadata().Value<long>("max_total_bytes");
            using (var stream = new FileStream(retained, FileMode.CreateNew, FileAccess.Write))
                stream.SetLength(total);
            ReferenceGraphRuntime.Load("custom", "refused");
            Drain();
            Assert.AreEqual("max_total_bytes", Metadata().Value<string>("stop_reason"));
            Assert.AreEqual(total, new FileInfo(retained).Length);
            Assert.AreEqual(0, new FileInfo(location + ".events").Length);
        }

        [Test]
        public void MetadataBindsExactEditorAndCaptureIdentity()
        {
            JObject metadata = Metadata();
            Assert.AreEqual("unity-mcp-native-runtime", metadata.Value<string>("format"));
            Assert.AreEqual(1, metadata.Value<int>("version"));
            Assert.AreEqual(Path.GetFileName(Path.GetDirectoryName(location)), metadata.Value<string>("editor_id"));
            Assert.AreEqual(Path.GetFileName(location), metadata.Value<string>("capture_id"));
            Assert.AreEqual("byte-range-v1", metadata.Value<string>("lease_protocol"));
        }

        [Test]
        public void FreshEditorRejectedByRetainedRootCanReleaseWithoutCreatingCaptureFiles()
        {
            captureType.GetMethod("Close", BindingFlags.Instance | BindingFlags.NonPublic).Invoke(capture, new object[] { "retained" });
            string retained = location + ".events";
            long total = Metadata().Value<long>("max_total_bytes");
            using (var stream = new FileStream(retained, FileMode.Open, FileAccess.Write))
                stream.SetLength(total);
            byte[] retainedMetadata = File.ReadAllBytes(location + ".json");
            string freshEditor = Path.Combine(root, Guid.NewGuid().ToString("N"));
            string token = Guid.NewGuid().ToString("N") + Guid.NewGuid().ToString("N");
            string freshLocation = Path.Combine(freshEditor, token);
            MethodInfo handler = captureType.DeclaringType.GetMethod("HandleAt", BindingFlags.Static | BindingFlags.NonPublic);
            JObject start = (JObject)
                handler.Invoke(
                    null,
                    new object[]
                    {
                        new JObject { ["action"] = "runtime_start", ["token"] = token },
                        freshLocation,
                    }
                );
            Assert.AreEqual("storage_limit", start.Value<string>("state"));
            Assert.AreEqual("max_total_bytes", start.Value<string>("stop_reason"));
            Assert.IsFalse(Directory.Exists(freshEditor));
            for (int i = 0; i < 2; i++)
            {
                JObject release = (JObject)
                    handler.Invoke(
                        null,
                        new object[]
                        {
                            new JObject { ["action"] = "runtime_release", ["token"] = token },
                            freshLocation,
                        }
                    );
                Assert.IsTrue(release.Value<bool>("released"));
                Assert.IsFalse(Directory.Exists(freshEditor));
            }
            // A missing token in an already existing Editor directory is also
            // an idempotent no-op, without manufacturing an ownership file.
            Directory.CreateDirectory(freshEditor);
            JObject emptyRelease = (JObject)
                handler.Invoke(
                    null,
                    new object[]
                    {
                        new JObject { ["action"] = "runtime_release", ["token"] = token },
                        freshLocation,
                    }
                );
            Assert.IsTrue(emptyRelease.Value<bool>("released"));
            Assert.IsFalse(Directory.Exists(freshEditor));
            Assert.AreEqual(total, new FileInfo(retained).Length);
            CollectionAssert.AreEqual(retainedMetadata, File.ReadAllBytes(location + ".json"));
        }

        [Test, Platform(Include = "Win")]
        public void HeldMaintenanceRootLeaseBuffersAndReplaysWithoutRetiringCapture()
        {
            using (HoldRoot())
            {
                ReferenceGraphRuntime.Load("custom", "first");
                ReferenceGraphRuntime.Load("custom", "second");
                Drain();
                JObject waiting = Status();
                Assert.AreEqual("active", waiting.Value<string>("state"));
                Assert.IsTrue(waiting.Value<bool>("storage_waiting"));
                Assert.AreEqual(2, waiting.Value<int>("pending_count"));
                Assert.AreEqual(0, waiting.Value<long>("count"));
                Assert.AreEqual(0, Events().Length);
                Assert.IsTrue(ReferenceGraphRuntime.Enabled);
            }
            JObject read = Read();
            Assert.AreEqual("active", read.Value<string>("state"));
            Assert.AreEqual(2, read.Value<long>("count"));
            Assert.AreEqual(0, read.Value<int>("pending_count"));
            Assert.AreEqual("first", read["events"][0].Value<string>("key"));
            Assert.AreEqual("second", read["events"][1].Value<string>("key"));
            Assert.AreEqual(1, read["events"][0].Value<long>("sequence"));
            Assert.AreEqual(2, read["events"][1].Value<long>("sequence"));
            ReferenceGraphRuntime.Load("custom", "third");
            Assert.AreEqual(3, Read().Value<long>("count"));
            Assert.IsTrue(ReferenceGraphRuntime.Enabled);
        }

        [Test, Platform(Include = "Win")]
        public void ExhaustedContentionBufferReportsEveryUnwrittenObservation()
        {
            using (HoldRoot())
            {
                for (int i = 0; i < 129; i++)
                    ReferenceGraphRuntime.Load("custom", "queued/" + i);
                JObject stopped = Metadata();
                Assert.AreEqual("storage_backpressure", stopped.Value<string>("state"));
                Assert.AreEqual("pending_capacity", stopped.Value<string>("stop_reason"));
                Assert.AreEqual(129, stopped.Value<long>("unwritten_count"));
                Assert.AreEqual(0, stopped.Value<long>("count"));
                Assert.AreEqual(0, Events().Length);
                Assert.IsFalse(ReferenceGraphRuntime.Enabled);
            }
        }

        [Test, Platform(Include = "Win")]
        public void ClosingDuringContentionPersistsAnHonestTerminalReceipt()
        {
            using (HoldRoot())
            {
                ReferenceGraphRuntime.Load("custom", "queued");
                captureType.GetMethod("Close", BindingFlags.Instance | BindingFlags.NonPublic).Invoke(capture, new object[] { "stopped" });
                JObject stopped = Metadata();
                Assert.AreEqual("storage_backpressure", stopped.Value<string>("state"));
                Assert.AreEqual("pending_events_on_stopped", stopped.Value<string>("stop_reason"));
                Assert.AreEqual(1, stopped.Value<long>("unwritten_count"));
                Assert.AreEqual(0, stopped.Value<long>("count"));
                Assert.IsFalse(ReferenceGraphRuntime.Enabled);
            }
        }
    }
}
