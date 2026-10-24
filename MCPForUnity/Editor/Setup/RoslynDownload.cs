using System;
using System.Diagnostics;
using System.IO;
using System.Threading;
using System.Threading.Tasks;

namespace MCPForUnity.Editor.Setup
{
    internal static class RoslynDownload
    {
        internal interface ITransfer : IDisposable
        {
            void Start();
            bool IsDone { get; }
            ulong DownloadedBytes { get; }
            float Progress { get; }
            bool Succeeded { get; }
            bool Retryable { get; }
            string Error { get; }
            byte[] Data { get; }
            void Abort();
        }

        internal sealed class Policy
        {
            internal TimeSpan ConnectTimeout = TimeSpan.FromSeconds(90);
            internal TimeSpan NoProgressTimeout = TimeSpan.FromSeconds(120);
            internal TimeSpan? TotalTimeout;
            internal TimeSpan PollInterval = TimeSpan.FromMilliseconds(100);
            internal TimeSpan RetryDelay = TimeSpan.FromSeconds(1);
            internal int MaxAttempts = 3;
        }

        internal static async Task<byte[]> FetchAsync(
            Func<ITransfer> create,
            CancellationToken cancellationToken,
            Action<ulong, float, int> progress = null,
            Policy policy = null,
            Func<double> seconds = null,
            Func<TimeSpan, CancellationToken, Task> delay = null
        )
        {
            policy ??= new Policy();
            if (
                policy.ConnectTimeout <= TimeSpan.Zero
                || policy.NoProgressTimeout <= TimeSpan.Zero
                || policy.TotalTimeout <= TimeSpan.Zero
                || policy.PollInterval <= TimeSpan.Zero
                || policy.RetryDelay < TimeSpan.Zero
                || policy.MaxAttempts < 1
            )
                throw new ArgumentOutOfRangeException(nameof(policy));
            var clock = Stopwatch.StartNew();
            seconds ??= () => clock.Elapsed.TotalSeconds;
            delay ??= Task.Delay;
            double overallStart = seconds();
            for (int attempt = 1; attempt <= policy.MaxAttempts; attempt++)
            {
                cancellationToken.ThrowIfCancellationRequested();
                CheckTotalTimeout();
                try
                {
                    using (var transfer = create())
                    {
                        try
                        {
                            double started = seconds(),
                                lastProgress = started;
                            ulong previousBytes = 0;
                            transfer.Start();
                            while (true)
                            {
                                cancellationToken.ThrowIfCancellationRequested();
                                CheckTotalTimeout();
                                double now = seconds();
                                ulong bytes = transfer.DownloadedBytes;
                                if (bytes > MaxArchiveBytes)
                                    throw new IOException("Roslyn dependency archive exceeds the 128 MiB size limit.");
                                if (bytes > previousBytes)
                                    lastProgress = now;
                                previousBytes = bytes;
                                progress?.Invoke(bytes, transfer.Progress, attempt);
                                cancellationToken.ThrowIfCancellationRequested();
                                if (transfer.IsDone)
                                    break;
                                if (bytes == 0 && now - started >= policy.ConnectTimeout.TotalSeconds)
                                    throw new TimeoutException("Roslyn dependency download did not receive data within the connection limit.");
                                if (bytes > 0 && now - lastProgress >= policy.NoProgressTimeout.TotalSeconds)
                                    throw new TimeoutException("Roslyn dependency download stopped making progress.");
                                await Wait(policy.PollInterval);
                            }
                            if (transfer.Succeeded)
                            {
                                byte[] data = transfer.Data;
                                if (data == null || (ulong)data.Length > MaxArchiveBytes)
                                    throw new IOException("Invalid Roslyn dependency archive size.");
                                return data;
                            }
                            if (!transfer.Retryable || attempt == policy.MaxAttempts)
                                throw new IOException($"Failed to download a Roslyn dependency: {transfer.Error}");
                        }
                        finally
                        {
                            // Unity requests must be aborted on the captured Editor context, never in a
                            // CancellationToken callback that may run on a worker thread. The token-aware
                            // delay wakes cancellation without waiting for operation.completed.
                            if (!transfer.IsDone)
                                transfer.Abort();
                        }
                    }
                }
                catch (TimeoutException) when (attempt < policy.MaxAttempts && !cancellationToken.IsCancellationRequested)
                {
                    CheckTotalTimeout();
                }
                cancellationToken.ThrowIfCancellationRequested();
                CheckTotalTimeout();
                await Wait(TimeSpan.FromTicks(policy.RetryDelay.Ticks * attempt));
            }
            throw new IOException("Roslyn dependency download exhausted its retry limit.");

            void CheckTotalTimeout()
            {
                if (policy.TotalTimeout.HasValue && seconds() - overallStart >= policy.TotalTimeout.Value.TotalSeconds)
                    throw new TimeoutException("Roslyn dependency download exceeded the configured total time limit.");
            }

            async Task Wait(TimeSpan duration)
            {
                CheckTotalTimeout();
                if (policy.TotalTimeout.HasValue)
                {
                    var remaining = policy.TotalTimeout.Value - TimeSpan.FromSeconds(seconds() - overallStart);
                    if (remaining < duration)
                        duration = remaining > TimeSpan.Zero ? remaining : TimeSpan.Zero;
                }
                await delay(duration, cancellationToken);
                cancellationToken.ThrowIfCancellationRequested();
                CheckTotalTimeout();
            }
        }

        private const ulong MaxArchiveBytes = 128UL * 1024 * 1024;
    }
}
