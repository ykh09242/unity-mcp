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
using MCPForUnity.Editor.Tools;
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
    public class WebSocketTransportClient : IMcpTransportClient, IToolSettingsStatusSource, IDisposable
    {
        private const string TransportDisplayName = "websocket";
        private static readonly TimeSpan[] ReconnectSchedule =
        {
            TimeSpan.Zero,
            TimeSpan.FromSeconds(1),
            TimeSpan.FromSeconds(3),
            TimeSpan.FromSeconds(5),
            TimeSpan.FromSeconds(10),
            TimeSpan.FromSeconds(30),
        };
        private static readonly TimeSpan ReconnectTailInterval = TimeSpan.FromSeconds(30);

        private static readonly TimeSpan DefaultKeepAliveInterval = TimeSpan.FromSeconds(15);
        private static readonly TimeSpan DefaultCommandTimeout = TimeSpan.FromSeconds(300);

        private readonly IToolDiscoveryService _toolDiscoveryService;
        private readonly object _ownershipLock = new object();
        private long _startVersion;
        private readonly Dictionary<CancellationTokenSource, Task> _cancellations = new Dictionary<CancellationTokenSource, Task>();
        private ClientWebSocket _socket;
        private CancellationTokenSource _lifecycleCts;
        private CancellationTokenSource _connectionCts;
        private CommandAdmission _commandAdmission;
        private Task _receiveTask;
        private Task _keepAliveTask;
        private Task _registrationTask;
        private ToolSettingsRegistration _toolSettingsRegistration;
        private ToolSettingsState _toolSettingsState;
        private bool _toolSettingsNegotiated;
        private ConnectionCommandWork _commandWork;
        private Task _forcedCommandSettlement = Task.CompletedTask;
        private Task _stopSettlement = Task.CompletedTask;
        private CancellationTokenSource _stoppingLifecycle;
        private static TimeSpan ControlObservationTimeout =>
            TimeSpan.FromSeconds(OperationTimeouts.EnvironmentSeconds("UNITY_MCP_WEBSOCKET_CONTROL_TIMEOUT_SECONDS", 10));
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
        private bool _phaseTimingOffered;
        private bool _phaseTimingNegotiated;
        private bool _arbitrationOffered;
        private bool _arbitrationNegotiated;
        private bool _resumeNegotiated;
        private bool _resumeOffered;
        private bool _commandProgressOffered;
        private bool _commandProgressNegotiated;

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

            internal OwnedConnectionOptions(
                Uri endpoint,
                string launchToken,
                string projectName,
                string projectHash,
                string projectPath,
                string unityVersion,
                bool allowCompression = false
            )
            {
                if (
                    endpoint == null
                    || !endpoint.IsAbsoluteUri
                    || !endpoint.IsLoopback
                    || (endpoint.Scheme != "ws" && endpoint.Scheme != "wss")
                    || !string.IsNullOrEmpty(endpoint.UserInfo)
                )
                    throw new ArgumentException("Owned connections require a loopback WebSocket endpoint", nameof(endpoint));
                if (string.IsNullOrWhiteSpace(launchToken))
                    throw new ArgumentException("An owned launch token is required", nameof(launchToken));
                if (
                    string.IsNullOrWhiteSpace(projectName)
                    || string.IsNullOrWhiteSpace(projectHash)
                    || string.IsNullOrWhiteSpace(projectPath)
                    || string.IsNullOrWhiteSpace(unityVersion)
                )
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
            : this(toolDiscoveryService, null) { }

        internal WebSocketTransportClient(IToolDiscoveryService toolDiscoveryService, OwnedConnectionOptions options)
        {
            _toolDiscoveryService = toolDiscoveryService;
            _ownedConnection = options;
            _toolSettingsState = options == null ? ToolSettingsState.Current : new ToolSettingsState();
        }

        public ToolSettingsStatus Status => _toolSettingsState.Status;
        public event Action StatusChanged
        {
            add => _toolSettingsState.StatusChanged += value;
            remove => _toolSettingsState.StatusChanged -= value;
        }

        public void NotifyToolSettingsChanged() => _toolSettingsState.NotifyToolSettingsChanged();

        public Task RetryToolSettingsAsync() => _toolSettingsState.RetryToolSettingsAsync();

        public bool IsConnected => _isConnected;
        public string TransportName => TransportDisplayName;
        public TransportState State => _state;

        private Task<List<ToolMetadata>> GetEnabledToolsOnMainThreadAsync(CancellationToken token)
        {
            return TransportCommandDispatcher.RunOnMainThreadAsync(() => _toolDiscoveryService?.GetEnabledTools() ?? new List<ToolMetadata>(), token);
        }

        public async Task<bool> StartAsync()
        {
            long startVersion;
            lock (_ownershipLock)
                startVersion = ++_startVersion;

            // Capture identity values on the main thread before any async context switching
            _projectName = _ownedConnection?.ProjectName ?? ProjectIdentityUtility.GetProjectName();
            _projectHash = _ownedConnection?.ProjectHash ?? ProjectIdentityUtility.GetProjectHash();
            _unityVersion = _ownedConnection?.UnityVersion ?? Application.unityVersion;
            bool remote = _ownedConnection == null && HttpEndpointUtility.IsRemoteScope();
            _useLocalAuth = !remote;
            _apiKey = remote ? EditorPrefs.GetString(EditorPrefKeys.ApiKey, string.Empty) : string.Empty;

            if (remote && !HttpEndpointUtility.IsCurrentRemoteUrlAllowed(out string remoteUrlError))
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
                    _projectPath = normalized; // Fallback if path doesn't end with Assets
                }
            }

            Task previousStop;
            lock (_ownershipLock)
            {
                if (startVersion != _startVersion)
                    return false;
                // Internal restart cleanup must not invalidate this or a newer start intent.
                previousStop = StopOwnedAsync(null);
            }
            await previousStop;

            CancellationTokenSource lifecycleCts;
            CancellationToken lifecycleToken;
            lock (_ownershipLock)
            {
                // Stop can run while the old drain or this continuation is pending.
                if (startVersion != _startVersion)
                    return false;
                if (!_stopSettlement.IsCompletedSuccessfully || !_forcedCommandSettlement.IsCompletedSuccessfully)
                {
                    PublishUnsettledState();
                    return false;
                }
                Uri endpointUri = _ownedConnection?.Endpoint ?? BuildWebSocketUri(HttpEndpointUtility.GetBaseUrl());
                lifecycleCts = new CancellationTokenSource();
                lifecycleToken = lifecycleCts.Token;
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
                if (startVersion != _startVersion || !ReferenceEquals(_lifecycleCts, lifecycleCts) || lifecycleCts.IsCancellationRequested)
                    return false;
                _state = TransportState.Connected(TransportDisplayName, sessionId: _sessionId, details: _endpointUri.ToString());
                _isConnected = true;
            }
            return true;
        }

        public Task StopAsync() => StopOwnedAsync(null, invalidateStart: true);

        private async Task StopOwnedAsync(CancellationTokenSource expectedLifecycle, bool invalidateStart = false)
        {
            CancellationTokenSource lifecycleCts;
            Task observation;
            lock (_ownershipLock)
            {
                if (invalidateStart)
                    ++_startVersion;
                lifecycleCts = _lifecycleCts;
                if (expectedLifecycle != null && !ReferenceEquals(lifecycleCts, expectedLifecycle))
                    return;
                if (lifecycleCts != null && !ReferenceEquals(_stoppingLifecycle, lifecycleCts))
                {
                    var socket = _socket;
                    var loops = CaptureConnectionLoops();
                    loops.Admission?.Retire();
                    var previous = _stopSettlement;
                    var completion = new TaskCompletionSource<bool>(TaskCreationOptions.RunContinuationsAsynchronously);
                    _stoppingLifecycle = lifecycleCts;
                    _stopSettlement = completion.Task;
                    _isConnected = false;
                    _state = TransportState.Disconnected(TransportDisplayName);
                    // Publish ownership before cancellation can reenter Start/Stop.
                    // User cancellation callbacks must not block the Editor thread.
                    _ = Task.Run(() => CompleteOwnedStopAsync(previous, lifecycleCts, loops, socket, completion));
                }
                observation = Task.WhenAll(_stopSettlement, _forcedCommandSettlement);
            }
            await ObserveControlAsync(observation, lifecycleCts).ConfigureAwait(false);
        }

        private async Task CompleteOwnedStopAsync(
            Task previous,
            CancellationTokenSource lifecycleCts,
            ConnectionLoops loops,
            ClientWebSocket socket,
            TaskCompletionSource<bool> completion
        )
        {
            try
            {
                await CancelOwnedAsync(loops.Cts).ConfigureAwait(false);
                await CancelOwnedAsync(lifecycleCts).ConfigureAwait(false);
                await Task.WhenAll(previous, StopCapturedConnectionLoopsAsync(loops)).ConfigureAwait(false);
                if (socket != null)
                {
                    try
                    {
                        using var closeBudget = new CancellationTokenSource(ControlObservationTimeout);
                        if (socket.State == WebSocketState.Open || socket.State == WebSocketState.CloseReceived)
                            await socket.CloseAsync(WebSocketCloseStatus.NormalClosure, "Shutdown", closeBudget.Token).ConfigureAwait(false);
                    }
                    catch
                    {
                        try
                        {
                            socket.Abort();
                        }
                        catch { }
                    }
                    finally
                    {
                        socket.Dispose();
                        Interlocked.CompareExchange(ref _socket, null, socket);
                    }
                }
                lifecycleCts.Dispose();
                lock (_ownershipLock)
                {
                    if (ReferenceEquals(_lifecycleCts, lifecycleCts))
                    {
                        _lifecycleCts = null;
                        _isConnected = false;
                        _state = TransportState.Disconnected(TransportDisplayName);
                    }
                    if (ReferenceEquals(_stopSettlement, completion.Task))
                    {
                        _stopSettlement = Task.CompletedTask;
                        _stoppingLifecycle = null;
                    }
                    completion.TrySetResult(true);
                    ClearSettledState();
                }
            }
            catch (Exception error)
            {
                // A failed cleanup is not proof that the old owner stopped.
                completion.TrySetException(error);
                _ = completion.Task.Exception;
                lock (_ownershipLock)
                    if (ReferenceEquals(_lifecycleCts, lifecycleCts) || _lifecycleCts == null)
                        PublishUnsettledState();
            }
        }

        private async Task<bool> ObserveControlAsync(Task settlement, CancellationTokenSource owner, CancellationToken cancellation = default)
        {
            using var observation = CancellationTokenSource.CreateLinkedTokenSource(cancellation);
            observation.CancelAfter(ControlObservationTimeout);
            try
            {
                await ConnectionCommandWork.WaitAsync(settlement, observation.Token).ConfigureAwait(false);
                return true;
            }
            catch (OperationCanceledException) when (!cancellation.IsCancellationRequested)
            {
                lock (_ownershipLock)
                {
                    if (settlement.IsCompletedSuccessfully)
                        return true;
                    if (ReferenceEquals(_lifecycleCts, owner) || _lifecycleCts == null)
                        PublishUnsettledState();
                }
                return false;
            }
        }

        // Caller holds _ownershipLock. An observation limit never releases execution ownership.
        private void PublishUnsettledState()
        {
            _isConnected = false;
            _state = TransportState.Disconnected(
                TransportDisplayName,
                "execution_unsettled: previous connection work is still settling. New commands remain blocked until it finishes."
            );
        }

        private void ClearSettledState()
        {
            if (
                _lifecycleCts == null
                && _stopSettlement.IsCompletedSuccessfully
                && _forcedCommandSettlement.IsCompletedSuccessfully
                && _state.Error?.StartsWith("execution_unsettled:", StringComparison.Ordinal) == true
            )
                _state = TransportState.Disconnected(TransportDisplayName);
        }

        /// <summary>
        /// Synchronous teardown for use in beforeAssemblyReload where async is not possible.
        /// Skips the graceful close handshake and aborts the socket immediately. Live tasks retain their cancellation owners.
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
                ++_startVersion;
                lifecycleCts = _lifecycleCts;
                loops = CaptureConnectionLoops();
                loops.Admission?.Retire();
                if (
                    lifecycleCts != null
                    || loops.Cts != null
                    || loops.Commands != null
                    || loops.Receive != null
                    || loops.KeepAlive != null
                    || loops.Registration != null
                    || loops.ToolSettings != null
                    || loops.Publisher != null
                )
                {
                    previousSettlement = Task.WhenAll(_forcedCommandSettlement, _stopSettlement);
                    settlement = new TaskCompletionSource<bool>(TaskCreationOptions.RunContinuationsAsynchronously);
                    // Publish before cancellation callbacks can attempt a restart.
                    _forcedCommandSettlement = settlement.Task;
                }
                socket = _socket;
                _lifecycleCts = null;
                _connectionCts = null;
                _commandAdmission = null;
                _socket = null;
                _receiveTask = null;
                _keepAliveTask = null;
                _registrationTask = null;
                _toolSettingsRegistration = null;
                _toolSettingsNegotiated = false;
                _commandWork = null;
                _statePublisher = null;
                _largeResultNegotiated = false;
                _arbitrationOffered = false;
                _arbitrationNegotiated = false;
                _resumeNegotiated = false;
                _resumeOffered = false;
                _phaseTimingOffered = false;
                _phaseTimingNegotiated = false;
                _commandProgressOffered = false;
                _commandProgressNegotiated = false;
                Interlocked.Exchange(ref _isReconnectingFlag, 0);
                _isConnected = false;
                _state = TransportState.Disconnected(TransportDisplayName);
            }
            try
            {
                socket?.Abort();
            }
            catch { }
            try
            {
                socket?.Dispose();
            }
            catch { }
            if (settlement != null)
                _ = Task.Run(() => CompleteForcedStopAsync(previousSettlement, loops, lifecycleCts, settlement));
        }

        private async Task CompleteForcedStopAsync(
            Task previous,
            ConnectionLoops loops,
            CancellationTokenSource lifecycle,
            TaskCompletionSource<bool> settlement
        )
        {
            try
            {
                await CancelOwnedAsync(loops.Cts).ConfigureAwait(false);
                await CancelOwnedAsync(lifecycle).ConfigureAwait(false);
                await Task.WhenAll(previous, StopCapturedConnectionLoopsAsync(loops)).ConfigureAwait(false);
                lifecycle?.Dispose();
                lock (_ownershipLock)
                {
                    if (ReferenceEquals(_forcedCommandSettlement, settlement.Task))
                        _forcedCommandSettlement = Task.CompletedTask;
                    settlement.TrySetResult(true);
                    ClearSettledState();
                }
            }
            catch (Exception ex)
            {
                settlement.TrySetException(ex);
                _ = settlement.Task.Exception;
            }
        }

        // CancellationTokenSource.Cancel runs user callbacks synchronously. Share its
        // actual completion across overlapping stop/reconnect owners before disposing it.
        private Task CancelOwnedAsync(CancellationTokenSource source)
        {
            if (source == null)
                return Task.CompletedTask;
            lock (_ownershipLock)
            {
                if (_cancellations.TryGetValue(source, out Task pending))
                    return pending;
                var completion = new TaskCompletionSource<bool>(TaskCreationOptions.RunContinuationsAsynchronously);
                _cancellations.Add(source, completion.Task);
                _ = Task.Run(() =>
                {
                    try
                    {
                        source.Cancel();
                    }
                    catch { }
                    finally
                    {
                        lock (_ownershipLock)
                        {
                            _cancellations.Remove(source);
                            completion.TrySetResult(true);
                        }
                    }
                });
                return completion.Task;
            }
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
                    if (_connectionCts == null)
                        return false;
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
                if (lifecycleCts == null || token.IsCancellationRequested || ReferenceEquals(_stoppingLifecycle, lifecycleCts))
                    return false;
                try
                {
                    if (lifecycleCts.Token != token)
                        return false;
                }
                catch (ObjectDisposedException)
                {
                    return false;
                }
                loops = CaptureConnectionLoops();
                forcedSettlement = _forcedCommandSettlement;
            }
            // A manual restart may have no current loops after ForceStop, but its old handlers still own mutations.
            var settlement = Task.WhenAll(StopCapturedConnectionLoopsAsync(loops), forcedSettlement);
            if (!await ObserveControlAsync(settlement, lifecycleCts, token).ConfigureAwait(false))
                return false;
            token.ThrowIfCancellationRequested();
            var connectionCts = CancellationTokenSource.CreateLinkedTokenSource(token);
            var admission = new CommandAdmission();
            CancellationToken connectionToken = connectionCts.Token;
            bool ownsLifecycle;
            lock (_ownershipLock)
            {
                ownsLifecycle =
                    ReferenceEquals(_lifecycleCts, lifecycleCts) && !ReferenceEquals(_stoppingLifecycle, lifecycleCts) && !token.IsCancellationRequested;
                if (ownsLifecycle)
                {
                    _connectionCts = connectionCts;
                    _commandAdmission = admission;
                    // A registered reply can arrive before the Start/reconnect continuation.
                    _sessionId = "pending";
                    _commandWork = new ConnectionCommandWork(connectionToken);
                    _largeResultNegotiated = false;
                    _commandCancellationNegotiated = false;
                    _compressionNegotiated = false;
                    _arbitrationOffered = false;
                    _arbitrationNegotiated = false;
                    _resumeNegotiated = false;
                    _resumeOffered = false;
                    _phaseTimingOffered = false;
                    _phaseTimingNegotiated = false;
                    _commandProgressOffered = false;
                    _commandProgressNegotiated = false;
                }
            }
            if (!ownsLifecycle)
            {
                admission.Dispose();
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
                if (_ownedConnection != null)
                {
                    socket.Options.Proxy = null;
                }

                // Add API key header if configured (for remote-hosted mode)
                if (!string.IsNullOrEmpty(_apiKey))
                {
                    socket.Options.SetRequestHeader(AuthConstants.ApiKeyHeader, _apiKey);
                }

                ClientWebSocket previousSocket;
                lock (_ownershipLock)
                {
                    ownsLifecycle =
                        ReferenceEquals(_connectionCts, connectionCts)
                        && !ReferenceEquals(_stoppingLifecycle, lifecycleCts)
                        && !connectionToken.IsCancellationRequested;
                    previousSocket = ownsLifecycle ? _socket : null;
                    if (ownsLifecycle)
                        _socket = socket;
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
                if (!TryPublishConnectedEndpoint(connectionToken, connectedEndpoint))
                    return false;
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

        // These tokens are observed by admission only: never register callbacks or pass
        // them to a handler. Retirement must finish before user cancellation can run.
        private sealed class CommandAdmission : IDisposable
        {
            private readonly object gate = new object();
            private CancellationTokenSource source = new CancellationTokenSource();
            internal CancellationToken Token { get; }

            internal CommandAdmission() => Token = source.Token;

            internal void Retire()
            {
                lock (gate)
                    source?.Cancel();
            }

            public void Dispose()
            {
                lock (gate)
                {
                    source?.Cancel();
                    source?.Dispose();
                    source = null;
                }
            }
        }

        private sealed class ConnectionLoops
        {
            public CancellationTokenSource Cts;
            public CommandAdmission Admission;
            public Task Receive;
            public Task KeepAlive;
            public Task Registration;
            public ToolSettingsRegistration ToolSettings;
            public ConnectionCommandWork Commands;
            public IDisposable Publisher;
        }

        // Caller holds _ownershipLock; cancellation callbacks run only after capture.
        private ConnectionLoops CaptureConnectionLoops() =>
            new ConnectionLoops
            {
                Cts = _connectionCts,
                Admission = _commandAdmission,
                Receive = _receiveTask,
                KeepAlive = _keepAliveTask,
                Registration = _registrationTask,
                ToolSettings = _toolSettingsRegistration,
                Commands = _commandWork,
                Publisher = _statePublisher,
            };

        // Callers hold _ownershipLock when checking and publishing connection state.
        private bool IsCurrentConnectionToken(CancellationToken token)
        {
            if (token.IsCancellationRequested || _connectionCts == null || _commandAdmission?.Token.IsCancellationRequested == true)
                return false;
            try
            {
                return _connectionCts.Token == token;
            }
            catch (ObjectDisposedException)
            {
                return false;
            }
        }

        private bool TryPublishConnectedEndpoint(CancellationToken token, Uri endpoint)
        {
            lock (_ownershipLock)
            {
                if (!IsCurrentConnectionToken(token))
                    return false;
                _endpointUri = endpoint;
                return true;
            }
        }

        /// <summary>Stops only the connection loops captured by the owning operation.</summary>
        private Task StopCapturedConnectionLoopsAsync(ConnectionLoops loops)
        {
            loops.Admission?.Retire();
            return Task.Run(() => DrainCapturedConnectionLoopsAsync(loops));
        }

        private async Task DrainCapturedConnectionLoopsAsync(ConnectionLoops loops)
        {
            // Keep the captured owners across awaits; a replacement may change fields.
            await CancelOwnedAsync(loops.Cts).ConfigureAwait(false);
            loops.Publisher?.Dispose();
            loops.ToolSettings?.Dispose();
            if (loops.ToolSettings != null)
            {
                await loops.ToolSettings.Completion.ConfigureAwait(false);
                Interlocked.CompareExchange(ref _toolSettingsRegistration, null, loops.ToolSettings);
            }
            if (loops.Publisher != null)
                Interlocked.CompareExchange(ref _statePublisher, null, loops.Publisher);
            if (loops.Commands != null)
            {
                await loops.Commands.DrainAsync().ConfigureAwait(false);
                Interlocked.CompareExchange(ref _commandWork, null, loops.Commands);
            }
            if (loops.Registration != null)
            {
                try
                {
                    await loops.Registration.ConfigureAwait(false);
                }
                catch { }
                _ = Interlocked.CompareExchange(ref _registrationTask, null, loops.Registration);
            }
            if (loops.Receive != null)
            {
                try
                {
                    await loops.Receive.ConfigureAwait(false);
                }
                catch { }
                _ = Interlocked.CompareExchange(ref _receiveTask, null, loops.Receive);
            }
            if (loops.KeepAlive != null)
            {
                try
                {
                    await loops.KeepAlive.ConfigureAwait(false);
                }
                catch { }
                _ = Interlocked.CompareExchange(ref _keepAliveTask, null, loops.KeepAlive);
            }
            if (loops.Admission != null)
            {
                loops.Admission.Dispose();
                Interlocked.CompareExchange(ref _commandAdmission, null, loops.Admission);
            }
            if (loops.Cts != null)
            {
                loops.Cts.Dispose();
                Interlocked.CompareExchange(ref _connectionCts, null, loops.Cts);
            }
        }

        private void StartBackgroundLoops(CancellationToken token)
        {
            lock (_ownershipLock)
            {
                if (token.IsCancellationRequested || _connectionCts == null)
                    return;
                try
                {
                    if (_connectionCts.Token != token)
                        return;
                }
                catch (ObjectDisposedException)
                {
                    return;
                }
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
            ClientWebSocket socket;
            lock (_ownershipLock)
            {
                if (!IsCurrentConnectionToken(token))
                    return;
                socket = _socket;
            }
            if (socket == null)
                return;
            using var reader = new WebSocketMessageReader();
            while (!token.IsCancellationRequested)
            {
                try
                {
                    string message = await reader.ReadAsync(socket, token).ConfigureAwait(false);
                    if (message == null)
                    {
                        await HandleSocketClosureAsync(reader.CloseReason, token).ConfigureAwait(false);
                        break;
                    }
                    if (message.Length == 0)
                        continue;
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

        private async Task HandleMessageAsync(string message, CancellationToken token)
        {
            JObject payload;
            try
            {
                payload = TransportJson.ParseObject(message);
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
                case "tool_settings_result":
                    lock (_ownershipLock)
                    {
                        if (!IsCurrentConnectionToken(token) || !_toolSettingsNegotiated)
                            break;
                        if (_toolSettingsRegistration != null)
                            _toolSettingsRegistration.HandleReceipt(payload);
                        else
                            _toolSettingsState.AcceptReceipt(_connectionCts, payload);
                    }
                    break;
                case "execute":
                    await HandleExecuteAsync(payload, token).ConfigureAwait(false);
                    break;
                case "cancel":
                    ConnectionCommandWork work;
                    lock (_ownershipLock)
                    {
                        if (!IsCurrentConnectionToken(token) || !_commandCancellationNegotiated)
                            break;
                        work = _commandWork;
                    }
                    if (_resumeNegotiated && payload["resume"] is JObject cancelResume)
                        await TransportCommandDispatcher
                            .RunOnMainThreadAsync(
                                () =>
                                    CommandResumeState.Journal.Admit(
                                        cancelResume,
                                        payload.Value<string>("name"),
                                        payload["params"] as JObject ?? new JObject()
                                    ),
                                CancellationToken.None
                            )
                            .ConfigureAwait(false);
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
                if (!IsCurrentConnectionToken(token))
                    return;
                _arbitrationOffered = HasCapability(payload, NativeCommandArbitrator.Capability);
                _resumeOffered = HasCapability(payload, CommandResumeJournal.Capability);
                if (!_resumeOffered)
                    _resumeNegotiated = false;
                if (!_arbitrationOffered)
                    _arbitrationNegotiated = false;
                _phaseTimingOffered = HasCapability(payload, CommandPhaseTiming.Capability);
                if (!_phaseTimingOffered)
                    _phaseTimingNegotiated = false;
                _commandProgressOffered = HasCommandProgressCapability(payload);
                if (!_commandProgressOffered)
                    _commandProgressNegotiated = false;
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
                    if (!IsCurrentConnectionToken(token))
                        return Task.CompletedTask;
                    _sessionId = newSessionId;
                    _toolSettingsNegotiated = HasCapability(payload, ToolSettingsState.Capability);
                    _toolSettingsRegistration?.Dispose();
                    // A linked work token propagates cancellation; the connection token remains the ownership identity.
                    _toolSettingsRegistration = new ToolSettingsRegistration(
                        _toolSettingsState,
                        _connectionCts,
                        newSessionId,
                        _toolSettingsNegotiated,
                        workToken => CaptureToolSettingsForOwnerOnMainThreadAsync(token, workToken),
                        (frame, workToken) => SendStateAsync(frame, token, workToken),
                        token,
                        TimeSpan.FromSeconds(5)
                    );
                    _largeResultNegotiated = HasCapability(payload, LargeResultWriter.Capability);
                    _compressionNegotiated = _largeResultNegotiated && HasCapability(payload, LargeResultWriter.CompressionCapability);
                    _commandCancellationNegotiated = HasCapability(payload, ConnectionCommandWork.CancellationCapability);
                    _arbitrationNegotiated = _arbitrationOffered && HasCapability(payload, NativeCommandArbitrator.Capability);
                    _resumeNegotiated = _resumeOffered && HasCapability(payload, CommandResumeJournal.Capability);
                    _phaseTimingNegotiated = _phaseTimingOffered && HasCapability(payload, CommandPhaseTiming.Capability);
                    _commandProgressNegotiated = _commandProgressOffered && HasCommandProgressCapability(payload);
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
                    if (!IsCurrentConnectionToken(token))
                        return Task.CompletedTask;
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
                    var publisher = await TransportCommandDispatcher
                        .RunOnMainThreadAsync(() => EditorStatePublisher.Start((state, sendToken) => SendStateAsync(state, token, sendToken), token), token)
                        .ConfigureAwait(false);
                    lock (_ownershipLock)
                    {
                        if (IsCurrentConnectionToken(token))
                        {
                            _statePublisher?.Dispose();
                            _statePublisher = publisher;
                        }
                        else
                            publisher.Dispose();
                    }
                }
            }
            catch (OperationCanceledException) { }
            catch (Exception ex)
            {
                await HandleSocketClosureAsync(ex.Message, token).ConfigureAwait(false);
            }
        }

        private void PersistSessionIdIfCurrent(string sessionId, CancellationToken token)
        {
            if (_ownedConnection != null)
                return;
            try
            {
                lock (_ownershipLock)
                {
                    if (!IsCurrentConnectionToken(token) || _sessionId != sessionId)
                        return;
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
            if (_toolDiscoveryService == null)
                return;
            ToolSettingsRegistration registration;
            lock (_ownershipLock)
            {
                if (!IsCurrentConnectionToken(token))
                    throw new OperationCanceledException(token);
                registration = _toolSettingsRegistration;
            }
            if (registration != null)
                await registration.RequestAsync().ConfigureAwait(false);
        }

        private Task<ToolSettingsSnapshot> CaptureToolSettingsOnMainThreadAsync(CancellationToken token) =>
            CaptureToolSettingsForOwnerOnMainThreadAsync(token, token);

        private Task<ToolSettingsSnapshot> CaptureToolSettingsForOwnerOnMainThreadAsync(CancellationToken ownerToken, CancellationToken workToken) =>
            TransportCommandDispatcher.RunOnMainThreadAsync(
                () =>
                {
                    workToken.ThrowIfCancellationRequested();
                    lock (_ownershipLock)
                        if (!IsCurrentConnectionToken(ownerToken))
                            throw new OperationCanceledException(ownerToken);
                    try
                    {
                        var enabled = new List<ToolMetadata>();
                        var enablement = new JObject();
                        foreach (var tool in _toolDiscoveryService.DiscoverAllTools())
                        {
                            bool active = _toolDiscoveryService.IsToolEnabled(tool.Name);
                            enablement[tool.Name] = active;
                            if (active)
                                enabled.Add(tool);
                        }
                        var complete = BuildToolsRegistration(enabled);
                        lock (_ownershipLock)
                        {
                            workToken.ThrowIfCancellationRequested();
                            if (!IsCurrentConnectionToken(ownerToken))
                                throw new OperationCanceledException(ownerToken);
                            return _toolSettingsState.Capture((JArray)complete["tools"], enablement);
                        }
                    }
                    catch (Exception error) when (!(error is OperationCanceledException))
                    {
                        lock (_ownershipLock)
                            if (!workToken.IsCancellationRequested && IsCurrentConnectionToken(ownerToken))
                                _toolSettingsState.PreparationFailed(ToolSettingsState.CaptureFailureMessage(error.GetBaseException().GetType().Name));
                        throw;
                    }
                },
                workToken
            );

        private static JObject BuildToolsRegistration(IReadOnlyList<ToolMetadata> tools)
        {
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
                    ["group"] = string.IsNullOrWhiteSpace(tool.Group) ? "core" : tool.Group,
                };

                var paramsArray = new JArray();
                if (tool.Parameters != null)
                {
                    foreach (var p in tool.Parameters)
                    {
                        paramsArray.Add(
                            new JObject
                            {
                                ["name"] = p.Name,
                                ["description"] = p.Description,
                                ["type"] = p.Type,
                                ["required"] = p.Required,
                                ["default_value"] = p.DefaultValue,
                            }
                        );
                    }
                }
                toolObj["parameters"] = paramsArray;
                ToolSchemaBuilder.AppendSchema(toolObj, tool);
                toolsArray.Add(toolObj);
            }

            return new JObject { ["type"] = "register_tools", ["tools"] = toolsArray };
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
                    if (_connectionCts == null)
                        return;
                    token = _connectionCts.Token;
                }
                await SendRegisterToolsAsync(token).ConfigureAwait(false);
                McpLog.Info("[WebSocket] Tool registration attempt settled; consult its acknowledgement status.", false);
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
            if (payload["resume"] != null && !(payload["resume"] is JObject))
                throw new InvalidOperationException("Invalid command continuation envelope");
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
            CommandAdmission admission;
            string sessionId;
            bool progressNegotiated;
            bool phaseTimingNegotiated;
            bool arbitrationNegotiated;
            bool resumeNegotiated;
            lock (_ownershipLock)
            {
                if (!IsCurrentConnectionToken(token))
                    return;
                work = _commandWork;
                admission = _commandAdmission;
                sessionId = _sessionId;
                progressNegotiated = _commandProgressNegotiated;
                phaseTimingNegotiated = _phaseTimingNegotiated;
                arbitrationNegotiated = _arbitrationNegotiated;
                resumeNegotiated = _resumeNegotiated;
            }
            if (work == null)
                return;
            var deadline = CancellationTokenSource.CreateLinkedTokenSource(token);
            deadline.CancelAfter(TimeSpan.FromSeconds(Math.Max(1, Math.Min(int.MaxValue / 1000, timeoutSeconds))));
            var command = new Command
            {
                type = commandName,
                @params = parameters,
                arbitration = arbitrationNegotiated ? payload["arbitration"] as JObject : null,
                server_info = payload["server_info"],
                resume = payload["resume"] as JObject,
            };
            if (command.resume != null)
            {
                if (!resumeNegotiated)
                {
                    deadline.Dispose();
                    await SendCommandResultAsync(commandId, new { status = "error", error = "Command continuation was not negotiated" }, token)
                        .ConfigureAwait(false);
                    return;
                }
                var admitted = await TransportCommandDispatcher
                    .RunOnMainThreadAsync(() => CommandResumeState.Journal.Admit(command.resume, command.type, command.@params), CancellationToken.None)
                    .ConfigureAwait(false);
                if (admitted.Ticket == null)
                {
                    deadline.Dispose();
                    await SendCommandResultAsync(commandId, admitted.Response(command.arbitration).Payload, token).ConfigureAwait(false);
                    return;
                }
                command.ResumeTicket = admitted.Ticket;
                long remaining = command.resume.Value<long>("deadline_unix_ms") - DateTimeOffset.UtcNow.ToUnixTimeMilliseconds();
                deadline.CancelAfter(TimeSpan.FromMilliseconds(Math.Max(1, Math.Min(int.MaxValue, remaining))));
            }
            var request = new QueuedCommand
            {
                Id = commandId,
                Command = command,
                TimeoutSeconds = timeoutSeconds,
                Deadline = deadline,
                Timing = CommandPhaseTiming.CreateForCommand(McpLogRecord.IsEnabled, phaseTimingNegotiated, payload),
                Owner = work,
                Admission = admission,
                SessionId = sessionId,
                Progress = progressNegotiated && RequestsCommandProgress(payload, commandId) ? new CommandProgressBuffer() : null,
            };
            command.ResumeTicket?.Owner.BindCancellation(
                command.ResumeTicket,
                () =>
                {
                    work.TryCancel(commandId);
                }
            );
            // Only negotiated peers can relax the legacy FIFO predecessor. Native admission
            // still rechecks the actual handler identity and all current owner barriers.
            bool bypass = AllowsArbitrationBypass(arbitrationNegotiated, command);
            string rejection = work.TryStartWithAsyncCancellation(
                commandId,
                previous => ExecuteQueuedCommandAsync(previous, request, token),
                request.CancelAsync,
                bypass,
                request.Dispose
            );
            if (rejection != null)
            {
                command.ResumeTicket?.Owner.Release(command.ResumeTicket);
                request.Progress?.Dispose();
                request.Dispose();
                request.Timing?.ExecutionDone();
                try
                {
                    var response = TransportCommandResponse.FromObject(new { status = "error", error = rejection });
                    if (rejection == "Command queue is full")
                        response.WithAdmission(command.arbitration, true, new NativeAdmissionBlocker("operation_busy", true));
                    await SendTimedCommandResultAsync(commandId, response.Payload, token, request.Timing).ConfigureAwait(false);
                    request.Timing?.DeliveryDone(CommandPhaseTiming.Delivery.Sent);
                }
                catch (OperationCanceledException)
                {
                    request.Timing?.DeliveryDone(CommandPhaseTiming.Delivery.Canceled);
                    throw;
                }
                catch
                {
                    request.Timing?.DeliveryDone(CommandPhaseTiming.Delivery.Failed);
                    throw;
                }
            }
        }

        internal static bool AllowsArbitrationBypass(bool negotiated, Command command)
        {
            if (!negotiated || command == null || !TransportCommandResponse.IsBoundRequest(command.arbitration))
                return false;
            // ping is implemented by the transport dispatcher itself, even if a custom
            // registry handler publishes that name. Its identity is therefore audited here.
            bool ping = string.Equals(command.type, "ping", StringComparison.OrdinalIgnoreCase);
            return ToolOperationPolicy
                    .Resolve(ping ? "ping" : command.type, command.@params ?? new JObject(), ping || CommandRegistry.IsTrustedBuiltInHandler(command.type))
                    .Mode != ToolOperationMode.Exclusive;
        }

        private sealed class QueuedCommand : IDisposable
        {
            public string Id;
            public Command Command;
            public int TimeoutSeconds;
            public CancellationTokenSource Deadline;
            public CommandPhaseTiming Timing;
            public ConnectionCommandWork Owner;
            public CommandAdmission Admission;
            public string SessionId;
            public CommandProgressBuffer Progress;
            public Task Response;
            private readonly CommandAdmission cancellation = new CommandAdmission();
            public CancellationToken RequestCancellation => cancellation.Token;
            public bool CancelRequested => RequestCancellation.IsCancellationRequested;

            public Task CancelAsync()
            {
                cancellation.Retire();
                Progress?.Close();
                return Task.Run(() => Deadline.Cancel());
            }

            public void Dispose()
            {
                cancellation.Dispose();
                Deadline.Dispose();
            }
        }

        private async Task ExecuteQueuedCommandAsync(Task previous, QueuedCommand request, CancellationToken token)
        {
            TransportCommandOperation operation = null;
            CancellationTokenSource progressStop = null;
            Task progressTask = Task.CompletedTask;
            request.Timing?.Start(CommandPhaseTiming.Phase.Queue);
            try
            {
                object response;
                try
                {
                    await ConnectionCommandWork.WaitAsync(previous, request.Deadline.Token).ConfigureAwait(false);
                    request.Deadline.Token.ThrowIfCancellationRequested();
                    if (request.CancelRequested)
                        throw new OperationCanceledException();
                    if (request.Progress != null)
                    {
                        progressStop = CancellationTokenSource.CreateLinkedTokenSource(request.Deadline.Token);
                        progressTask = PumpCommandProgressAsync(request, token, progressStop.Token);
                    }
                    operation = TransportCommandDispatcher.ExecuteTimedCommandAsync(
                        request.Command,
                        request.Deadline.Token,
                        null,
                        request.Timing,
                        request.Progress,
                        request.Admission?.Token ?? CancellationToken.None,
                        request.RequestCancellation
                    );
                    request.Response = operation.Response;
                    response = (await operation.Response.ConfigureAwait(false)).Payload;
                }
                catch (OperationCanceledException)
                {
                    token.ThrowIfCancellationRequested();
                    response = new
                    {
                        status = "error",
                        error = request.CancelRequested
                            ? $"Command '{request.Command.type}' canceled"
                            : $"Command '{request.Command.type}' timed out after {request.TimeoutSeconds} seconds",
                    };
                }
                catch (Exception ex)
                {
                    response = new { status = "error", error = ex.Message };
                }
                finally
                {
                    // Close/join before any final-result serialization. A retained actual
                    // child keeps its existing completion owner and only a closed buffer.
                    request.Progress?.Close();
                    progressStop?.Cancel();
                    await progressTask.ConfigureAwait(false);
                    progressStop?.Dispose();
                }
                await SendTimedCommandResultAsync(request.Id, response, token, request.Timing).ConfigureAwait(false);
                request.Timing?.DeliveryDone(CommandPhaseTiming.Delivery.Sent);
            }
            catch (OperationCanceledException)
            {
                request.Timing?.DeliveryDone(CommandPhaseTiming.Delivery.Canceled);
            }
            catch (Exception ex)
            {
                request.Timing?.DeliveryDone(CommandPhaseTiming.Delivery.Failed);
                await HandleSocketClosureAsync(ex.Message, token).ConfigureAwait(false);
            }
            finally
            {
                request.Progress?.Dispose();
                try
                {
                    // Response delivery may be canceled; mutation settlement must remain truthful.
                    await previous.ConfigureAwait(false);
                    if (operation != null)
                        await operation.Completion.ConfigureAwait(false);
                }
                catch (OperationCanceledException) { }
                request.Timing?.ExecutionDone();
                request.Command.ResumeTicket?.Owner.Release(request.Command.ResumeTicket);
            }
        }

        private static bool HasCommandProgressCapability(JObject payload)
        {
            if (payload["capabilities"] is not JArray capabilities || capabilities.Count > 16)
                return false;
            bool supported = false;
            foreach (var value in capabilities)
            {
                if (value.Type != JTokenType.String)
                    return false;
                string name = value.Value<string>();
                if (name == null || name.Length > 64 || Encoding.UTF8.GetByteCount(name) > 64)
                    return false;
                supported |= name == CommandProgressBuffer.Capability;
            }
            return supported;
        }

        private static bool RequestsCommandProgress(JObject payload, string id)
        {
            if (
                !Guid.TryParseExact(id, "D", out var parsed)
                || parsed.ToString("D") != id
                || payload["progress"] is not JObject progress
                || progress.Count != 1
                || progress["version"] is not JValue version
                || version.Type != JTokenType.Integer
            )
                return false;
            // Do not coerce booleans, floats, strings or overflowing BigIntegers.
            return version.Value is long number && number == 1 || version.Value is int small && small == 1;
        }

        // Caller holds _ownershipLock. The response waiter, not actual mutation
        // completion, owns optional reporting.
        private bool IsCurrentProgressOwner(QueuedCommand request, CancellationToken ownerToken) =>
            IsCurrentConnectionToken(ownerToken)
            && _commandProgressNegotiated
            && ReferenceEquals(_commandWork, request.Owner)
            && string.Equals(_sessionId, request.SessionId, StringComparison.Ordinal)
            && !request.Deadline.IsCancellationRequested
            && !request.Progress.IsClosed
            && (request.Response == null || !request.Response.IsCompleted);

        private async Task PumpCommandProgressAsync(QueuedCommand request, CancellationToken ownerToken, CancellationToken stopToken)
        {
            long nextSend = 0;
            try
            {
                while (!stopToken.IsCancellationRequested)
                {
                    await request.Progress.WaitAsync(stopToken).ConfigureAwait(false);
                    long now = System.Diagnostics.Stopwatch.GetTimestamp();
                    if (now < nextSend)
                        await Task.Delay(TimeSpan.FromSeconds((nextSend - now) / (double)System.Diagnostics.Stopwatch.Frequency), stopToken)
                            .ConfigureAwait(false);
                    stopToken.ThrowIfCancellationRequested();
                    if (!request.Progress.TryTake(out var update))
                        break;
                    await TrySendCommandProgressAsync(request, update, ownerToken, stopToken).ConfigureAwait(false);
                    nextSend = System.Diagnostics.Stopwatch.GetTimestamp() + System.Diagnostics.Stopwatch.Frequency / 10;
                }
            }
            catch (OperationCanceledException) { /* The foreground/deadline owns this optional sender. */ }
            catch (Exception) { /* Optional reporting never retries or changes a handler result. */ }
            finally
            {
                request.Progress.Close();
            }
        }

        private async Task<bool> TrySendCommandProgressAsync(
            QueuedCommand request,
            CommandProgressUpdate update,
            CancellationToken ownerToken,
            CancellationToken stopToken
        )
        {
            stopToken.ThrowIfCancellationRequested();
            ClientWebSocket socket;
            lock (_ownershipLock)
            {
                if (!IsCurrentProgressOwner(request, ownerToken))
                    return false;
                socket = _socket;
            }
            if (socket == null)
                return false;
            var payload = new JObject
            {
                ["type"] = "command_progress",
                ["id"] = request.Id,
                ["completed"] = update.Completed,
                ["total"] = update.Total,
                ["stage"] = "batch_items",
            };
            byte[] bytes = Encoding.UTF8.GetBytes(payload.ToString(Formatting.None));
            if (bytes.Length > CommandProgressBuffer.MaxFrameBytes)
                return false;
            // Busy optional capacity is dropped, never queued behind another writer.
            if (!await _sendLock.WaitAsync(0, stopToken).ConfigureAwait(false))
                return false;
            try
            {
                lock (_ownershipLock)
                    if (!IsCurrentProgressOwner(request, ownerToken) || !ReferenceEquals(_socket, socket))
                        return false;
                stopToken.ThrowIfCancellationRequested();
                if (socket.State != WebSocketState.Open)
                    return false;
                // Once SendAsync starts, normal foreground closure joins it instead of
                // interrupting a frame. The original admitted deadline still bounds it.
                using var write = CancellationTokenSource.CreateLinkedTokenSource(request.Deadline.Token);
                write.CancelAfter(TimeSpan.FromMilliseconds(200));
                write.Token.ThrowIfCancellationRequested();
                try
                {
                    await socket.SendAsync(new ArraySegment<byte>(bytes), WebSocketMessageType.Text, true, write.Token).ConfigureAwait(false);
                }
                catch
                {
                    // A started optional write may have emitted frame bytes. Close only
                    // that captured socket; never append a final result to partial framing.
                    socket.Abort();
                    throw;
                }
                return true;
            }
            finally
            {
                _sendLock.Release();
            }
        }

        private Task SendCommandResultAsync(string id, object result, CancellationToken token) => SendTimedCommandResultAsync(id, result, token, null);

        private Task SendTimedCommandResultAsync(string id, object result, CancellationToken token, CommandPhaseTiming timing) =>
            SendCommandResultCoreAsync(id, result, token, timing, SendFrameAsync);

        private Task SendCommandResultCoreAsync(
            string id,
            object result,
            CancellationToken token,
            CommandPhaseTiming timing,
            Func<ArraySegment<byte>, WebSocketMessageType, CancellationToken, Task> sendFrame
        )
        {
            token.ThrowIfCancellationRequested();
            timing?.Start(CommandPhaseTiming.Phase.Serialization);
            LargeResultWriter.PreparedJson responseJson;
            try
            {
                responseJson = new LargeResultWriter.PreparedJson(
                    BoundedResponseJson.Serialize(
                        new
                        {
                            type = "command_result",
                            id,
                            result,
                        }
                    )
                );
            }
            catch (ResponseLimitException)
            {
                responseJson = new LargeResultWriter.PreparedJson(
                    BoundedResponseJson.Serialize(
                        new
                        {
                            type = "command_result",
                            id,
                            result = new { status = "error", error = "Command result exceeds the transport size limit" },
                        }
                    )
                );
            }
            bool negotiated;
            bool compressionNegotiated;
            lock (_ownershipLock)
            {
                token.ThrowIfCancellationRequested();
                if (!IsCurrentConnectionToken(token))
                    throw new OperationCanceledException(token);
                negotiated = _largeResultNegotiated;
                compressionNegotiated = _compressionNegotiated;
            }
            // Legacy integrations can use opaque IDs; they retain text responses.
            if (!Guid.TryParseExact(id, "D", out var parsedId) || parsedId.ToString("D") != id)
                negotiated = false;
            bool allowCompression =
                _ownedConnection?.AllowCompression
                ?? string.Equals(Environment.GetEnvironmentVariable("UNITY_MCP_RESULT_COMPRESSION"), "gzip", StringComparison.OrdinalIgnoreCase);
            timing?.Stop(CommandPhaseTiming.Phase.Serialization);
            timing?.Start(CommandPhaseTiming.Phase.Send);
            var send = LargeResultWriter.SendPreparedJsonAsync(id, responseJson, negotiated, sendFrame, token, compressionNegotiated, allowCompression);
            return timing == null ? send : CompleteTimedSendAsync(send, timing);
        }

        private static async Task CompleteTimedSendAsync(Task send, CommandPhaseTiming timing)
        {
            await send.ConfigureAwait(false);
            timing.Stop(CommandPhaseTiming.Phase.Send);
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
                ["arbitration_generation"] = NativeCommandArbitrator.Shared.Generation,
                ["resume_generation"] = CommandResumeState.SessionId,
                ["project_hash"] = _projectHash,
                ["unity_version"] = _unityVersion,
                ["project_path"] = _projectPath,
                ["capabilities"] = new JArray(
                    EditorStatePublisher.Capability,
                    LargeResultWriter.Capability,
                    ConnectionCommandWork.CancellationCapability,
                    ToolSettingsState.Capability,
                    LargeResultWriter.CompressionCapability,
                    CommandProgressBuffer.Capability,
                    CommandPhaseTiming.Capability,
                    NativeCommandArbitrator.Capability,
                    CommandResumeJournal.Capability
                ),
            };

            await SendJsonAsync(registerPayload, token).ConfigureAwait(false);
        }

        private Task SendPongAsync(CancellationToken token)
        {
            var payload = new JObject
            {
                ["type"] = "pong",
                ["session_id"] = _sessionId, // Include session ID for server-side tracking
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
            if (payload["capabilities"] is not JArray capabilities)
                return false;
            foreach (var value in capabilities)
                if (value.Type == JTokenType.String && value.Value<string>() == capability)
                    return true;
            return false;
        }

        private Task SendStateAsync(EditorStateFrame payload, CancellationToken ownerToken, CancellationToken sendToken) =>
            SendOwnedFrameAsync(new ArraySegment<byte>(Encoding.UTF8.GetBytes(payload.ToJson())), WebSocketMessageType.Text, ownerToken, sendToken);

        private Task SendStateAsync(JObject payload, CancellationToken ownerToken, CancellationToken sendToken) =>
            SendOwnedFrameAsync(
                new ArraySegment<byte>(Encoding.UTF8.GetBytes(payload.ToString(Formatting.None))),
                WebSocketMessageType.Text,
                ownerToken,
                sendToken
            );

        private Task SendFrameAsync(ArraySegment<byte> buffer, WebSocketMessageType type, CancellationToken token) =>
            SendOwnedFrameAsync(buffer, type, token, token);

        private async Task SendOwnedFrameAsync(ArraySegment<byte> buffer, WebSocketMessageType type, CancellationToken ownerToken, CancellationToken token)
        {
            ClientWebSocket socket;
            lock (_ownershipLock)
            {
                if (!IsCurrentConnectionToken(ownerToken))
                    throw new OperationCanceledException(ownerToken);
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

        private Task HandleSocketClosureAsync(string reason, CancellationToken connectionToken)
        {
            CancellationTokenSource lifecycleCts;
            CancellationToken lifecycleToken;
            lock (_ownershipLock)
            {
                var connectionCts = _connectionCts;
                lifecycleCts = _lifecycleCts;
                if (connectionToken.IsCancellationRequested || connectionCts == null || lifecycleCts == null || lifecycleCts.IsCancellationRequested)
                    return Task.CompletedTask;
                try
                {
                    if (connectionCts.Token != connectionToken)
                        return Task.CompletedTask;
                    lifecycleToken = lifecycleCts.Token;
                }
                catch (ObjectDisposedException)
                {
                    return Task.CompletedTask;
                }
                if (Interlocked.CompareExchange(ref _isReconnectingFlag, 1, 0) != 0)
                    return Task.CompletedTask;
                _commandAdmission?.Retire();
                _isConnected = false;
                _state = TransportState.Disconnected(TransportDisplayName, reason ?? "Connection closed");
            }
            // Capture stack trace for debugging disconnection triggers
            var stackTrace = new System.Diagnostics.StackTrace(true);
            McpLog.Debug($"[WebSocket] HandleSocketClosureAsync called. Reason: {reason}\nStack trace:\n{stackTrace}");

            McpLog.Warn($"[WebSocket] Connection closed: {reason}");

            if (lifecycleToken.IsCancellationRequested || !ReferenceEquals(_lifecycleCts, lifecycleCts))
            {
                lock (_ownershipLock)
                    if (ReferenceEquals(_lifecycleCts, lifecycleCts))
                        Interlocked.Exchange(ref _isReconnectingFlag, 0);
                return Task.CompletedTask;
            }

            // Never await the receive loop from its own closure callback. The reconnect
            // worker observes an independently retained cancellation/drain operation.
            _ = Task.Run(() => AttemptReconnectAsync(lifecycleToken, lifecycleCts), CancellationToken.None);
            return Task.CompletedTask;
        }

        private async Task AttemptReconnectAsync(CancellationToken token, CancellationTokenSource lifecycleCts)
        {
            try
            {
                if (token.IsCancellationRequested)
                    return;
                ConnectionLoops loops;
                lock (_ownershipLock)
                {
                    if (!ReferenceEquals(_lifecycleCts, lifecycleCts) || token.IsCancellationRequested)
                        return;
                    loops = CaptureConnectionLoops();
                }
                Task drain = StopCapturedConnectionLoopsAsync(loops);
                if (!await ObserveControlAsync(drain, lifecycleCts, token).ConfigureAwait(false))
                    // Keep one background waiter; status and manual control remain responsive.
                    // Reconnect can resume only after the actual old execution has finished.
                    await ConnectionCommandWork.WaitAsync(drain, token).ConfigureAwait(false);

                foreach (TimeSpan delay in ReconnectSchedule)
                {
                    if (token.IsCancellationRequested)
                    {
                        return;
                    }

                    if (delay > TimeSpan.Zero)
                    {
                        try
                        {
                            await Task.Delay(delay, token).ConfigureAwait(false);
                        }
                        catch (OperationCanceledException)
                        {
                            return;
                        }
                    }

                    if (await EstablishConnectionAsync(token).ConfigureAwait(false))
                    {
                        lock (_ownershipLock)
                        {
                            if (!ReferenceEquals(_lifecycleCts, lifecycleCts) || token.IsCancellationRequested)
                                return;
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
                    if (!ReferenceEquals(_lifecycleCts, lifecycleCts) || token.IsCancellationRequested)
                        return;
                    _state = _state.WithError($"Server unreachable – retrying every {ReconnectTailInterval.TotalSeconds} s");
                }
                while (!token.IsCancellationRequested)
                {
                    try
                    {
                        await Task.Delay(ReconnectTailInterval, token).ConfigureAwait(false);
                    }
                    catch (OperationCanceledException)
                    {
                        return;
                    }

                    if (await EstablishConnectionAsync(token).ConfigureAwait(false))
                    {
                        lock (_ownershipLock)
                        {
                            if (!ReferenceEquals(_lifecycleCts, lifecycleCts) || token.IsCancellationRequested)
                                return;
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
                Path = httpUri.AbsolutePath.TrimEnd('/') + "/hub/plugin",
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
