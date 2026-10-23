using System;
using System.Diagnostics;
using System.Threading;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Editor.Services.Transport;

namespace MCPForUnity.Editor.Setup
{
    /// <summary>Sequential Editor selection reads share an allowance without capping download or staging work.</summary>
    internal sealed class SkillSyncWaitBudget
    {
        private readonly CancellationToken _cancellation;
        private readonly Func<double> _clock;
        internal double RemainingSeconds { get; private set; }
        internal static double DefaultSeconds => OperationTimeouts.EnvironmentSeconds("UNITY_MCP_SKILL_SYNC_EDITOR_WAIT_TIMEOUT", 300);

        internal SkillSyncWaitBudget(double seconds, CancellationToken cancellation, Func<double> clock = null)
        {
            if (double.IsNaN(seconds) || double.IsInfinity(seconds) || seconds <= 0 || seconds > OperationTimeouts.MaximumSeconds)
                throw new ArgumentOutOfRangeException(nameof(seconds), "Editor selection wait must be finite positive seconds up to 86400.");
            RemainingSeconds = seconds;
            _cancellation = cancellation;
            _clock = clock ?? (() => (double)Stopwatch.GetTimestamp() / Stopwatch.Frequency);
        }

        internal void ThrowIfCancellationRequested() => _cancellation.ThrowIfCancellationRequested();

        internal SkillSyncSource Read(Func<SkillSyncSource> reader)
        {
            if (reader == null)
                throw new ArgumentNullException(nameof(reader));
            ThrowIfCancellationRequested();
            double remaining = RemainingSeconds;
            if (remaining <= 0)
                throw Expired();
            double started = _clock();
            using var timeout = CancellationTokenSource.CreateLinkedTokenSource(_cancellation);
            timeout.CancelAfter(TimeSpan.FromSeconds(remaining));
            try
            {
                var result = TransportCommandDispatcher
                    .RunOnMainThreadAsync(
                        () =>
                        {
                            RequireRemaining();
                            return reader();
                        },
                        timeout.Token
                    )
                    .GetAwaiter()
                    .GetResult();
                RequireRemaining();
                return result;
            }
            catch (OperationCanceledException) when (timeout.IsCancellationRequested)
            {
                ThrowIfCancellationRequested();
                throw Expired();
            }
            finally
            {
                RemainingSeconds = Math.Max(0, remaining - Math.Max(0, _clock() - started));
            }

            void RequireRemaining()
            {
                ThrowIfCancellationRequested();
                if (timeout.IsCancellationRequested || _clock() - started >= remaining)
                    throw Expired();
            }
        }

        private static TimeoutException Expired() =>
            new("The Editor selection wait budget expired. Preview again or increase UNITY_MCP_SKILL_SYNC_EDITOR_WAIT_TIMEOUT.");
    }
}
