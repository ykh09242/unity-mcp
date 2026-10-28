using System;
using System.Collections.Generic;
using System.Threading;
using System.Threading.Tasks;

namespace MCPForUnity.Editor.Services.Transport
{
    /// <summary>Bounds and owns command tasks for one socket generation.</summary>
    internal sealed class ConnectionCommandWork
    {
        public const string CancellationCapability = "command_cancel_v1";
        private readonly object _gate = new object();
        private readonly Dictionary<string, Task> _tasks = new Dictionary<string, Task>(StringComparer.Ordinal);
        private readonly Dictionary<string, Func<Task>> _cancellations = new Dictionary<string, Func<Task>>(StringComparer.Ordinal);
        private readonly Dictionary<string, Task> _cancellationTasks = new Dictionary<string, Task>(StringComparer.Ordinal);
        private readonly CancellationToken _token;
        private readonly int _capacity;
        private Task _tail = Task.CompletedTask;

        public ConnectionCommandWork(CancellationToken token, int capacity = 32)
        {
            if (capacity < 1)
                throw new ArgumentOutOfRangeException(nameof(capacity));
            _token = token;
            _capacity = capacity;
        }

        public string TryStart(string id, Func<Task, Task> work, Action cancel = null, bool bypassPredecessor = false) =>
            TryStartWithAsyncCancellation(id, work, cancel == null ? null : () => Task.Run(cancel), bypassPredecessor);

        public string TryStartWithAsyncCancellation(string id, Func<Task, Task> work, Func<Task> cancel, bool bypassPredecessor = false, Action settled = null)
        {
            var completion = new TaskCompletionSource<bool>(TaskCreationOptions.RunContinuationsAsynchronously);
            Task predecessor;
            lock (_gate)
            {
                if (_token.IsCancellationRequested)
                    return "Connection closed";
                if (_tasks.ContainsKey(id))
                    return "Duplicate command id";
                if (_tasks.Count >= _capacity)
                    return "Command queue is full";
                _tasks.Add(id, completion.Task);
                if (cancel != null)
                    _cancellations.Add(id, cancel);
                predecessor = bypassPredecessor ? Task.CompletedTask : _tail;
                if (!bypassPredecessor)
                    _tail = completion.Task;
            }

            // Submit synchronously up to the first await: dispatcher insertion order is receive order.
            Task operation;
            try
            {
                operation = work(predecessor);
            }
            catch (Exception ex)
            {
                operation = Task.FromException(ex);
            }
            _ = ObserveAsync(id, operation, completion, settled);
            return null;
        }

        public bool TryCancel(string id)
        {
            if (string.IsNullOrEmpty(id))
                return false;
            Func<Task> cancel;
            var completion = new TaskCompletionSource<bool>(TaskCreationOptions.RunContinuationsAsynchronously);
            lock (_gate)
            {
                if (_token.IsCancellationRequested || !_cancellations.TryGetValue(id, out cancel))
                    return false;
                _cancellations.Remove(id);
                _cancellationTasks.Add(id, completion.Task);
            }
            // Publish callback ownership before invoking the request marker. The async
            // callback must return promptly; user token callbacks run on its owned task.
            _ = ObserveCancellationAsync(cancel, completion);
            return true;
        }

        private static async Task ObserveCancellationAsync(Func<Task> cancel, TaskCompletionSource<bool> completion)
        {
            try
            {
                await cancel().ConfigureAwait(false);
            }
            catch (Exception) { /* Cancellation callback failures must not terminate receive-loop control. */ }
            finally
            {
                completion.TrySetResult(true);
            }
        }

        private async Task ObserveAsync(string id, Task operation, TaskCompletionSource<bool> completion, Action settled)
        {
            try
            {
                await operation.ConfigureAwait(false);
            }
            catch (Exception) { /* The owning transport reports failures; observe every owned task. */ }
            finally
            {
                Task cancellation;
                lock (_gate)
                {
                    // Claim settlement before checking callback ownership, so a late
                    // cancel cannot start after this command has been released.
                    _cancellations.Remove(id);
                    _cancellationTasks.TryGetValue(id, out cancellation);
                }
                if (cancellation != null)
                    await cancellation.ConfigureAwait(false);
                try
                {
                    settled?.Invoke();
                }
                catch (Exception) { /* Resource cleanup must not strand the owned queue entry. */ }
                finally
                {
                    lock (_gate)
                    {
                        _tasks.Remove(id);
                        _cancellationTasks.Remove(id);
                    }
                    completion.TrySetResult(true);
                }
            }
        }

        public Task DrainAsync()
        {
            lock (_gate)
            {
                var tasks = new Task[_tasks.Count];
                _tasks.Values.CopyTo(tasks, 0);
                return Task.WhenAll(tasks);
            }
        }

        internal static async Task WaitAsync(Task task, CancellationToken token)
        {
            if (task.IsCompleted)
            {
                await task.ConfigureAwait(false);
                return;
            }
            var canceled = new TaskCompletionSource<bool>(TaskCreationOptions.RunContinuationsAsynchronously);
            using (token.Register(() => canceled.TrySetCanceled(token)))
            {
                await await Task.WhenAny(task, canceled.Task).ConfigureAwait(false);
            }
        }
    }
}
