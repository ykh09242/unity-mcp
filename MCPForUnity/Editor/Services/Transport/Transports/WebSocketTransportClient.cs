using System;
using System.Buffers;
using System.Collections.Generic;
using System.IO;
using System.Net.WebSockets;
using System.Text;
using System.Threading;
using System.Threading.Tasks;
using MCPForUnity.Editor.Constants;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Editor.Models;
using MCPForUnity.Editor.Services;
using MCPForUnity.Editor.Services.Transport;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEngine;

namespace MCPForUnity.Editor.Services.Transport.Transports
{
    /// <summary>
    /// Maintains a persistent WebSocket connection to the MCP server plugin hub.
    /// Handles registration, keep-alives, and command dispatch back into Unity via
    /// <see cref="TransportCommandDispatcher"/>.
    /// </summary>
    public class WebSocketTransportClient : IMcpTransportClient, IDisposable
    {
        private const string TransportDisplayName = "websocket";
        private static readonly TimeSpan[] ReconnectSchedule =
        {
            TimeSpan.Zero,
            TimeSpan.FromSeconds(1),
            TimeSpan.FromSeconds(3),
            TimeSpan.FromSeconds(5),
            TimeSpan.FromSeconds(10),
            TimeSpan.FromSeconds(30)
        };
        private static readonly TimeSpan ReconnectTailInterval = TimeSpan.FromSeconds(30);

        private static readonly TimeSpan DefaultKeepAliveInterval = TimeSpan.FromSeconds(15);
        private static readonly TimeSpan DefaultCommandTimeout = TimeSpan.FromSeconds(30);

        private readonly IToolDiscoveryService _toolDiscoveryService;
        private readonly object _ownershipLock = new object();
        private ClientWebSocket _socket;
        private CancellationTokenSource _lifecycleCts;
        private CancellationTokenSource _connectionCts;
        private Task _receiveTask;
        private Task _keepAliveTask;
        private Task _registrationTask;
        private ConnectionCommandWork _commandWork;
        private Task _forcedCommandSettlement = Task.CompletedTask;
        private IDisposable _statePublisher;
        private bool _largeResultNegotiated;
        private readonly SemaphoreSlim _sendLock = new(1, 1);

        private Uri _endpointUri;
        private string _sessionId;
        private string _projectHash;
        private string _projectName;
        private string _projectPath;
        private string _unityVersion;
        private TimeSpan _keepAliveInterval = DefaultKeepAliveInterval;
        private TimeSpan _socketKeepAliveInterval = DefaultKeepAliveInterval;
        private volatile bool _isConnected;
        private int _isReconnectingFlag;
        private TransportState _state = TransportState.Disconnected(TransportDisplayName, "Transport not started");
        private string _apiKey;
        private bool _useLocalAuth;
        private bool _disposed;
        private readonly OwnedConnectionOptions _ownedConnection;
        private bool _commandCancellationNegotiated;
        private bool _compressionNegotiated;

        // An explicitly owned connection keeps batch integration tests and embedded
        // callers independent of the user's saved endpoint, identity and session.
        internal sealed class OwnedConnectionOptions
        {
            internal readonly Uri Endpoint;
            internal readonly string LaunchToken;
            internal readonly string ProjectName;
            internal readonly string ProjectHash;
            internal readonly string ProjectPath;
            internal readonly string UnityVersion;
            internal readonly bool AllowCompression;

            internal OwnedConnectionOptions(Uri endpoint, string launchToken, string projectName,
                string projectHash, string projectPath, string unityVersion, bool allowCompression = false)
            {
                if (endpoint == null || !endpoint.IsAbsoluteUri || !endpoint.IsLoopback
                    || (endpoint.Scheme != "ws" && endpoint.Scheme != "wss")
                    || !string.IsNullOrEmpty(endpoint.UserInfo))
                    throw new ArgumentException("Owned connections require a loopback WebSocket endpoint", nameof(endpoint));
                if (string.IsNullOrWhiteSpace(launchToken))
                    throw new ArgumentException("An owned launch token is required", nameof(launchToken));
                if (string.IsNullOrWhiteSpace(projectName) || string.IsNullOrWhiteSpace(projectHash)
                    || string.IsNullOrWhiteSpace(projectPath) || string.IsNullOrWhiteSpace(unityVersion))
                    throw new ArgumentException("Owned project identity must be provided explicitly");
                Endpoint = endpoint;
                LaunchToken = launchToken;
                ProjectName = projectName;
                ProjectHash = projectHash;
                ProjectPath = projectPath;
                UnityVersion = unityVersion;
                AllowCompression = allowCompression;
            }
        }

        public WebSocketTransportClient(IToolDiscoveryService toolDiscoveryService = null)
            : this(toolDiscoveryService, null)
        {
        }

        internal WebSocketTransportClient(IToolDiscoveryService toolDiscoveryService, OwnedConnectionOptions options)
        {
            _toolDiscoveryService = toolDiscoveryService;
            _ownedConnection = options;
        }

        public bool IsConnected => _isConnected;
        public string TransportName => TransportDisplayName;
        public TransportState State => _state;

        private Task<List<ToolMetadata>> GetEnabledToolsOnMainThreadAsync(CancellationToken token)
        {
            return TransportCommandDispatcher.RunOnMainThreadAsync(
                () => _toolDiscoveryService?.GetEnabledTools() ?? new List<ToolMetadata>(),
                token);
        }

        public async Task<bool> StartAsync()
        {
            // Capture identity values on the main thread before any async context switching
            _projectName = _ownedConnection?.ProjectName ?? ProjectIdentityUtility.GetProjectName();
            _projectHash = _ownedConnection?.ProjectHash ?? ProjectIdentityUtility.GetProjectHash();
            _unityVersion = _ownedConnection?.UnityVersion ?? Application.unityVersion;
            bool remote = _ownedConnection == null && HttpEndpointUtility.IsRemoteScope();
            _useLocalAuth = !remote;
            _apiKey = remote
                ? EditorPrefs.GetString(EditorPrefKeys.ApiKey, string.Empty)
                : string.Empty;

            if (remote
                && !HttpEndpointUtility.IsCurrentRemoteUrlAllowed(out string remoteUrlError))
            {
                string message = remoteUrlError ?? "HTTP Remote URL is not allowed by current security settings.";
                _state = TransportState.Disconnected(TransportDisplayName, message);
                McpLog.Error($"[WebSocket] {message}");
                return false;
            }

            // Get project root path (strip /Assets from dataPath) for focus nudging
            _projectPath = _ownedConnection?.ProjectPath;
            string dataPath = _ownedConnection == null ? Application.dataPath : null;
            if (!string.IsNullOrEmpty(dataPath))
            {
                string normalized = dataPath.TrimEnd('/', '\\');
                if (string.Equals(System.IO.Path.GetFileName(normalized), "Assets", StringComparison.Ordinal))
                {
                    _projectPath = System.IO.Path.GetDirectoryName(normalized) ?? normalized;
                }
                else
                {
                    _projectPath = normalized;  // Fallback if path doesn't end with Assets
                }
            }

            await StopAsync();

            var lifecycleCts = new CancellationTokenSource();
            CancellationToken lifecycleToken = lifecycleCts.Token;
            Uri endpointUri = _ownedConnection?.Endpoint ?? BuildWebSocketUri(HttpEndpointUtility.GetBaseUrl());
            lock (_ownershipLock)
            {
                _lifecycleCts = lifecycleCts;
                _endpointUri = endpointUri;
                _sessionId = null;
            }

            if (!await EstablishConnectionAsync(lifecycleToken))
            {
                await StopOwnedAsync(lifecycleCts);
                return false;
            }

            // State is connected but session ID might be pending until 'registered' message
            lock (_ownershipLock)
            {
                if (!ReferenceEquals(_lifecycleCts, lifecycleCts) || lifecycleCts.IsCancellationRequested) return false;
                _state = TransportState.Connected(TransportDisplayName, sessionId: "pending", details: _endpointUri.ToString());
                _isConnected = true;
            }
            return true;
        }

        public Task StopAsync() => StopOwnedAsync(null);

        private async Task StopOwnedAsync(CancellationTokenSource expectedLifecycle)
        {
            CancellationTokenSource lifecycleCts;
            ClientWebSocket socket;
            ConnectionLoops loops;
            lock (_ownershipLock)
            {
                lifecycleCts = _lifecycleCts;
                if (expectedLifecycle != null && !ReferenceEquals(lifecycleCts, expectedLifecycle)) return;
                socket = _socket;
                loops = CaptureConnectionLoops();
            }
            if (lifecycleCts == null)
            {
                return;
            }

            try
            {
                lifecycleCts.Cancel();
            }
            catch { }

            await StopCapturedConnectionLoopsAsync(loops).ConfigureAwait(false);

            if (socket != null)
            {
                try
                {
                    if (socket.State == WebSocketState.Open || socket.State == WebSocketState.CloseReceived)
                    {
                        await socket.CloseAsync(WebSocketCloseStatus.NormalClosure, "Shutdown", CancellationToken.None).ConfigureAwait(false);
                    }
                }
                catch { }
                finally
                {
                    socket.Dispose();
                    Interlocked.CompareExchange(ref _socket, null, socket);
                }
            }

            lifecycleCts.Dispose();
            lock (_ownershipLock)
            {
                if (ReferenceEquals(Interlocked.CompareExchange(ref _lifecycleCts, null, lifecycleCts), lifecycleCts))
                {
                    _isConnected = false;
                    _state = TransportState.Disconnected(TransportDisplayName);
                }
            }
        }

        /// <summary>
        /// Synchronous teardown for use in beforeAssemblyReload where async is not possible.
        /// Skips the graceful WebSocket close handshake and just disposes resources immediately.
        /// The server handles ungraceful disconnects via its ping timeout.
        /// </summary>
        public void ForceStop()
        {
            CancellationTokenSource lifecycleCts;
            ConnectionLoops loops;
            ClientWebSocket socket;
            Task previousSettlement = null;
            TaskCompletionSource<bool> settlement = null;
            lock (_ownershipLock)
            {
                lifecycleCts = _lifecycleCts;
                loops = CaptureConnectionLoops();
                if (loops.Commands != null)
                {
                    previousSettlement = _forcedCommandSettlement;
                    settlement = new TaskCompletionSource<bool>(TaskCreationOptions.RunContinuationsAsynchronously);
                    // Publish before cancellation callbacks can attempt a restart.
                    _forcedCommandSettlement = settlement.Task;
                }
                socket = _socket;
                _lifecycleCts = null;
                _connectionCts = null;
                _socket = null;
                _receiveTask = null;
                _keepAliveTask = null;
                _registrationTask = null;
                _commandWork = null;
                _statePublisher = null;
                _largeResultNegotiated = false;
                Interlocked.Exchange(ref _isReconnectingFlag, 0);
                _isConnected = false;
                _state = TransportState.Disconnected(TransportDisplayName);
            }
            try { lifecycleCts?.Cancel(); } catch { }
            try { loops.Cts?.Cancel(); } catch { }
            if (settlement != null) _ = SettleForcedCommandsAsync(previousSettlement, loops.Commands, settlement);
            loops.Publisher?.Dispose();
            try { socket?.Abort(); } catch { }
            try { socket?.Dispose(); } catch { }
            try { loops.Cts?.Dispose(); } catch { }
            try { lifecycleCts?.Dispose(); } catch { }
        }

        private async Task SettleForcedCommandsAsync(Task previous, ConnectionCommandWork commands,
            TaskCompletionSource<bool> settlement)
        {
            try
            {
                await previous.ConfigureAwait(false);
                // Cancellation has sealed the old queue; capture all accepted work now.
                await commands.DrainAsync().ConfigureAwait(false);
                lock (_ownershipLock)
                    if (ReferenceEquals(_forcedCommandSettlement, settlement.Task))
                        _forcedCommandSettlement = Task.CompletedTask;
                settlement.TrySetResult(true);
            }
            catch (Exception ex) { settlement.TrySetException(ex); }
        }

        public async Task<bool> VerifyAsync()
        {
            if (_socket == null || _socket.State != WebSocketState.Open)
            {
                return false;
            }

            if (_lifecycleCts == null)
            {
                return false;
            }

            try
            {
                CancellationToken ownerToken;
                lock (_ownershipLock)
                {
                    if (_connectionCts == null) return false;
                    ownerToken = _connectionCts.Token;
                }
                using var timeoutCts = CancellationTokenSource.CreateLinkedTokenSource(ownerToken);
                timeoutCts.CancelAfter(TimeSpan.FromSeconds(5));
                var payload = new JObject { ["type"] = "pong", ["session_id"] = _sessionId };
                await SendStateAsync(payload, ownerToken, timeoutCts.Token).ConfigureAwait(false);
                return true;
            }
            catch (Exception ex)
            {
                McpLog.Warn($"[WebSocket] Verify ping failed: {ex.Message}");
                return false;
            }
        }

        public void Dispose()
        {
            if (_disposed)
            {
                return;
            }

            try
            {
                // Handler cleanup can require the Editor main thread; never block it here.
                ForceStop();
            }
            catch (Exception ex)
            {
                McpLog.Warn($"[WebSocket] Dispose failed to stop cleanly: {ex.Message}");
            }

            _sendLock?.Dispose();
            _socket?.Dispose();
            _lifecycleCts?.Dispose();
            _disposed = true;
        }

        private async Task<bool> EstablishConnectionAsync(CancellationToken token)
        {
            ConnectionLoops loops;
            Task forcedSettlement;
            CancellationTokenSource lifecycleCts;
            lock (_ownershipLock)
            {
                lifecycleCts = _lifecycleCts;
                if (lifecycleCts == null || token.IsCancellationRequested) return false;
                try { if (lifecycleCts.Token != token) return false; }
                catch (ObjectDisposedException) { return false; }
                loops = CaptureConnectionLoops();
                forcedSettlement = _forcedCommandSettlement;
            }
            await StopCapturedConnectionLoopsAsync(loops).ConfigureAwait(false);
            // A manual restart may have no current loops after ForceStop, but its old handlers still own mutations.
            await ConnectionCommandWork.WaitAsync(forcedSettlement, token).ConfigureAwait(false);
            token.ThrowIfCancellationRequested();
            var connectionCts = CancellationTokenSource.CreateLinkedTokenSource(token);
            CancellationToken connectionToken = connectionCts.Token;
            bool ownsLifecycle;
            lock (_ownershipLock)
            {
                ownsLifecycle = ReferenceEquals(_lifecycleCts, lifecycleCts) && !token.IsCancellationRequested;
                if (ownsLifecycle)
                {
                    _connectionCts = connectionCts;
                    _commandWork = new ConnectionCommandWork(connectionToken);
                    _largeResultNegotiated = false;
                    _commandCancellationNegotiated = false;
                    _compressionNegotiated = false;
                }
            }
            if (!ownsLifecycle)
            {
                connectionCts.Dispose();
                return false;
            }

            Uri originalEndpoint = _endpointUri;
            Uri connectedEndpoint = null;
            Exception lastConnectError = null;

            foreach (Uri candidate in BuildConnectionCandidateUris(originalEndpoint))
            {
                connectionToken.ThrowIfCancellationRequested();

                var socket = new ClientWebSocket();
                socket.Options.KeepAliveInterval = _socketKeepAliveInterval;

                // Add API key header if configured (for remote-hosted mode)
                if (!string.IsNullOrEmpty(_apiKey))
                {
                    socket.Options.SetRequestHeader(AuthConstants.ApiKeyHeader, _apiKey);
                }

                ClientWebSocket previousSocket;
                lock (_ownershipLock)
                {
                    ownsLifecycle = ReferenceEquals(_connectionCts, connectionCts) && !connectionToken.IsCancellationRequested;
                    previousSocket = ownsLifecycle ? _socket : null;
                    if (ownsLifecycle) _socket = socket;
                }
                if (!ownsLifecycle)
                {
                    socket.Dispose();
                    return false;
                }
                previousSocket?.Dispose();

                try
                {
                    if (_useLocalAuth)
                    {
                        string launchToken = _ownedConnection?.LaunchToken ?? HttpEndpointUtility.ReadLocalAuthToken(candidate);
                        if (string.IsNullOrEmpty(launchToken))
                        {
                            throw new InvalidOperationException("Local authentication token not found. Start the local HTTP server first.");
                        }
                        socket.Options.SetRequestHeader(AuthConstants.LocalTokenHeader, launchToken);
                    }
                    await socket.ConnectAsync(candidate, connectionToken).ConfigureAwait(false);
                    connectedEndpoint = candidate;
                    break;
                }
                catch (OperationCanceledException) when (connectionToken.IsCancellationRequested)
                {
                    throw;
                }
                catch (Exception ex)
                {
                    lastConnectError = ex;
                    McpLog.Debug($"[WebSocket] Connect failed for {candidate}: {ex.Message}");
                }
            }

            if (connectedEndpoint == null)
            {
                string errorMsg = "Connection failed. Check that the server URL is correct, the server is running, and your API key (if required) is valid.";
                McpLog.Error($"[WebSocket] {errorMsg} (Detail: {lastConnectError?.Message ?? "Unknown error"})");
                lock (_ownershipLock)
                    if (ReferenceEquals(_connectionCts, connectionCts))
                        _state = TransportState.Disconnected(TransportDisplayName, errorMsg);
                return false;
            }

            if (!string.Equals(connectedEndpoint.Host, originalEndpoint.Host, StringComparison.OrdinalIgnoreCase))
            {
                McpLog.Warn($"[WebSocket] Connected via fallback host '{connectedEndpoint.Host}' after '{originalEndpoint.Host}' failed.");
                if (!TryPublishConnectedEndpoint(connectionToken, connectedEndpoint)) return false;
            }

            StartBackgroundLoops(connectionToken);

            try
            {
                await SendRegisterAsync(connectionToken).ConfigureAwait(false);
            }
            catch (Exception ex)
            {
                string regMsg = $"Registration with server failed: {ex.Message}";
                McpLog.Error($"[WebSocket] {regMsg}");
                lock (_ownershipLock)
                    if (ReferenceEquals(_connectionCts, connectionCts))
                        _state = TransportState.Disconnected(TransportDisplayName, regMsg);
                return false;
            }

            return true;
        }

        private sealed class ConnectionLoops
        {
            public CancellationTokenSource Cts;
            public Task Receive;
            public Task KeepAlive;
            public Task Registration;
            public ConnectionCommandWork Commands;
            public IDisposable Publisher;
        }

        // Caller holds _ownershipLock; cancellation callbacks run only after capture.
        private ConnectionLoops CaptureConnectionLoops() => new ConnectionLoops
        {
            Cts = _connectionCts, Receive = _receiveTask, KeepAlive = _keepAliveTask,
            Registration = _registrationTask,
            Commands = _commandWork, Publisher = _statePublisher
        };

        // Callers hold _ownershipLock when checking and publishing connection state.
        private bool IsCurrentConnectionToken(CancellationToken token)
        {
            if (token.IsCancellationRequested || _connectionCts == null) return false;
            try { return _connectionCts.Token == token; }
            catch (ObjectDisposedException) { return false; }
        }

        private bool TryPublishConnectedEndpoint(CancellationToken token, Uri endpoint)
        {
            lock (_ownershipLock)
            {
                if (!IsCurrentConnectionToken(token)) return false;
                _endpointUri = endpoint;
                return true;
            }
        }

        /// <summary>Stops only the connection loops captured by the owning operation.</summary>
        private async Task StopCapturedConnectionLoopsAsync(ConnectionLoops loops, bool awaitTasks = true)
        {
            // Keep ownership of the loops being stopped across awaits. ForceStop or a
            // replacement connection can change these fields before an old loop finishes.
            var connectionCts = loops.Cts;
            var receiveTask = loops.Receive;
            var keepAliveTask = loops.KeepAlive;
            if (connectionCts != null && !connectionCts.IsCancellationRequested)
            {
                try { connectionCts.Cancel(); } catch { }
            }
            loops.Publisher?.Dispose();
            if (loops.Publisher != null) Interlocked.CompareExchange(ref _statePublisher, null, loops.Publisher);
            if (awaitTasks && loops.Commands != null)
            {
                await loops.Commands.DrainAsync().ConfigureAwait(false);
                Interlocked.CompareExchange(ref _commandWork, null, loops.Commands);
            }
            if (awaitTasks && loops.Registration != null)
            {
                try { await loops.Registration.ConfigureAwait(false); } catch { }
                _ = Interlocked.CompareExchange(ref _registrationTask, null, loops.Registration);
            }

            if (receiveTask != null)
            {
                if (awaitTasks)
                {
                    try { await receiveTask.ConfigureAwait(false); } catch { }
                    _ = Interlocked.CompareExchange(ref _receiveTask, null, receiveTask);
                }
                else if (receiveTask.IsCompleted)
                {
                    _ = Interlocked.CompareExchange(ref _receiveTask, null, receiveTask);
                }
            }

            if (keepAliveTask != null)
            {
                if (awaitTasks)
                {
                    try { await keepAliveTask.ConfigureAwait(false); } catch { }
                    _ = Interlocked.CompareExchange(ref _keepAliveTask, null, keepAliveTask);
                }
                else if (keepAliveTask.IsCompleted)
                {
                    _ = Interlocked.CompareExchange(ref _keepAliveTask, null, keepAliveTask);
                }
            }

            if (connectionCts != null)
            {
                connectionCts.Dispose();
                Interlocked.CompareExchange(ref _connectionCts, null, connectionCts);
            }
        }

        private void StartBackgroundLoops(CancellationToken token)
        {
            lock (_ownershipLock)
            {
                if (token.IsCancellationRequested || _connectionCts == null) return;
                try { if (_connectionCts.Token != token) return; }
                catch (ObjectDisposedException) { return; }
                if ((_receiveTask != null && !_receiveTask.IsCompleted) || (_keepAliveTask != null && !_keepAliveTask.IsCompleted))
                {
                    return;
                }

                _receiveTask = Task.Run(() => ReceiveLoopAsync(token), CancellationToken.None);
                _keepAliveTask = Task.Run(() => KeepAliveLoopAsync(token), CancellationToken.None);
            }
        }

        private async Task ReceiveLoopAsync(CancellationToken token)
        {
            while (!token.IsCancellationRequested)
            {
                try
                {
                    string message = await ReceiveMessageAsync(token).ConfigureAwait(false);
                    if (message == null)
                    {
                        continue;
                    }
                    await HandleMessageAsync(message, token).ConfigureAwait(false);
                }
                catch (OperationCanceledException)
                {
                    break;
                }
                catch (WebSocketException wse)
                {
                    McpLog.Warn($"[WebSocket] Receive loop error: {wse.Message}");
                    await HandleSocketClosureAsync(wse.Message, token).ConfigureAwait(false);
                    break;
                }
                catch (Exception ex)
                {
                    McpLog.Warn($"[WebSocket] Unexpected receive error: {ex.Message}");
                    await HandleSocketClosureAsync(ex.Message, token).ConfigureAwait(false);
                    break;
                }
            }
        }

        private async Task<string> ReceiveMessageAsync(CancellationToken token)
        {
            if (_socket == null)
            {
                return null;
            }

            byte[] rentedBuffer = System.Buffers.ArrayPool<byte>.Shared.Rent(8192);
            var buffer = new ArraySegment<byte>(rentedBuffer);
            using var ms = new MemoryStream(8192);

            try
            {
                while (!token.IsCancellationRequested)
                {
                    WebSocketReceiveResult result = await _socket.ReceiveAsync(buffer, token).ConfigureAwait(false);

                    if (result.MessageType == WebSocketMessageType.Close)
                    {
                        await HandleSocketClosureAsync(result.CloseStatusDescription ?? "Server closed connection", token).ConfigureAwait(false);
                        return null;
                    }

                    if (result.Count > 0)
                    {
                        ms.Write(buffer.Array!, buffer.Offset, result.Count);
                    }

                    if (result.EndOfMessage)
                    {
                        break;
                    }
                }

                if (ms.Length == 0)
                {
                    return null;
                }

                return Encoding.UTF8.GetString(ms.ToArray());
            }
            finally
            {
                System.Buffers.ArrayPool<byte>.Shared.Return(rentedBuffer);
            }
        }

        private async Task HandleMessageAsync(string message, CancellationToken token)
        {
            JObject payload;
            try
            {
                payload = JObject.Parse(message);
            }
            catch (Exception ex)
            {
                McpLog.Warn($"[WebSocket] Invalid JSON payload: {ex.Message}");
                return;
            }

            string messageType = payload.Value<string>("type") ?? string.Empty;

            switch (messageType)
            {
                case "welcome":
                    ApplyWelcome(payload, token);
                    break;
                case "registered":
                    await HandleRegisteredAsync(payload, token).ConfigureAwait(false);
                    break;
                case "execute":
                    await HandleExecuteAsync(payload, token).ConfigureAwait(false);
                    break;
                case "cancel":
                    ConnectionCommandWork work;
                    lock (_ownershipLock)
                    {
                        if (!IsCurrentConnectionToken(token) || !_commandCancellationNegotiated) break;
                        work = _commandWork;
                    }
                    work?.TryCancel(payload.Value<string>("id"));
                    break;
                case "ping":
                    await SendPongAsync(token).ConfigureAwait(false);
                    break;
                default:
                    // No-op for unrecognised types (keep-alives, telemetry, etc.)
                    break;
            }
        }

        private void ApplyWelcome(JObject payload, CancellationToken token)
        {
            int? keepAliveSeconds = payload.Value<int?>("keepAliveInterval");
            int? serverTimeoutSeconds = payload.Value<int?>("serverTimeout");
            lock (_ownershipLock)
            {
                if (!IsCurrentConnectionToken(token)) return;
                if (keepAliveSeconds.HasValue && keepAliveSeconds.Value > 0)
                {
                    _keepAliveInterval = TimeSpan.FromSeconds(keepAliveSeconds.Value);
                    _socketKeepAliveInterval = _keepAliveInterval;
                }

                if (serverTimeoutSeconds.HasValue)
                {
                    int sourceSeconds = keepAliveSeconds ?? serverTimeoutSeconds.Value;
                    int safeSeconds = Math.Max(5, Math.Min(serverTimeoutSeconds.Value, sourceSeconds));
                    _socketKeepAliveInterval = TimeSpan.FromSeconds(safeSeconds);
                }
            }
        }

        private Task HandleRegisteredAsync(JObject payload, CancellationToken token)
        {
            string newSessionId = payload.Value<string>("session_id");
            if (!string.IsNullOrEmpty(newSessionId))
            {
                lock (_ownershipLock)
                {
                    if (!IsCurrentConnectionToken(token)) return Task.CompletedTask;
                    _sessionId = newSessionId;
                    _largeResultNegotiated = HasCapability(payload, LargeResultWriter.Capability);
                    _compressionNegotiated = _largeResultNegotiated && HasCapability(payload, LargeResultWriter.CompressionCapability);
                    _commandCancellationNegotiated = HasCapability(payload, ConnectionCommandWork.CancellationCapability);
                    _statePublisher?.Dispose();
                    _statePublisher = null;
                    _state = TransportState.Connected(TransportDisplayName, sessionId: newSessionId, details: _endpointUri.ToString());
                }
                // Validate again when the deferred main-thread write actually executes.
                if (_ownedConnection == null)
                    EditorApplication.delayCall += () => PersistSessionIdIfCurrent(newSessionId, token);
                McpLog.Info($"[WebSocket] Registered with session ID: {newSessionId}", false);

                lock (_ownershipLock)
                {
                    if (!IsCurrentConnectionToken(token)) return Task.CompletedTask;
                    if (_registrationTask == null || _registrationTask.IsCompleted)
                        _registrationTask = CompleteRegistrationAsync(payload, token);
                }
            }
            return Task.CompletedTask;
        }

        private async Task CompleteRegistrationAsync(JObject payload, CancellationToken token)
        {
            try
            {
                await SendRegisterToolsAsync(token).ConfigureAwait(false);
                if (HasCapability(payload, EditorStatePublisher.Capability))
                {
                    var publisher = await TransportCommandDispatcher.RunOnMainThreadAsync(
                        () => EditorStatePublisher.Start((state, sendToken) => SendStateAsync(state, token, sendToken), token), token).ConfigureAwait(false);
                    lock (_ownershipLock)
                    {
                        if (IsCurrentConnectionToken(token))
                        {
                            _statePublisher?.Dispose();
                            _statePublisher = publisher;
                        }
                        else publisher.Dispose();
                    }
                }
            }
            catch (OperationCanceledException) { }
            catch (Exception ex) { await HandleSocketClosureAsync(ex.Message, token).ConfigureAwait(false); }
        }

        private void PersistSessionIdIfCurrent(string sessionId, CancellationToken token)
        {
            if (_ownedConnection != null) return;
            try
            {
                lock (_ownershipLock)
                {
                    if (!IsCurrentConnectionToken(token) || _sessionId != sessionId) return;
                    ProjectIdentityUtility.PersistSessionIdOnMainThread(sessionId);
                }
            }
            catch (Exception ex)
            {
                McpLog.Warn($"Failed to persist session ID: {ex.Message}");
            }
        }

        private async Task SendRegisterToolsAsync(CancellationToken token)
        {
            if (_toolDiscoveryService == null) return;

            token.ThrowIfCancellationRequested();
            var tools = await GetEnabledToolsOnMainThreadAsync(token).ConfigureAwait(false);
            token.ThrowIfCancellationRequested();
            McpLog.Info($"[WebSocket] Preparing to register {tools.Count} tool(s) with the bridge.", false);
            var toolsArray = new JArray();

            foreach (var tool in tools)
            {
                var toolObj = new JObject
                {
                    ["name"] = tool.Name,
                    ["description"] = tool.Description,
                    ["structured_output"] = tool.StructuredOutput,
                    ["requires_polling"] = tool.RequiresPolling,
                    ["poll_action"] = tool.PollAction ?? "status",
                    ["max_poll_seconds"] = tool.MaxPollSeconds,
                    ["group"] = string.IsNullOrWhiteSpace(tool.Group) ? "core" : tool.Group
                };

                var paramsArray = new JArray();
                if (tool.Parameters != null)
                {
                    foreach (var p in tool.Parameters)
                    {
                        paramsArray.Add(new JObject
                        {
                            ["name"] = p.Name,
                            ["description"] = p.Description,
                            ["type"] = p.Type,
                            ["required"] = p.Required,
                            ["default_value"] = p.DefaultValue
                        });
                    }
                }
                toolObj["parameters"] = paramsArray;
                toolsArray.Add(toolObj);
            }

            var payload = new JObject
            {
                ["type"] = "register_tools",
                ["tools"] = toolsArray
            };

            await SendJsonAsync(payload, token).ConfigureAwait(false);
            McpLog.Info($"[WebSocket] Sent {tools.Count} tools registration", false);
        }

        public async Task ReregisterToolsAsync()
        {
            if (!IsConnected || _lifecycleCts == null)
            {
                McpLog.Warn("[WebSocket] Cannot reregister tools: not connected");
                return;
            }

            try
            {
                CancellationToken token;
                lock (_ownershipLock)
                {
                    if (_connectionCts == null) return;
                    token = _connectionCts.Token;
                }
                await SendRegisterToolsAsync(token).ConfigureAwait(false);
                McpLog.Info("[WebSocket] Tool reregistration completed", false);
            }
            catch (System.OperationCanceledException)
            {
                McpLog.Warn("[WebSocket] Tool reregistration cancelled");
            }
            catch (System.Exception ex)
            {
                McpLog.Error($"[WebSocket] Tool reregistration failed: {ex.Message}");
            }
        }

        private async Task HandleExecuteAsync(JObject payload, CancellationToken token)
        {
            string commandId = payload.Value<string>("id");
            string commandName = payload.Value<string>("name");
            JObject parameters = payload.Value<JObject>("params") ?? new JObject();
            int timeoutSeconds = payload.Value<int?>("timeout") ?? (int)DefaultCommandTimeout.TotalSeconds;

            if (string.IsNullOrEmpty(commandId) || string.IsNullOrEmpty(commandName))
            {
                McpLog.Warn("[WebSocket] Invalid execute payload (missing id or name)");
                return;
            }

            ConnectionCommandWork work;
            lock (_ownershipLock)
            {
                if (!IsCurrentConnectionToken(token)) return;
                work = _commandWork;
            }
            if (work == null) return;
            var deadline = CancellationTokenSource.CreateLinkedTokenSource(token);
            deadline.CancelAfter(TimeSpan.FromSeconds(Math.Max(1, Math.Min(int.MaxValue / 1000, timeoutSeconds))));
            var command = new Command { type = commandName, @params = parameters };
            var request = new QueuedCommand { Id = commandId, Command = command, TimeoutSeconds = timeoutSeconds, Deadline = deadline };
            string rejection = work.TryStart(commandId, previous => ExecuteQueuedCommandAsync(previous, request, token), request.Cancel);
            if (rejection != null)
            {
                deadline.Dispose();
                await SendCommandResultAsync(commandId, new { status = "error", error = rejection }, token).ConfigureAwait(false);
            }
        }

        private sealed class QueuedCommand
        {
            public string Id;
            public Command Command;
            public int TimeoutSeconds;
            public CancellationTokenSource Deadline;
            private int _cancelRequested;
            public bool CancelRequested => Volatile.Read(ref _cancelRequested) != 0;
            public void Cancel()
            {
                Interlocked.Exchange(ref _cancelRequested, 1);
                Deadline.Cancel();
            }
        }

        private async Task ExecuteQueuedCommandAsync(Task previous, QueuedCommand request, CancellationToken token)
        {
            TransportCommandOperation operation = null;
            try
            {
                object response;
                try
                {
                    await ConnectionCommandWork.WaitAsync(previous, request.Deadline.Token).ConfigureAwait(false);
                    request.Deadline.Token.ThrowIfCancellationRequested();
                    operation = TransportCommandDispatcher.ExecuteCommandAsync(request.Command, request.Deadline.Token);
                    response = (await operation.Response.ConfigureAwait(false)).Payload;
                }
                catch (OperationCanceledException)
                {
                    token.ThrowIfCancellationRequested();
                    response = new { status = "error", error = request.CancelRequested
                        ? $"Command '{request.Command.type}' canceled"
                        : $"Command '{request.Command.type}' timed out after {request.TimeoutSeconds} seconds" };
                }
                catch (Exception ex) { response = new { status = "error", error = ex.Message }; }
                await SendCommandResultAsync(request.Id, response, token).ConfigureAwait(false);
            }
            catch (OperationCanceledException) { }
            catch (Exception ex)
            {
                await HandleSocketClosureAsync(ex.Message, token).ConfigureAwait(false);
            }
            finally
            {
                try
                {
                    // Response delivery may be canceled; mutation settlement must remain truthful.
                    await previous.ConfigureAwait(false);
                    if (operation != null) await operation.Completion.ConfigureAwait(false);
                }
                catch (OperationCanceledException) { }
                request.Deadline.Dispose();
            }
        }

        private Task SendCommandResultAsync(string id, object result, CancellationToken token)
        {
            var responseJson = new LargeResultWriter.PreparedJson(JsonConvert.SerializeObject(new { type = "command_result", id, result }));
            bool negotiated;
            bool compressionNegotiated;
            lock (_ownershipLock)
            {
                token.ThrowIfCancellationRequested();
                if (!IsCurrentConnectionToken(token)) throw new OperationCanceledException(token);
                negotiated = _largeResultNegotiated;
                compressionNegotiated = _compressionNegotiated;
            }
            if (responseJson.ByteCount > LargeResultWriter.MaxResultBytes)
                responseJson = new LargeResultWriter.PreparedJson(JsonConvert.SerializeObject(new { type = "command_result", id,
                    result = new { status = "error", error = "Command result exceeds the transport size limit" } }));
            // Legacy integrations can use opaque IDs; they retain text responses.
            if (!Guid.TryParseExact(id, "D", out var parsedId) || parsedId.ToString("D") != id) negotiated = false;
            bool allowCompression = _ownedConnection?.AllowCompression ?? string.Equals(
                Environment.GetEnvironmentVariable("UNITY_MCP_RESULT_COMPRESSION"), "gzip", StringComparison.OrdinalIgnoreCase);
            return LargeResultWriter.SendPreparedJsonAsync(id, responseJson, negotiated, SendFrameAsync, token,
                compressionNegotiated, allowCompression);
        }

        private async Task KeepAliveLoopAsync(CancellationToken token)
        {
            while (!token.IsCancellationRequested)
            {
                try
                {
                    await Task.Delay(_keepAliveInterval, token).ConfigureAwait(false);
                    if (_socket == null || _socket.State != WebSocketState.Open)
                    {
                        break;
                    }
                    await SendPongAsync(token).ConfigureAwait(false);
                }
                catch (OperationCanceledException)
                {
                    break;
                }
                catch (Exception ex)
                {
                    McpLog.Warn($"[WebSocket] Keep-alive failed: {ex.Message}");
                    await HandleSocketClosureAsync(ex.Message, token).ConfigureAwait(false);
                    break;
                }
            }
        }

        private async Task SendRegisterAsync(CancellationToken token)
        {
            var registerPayload = new JObject
            {
                ["type"] = "register",
                // session_id is now server-authoritative; omitted here or sent as null
                ["project_name"] = _projectName,
                ["project_hash"] = _projectHash,
                ["unity_version"] = _unityVersion,
                ["project_path"] = _projectPath,
                ["capabilities"] = new JArray(EditorStatePublisher.Capability, LargeResultWriter.Capability,
                    ConnectionCommandWork.CancellationCapability, LargeResultWriter.CompressionCapability)
            };

            await SendJsonAsync(registerPayload, token).ConfigureAwait(false);
        }

        private Task SendPongAsync(CancellationToken token)
        {
            var payload = new JObject
            {
                ["type"] = "pong",
                ["session_id"] = _sessionId  // Include session ID for server-side tracking
            };
            return SendJsonAsync(payload, token);
        }

        private async Task SendJsonAsync(JObject payload, CancellationToken token)
        {
            string json = payload.ToString(Formatting.None);
            byte[] bytes = Encoding.UTF8.GetBytes(json);
            await SendFrameAsync(new ArraySegment<byte>(bytes), WebSocketMessageType.Text, token).ConfigureAwait(false);
        }

        private static bool HasCapability(JObject payload, string capability)
        {
            if (payload["capabilities"] is not JArray capabilities) return false;
            foreach (var value in capabilities)
                if (value.Type == JTokenType.String && value.Value<string>() == capability) return true;
            return false;
        }

        private Task SendStateAsync(JObject payload, CancellationToken ownerToken, CancellationToken sendToken)
            => SendOwnedFrameAsync(new ArraySegment<byte>(Encoding.UTF8.GetBytes(payload.ToString(Formatting.None))),
                WebSocketMessageType.Text, ownerToken, sendToken);

        private Task SendFrameAsync(ArraySegment<byte> buffer, WebSocketMessageType type, CancellationToken token)
            => SendOwnedFrameAsync(buffer, type, token, token);

        private async Task SendOwnedFrameAsync(ArraySegment<byte> buffer, WebSocketMessageType type,
            CancellationToken ownerToken, CancellationToken token)
        {
            ClientWebSocket socket;
            lock (_ownershipLock)
            {
                if (!IsCurrentConnectionToken(ownerToken)) throw new OperationCanceledException(ownerToken);
                socket = _socket ?? throw new InvalidOperationException("WebSocket is not initialised");
            }

            await _sendLock.WaitAsync(token).ConfigureAwait(false);
            try
            {
                lock (_ownershipLock)
                    if (!IsCurrentConnectionToken(ownerToken) || !ReferenceEquals(_socket, socket))
                        throw new OperationCanceledException(ownerToken);
                if (socket.State != WebSocketState.Open)
                {
                    throw new InvalidOperationException("WebSocket is not open");
                }

                await socket.SendAsync(buffer, type, true, token).ConfigureAwait(false);
            }
            finally
            {
                _sendLock.Release();
            }
        }

        private async Task HandleSocketClosureAsync(string reason, CancellationToken connectionToken)
        {
            CancellationTokenSource lifecycleCts;
            CancellationToken lifecycleToken;
            ConnectionLoops loops;
            lock (_ownershipLock)
            {
                var connectionCts = _connectionCts;
                lifecycleCts = _lifecycleCts;
                if (connectionToken.IsCancellationRequested || connectionCts == null ||
                    lifecycleCts == null || lifecycleCts.IsCancellationRequested) return;
                try
                {
                    if (connectionCts.Token != connectionToken) return;
                    lifecycleToken = lifecycleCts.Token;
                }
                catch (ObjectDisposedException) { return; }
                if (Interlocked.CompareExchange(ref _isReconnectingFlag, 1, 0) != 0) return;
                _isConnected = false;
                _state = TransportState.Disconnected(TransportDisplayName, reason ?? "Connection closed");
                loops = CaptureConnectionLoops();
            }
            // Capture stack trace for debugging disconnection triggers
            var stackTrace = new System.Diagnostics.StackTrace(true);
            McpLog.Debug($"[WebSocket] HandleSocketClosureAsync called. Reason: {reason}\nStack trace:\n{stackTrace}");

            McpLog.Warn($"[WebSocket] Connection closed: {reason}");

            await StopCapturedConnectionLoopsAsync(loops, awaitTasks: false).ConfigureAwait(false);

            if (lifecycleToken.IsCancellationRequested || !ReferenceEquals(_lifecycleCts, lifecycleCts))
            {
                lock (_ownershipLock)
                    if (ReferenceEquals(_lifecycleCts, lifecycleCts))
                        Interlocked.Exchange(ref _isReconnectingFlag, 0);
                return;
            }

            _ = Task.Run(() => AttemptReconnectAsync(lifecycleToken, lifecycleCts), CancellationToken.None);
        }

        private async Task AttemptReconnectAsync(CancellationToken token, CancellationTokenSource lifecycleCts)
        {
            try
            {
                if (token.IsCancellationRequested) return;
                ConnectionLoops loops;
                lock (_ownershipLock)
                {
                    if (!ReferenceEquals(_lifecycleCts, lifecycleCts) || token.IsCancellationRequested) return;
                    loops = CaptureConnectionLoops();
                }
                await StopCapturedConnectionLoopsAsync(loops).ConfigureAwait(false);

                foreach (TimeSpan delay in ReconnectSchedule)
                {
                    if (token.IsCancellationRequested)
                    {
                        return;
                    }

                    if (delay > TimeSpan.Zero)
                    {
                        try { await Task.Delay(delay, token).ConfigureAwait(false); }
                        catch (OperationCanceledException) { return; }
                    }

                    if (await EstablishConnectionAsync(token).ConfigureAwait(false))
                    {
                        lock (_ownershipLock)
                        {
                            if (!ReferenceEquals(_lifecycleCts, lifecycleCts) || token.IsCancellationRequested) return;
                            _state = TransportState.Connected(TransportDisplayName, sessionId: _sessionId, details: _endpointUri.ToString());
                            _isConnected = true;
                        }
                        McpLog.Info("[WebSocket] Reconnected to MCP server", false);
                        return;
                    }
                }

                // Schedule exhausted — keep retrying every 30 s indefinitely so a transient
                // server outage longer than ~49 s doesn't leave the plugin permanently dead.
                McpLog.Warn($"[WebSocket] Initial reconnect schedule exhausted. Retrying every {ReconnectTailInterval.TotalSeconds}s until cancelled.");
                lock (_ownershipLock)
                {
                    if (!ReferenceEquals(_lifecycleCts, lifecycleCts) || token.IsCancellationRequested) return;
                    _state = _state.WithError($"Server unreachable – retrying every {ReconnectTailInterval.TotalSeconds} s");
                }
                while (!token.IsCancellationRequested)
                {
                    try { await Task.Delay(ReconnectTailInterval, token).ConfigureAwait(false); }
                    catch (OperationCanceledException) { return; }

                    if (await EstablishConnectionAsync(token).ConfigureAwait(false))
                    {
                        lock (_ownershipLock)
                        {
                            if (!ReferenceEquals(_lifecycleCts, lifecycleCts) || token.IsCancellationRequested) return;
                            _state = TransportState.Connected(TransportDisplayName, sessionId: _sessionId, details: _endpointUri.ToString());
                            _isConnected = true;
                        }
                        McpLog.Info("[WebSocket] Reconnected to MCP server", false);
                        return;
                    }
                }
            }
            finally
            {
                lock (_ownershipLock)
                    if (ReferenceEquals(_lifecycleCts, lifecycleCts))
                        Interlocked.Exchange(ref _isReconnectingFlag, 0);
            }
        }

        private static Uri BuildWebSocketUri(string baseUrl)
        {
            if (!Uri.TryCreate(baseUrl, UriKind.Absolute, out var httpUri))
            {
                throw new InvalidOperationException($"Invalid MCP base URL: {baseUrl}");
            }

            // Replace bind-only addresses for client connections
            // 0.0.0.0 and :: are only valid for server binding, not client connections
            string host = httpUri.Host;
            if (host == "0.0.0.0")
            {
                McpLog.Warn($"[WebSocket] Base URL host '{host}' is bind-only; using '127.0.0.1' for client connection.");
                host = "127.0.0.1";
            }
            else if (host == "::")
            {
                McpLog.Warn($"[WebSocket] Base URL host '{host}' is bind-only; using '::1' for client connection.");
                host = "::1";
            }

            var builder = new UriBuilder(httpUri)
            {
                Scheme = httpUri.Scheme.Equals("https", StringComparison.OrdinalIgnoreCase) ? "wss" : "ws",
                Host = host,
                Path = httpUri.AbsolutePath.TrimEnd('/') + "/hub/plugin"
            };

            return builder.Uri;
        }

        private static List<Uri> BuildConnectionCandidateUris(Uri endpointUri)
        {
            var candidates = new List<Uri>();
            if (endpointUri == null)
            {
                return candidates;
            }

            candidates.Add(endpointUri);

            if (!string.Equals(endpointUri.Host, "localhost", StringComparison.OrdinalIgnoreCase))
            {
                return candidates;
            }

            // Retry localhost using explicit loopback hosts to avoid DNS family ambiguity on some machines.
            TryAddCandidate(candidates, endpointUri, "127.0.0.1");
            TryAddCandidate(candidates, endpointUri, "::1");
            return candidates;
        }

        private static void TryAddCandidate(List<Uri> candidates, Uri template, string host)
        {
            try
            {
                var builder = new UriBuilder(template) { Host = host };
                Uri candidate = builder.Uri;
                foreach (Uri existing in candidates)
                {
                    if (Uri.Compare(existing, candidate, UriComponents.AbsoluteUri, UriFormat.SafeUnescaped, StringComparison.OrdinalIgnoreCase) == 0)
                    {
                        return;
                    }
                }
                candidates.Add(candidate);
            }
            catch
            {
                // Ignore malformed fallback candidate and continue with remaining options.
            }
        }
    }
}
