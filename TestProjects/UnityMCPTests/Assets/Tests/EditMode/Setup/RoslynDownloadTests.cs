using System;
using System.Collections.Generic;
using System.IO;
using System.Threading;
using System.Threading.Tasks;
using MCPForUnity.Editor.Setup;
using NUnit.Framework;

namespace MCPForUnityTests.Editor.Setup
{
    public class RoslynDownloadTests
    {
        private sealed class Transfer : RoslynDownload.ITransfer
        {
            public bool IsDone { get; set; }
            public ulong DownloadedBytes { get; set; }
            public float Progress => -1;
            public bool Succeeded { get; set; } = true;
            public bool Retryable { get; set; }
            public string Error => "synthetic HTTP error";
            public byte[] Data => new byte[] { 1, 2, 3 };
            public int Aborted,
                Disposed;
            public Action OnStart;

            public void Start() => OnStart?.Invoke();

            public void Abort() => Aborted++;

            public void Dispose() => Disposed++;
        }

        [Test]
        public async Task CancellationAbortsAndDisposesWithoutWaitingForCompletion()
        {
            using var cancellation = new CancellationTokenSource();
            var transfer = new Transfer { OnStart = cancellation.Cancel };
            try
            {
                await RoslynDownload.FetchAsync(() => transfer, cancellation.Token);
                Assert.Fail("Cancellation must end an unfinished transfer.");
            }
            catch (OperationCanceledException) { }
            Assert.That(transfer.Aborted, Is.EqualTo(1));
            Assert.That(transfer.Disposed, Is.EqualTo(1));
        }

        [Test]
        public async Task CancellationInterruptsAnActivePollingDelay()
        {
            using var cancellation = new CancellationTokenSource();
            var transfer = new Transfer();
            var pending = RoslynDownload.FetchAsync(
                () => transfer,
                cancellation.Token,
                policy: new RoslynDownload.Policy { PollInterval = TimeSpan.FromDays(1) }
            );
            cancellation.Cancel();
            Assert.That(await Task.WhenAny(pending, Task.Delay(3000)), Is.SameAs(pending));
            try
            {
                await pending;
                Assert.Fail("The cancelled download must not succeed.");
            }
            catch (OperationCanceledException) { }
            Assert.That(transfer.Aborted, Is.EqualTo(1));
            Assert.That(transfer.Disposed, Is.EqualTo(1));
        }

        [Test]
        public void CancellationDuringRetryBackoffDoesNotStartAnotherRequest()
        {
            using var cancellation = new CancellationTokenSource();
            int attempts = 0;
            var transfer = new Transfer
            {
                IsDone = true,
                Succeeded = false,
                Retryable = true,
            };
            Assert.CatchAsync<OperationCanceledException>(async () =>
                await RoslynDownload.FetchAsync(
                    () =>
                    {
                        attempts++;
                        return transfer;
                    },
                    cancellation.Token,
                    delay: (_, token) =>
                    {
                        cancellation.Cancel();
                        return Task.FromCanceled(token);
                    }
                )
            );
            Assert.That(attempts, Is.EqualTo(1));
            Assert.That(transfer.Disposed, Is.EqualTo(1));
        }

        [Test]
        public async Task ContinuousProgressSurvivesThirtySecondsWithoutATotalDeadline()
        {
            double now = 0;
            var transfer = new Transfer();
            var bytes = new List<ulong>();
            var result = await RoslynDownload.FetchAsync(
                () => transfer,
                CancellationToken.None,
                (count, fraction, attempt) => bytes.Add(count),
                seconds: () => now,
                delay: (_, token) =>
                {
                    now += 20;
                    transfer.DownloadedBytes++;
                    transfer.IsDone = now >= 200;
                    return Task.CompletedTask;
                }
            );
            Assert.That(result, Is.EqualTo(new byte[] { 1, 2, 3 }));
            Assert.That(now, Is.EqualTo(200));
            Assert.That(bytes, Does.Contain(5UL));
            Assert.That(transfer.Disposed, Is.EqualTo(1));
            Assert.That(transfer.Aborted, Is.Zero);
        }

        [TestCase(false)]
        [TestCase(true)]
        public void StalledConnectionAndBodyAreAborted(bool receivedBytes)
        {
            double now = 0;
            var transfer = new Transfer { DownloadedBytes = receivedBytes ? 1UL : 0UL };
            Assert.ThrowsAsync<TimeoutException>(async () =>
                await RoslynDownload.FetchAsync(
                    () => transfer,
                    CancellationToken.None,
                    policy: new RoslynDownload.Policy { MaxAttempts = 1 },
                    seconds: () => now,
                    delay: (_, token) =>
                    {
                        now += 121;
                        return Task.CompletedTask;
                    }
                )
            );
            Assert.That(transfer.Aborted, Is.EqualTo(1));
            Assert.That(transfer.Disposed, Is.EqualTo(1));
        }

        [Test]
        public void OptionalTotalDeadlineBoundsAContinuouslyProgressingTransfer()
        {
            double now = 0;
            var transfer = new Transfer();
            Assert.ThrowsAsync<TimeoutException>(async () =>
                await RoslynDownload.FetchAsync(
                    () => transfer,
                    CancellationToken.None,
                    policy: new RoslynDownload.Policy { TotalTimeout = TimeSpan.FromSeconds(30), MaxAttempts = 1 },
                    seconds: () => now,
                    delay: (_, token) =>
                    {
                        now += 20;
                        transfer.DownloadedBytes++;
                        return Task.CompletedTask;
                    }
                )
            );
            Assert.That(transfer.Aborted, Is.EqualTo(1));
            Assert.That(transfer.Disposed, Is.EqualTo(1));
        }

        [Test]
        public void TotalDeadlineAlsoBoundsRetryBackoff()
        {
            double now = 0;
            TimeSpan waited = TimeSpan.Zero;
            int attempts = 0;
            Assert.ThrowsAsync<TimeoutException>(async () =>
                await RoslynDownload.FetchAsync(
                    () =>
                    {
                        attempts++;
                        return new Transfer
                        {
                            IsDone = true,
                            Succeeded = false,
                            Retryable = true,
                        };
                    },
                    CancellationToken.None,
                    policy: new RoslynDownload.Policy { TotalTimeout = TimeSpan.FromMilliseconds(250) },
                    seconds: () => now,
                    delay: (duration, token) =>
                    {
                        waited = duration;
                        now += duration.TotalSeconds;
                        return Task.CompletedTask;
                    }
                )
            );
            Assert.That(waited, Is.EqualTo(TimeSpan.FromMilliseconds(250)));
            Assert.That(attempts, Is.EqualTo(1));
        }

        [TestCase(true, 3)]
        [TestCase(false, 1)]
        public void OnlyTransientGetFailuresAreRetried(bool retryable, int expectedAttempts)
        {
            var transfers = new List<Transfer>();
            Assert.ThrowsAsync<IOException>(async () =>
                await RoslynDownload.FetchAsync(
                    () =>
                    {
                        var transfer = new Transfer
                        {
                            IsDone = true,
                            Succeeded = false,
                            Retryable = retryable,
                        };
                        transfers.Add(transfer);
                        return transfer;
                    },
                    CancellationToken.None,
                    delay: (_, token) => Task.CompletedTask
                )
            );
            Assert.That(transfers.Count, Is.EqualTo(expectedAttempts));
            Assert.That(transfers.TrueForAll(transfer => transfer.Disposed == 1), Is.True);
        }

        [Test]
        public void SizeLimitAbortsBeforeReadingArchiveData()
        {
            var transfer = new Transfer { DownloadedBytes = 129UL * 1024 * 1024 };
            Assert.ThrowsAsync<IOException>(async () => await RoslynDownload.FetchAsync(() => transfer, CancellationToken.None));
            Assert.That(transfer.Aborted, Is.EqualTo(1));
            Assert.That(transfer.Disposed, Is.EqualTo(1));
        }
    }
}
