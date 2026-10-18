using System;
using System.Threading;
using System.Threading.Tasks;

namespace MCPForUnity.Editor.Services.Transport
{
    public enum CommandProgressStage
    {
        BatchItems,
    }

    /// <summary>Optional invocation-owned observations; reporting never waits for delivery.</summary>
    public interface ICommandProgress
    {
        void Report(int completed, int total, CommandProgressStage stage);
    }

    public readonly struct CommandProgressUpdate
    {
        public int Completed { get; }
        public int Total { get; }
        public CommandProgressStage Stage { get; }

        public CommandProgressUpdate(int completed, int total, CommandProgressStage stage)
        {
            Completed = completed;
            Total = total;
            Stage = stage;
        }
    }

    /// <summary>One latest observation and one wake signal for an explicitly owned invocation.</summary>
    internal sealed class CommandProgressBuffer : ICommandProgress, IDisposable
    {
        internal const string Capability = "command_progress_v1";
        internal const int MaxItems = 100;
        internal const int MaxFrameBytes = 512;
        private readonly object _gate = new object();
        private TaskCompletionSource<bool> _changed = NewSignal();
        private CommandProgressUpdate? _latest;
        private int _total;
        private int _completed;
        private bool _closed;

        internal bool IsClosed
        {
            get
            {
                lock (_gate)
                    return _closed;
            }
        }

        private static TaskCompletionSource<bool> NewSignal() => new TaskCompletionSource<bool>(TaskCreationOptions.RunContinuationsAsynchronously);

        public void Report(int completed, int total, CommandProgressStage stage)
        {
            if (stage != CommandProgressStage.BatchItems || total < 1 || total > MaxItems || completed < 1 || completed > total)
                return;
            lock (_gate)
            {
                if (_closed || completed <= _completed || _total != 0 && total != _total)
                    return;
                _total = total;
                _completed = completed;
                _latest = new CommandProgressUpdate(completed, total, stage);
                // Continuations are asynchronous; no callback or transport runs under this lock.
                _changed.TrySetResult(true);
            }
        }

        internal bool TryTake(out CommandProgressUpdate update)
        {
            lock (_gate)
            {
                if (_closed || !_latest.HasValue)
                {
                    update = default;
                    return false;
                }
                update = _latest.Value;
                _latest = null;
                _changed = NewSignal();
                return true;
            }
        }

        internal Task WaitAsync(CancellationToken token)
        {
            token.ThrowIfCancellationRequested();
            Task signal;
            lock (_gate)
                signal = _changed.Task;
            return ConnectionCommandWork.WaitAsync(signal, token);
        }

        internal void Close()
        {
            lock (_gate)
            {
                if (_closed)
                    return;
                _closed = true;
                _latest = null;
                _changed.TrySetResult(false);
            }
        }

        public void Dispose() => Close();
    }
}
