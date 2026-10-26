using System;
using System.Collections.Generic;
using System.Threading;
using System.Threading.Tasks;

namespace MCPForUnity.Editor.Services.Transport
{
    /// <summary>
    /// Conservative accounting for retained transport input, shared across listener generations.
    /// Five times the declared UTF-8 length allows wire/text/parser headroom; it is not a bound
    /// on arbitrary JSON graphs, handler output or process RSS. Small frames have a separate
    /// bounded lane so large input cannot prevent status/cancel ingress. Size grants no tool authority.
    /// </summary>
    internal sealed class StdioFrameBudget
    {
        private readonly Lane large;
        private readonly Lane small;
        private readonly int smallFrameBytes;

        internal StdioFrameBudget()
            : this(1280L * 1024 * 1024, 8L * 1024 * 1024, 8 * 1024) { }

        internal StdioFrameBudget(long largeCapacityBytes, long smallCapacityBytes, int smallFrameBytes, int maxWaiters = 128)
        {
            if (largeCapacityBytes <= 0 || smallCapacityBytes <= 0 || smallFrameBytes < 0 || maxWaiters < 1)
                throw new ArgumentOutOfRangeException();
            large = new Lane(largeCapacityBytes, maxWaiters);
            small = new Lane(smallCapacityBytes, maxWaiters);
            this.smallFrameBytes = smallFrameBytes;
        }

        internal long LargeReservedBytes => large.ReservedBytes;
        internal long SmallReservedBytes => small.ReservedBytes;
        internal long ReservedBytes => LargeReservedBytes + SmallReservedBytes;
        internal int PendingCount => large.PendingCount + small.PendingCount;

        internal Task<Lease> AcquireAsync(int bodyUtf8Bytes, CancellationToken cancellation)
        {
            if (bodyUtf8Bytes <= 0 || bodyUtf8Bytes > 128 * 1024 * 1024)
                throw new ArgumentOutOfRangeException(nameof(bodyUtf8Bytes));
            return (bodyUtf8Bytes <= smallFrameBytes ? small : large).AcquireAsync(bodyUtf8Bytes * 5L, cancellation);
        }

        internal sealed class Lease : IDisposable
        {
            private Lane owner;
            private readonly long bytes;

            internal Lease(Lane owner, long bytes)
            {
                this.owner = owner;
                this.bytes = bytes;
            }

            public void Dispose() => Interlocked.Exchange(ref owner, null)?.Release(bytes);
        }

        internal sealed class Lane
        {
            private readonly object gate = new();
            private readonly long capacity;
            private readonly int maxWaiters;
            private readonly LinkedList<Waiter> pending = new();
            private long reserved;

            internal Lane(long capacity, int maxWaiters)
            {
                this.capacity = capacity;
                this.maxWaiters = maxWaiters;
            }

            internal long ReservedBytes
            {
                get
                {
                    lock (gate)
                        return reserved;
                }
            }
            internal int PendingCount
            {
                get
                {
                    lock (gate)
                        return pending.Count;
                }
            }

            internal Task<Lease> AcquireAsync(long bytes, CancellationToken cancellation)
            {
                Waiter waiter;
                lock (gate)
                {
                    if (cancellation.IsCancellationRequested)
                        return Task.FromCanceled<Lease>(cancellation);
                    if (bytes > capacity)
                        return Task.FromException<Lease>(new ArgumentOutOfRangeException(nameof(bytes)));
                    if (pending.Count == 0 && bytes <= capacity - reserved)
                    {
                        reserved += bytes;
                        return Task.FromResult(new Lease(this, bytes));
                    }
                    if (pending.Count >= maxWaiters)
                        return Task.FromException<Lease>(new InvalidOperationException("Stdio input admission capacity reached"));
                    waiter = new Waiter(bytes, cancellation);
                    waiter.Node = pending.AddLast(waiter);
                }
                // Register outside the gate: already-canceled tokens invoke synchronously.
                var registration = cancellation.Register(() => Cancel(waiter));
                bool attached;
                lock (gate)
                {
                    attached = waiter.Node != null;
                    if (attached)
                        waiter.Registration = registration;
                }
                if (!attached)
                    registration.Dispose();
                return waiter.Completion.Task;
            }

            private void Cancel(Waiter waiter)
            {
                List<Waiter> granted;
                lock (gate)
                {
                    if (waiter.Node == null)
                        return; // Grant won the race; the reader now owns the lease.
                    pending.Remove(waiter.Node);
                    waiter.Node = null;
                    granted = GrantUnderLock();
                }
                waiter.Completion.TrySetCanceled(waiter.Cancellation);
                Complete(granted);
            }

            internal void Release(long bytes)
            {
                List<Waiter> granted;
                lock (gate)
                {
                    reserved -= bytes;
                    granted = GrantUnderLock();
                }
                Complete(granted);
            }

            private List<Waiter> GrantUnderLock()
            {
                List<Waiter> granted = null;
                while (pending.First != null && pending.First.Value.Bytes <= capacity - reserved)
                {
                    var waiter = pending.First.Value;
                    pending.RemoveFirst();
                    waiter.Node = null;
                    reserved += waiter.Bytes;
                    (granted ??= new List<Waiter>()).Add(waiter);
                }
                return granted;
            }

            private void Complete(List<Waiter> granted)
            {
                if (granted == null)
                    return;
                foreach (var waiter in granted)
                {
                    waiter.Registration.Dispose();
                    waiter.Completion.TrySetResult(new Lease(this, waiter.Bytes));
                }
            }

            private sealed class Waiter
            {
                internal readonly long Bytes;
                internal readonly CancellationToken Cancellation;
                internal readonly TaskCompletionSource<Lease> Completion = new(TaskCreationOptions.RunContinuationsAsynchronously);
                internal LinkedListNode<Waiter> Node;
                internal CancellationTokenRegistration Registration;

                internal Waiter(long bytes, CancellationToken cancellation)
                {
                    Bytes = bytes;
                    Cancellation = cancellation;
                }
            }
        }
    }
}
