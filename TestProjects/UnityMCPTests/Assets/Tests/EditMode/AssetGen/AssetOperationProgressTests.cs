using System;
using System.Diagnostics;
using System.IO;
using System.Threading;
using System.Threading.Tasks;
using MCPForUnity.Editor.Services.AssetGen;
using MCPForUnity.Editor.Services.AssetGen.Http;
using NUnit.Framework;
using UnityEngine;

namespace MCPForUnityTests.Editor.AssetGen
{
    public class AssetOperationProgressTests
    {
        [Test]
        public void PhaseCountsStayMonotonicAndLargeTotalsAreNotTruncated()
        {
            var progress = new AssetOperationProgress();
            Assert.IsNull(progress.Read().completed);
            progress.Begin(AssetOperationPhase.Downloading);
            long size = 8L * 1024 * 1024 * 1024;
            progress.ReportBytes(size - 1, size);
            progress.ReportBytes(4, size);
            Assert.AreEqual(size - 1, progress.Read().completed);
            Assert.AreEqual(size, progress.Read().total);
            long sequence = progress.Read().phase_sequence;
            progress.BeginTransfer();
            progress.ReportBytes(3, -1);
            Assert.Greater(progress.Read().phase_sequence, sequence);
            Assert.AreEqual(3, progress.Read().completed);
            Assert.IsNull(progress.Read().total);
            progress.Begin(AssetOperationPhase.Importing);
            Assert.IsNull(progress.Read().completed);
            progress.ReportBytes(size, size);
            Assert.IsNull(progress.Read().completed);
        }

        [Test]
        public void ClosedObservationIgnoresLateWorkersAndFreezesElapsedTime()
        {
            var progress = new AssetOperationProgress();
            progress.Begin(AssetOperationPhase.Downloading);
            progress.ReportBytes(123, -1);
            progress.RequestCancellation();
            progress.Stop();
            var stopped = progress.Read();
            progress.ReportBytes(456, -1);
            progress.Begin(AssetOperationPhase.Importing);
            progress.BeginTransfer();
            Assert.AreEqual(stopped.completed, progress.Read().completed);
            Assert.AreEqual(stopped.phase_elapsed_seconds, progress.Read().phase_elapsed_seconds);
            Assert.IsFalse(progress.Read().active);
            Assert.IsTrue(progress.Read().cancellation_requested);
        }

        [Test]
        public void ChunkReportingHasNoManagedAllocationsAndMeasuresOverhead()
        {
            var progress = new AssetOperationProgress();
            progress.Begin(AssetOperationPhase.Downloading);
            progress.ReportBytes(0, -1);
            const int count = 200000;
            Action reports = () =>
            {
                for (int i = 1; i <= count; i++)
                    progress.ReportBytes(i * 65536L, -1);
            };
            long started = Stopwatch.GetTimestamp();
            int allocations = ManagedAllocation.Count(reports);
            double milliseconds = (Stopwatch.GetTimestamp() - started) * 1000.0 / Stopwatch.Frequency;
            Assert.AreEqual(0, allocations, "Reporting must not allocate per chunk.");
            Assert.AreEqual(count * 65536L, progress.Read().completed);
            TestContext.Out.WriteLine($"progress_updates={count}; elapsed_ms={milliseconds:F3}; gc_alloc_events={allocations}");
        }

        private sealed class PausedBody : MemoryStream
        {
            internal readonly TaskCompletionSource<bool> Paused = new TaskCompletionSource<bool>();
            internal readonly TaskCompletionSource<bool> Release = new TaskCompletionSource<bool>();
            private bool waited;

            internal PausedBody(byte[] bytes)
                : base(bytes) { }

            public override async Task<int> ReadAsync(byte[] buffer, int offset, int count, CancellationToken token)
            {
                if (Position > 0 && !waited)
                {
                    waited = true;
                    Paused.TrySetResult(true);
                    await Release.Task;
                }
                token.ThrowIfCancellationRequested();
                return await base.ReadAsync(buffer, offset, count, token);
            }
        }

        [TestCase(true), TestCase(false)]
        public async Task RealStagedDownloadExposesBytesBeforeCompletionAndKeepsResult(bool observe)
        {
            var progress = observe ? new AssetOperationProgress() : null;
            progress?.Begin(AssetOperationPhase.Downloading);
            byte[] bytes = new byte[2 * 1024 * 1024];
            bytes[bytes.Length - 1] = 91;
            using var body = new PausedBody(bytes);
            Task<DownloadedArtifact> task = DownloadedArtifact.ReadAsync(
                body,
                -1,
                AssetDownloadTransport.MaxDownloadBytes,
                Path.GetDirectoryName(Application.dataPath),
                CancellationToken.None,
                progress
            );
            try
            {
                await body.Paused.Task;
                Assert.IsFalse(task.IsCompleted);
                if (observe)
                {
                    Assert.AreEqual(65536, progress.Read().completed);
                    Assert.IsNull(progress.Read().total);
                }
            }
            finally
            {
                body.Release.TrySetResult(true);
            }
            string path;
            using (DownloadedArtifact result = await task)
            {
                path = result.Path;
                Assert.AreEqual(bytes.Length, result.Length);
                Stream stream = result.Rewind();
                stream.Seek(-1, SeekOrigin.End);
                Assert.AreEqual(91, stream.ReadByte());
                if (observe)
                    Assert.AreEqual(bytes.Length, progress.Read().completed);
            }
            Assert.IsFalse(File.Exists(path));
        }
    }
}
