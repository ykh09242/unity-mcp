using System;
using System.Threading;
using System.Threading.Tasks;
using Newtonsoft.Json.Linq;

namespace MCPForUnity.Editor.Services
{
    /// <summary>One connection's bounded catalog request/receipt coordinator; never executes commands.</summary>
    public sealed class ToolSettingsRegistration : IDisposable
    {
        private readonly object _gate = new();
        private readonly ToolSettingsState _state;
        private readonly object _owner;
        private readonly bool _supported;
        private readonly Func<CancellationToken, Task<ToolSettingsSnapshot>> _capture;
        private readonly Func<JObject, CancellationToken, Task> _send;
        private readonly CancellationTokenSource _lifetime;
        private readonly CancellationToken _token;
        private readonly TimeSpan _timeout;
        private Task _worker;
        private TaskCompletionSource<bool> _receipt;
        private bool _requested;
        private bool _running;
        private bool _disposed;

        public ToolSettingsRegistration(
            ToolSettingsState state,
            object owner,
            string session,
            bool supported,
            Func<CancellationToken, Task<ToolSettingsSnapshot>> capture,
            Func<JObject, CancellationToken, Task> send,
            CancellationToken token,
            TimeSpan timeout
        )
        {
            if (timeout <= TimeSpan.Zero || timeout > TimeSpan.FromSeconds(5))
                throw new ArgumentOutOfRangeException(nameof(timeout));
            _state = state ?? throw new ArgumentNullException(nameof(state));
            _owner = owner ?? throw new ArgumentNullException(nameof(owner));
            _supported = supported;
            _capture = capture ?? throw new ArgumentNullException(nameof(capture));
            _send = send ?? throw new ArgumentNullException(nameof(send));
            _lifetime = CancellationTokenSource.CreateLinkedTokenSource(token);
            _token = _lifetime.Token;
            _timeout = timeout;
            _state.SetConnection(owner, session, supported, RequestAsync);
        }

        public Task Completion
        {
            get
            {
                lock (_gate)
                    return _worker ?? Task.CompletedTask;
            }
        }

        public Task RequestAsync()
        {
            TaskCompletionSource<bool> completion;
            lock (_gate)
            {
                if (_disposed)
                    return Task.CompletedTask;
                _requested = true;
                if (_running)
                    return _worker;
                _running = true;
                completion = new TaskCompletionSource<bool>(TaskCreationOptions.RunContinuationsAsynchronously);
                _worker = completion.Task;
            }
            _ = CompleteAsync(completion);
            return completion.Task;
        }

        private async Task CompleteAsync(TaskCompletionSource<bool> completion)
        {
            try
            {
                await RunAsync(_token).ConfigureAwait(false);
                completion.TrySetResult(true);
            }
            catch (Exception error)
            {
                completion.TrySetException(error);
            }
            finally
            {
                lock (_gate)
                    if (ReferenceEquals(_worker, completion.Task))
                        _running = false;
            }
        }

        public bool HandleReceipt(JObject payload)
        {
            lock (_gate)
            {
                if (_disposed || !_supported || !_state.AcceptReceipt(_owner, payload))
                    return false;
                _receipt?.TrySetResult(true);
                return true;
            }
        }

        private async Task RunAsync(CancellationToken token)
        {
            while (true)
            {
                lock (_gate)
                {
                    if (_disposed || !_requested)
                    {
                        _running = false;
                        return;
                    }
                    _requested = false;
                }
                ToolSettingsSnapshot snapshot = null;
                ToolSettingsStatus captureStatus = _state.Status;
                TaskCompletionSource<bool> attemptReceipt = null;
                try
                {
                    token.ThrowIfCancellationRequested();
                    snapshot = await _capture(token).ConfigureAwait(false);
                    token.ThrowIfCancellationRequested();
                    var frame = new JObject { ["type"] = "register_tools", ["tools"] = snapshot.Tools };
                    if (_supported)
                    {
                        snapshot = _state.BeginAttempt(_owner);
                        if (snapshot == null)
                            continue;
                        frame["tools"] = snapshot.Tools;
                        frame["settings_epoch"] = snapshot.Epoch;
                        frame["desired_revision"] = snapshot.Revision;
                        lock (_gate)
                        {
                            attemptReceipt = new TaskCompletionSource<bool>(TaskCreationOptions.RunContinuationsAsynchronously);
                            _receipt = attemptReceipt;
                        }
                    }
                    await _send(frame, token).ConfigureAwait(false);
                    token.ThrowIfCancellationRequested();
                    if (!_supported)
                        continue;
                    _state.MarkSent(_owner, snapshot);
                    TaskCompletionSource<bool> receipt;
                    lock (_gate)
                        receipt = _receipt;
                    if (receipt == null)
                    {
                        token.ThrowIfCancellationRequested();
                        return;
                    }
                    using var timer = CancellationTokenSource.CreateLinkedTokenSource(token);
                    try
                    {
                        Task delay = Task.Delay(_timeout, timer.Token);
                        if (await Task.WhenAny(receipt.Task, delay).ConfigureAwait(false) != receipt.Task)
                        {
                            token.ThrowIfCancellationRequested();
                            _state.FailAttempt(
                                _owner,
                                snapshot,
                                "unconfirmed",
                                "ack_timeout",
                                "The server did not confirm this catalog within five seconds; it may have applied."
                            );
                        }
                    }
                    finally
                    {
                        timer.Cancel();
                        lock (_gate)
                            if (ReferenceEquals(_receipt, receipt))
                                _receipt = null;
                    }
                }
                catch (OperationCanceledException) when (token.IsCancellationRequested)
                {
                    return;
                }
                catch (Exception error)
                {
                    if (token.IsCancellationRequested)
                        return;
                    if (snapshot != null)
                        _state.FailAttempt(_owner, snapshot, "send_failed", "send_failed", error.GetBaseException().Message);
                    else
                        _state.PreparationFailed(_owner, captureStatus, error.GetBaseException().GetType().Name);
                }
                finally
                {
                    lock (_gate)
                    {
                        if (ReferenceEquals(_receipt, attemptReceipt))
                        {
                            _receipt?.TrySetCanceled();
                            _receipt = null;
                        }
                    }
                }
            }
        }

        public void Dispose()
        {
            lock (_gate)
            {
                if (_disposed)
                    return;
                _disposed = true;
                _requested = false;
                _lifetime.Cancel();
                _receipt?.TrySetCanceled();
                _receipt = null;
            }
            _state.Disconnect(_owner);
            _lifetime.Dispose();
        }
    }
}
