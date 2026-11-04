using System;
using System.Threading.Tasks;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Editor.Services.Transport.Transports;

namespace MCPForUnity.Editor.Services.Transport
{
    /// <summary>
    /// Coordinates the active transport client and exposes lifecycle helpers.
    /// </summary>
    public class TransportManager
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

        public TransportManager()
        {
            Configure(() => new WebSocketTransportClient(MCPServiceLocator.ToolDiscovery), () => new StdioTransportClient());
        }

        public void Configure(Func<IMcpTransportClient> webSocketFactory, Func<IMcpTransportClient> stdioFactory)
        {
            _webSocketFactory = webSocketFactory ?? throw new ArgumentNullException(nameof(webSocketFactory));
            _stdioFactory = stdioFactory ?? throw new ArgumentNullException(nameof(stdioFactory));
        }

        private IMcpTransportClient GetOrCreateClient(TransportMode mode)
        {
            return mode switch
            {
                TransportMode.Http => _httpClient ??= _webSocketFactory(),
                TransportMode.Stdio => _stdioClient ??= _stdioFactory(),
                _ => throw new ArgumentOutOfRangeException(nameof(mode), mode, "Unsupported transport mode"),
            };
        }

        public Task<bool> StartAsync(TransportMode mode)
        {
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
            catch
            {
                if (IsCurrent(mode, generation))
                    EndIntent(mode);
                throw;
            }
            bool started;
            try
            {
                started = await client.StartAsync();
            }
            catch
            {
                if (IsCurrent(mode, generation))
                {
                    long cleanupGeneration = EndIntent(mode);
                    try
                    {
                        await client.StopAsync();
                    }
                    catch (Exception ex)
                    {
                        McpLog.Warn($"Error while stopping transport {client.TransportName}: {ex.Message}");
                    }
                    if (IsCurrent(mode, cleanupGeneration))
                        UpdateState(mode, TransportState.Disconnected(client.TransportName, "Failed to start"));
                }
                throw;
            }
            if (!IsCurrent(mode, generation))
                return false;
            if (!started)
            {
                long cleanupGeneration = EndIntent(mode);
                try
                {
                    await client.StopAsync();
                }
                catch (Exception ex)
                {
                    McpLog.Warn($"Error while stopping transport {client.TransportName}: {ex.Message}");
                }
                if (IsCurrent(mode, cleanupGeneration))
                    UpdateState(mode, TransportState.Disconnected(client.TransportName, client.State?.Error ?? "Failed to start"));
                return false;
            }

            UpdateState(mode, client.State ?? TransportState.Connected(client.TransportName));
            return true;
        }

        public Task StopAsync(TransportMode? mode = null) => StopAsync(mode, false);

        internal Task StopStdioForReloadAsync() => StopAsync(TransportMode.Stdio, true);

        private async Task StopAsync(TransportMode? mode, bool forReload)
        {
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
                        UpdateState(clientMode, TransportState.Disconnected(client.TransportName));
                }
            }

            if (mode == null)
            {
                long httpGeneration = EndIntent(TransportMode.Http);
                long stdioGeneration = EndIntent(TransportMode.Stdio);
                Task httpStop = StopClient(_httpClient, TransportMode.Http, httpGeneration);
                Task stdioStop = StopClient(_stdioClient, TransportMode.Stdio, stdioGeneration);
                await Task.WhenAll(httpStop, stdioStop);
                return;
            }

            if (mode == TransportMode.Http)
            {
                long generation = EndIntent(TransportMode.Http);
                await StopClient(_httpClient, TransportMode.Http, generation);
            }
            else
            {
                long generation = EndIntent(TransportMode.Stdio);
                await StopClient(_stdioClient, TransportMode.Stdio, generation);
            }
        }

        public async Task<bool> VerifyAsync(TransportMode mode)
        {
            IMcpTransportClient client = GetClient(mode);
            if (client == null)
            {
                return false;
            }

            bool ok = await client.VerifyAsync();
            var state = client.State ?? TransportState.Disconnected(client.TransportName, "No state reported");
            UpdateState(mode, state);
            return ok;
        }

        public TransportState GetState(TransportMode mode)
        {
            return mode switch
            {
                TransportMode.Http => _httpClient?.State ?? _httpState,
                TransportMode.Stdio => ReconciledStdioState(),
                _ => throw new ArgumentOutOfRangeException(nameof(mode), mode, "Unsupported transport mode"),
            };
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
            if (connectivityChanged || portChanged)
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
            mode == TransportMode.Http ? _httpGeneration == generation : _stdioGeneration == generation;

        /// <summary>
        /// Synchronous teardown for shutdown/reload hooks where async awaits are not possible.
        /// </summary>
        public void ForceStop(TransportMode mode)
        {
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
    }

    public enum TransportMode
    {
        Http,
        Stdio,
    }
}
