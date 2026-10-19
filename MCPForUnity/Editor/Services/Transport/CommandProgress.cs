using System;
using System.IO;
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

    /// <summary>One connection's shared optional/final frame writer; never owns Unity work.</summary>
    internal sealed class StdioFrameWriter
    {
        private const int MaxResultBytes = 64 * 1024 * 1024;
        private readonly Stream _stream;
        private readonly Action _abortExactConnection;
        private readonly CancellationToken _connectionToken;
        private readonly CancellationTokenRegistration _connectionCancellation;
        private readonly object _gate = new object();
        private readonly SemaphoreSlim _serial = new SemaphoreSlim(1, 1);
        private TaskCompletionSource<bool> _idle = CompletedSignal();
        private int _owners;
        private int _pendingFinal;
        private int _aborted;
        private bool _closed;

        internal StdioFrameWriter(Stream stream, Action abortExactConnection, CancellationToken connectionToken)
        {
            _stream = stream ?? throw new ArgumentNullException(nameof(stream));
            _abortExactConnection = abortExactConnection ?? throw new ArgumentNullException(nameof(abortExactConnection));
            _connectionToken = connectionToken;
            _connectionCancellation = connectionToken.Register(AbortOnce);
        }

        private static TaskCompletionSource<bool> CompletedSignal()
        {
            var signal = new TaskCompletionSource<bool>(TaskCreationOptions.RunContinuationsAsynchronously);
            signal.TrySetResult(true);
            return signal;
        }

        internal Task Completion
        {
            get
            {
                lock (_gate)
                    return _idle.Task;
            }
        }

        private void AdmitOwner()
        {
            if (_owners++ == 0)
                _idle = new TaskCompletionSource<bool>(TaskCreationOptions.RunContinuationsAsynchronously);
        }

        private void ReleaseOwner()
        {
            lock (_gate)
                if (--_owners == 0)
                    _idle.TrySetResult(true);
        }

        internal Task<bool> TryWriteProgressAsync(byte[] payload, CancellationToken originalDeadline)
        {
            lock (_gate)
            {
                if (
                    _closed
                    || _aborted != 0
                    || originalDeadline.IsCancellationRequested
                    || _connectionToken.IsCancellationRequested
                    || payload == null
                    || payload.Length < 4
                    || payload.Length > CommandProgressBuffer.MaxFrameBytes + 4
                    || payload[0] != 'M'
                    || payload[1] != 'C'
                    || payload[2] != 'P'
                    || payload[3] != 'G'
                    || _pendingFinal != 0
                    || !_serial.Wait(0)
                )
                    return Task.FromResult(false);
                AdmitOwner();
            }
            return WriteOptionalAsync(payload, originalDeadline);
        }

        private async Task<bool> WriteOptionalAsync(byte[] payload, CancellationToken deadline)
        {
            try
            {
                await WriteOwnedFrameAsync(payload, deadline).ConfigureAwait(false);
                return true;
            }
            catch (Exception)
            {
                // A failed/partial frame has already aborted this exact connection.
                // The invocation pump observes failure without retrying Unity work.
                return false;
            }
            finally
            {
                _serial.Release();
                ReleaseOwner();
            }
        }

        internal Task WriteResultAsync(byte[] payload, CancellationToken originalDeadline)
        {
            lock (_gate)
            {
                if (_closed || _aborted != 0 || _pendingFinal != 0)
                    return Task.FromException(new IOException("Stdio connection writer is unavailable"));
                if (payload == null || payload.Length > MaxResultBytes)
                    return Task.FromException(new IOException("Invalid stdio result frame size"));
                _pendingFinal++;
                AdmitOwner();
            }
            return WriteFinalAsync(payload, originalDeadline);
        }

        private async Task WriteFinalAsync(byte[] payload, CancellationToken deadline)
        {
            bool acquired = false;
            using var lifetime = CancellationTokenSource.CreateLinkedTokenSource(_connectionToken, deadline);
            try
            {
                await _serial.WaitAsync(lifetime.Token).ConfigureAwait(false);
                acquired = true;
                await WriteOwnedFrameAsync(payload, lifetime.Token).ConfigureAwait(false);
            }
            finally
            {
                if (acquired)
                    _serial.Release();
                lock (_gate)
                    _pendingFinal--;
                ReleaseOwner();
            }
        }

        private async Task WriteOwnedFrameAsync(byte[] payload, CancellationToken deadline)
        {
            using var lifetime = CancellationTokenSource.CreateLinkedTokenSource(_connectionToken, deadline);
            lifetime.Token.ThrowIfCancellationRequested();
            // Once a write can have emitted ANY bytes, token cancellation aborts
            // this captured stream even on Mono versions that ignore WriteAsync's token.
            using var cancelWrite = lifetime.Token.Register(AbortOnce);
            try
            {
                byte[] header = new byte[8];
                ulong size = (ulong)payload.Length;
                for (int index = 7; index >= 0; index--)
                {
                    header[index] = (byte)size;
                    size >>= 8;
                }
#if NETSTANDARD2_1 || NET6_0_OR_GREATER
                await _stream.WriteAsync(header.AsMemory(), lifetime.Token).ConfigureAwait(false);
                lifetime.Token.ThrowIfCancellationRequested();
                await _stream.WriteAsync(payload.AsMemory(), lifetime.Token).ConfigureAwait(false);
#else
                await _stream.WriteAsync(header, 0, header.Length, lifetime.Token).ConfigureAwait(false);
                lifetime.Token.ThrowIfCancellationRequested();
                await _stream.WriteAsync(payload, 0, payload.Length, lifetime.Token).ConfigureAwait(false);
#endif
                lifetime.Token.ThrowIfCancellationRequested();
            }
            catch
            {
                AbortOnce();
                throw;
            }
        }

        private void AbortOnce()
        {
            if (Interlocked.Exchange(ref _aborted, 1) != 0)
                return;
            try
            {
                _abortExactConnection();
            }
            catch (Exception) { /* Cancellation callbacks must not throw into a mutation owner. */ }
            try
            {
                _stream.Dispose();
            }
            catch (Exception) { /* The exact connection is already marked unavailable. */ }
        }

        internal async Task CloseAsync()
        {
            Task closing;
            lock (_gate)
            {
                _closed = true;
                closing = _idle.Task;
            }
            // Normal closure joins a started frame. The original token, not a
            // fresh timer, aborts a stalled frame and releases this join.
            await closing.ConfigureAwait(false);
            _connectionCancellation.Dispose();
        }
    }
}
