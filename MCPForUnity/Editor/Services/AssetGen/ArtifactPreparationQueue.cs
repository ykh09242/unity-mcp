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
                    return;
                Waiter next = waiting.First.Value;
                // After two seconds, the oldest waiter takes precedence. This is
                // scheduling age, not a timeout or a bound on supported work.
                if (clock() - next.QueuedAt < 2.0)
                    for (LinkedListNode<Waiter> node = waiting.First.Next; node != null; node = node.Next)
                        if (node.Value.Bytes < next.Bytes)
                            next = node.Value;
                waiting.Remove(next.Node);
                active++;
                next.Completion.SetResult(new Lease(this));
            }
        }
    }
}
