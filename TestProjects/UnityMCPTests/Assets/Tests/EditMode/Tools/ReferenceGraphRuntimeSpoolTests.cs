using System;
using System.Diagnostics;
using System.IO;
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
            capture = Activator.CreateInstance(
                captureType,
                BindingFlags.Instance | BindingFlags.NonPublic,
                null,
                new object[] { location, Guid.NewGuid().ToString("N") },
                null
            );
        }

        [TearDown]
        public void TearDown()
        {
            captureType.GetMethod("Close", BindingFlags.Instance | BindingFlags.NonPublic).Invoke(capture, new object[] { "test_complete" });
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
            Assert.IsEmpty(Directory.GetFileSystemEntries(freshEditor));
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
