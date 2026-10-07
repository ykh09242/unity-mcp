using System;
using System.Threading;
using System.Threading.Tasks;
using MCPForUnity.Editor.Helpers;
using Newtonsoft.Json.Linq;

namespace MCPForUnity.Editor.Services
{
    /// <summary>
    /// A connection-owned sender with one in-flight and one replaceable latest slot.
    /// Unity observation happens in EditorStateCache; this sender never accesses Unity.
    /// </summary>
    internal sealed class EditorStatePublisher : IDisposable
    {
        internal const string Capability = "editor_state_v1";
        private readonly object _gate = new object();
        private readonly Func<JObject, CancellationToken, Task> _send;
        private readonly CancellationTokenSource _lifetime;
        private IDisposable _subscription;
        private JObject _pending;
        private bool _sending;
        private bool _disposed;

        private EditorStatePublisher(Func<JObject, CancellationToken, Task> send, CancellationToken token)
        {
            _send = send ?? throw new ArgumentNullException(nameof(send));
            _lifetime = CancellationTokenSource.CreateLinkedTokenSource(token);
        }

        /// <summary>Call on Unity's main thread after capability negotiation and registration.</summary>
        internal static EditorStatePublisher Start(Func<JObject, CancellationToken, Task> send, CancellationToken connectionToken)
        {
            connectionToken.ThrowIfCancellationRequested();
            var publisher = new EditorStatePublisher(send, connectionToken);
            try
            {
                var subscription = EditorStateCache.Subscribe(publisher.Observe);
                lock (publisher._gate)
                {
                    if (publisher._disposed)
                        subscription.Dispose();
                    else
                        publisher._subscription = subscription;
                }
                return publisher;
            }
            catch
            {
                publisher.Dispose();
                throw;
            }
        }

        private void Observe(JObject snapshot)
        {
            lock (_gate)
            {
                if (_disposed || _lifetime.IsCancellationRequested)
                    return;
                _pending = new JObject
                {
                    ["type"] = "editor_state",
                    ["epoch"] = EditorStateCache.Epoch,
                    ["sequence"] = snapshot["sequence"].DeepClone(),
                    ["observed_at_unix_ms"] = snapshot["observed_at_unix_ms"].DeepClone(),
                    ["state"] = snapshot,
                };
                if (_sending)
                    return;
                _sending = true;
                _ = Task.Run(DrainAsync);
            }
        }

        private async Task DrainAsync()
        {
            try
            {
                while (true)
                {
                    JObject message;
                    lock (_gate)
                    {
                        if (_disposed || _lifetime.IsCancellationRequested || _pending == null)
                        {
                            _pending = null;
                            return;
                        }
                        message = _pending;
                        _pending = null;
                    }
                    await _send(message, _lifetime.Token).ConfigureAwait(false);
                }
            }
            catch (OperationCanceledException)
            {
                Dispose();
            }
            catch (Exception ex)
            {
                McpLog.Warn($"[EditorStatePublisher] State push stopped: {ex.Message}");
                Dispose();
            }
            finally
            {
                lock (_gate)
                {
                    _sending = false;
                    if (_disposed)
                        _lifetime.Dispose();
                    else if (_pending != null)
                    {
                        // An observation may have arrived between the last empty
                        // check and this cleanup; retain it and start one drain.
                        _sending = true;
                        _ = Task.Run(DrainAsync);
                    }
                }
            }
        }

        public void Dispose()
        {
            IDisposable subscription;
            lock (_gate)
            {
                if (_disposed)
                    return;
                _disposed = true;
                _pending = null;
                subscription = _subscription;
                _subscription = null;
                _lifetime.Cancel();
                if (!_sending)
                    _lifetime.Dispose();
            }
            subscription?.Dispose();
        }
    }
}
