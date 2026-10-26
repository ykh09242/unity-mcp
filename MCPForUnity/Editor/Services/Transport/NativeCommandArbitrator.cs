using System;
using System.Collections.Generic;
using System.Threading;
using MCPForUnity.Editor.Services;
using Newtonsoft.Json.Linq;

namespace MCPForUnity.Editor.Services.Transport
{
    internal readonly struct NativeAdmissionBlocker
    {
        internal string Reason { get; }
        internal bool Retryable { get; }

        internal NativeAdmissionBlocker(string reason, bool retryable)
        {
            Reason = reason;
            Retryable =
                retryable && (reason == "operation_busy" || reason == "editor_compiling" || reason == "editor_importing" || reason == "native_job_active");
        }
    }

    /// <summary>One Editor-domain authority. Admission never waits while a transport owns its wire.</summary>
    internal sealed class NativeCommandArbitrator
    {
        internal const string Capability = "native_arbitration_v1";
        internal static readonly NativeCommandArbitrator Shared = new NativeCommandArbitrator(
            (command, parameters) =>
                NativeOperationBarriers.TryGetBlocker(command, parameters, out NativeAdmissionBlocker reason) ? reason : (NativeAdmissionBlocker?)null
        );
        private static readonly AsyncLocal<Lease> BatchContext = new AsyncLocal<Lease>();
        private readonly object gate = new object();
        private readonly HashSet<Lease> active = new HashSet<Lease>();
        private readonly Func<string, JObject, NativeAdmissionBlocker?> blocker;
        internal string Generation { get; } = Guid.NewGuid().ToString("N");

        // The trusted batch consumes the handoff before its first await. Child/custom tasks
        // cannot inherit a reentrancy capability from the surrounding ExecutionContext.
        internal static Lease TakeBatchLease()
        {
            var lease = BatchContext.Value;
            BatchContext.Value = null;
            return lease;
        }

        // Exclusive handlers may invoke project callbacks (menu actions, property getters,
        // OnEnable/OnValidate or import hooks) that start Client requests. Only audited reads
        // can claim a narrower package candidate while another read-only request is retained.
        internal static string BarrierCandidate(string commandName, ToolOperationMode mode, bool trustedBuiltIn) =>
            trustedBuiltIn && mode != ToolOperationMode.Exclusive ? commandName : null;

        internal object CaptureStatus()
        {
            lock (gate)
            {
                int reads = 0,
                    writers = 0;
                foreach (var lease in active)
                    if (lease.Mode == ToolOperationMode.SharedRead)
                        reads++;
                    else
                        writers++;
                return new
                {
                    version = 1,
                    instance_generation = Generation,
                    active_reads = reads,
                    active_writers = writers,
                };
            }
        }

        internal NativeCommandArbitrator(Func<string, JObject, NativeAdmissionBlocker?> blocker) =>
            this.blocker = blocker ?? throw new ArgumentNullException(nameof(blocker));

        internal bool TryAcquire(ToolOperationMode mode, string command, JObject parameters, Lease parent, out Lease lease, out NativeAdmissionBlocker reason)
        {
            lease = null;
            reason = default;
            // The caller is on the Editor thread. Owners, not cached public job outcomes, supply barriers.
            var barrier = mode == ToolOperationMode.Control ? null : blocker(command, parameters);
            if (barrier.HasValue)
            {
                reason = barrier.Value;
                return false;
            }
            lock (gate)
            {
                if (parent != null)
                {
                    if (!ReferenceEquals(parent.Owner, this) || !active.Contains(parent) || parent.Mode != ToolOperationMode.Exclusive)
                    {
                        reason = new NativeAdmissionBlocker("stale_parent", false);
                        return false;
                    }
                    lease = new Lease(null, mode);
                    return true;
                }
                if (mode != ToolOperationMode.Control)
                {
                    foreach (var running in active)
                    {
                        if (mode == ToolOperationMode.Exclusive || running.Mode == ToolOperationMode.Exclusive)
                        {
                            reason = new NativeAdmissionBlocker("operation_busy", true);
                            return false;
                        }
                    }
                }
                lease = new Lease(mode == ToolOperationMode.Control ? null : this, mode);
                if (lease.Owner != null)
                    active.Add(lease);
                return true;
            }
        }

        internal static IDisposable EnterBatch(Lease lease)
        {
            var previous = BatchContext.Value;
            BatchContext.Value = lease;
            return new BatchScope(previous);
        }

        private sealed class BatchScope : IDisposable
        {
            private readonly Lease previous;

            internal BatchScope(Lease previous) => this.previous = previous;

            public void Dispose() => BatchContext.Value = previous;
        }

        internal sealed class Lease : IDisposable
        {
            internal NativeCommandArbitrator Owner { get; }
            internal ToolOperationMode Mode { get; }
            private int disposed;

            internal Lease(NativeCommandArbitrator owner, ToolOperationMode mode)
            {
                Owner = owner;
                Mode = mode;
            }

            public void Dispose()
            {
                if (Interlocked.Exchange(ref disposed, 1) != 0 || Owner == null)
                    return;
                lock (Owner.gate)
                    Owner.active.Remove(this);
            }
        }
    }

    internal sealed class NativeCommandBlockedException : InvalidOperationException
    {
        internal NativeAdmissionBlocker Blocker { get; }
        internal string Reason => Blocker.Reason;

        internal NativeCommandBlockedException(NativeAdmissionBlocker blocker)
            : base("Native command was not started: " + blocker.Reason) => Blocker = blocker;
    }
}
