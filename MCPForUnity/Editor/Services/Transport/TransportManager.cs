using System;
using System.Threading.Tasks;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Editor.Services.Transport.Transports;

namespace MCPForUnity.Editor.Services.Transport
{
    /// <summary>
    /// Coordinates the active transport client and exposes lifecycle helpers.
    /// </summary>
    public class TransportManager : IDisposable
    {
        private IMcpTransportClient _httpClient;
        private IMcpTransportClient _stdioClient;
        private TransportState _httpState = TransportState.Disconnected("http");
        private TransportState _stdioState = TransportState.Disconnected("stdio");
        private Func<IMcpTransportClient> _webSocketFactory;
        private Func<IMcpTransportClient> _stdioFactory;
        private Task<bool> _httpStartTask;
        private Task<bool> _stdioStartTask;
        private long _httpGeneration;
        private long _stdioGeneration;
        private bool _httpConnectionIntent;
        private bool _stdioConnectionIntent;
        private bool _disposed;

        internal bool IsDisposed => _disposed;

        static TransportManager()
        {
            // Observe configuration once per domain, without retaining replaced/test managers.
            EditorConfigurationCache.Instance.OnConfigurationChanged += OnConfigurationChanged;
        }

        internal static bool IsHttpConnectionConfiguration(string key)
        {
            var configuration = EditorConfigurationCache.Instance;
            bool remote = string.Equals(configuration.HttpTransportScope, "remote", StringComparison.OrdinalIgnoreCase);
            return key == nameof(EditorConfigurationCache.HttpTransportScope)
                || key == (remote ? nameof(EditorConfigurationCache.HttpRemoteBaseUrl) : nameof(EditorConfigurationCache.HttpBaseUrl));
        }

        private static void OnConfigurationChanged(string key)
        {
            bool selectionChanged = key == nameof(EditorConfigurationCache.UseHttpTransport);
            if (!selectionChanged && !IsHttpConnectionConfiguration(key))
                return;

            // A changed destination needs a new, explicit connection and credential snapshot.
            // Do not let a pending reload/auto-start reconnect using the newly displayed settings.
            HttpBridgeReloadHandler.CancelPendingResume();
            HttpAutoStartHandler.CancelPendingReconnect();
            if (selectionChanged)
                StdioBridgeReloadHandler.CancelPendingResume();
            var manager = MCPServiceLocator.ExistingTransportManager;
            if (manager == null)
                return;
            TransportMode retire = selectionChanged && EditorConfigurationCache.Instance.UseHttpTransport ? TransportMode.Stdio : TransportMode.Http;
            _ = manager.StopAsync(retire);
        }

        public TransportManager()
        {
            Configure(() => new WebSocketTransportClient(MCPServiceLocator.ToolDiscovery), () => new StdioTransportClient());
        }

        public void Configure(Func<IMcpTransportClient> webSocketFactory, Func<IMcpTransportClient> stdioFactory)
        {
            ThrowIfDisposed();
            _webSocketFactory = webSocketFactory ?? throw new ArgumentNullException(nameof(webSocketFactory));
            _stdioFactory = stdioFactory ?? throw new ArgumentNullException(nameof(stdioFactory));
        }

        private IMcpTransportClient GetOrCreateClient(TransportMode mode)
        {
            ThrowIfDisposed();
            return mode switch
            {
                TransportMode.Http => _httpClient ??= _webSocketFactory(),
                TransportMode.Stdio => _stdioClient ??= _stdioFactory(),
                _ => throw new ArgumentOutOfRangeException(nameof(mode), mode, "Unsupported transport mode"),
            };
        }

        public Task<bool> StartAsync(TransportMode mode)
        {
            ThrowIfDisposed();
            // Editor-main-thread only (no locking needed). Coalesce concurrent starts for the
            // same mode: manual Connect, reload-resume, and auto-start can otherwise race, and
            // WebSocketTransportClient.StartAsync tears down a live connection first — two
            // interleaved starts bounce each other's session.
            Task<bool> inFlight = mode switch
            {
                TransportMode.Http => _httpStartTask,
                TransportMode.Stdio => _stdioStartTask,
                _ => throw new ArgumentOutOfRangeException(nameof(mode), mode, "Unsupported transport mode"),
            };
            if (inFlight != null && !inFlight.IsCompleted)
            {
                return inFlight;
            }

            long generation = BeginIntent(mode);
            Task<bool> started = StartCoreAsync(mode, generation);
            if (IsCurrent(mode, generation))
            {
                if (mode == TransportMode.Http)
                    _httpStartTask = started;
                else
                    _stdioStartTask = started;
            }
            return started;
        }

        private async Task<bool> StartCoreAsync(TransportMode mode, long generation)
        {
            IMcpTransportClient client;
            try
            {
                client = GetOrCreateClient(mode);
            }
            catch (Exception ex)
            {
                if (IsCurrent(mode, generation))
                {
                    EndIntent(mode);
                    UpdateState(mode, TransportState.Disconnected(mode.ToString().ToLowerInvariant(), HttpEndpointUtility.RedactUrls(ex.Message)));
                }
                throw;
            }
            bool started;
            try
            {
                started = await client.StartAsync();
            }
            catch (Exception error)
            {
                if (IsCurrent(mode, generation))
                {
                    string reason = HttpEndpointUtility.RedactUrls(client.State?.Error ?? error.Message);
                    long cleanupGeneration = EndIntent(mode);
                    UpdateState(mode, TransportState.Disconnected(client.TransportName, reason));
                    try
                    {
                        await client.StopAsync();
                    }
                    catch (Exception ex)
                    {
                        if (IsCurrent(mode, cleanupGeneration))
                            McpLog.Warn($"Error while stopping transport {client.TransportName}: {HttpEndpointUtility.RedactUrls(ex.Message)}");
                    }
                    if (IsCurrent(mode, cleanupGeneration))
                        UpdateState(mode, TransportState.Disconnected(client.TransportName, reason));
                }
                throw;
            }
            if (!IsCurrent(mode, generation))
                return false;
            if (!started)
            {
                string reason = HttpEndpointUtility.RedactUrls(client.State?.Error ?? "Failed to start");
                long cleanupGeneration = EndIntent(mode);
                UpdateState(mode, TransportState.Disconnected(client.TransportName, reason));
                try
                {
                    await client.StopAsync();
                }
                catch (Exception ex)
                {
                    if (IsCurrent(mode, cleanupGeneration))
                        McpLog.Warn($"Error while stopping transport {client.TransportName}: {HttpEndpointUtility.RedactUrls(ex.Message)}");
                }
                if (IsCurrent(mode, cleanupGeneration))
                    UpdateState(mode, TransportState.Disconnected(client.TransportName, reason));
                return false;
            }

            UpdateState(mode, client.State ?? TransportState.Connected(client.TransportName));
            return true;
        }

        public Task StopAsync(TransportMode? mode = null) => StopAsync(mode, false);

        internal Task StopStdioForReloadAsync() => StopAsync(TransportMode.Stdio, true);

        private async Task StopAsync(TransportMode? mode, bool forReload)
        {
            if (_disposed)
                return;
            async Task StopClient(IMcpTransportClient client, TransportMode clientMode, long generation)
            {
                if (client == null)
                    return;
                try
                {
                    if (forReload && client is StdioTransportClient stdio)
                        await stdio.StopForReloadAsync();
                    else
                        await client.StopAsync();
                }
                catch (Exception ex)
                {
                    McpLog.Warn($"Error while stopping transport {client.TransportName}: {ex.Message}");
                }
                finally
                {
                    if (IsCurrent(clientMode, generation))
                        UpdateState(clientMode, StoppedState(client));
                }
            }

            if (mode == null)
            {
                long httpGeneration = EndIntent(TransportMode.Http);
                long stdioGeneration = EndIntent(TransportMode.Stdio);
                UpdateState(TransportMode.Http, TransportState.Disconnected(_httpClient?.TransportName ?? "http"));
                UpdateState(TransportMode.Stdio, TransportState.Disconnected(_stdioClient?.TransportName ?? "stdio"));
                Task httpStop = StopClient(_httpClient, TransportMode.Http, httpGeneration);
                Task stdioStop = StopClient(_stdioClient, TransportMode.Stdio, stdioGeneration);
                await Task.WhenAll(httpStop, stdioStop);
                return;
            }

            if (mode == TransportMode.Http)
            {
                long generation = EndIntent(TransportMode.Http);
                UpdateState(TransportMode.Http, TransportState.Disconnected(_httpClient?.TransportName ?? "http"));
                await StopClient(_httpClient, TransportMode.Http, generation);
            }
            else
            {
                long generation = EndIntent(TransportMode.Stdio);
                UpdateState(TransportMode.Stdio, TransportState.Disconnected(_stdioClient?.TransportName ?? "stdio"));
                await StopClient(_stdioClient, TransportMode.Stdio, generation);
            }
        }

        public async Task<bool> VerifyAsync(TransportMode mode)
        {
            if (_disposed)
                return false;
            // Failed-start cleanup can erase the client's state. Preserve the owned failure
            // until an explicit start/stop, while still recognizing externally started stdio.
            if (!HasConnectionIntent(mode) && !GetState(mode).IsConnected)
                return false;
            IMcpTransportClient client = GetClient(mode);
            if (client == null)
            {
                return false;
            }

            long generation = mode == TransportMode.Http ? _httpGeneration : _stdioGeneration;
            bool ok = await client.VerifyAsync();
            if (!IsCurrent(mode, generation))
                return false;
            var state = client.State ?? TransportState.Disconnected(client.TransportName, "No state reported");
            UpdateState(mode, state);
            return ok;
        }

        public TransportState GetState(TransportMode mode)
        {
            return mode switch
            {
                TransportMode.Http => ReconciledHttpState(),
                TransportMode.Stdio => _disposed ? _stdioState : ReconciledStdioState(),
                _ => throw new ArgumentOutOfRangeException(nameof(mode), mode, "Unsupported transport mode"),
            };
        }

        private static bool HasUnsettledExecution(TransportState state) => state?.Error?.StartsWith("execution_unsettled:", StringComparison.Ordinal) == true;

        private static TransportState StoppedState(IMcpTransportClient client) =>
            TransportState.Disconnected(client.TransportName, HasUnsettledExecution(client.State) ? HttpEndpointUtility.RedactUrls(client.State.Error) : null);

        private TransportState ReconciledHttpState()
        {
            if (_disposed)
                return _httpState;
            TransportState live = _httpClient?.State;
            if (_httpConnectionIntent)
                return live ?? _httpState;
            // Ending connection intent must not hide work retained by bounded Stop.
            // Observe only settlement here: a stale connected client cannot revive intent,
            // and ordinary failed-start diagnostics remain cached through cleanup.
            if (HasUnsettledExecution(live))
                _httpState = TransportState.Disconnected(_httpClient.TransportName, HttpEndpointUtility.RedactUrls(live.Error));
            else if (HasUnsettledExecution(_httpState))
                _httpState = TransportState.Disconnected(_httpClient?.TransportName ?? "http");
            return _httpState;
        }

        /// <summary>
        /// The stdio bridge can start or stop outside this manager: after a domain reload the
        /// listener resumes through StdioBridgeHost's editor-idle retry once the OS releases
        /// the port, bypassing StartAsync entirely (CI auto-start does the same). Without
        /// reconciling, the cached snapshot stays "Disconnected" and the editor window reports
        /// a dead bridge while it is actually serving requests — until a manual Verify. A
        /// stop/start cycle between two reads can also rebind to a different port while both
        /// snapshots stay "connected", so the bound port is reconciled too.
        /// </summary>
        private TransportState ReconciledStdioState()
        {
            IMcpTransportClient client = GetOrCreateClient(TransportMode.Stdio);
            TransportState live = client.State;
            bool connectivityChanged = client.IsConnected != _stdioState.IsConnected;
            bool portChanged = client.IsConnected && live?.Port != _stdioState.Port;
            bool settlementChanged = HasUnsettledExecution(live) != HasUnsettledExecution(_stdioState);
            if (connectivityChanged || portChanged || settlementChanged)
            {
                _stdioState = live ?? (client.IsConnected ? TransportState.Connected(client.TransportName) : TransportState.Disconnected(client.TransportName));
            }
            return _stdioState;
        }

        public bool IsRunning(TransportMode mode) => GetState(mode).IsConnected;

        /// <summary>Whether a start is active, including while connecting or reconnecting.</summary>
        public bool HasConnectionIntent(TransportMode mode) =>
            mode switch
            {
                TransportMode.Http => _httpConnectionIntent,
                TransportMode.Stdio => _stdioConnectionIntent,
                _ => throw new ArgumentOutOfRangeException(nameof(mode), mode, "Unsupported transport mode"),
            };

        private long BeginIntent(TransportMode mode)
        {
            UpdateState(mode, TransportState.Disconnected(mode.ToString().ToLowerInvariant()));
            if (mode == TransportMode.Http)
            {
                _httpConnectionIntent = true;
                return ++_httpGeneration;
            }
            if (mode == TransportMode.Stdio)
            {
                _stdioConnectionIntent = true;
                return ++_stdioGeneration;
            }
            throw new ArgumentOutOfRangeException(nameof(mode), mode, "Unsupported transport mode");
        }

        private long EndIntent(TransportMode mode)
        {
            if (mode == TransportMode.Http)
            {
                _httpConnectionIntent = false;
                _httpStartTask = null;
                return ++_httpGeneration;
            }
            if (mode == TransportMode.Stdio)
            {
                _stdioConnectionIntent = false;
                _stdioStartTask = null;
                return ++_stdioGeneration;
            }
            throw new ArgumentOutOfRangeException(nameof(mode), mode, "Unsupported transport mode");
        }

        private bool IsCurrent(TransportMode mode, long generation) =>
            !_disposed && (mode == TransportMode.Http ? _httpGeneration == generation : _stdioGeneration == generation);

        /// <summary>
        /// Synchronous teardown for shutdown/reload hooks where async awaits are not possible.
        /// </summary>
        public void ForceStop(TransportMode mode)
        {
            if (_disposed)
                return;
            long generation = EndIntent(mode);
            IMcpTransportClient client = GetClient(mode);
            string transportName = client?.TransportName ?? mode.ToString().ToLowerInvariant();

            if (client == null)
            {
                if (IsCurrent(mode, generation))
                    UpdateState(mode, TransportState.Disconnected(transportName));
                return;
            }

            try
            {
                if (client is WebSocketTransportClient wsClient)
                {
                    wsClient.ForceStop();
                }
                else
                {
                    client.StopAsync().GetAwaiter().GetResult();
                }
            }
            catch (Exception ex)
            {
                McpLog.Warn($"Error while force-stopping transport {transportName}: {ex.Message}");
            }
            finally
            {
                if (IsCurrent(mode, generation))
                    UpdateState(mode, TransportState.Disconnected(transportName));
            }
        }

        /// <summary>
        /// Gets the active transport client for the specified mode.
        /// Returns null if the client hasn't been created yet.
        /// </summary>
        public IMcpTransportClient GetClient(TransportMode mode)
        {
            return mode switch
            {
                TransportMode.Http => _httpClient,
                TransportMode.Stdio => _stdioClient,
                _ => throw new ArgumentOutOfRangeException(nameof(mode), mode, "Unsupported transport mode"),
            };
        }

        private void UpdateState(TransportMode mode, TransportState state)
        {
            switch (mode)
            {
                case TransportMode.Http:
                    _httpState = state;
                    break;
                case TransportMode.Stdio:
                    _stdioState = state;
                    break;
                default:
                    throw new ArgumentOutOfRangeException(nameof(mode), mode, "Unsupported transport mode");
            }
        }

        /// <summary>Retires this Editor-main-thread owner without waiting for asynchronous transport cleanup.</summary>
        public void Dispose()
        {
            if (_disposed)
                return;
            _disposed = true;
            EndIntent(TransportMode.Http);
            EndIntent(TransportMode.Stdio);
            IMcpTransportClient http = _httpClient;
            IMcpTransportClient stdio = _stdioClient;
            _httpClient = null;
            _stdioClient = null;
            _httpState = TransportState.Disconnected(http?.TransportName ?? "http");
            _stdioState = TransportState.Disconnected(stdio?.TransportName ?? "stdio");
            RetireClient(http);
            if (!ReferenceEquals(http, stdio))
                RetireClient(stdio);
        }

        private static void RetireClient(IMcpTransportClient client)
        {
            if (client == null)
                return;
            try
            {
                if (client is IDisposable disposable)
                    disposable.Dispose();
                else
                    _ = ObserveRetirementAsync(client.StopAsync());
            }
            catch (Exception ex)
            {
                McpLog.Warn($"Error while retiring transport {client.TransportName}: {HttpEndpointUtility.RedactUrls(ex.Message)}");
            }
        }

        private static async Task ObserveRetirementAsync(Task settlement)
        {
            try
            {
                await settlement.ConfigureAwait(false);
            }
            catch (Exception ex)
            {
                McpLog.Warn($"Error while retiring transport: {HttpEndpointUtility.RedactUrls(ex.Message)}");
            }
        }

        private void ThrowIfDisposed()
        {
            if (_disposed)
                throw new ObjectDisposedException(nameof(TransportManager));
        }
    }

    public enum TransportMode
    {
        Http,
        Stdio,
    }
}
