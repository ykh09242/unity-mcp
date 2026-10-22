using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Threading;
using System.Threading.Tasks;

namespace MCPForUnity.Editor.Services.AssetGen
{
    /// <summary>Pure-data worker admission. Running work is never preempted.</summary>
    internal sealed class ArtifactPreparationQueue
    {
        private readonly object gate = new();
        private readonly LinkedList<Waiter> waiting = new();
        private readonly int workers;
        private readonly Func<double> clock;
        private int active;
        private int agedBypasses;
        private const int MaximumAgedBypasses = 3;

        internal ArtifactPreparationQueue(int workers = 2, Func<double> clock = null)
        {
            if (workers < 1)
                throw new ArgumentOutOfRangeException(nameof(workers));
            this.workers = workers;
            this.clock = clock ?? (() => (double)Stopwatch.GetTimestamp() / Stopwatch.Frequency);
        }

        private sealed class Waiter
        {
            internal long Bytes;
            internal double QueuedAt;
            internal LinkedListNode<Waiter> Node;
            internal readonly TaskCompletionSource<IDisposable> Completion = new(TaskCreationOptions.RunContinuationsAsynchronously);
        }

        private sealed class Lease : IDisposable
        {
            private ArtifactPreparationQueue owner;

            internal Lease(ArtifactPreparationQueue owner) => this.owner = owner;

            public void Dispose() => Interlocked.Exchange(ref owner, null)?.Release();
        }

        internal async Task<IDisposable> AcquireAsync(long bytes, CancellationToken token)
        {
            token.ThrowIfCancellationRequested();
            Waiter waiter;
            lock (gate)
            {
                if (active < workers)
                {
                    active++;
                    return new Lease(this);
                }
                waiter = new Waiter { Bytes = Math.Max(0, bytes), QueuedAt = clock() };
                waiter.Node = waiting.AddLast(waiter);
            }
            using (token.Register(() => Cancel(waiter, token)))
                return await waiter.Completion.Task.ConfigureAwait(false);
        }

        private void Cancel(Waiter waiter, CancellationToken token)
        {
            lock (gate)
            {
                if (waiter.Node.List == null)
                    return; // Already granted; the worker checks cancellation before touching input.
                waiting.Remove(waiter.Node);
                waiter.Completion.TrySetCanceled(token);
            }
        }

        private void Release()
        {
            lock (gate)
            {
                active--;
                if (waiting.Count == 0)
                {
                    agedBypasses = 0;
                    return;
                }
                Waiter oldest = waiting.First.Value;
                Waiter next = oldest;
                bool aged = clock() - oldest.QueuedAt >= 2.0;
                // Keep size preference useful after a long active copy, while
                // granting the oldest aged job at least once every four grants.
                // This bounds bypass grants, not execution time or input size.
                if (!aged || agedBypasses < MaximumAgedBypasses)
                    for (LinkedListNode<Waiter> node = waiting.First.Next; node != null; node = node.Next)
                        if (node.Value.Bytes < next.Bytes)
                            next = node.Value;
                agedBypasses = aged && !ReferenceEquals(next, oldest) ? agedBypasses + 1 : 0;
                waiting.Remove(next.Node);
                active++;
                next.Completion.SetResult(new Lease(this));
            }
        }
    }
}
