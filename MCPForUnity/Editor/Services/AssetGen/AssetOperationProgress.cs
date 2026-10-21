using System;
using System.Diagnostics;

namespace MCPForUnity.Editor.Services.AssetGen
{
    internal enum AssetOperationPhase
    {
        Queued,
        Preparing,
        Submitting,
        WaitingProvider,
        Downloading,
        Importing,
        Reconnecting,
    }

    /// <summary>One latest primitive snapshot. No callbacks, payloads, job or Unity object references.</summary>
    internal sealed class AssetOperationProgress
    {
        private readonly object gate = new object();
        private AssetOperationPhase phase;
        private long phaseStarted = Stopwatch.GetTimestamp();
        private long stoppedAt;
        private long completed;
        private long phaseSequence;
        private long? total;
        private bool hasBytes;
        private bool active = true;
        private bool cancellationRequested;

        internal void Begin(AssetOperationPhase next)
        {
            lock (gate)
            {
                if (!active || phase == next)
                    return;
                phase = next;
                phaseSequence++;
                phaseStarted = Stopwatch.GetTimestamp();
                completed = 0;
                total = null;
                hasBytes = false;
            }
        }

        internal void BeginTransfer()
        {
            lock (gate)
            {
                if (!active || phase != AssetOperationPhase.Downloading)
                    return;
                phaseSequence++;
                phaseStarted = Stopwatch.GetTimestamp();
                completed = 0;
                total = null;
                hasBytes = false;
            }
        }

        // Called after each existing 64 KiB file write/hash, never allocating per chunk.
        internal void ReportBytes(long value, long declaredLength)
        {
            lock (gate)
            {
                if (
                    !active
                    || phase != AssetOperationPhase.Downloading
                    || value < completed
                    || value < 0
                    || declaredLength < -1
                    || declaredLength >= 0 && value > declaredLength
                    || total.HasValue && total.Value != declaredLength
                )
                    return;
                completed = value;
                total = declaredLength >= 0 ? declaredLength : (long?)null;
                hasBytes = true;
            }
        }

        internal void RequestCancellation()
        {
            lock (gate)
                cancellationRequested = true;
        }

        internal void Stop()
        {
            lock (gate)
            {
                if (!active)
                    return;
                stoppedAt = Stopwatch.GetTimestamp();
                active = false;
            }
        }

        internal Snapshot Read()
        {
            lock (gate)
                return new Snapshot(
                    Name(phase),
                    phaseSequence,
                    Math.Max(0, ((active ? Stopwatch.GetTimestamp() : stoppedAt) - phaseStarted) / (double)Stopwatch.Frequency),
                    hasBytes ? completed : (long?)null,
                    total,
                    hasBytes ? "bytes" : null,
                    active,
                    cancellationRequested
                );
        }

        private static string Name(AssetOperationPhase phase) =>
            phase switch
            {
                AssetOperationPhase.Queued => "queued",
                AssetOperationPhase.Preparing => "preparing",
                AssetOperationPhase.Submitting => "submitting",
                AssetOperationPhase.WaitingProvider => "waiting_provider",
                AssetOperationPhase.Downloading => "downloading",
                AssetOperationPhase.Importing => "importing",
                AssetOperationPhase.Reconnecting => "reconnecting",
                _ => "unknown",
            };

        internal readonly struct Snapshot
        {
            public readonly string phase;
            public readonly long phase_sequence;
            public readonly double phase_elapsed_seconds;
            public readonly long? completed;
            public readonly long? total;
            public readonly string unit;
            public readonly bool active;
            public readonly bool cancellation_requested;
            public string source => "current_editor_session";

            internal Snapshot(string phase, long sequence, double elapsed, long? completed, long? total, string unit, bool active, bool cancellation)
            {
                this.phase = phase;
                phase_sequence = sequence;
                phase_elapsed_seconds = elapsed;
                this.completed = completed;
                this.total = total;
                this.unit = unit;
                this.active = active;
                cancellation_requested = cancellation;
            }
        }
    }
}
