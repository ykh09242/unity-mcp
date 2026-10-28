using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Net;
using System.Net.Sockets;
using System.Threading;
using System.Threading.Tasks;
using MCPForUnity.Editor.Constants;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Editor.Models;
using MCPForUnity.Editor.Services;
using MCPForUnity.Editor.Services.Transport;
using MCPForUnity.Editor.Tools;
using MCPForUnity.Editor.Tools.Prefabs;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEngine;

namespace MCPForUnity.Editor.Services.Transport.Transports
{
    class QueuedCommand
    {
        public string CommandJson;
        public TaskCompletionSource<string> Tcs;
        public bool IsExecuting;
        public long EnqueuedAtMs;
        public int TimeoutMs;
        public CancellationToken OwnerCancellation;
        public CancellationToken AdmissionCancellation;
        public CancellationToken ConnectionCancellation;
        public string RequestNonce;

        // Active connections awaiting this exact identified attempt's result; no progress subscription or lease ownership.
        public int IdentifiedResponseWaiters;
        public StdioFrameBudget.Lease InputReservation;
        public CommandPhaseTiming Timing;
        public CommandProgressBuffer Progress;
        public string ProgressId;
        public StdioAuthenticatedSession Session;
        public long DeadlineAtMs;

        /// <summary>
        /// Connection that queued this command. Payload equality alone does not prove retry intent.
        /// </summary>
        public object Owner;
    }

    [InitializeOnLoad]
    public static class StdioBridgeHost
    {
        private static TcpListener listener;
        private static volatile bool isRunning = false;
        private static readonly object lockObj = new();
        private static readonly object startStopLock = new();
        private static readonly object clientsLock = new();
        private static readonly HashSet<TcpClient> activeClients = new();
        private static readonly HashSet<TcpClient> authenticatingClients = new();
        private const int MaxAuthenticatingClients = 16;
        internal const int MaxAuthenticatedClients = 64;
        private static readonly StdioFrameBudget inputBudget = new();
        private static long lifecycleEpoch;
        private static StdioBridgeAuthentication authentication;
        private static bool ownedEndpoint;
        private static CancellationTokenSource cts;

        // Observation only: never register callbacks or pass this token to a handler.
        private static CancellationTokenSource commandAdmission;
        private static Task listenerTask;
        private static int processingCommands = 0;
        private static bool initScheduled = false;
        private static bool ensureUpdateHooked = false;
        private static bool isStarting = false;
        private static double nextStartAt = 0.0f;
        private static double nextHeartbeatAt = 0.0f;

        // EditorApplication.timeSinceStartup of the first AddressAlreadyInUse on the configured
        // port; 0 when the port binds cleanly. Drives the same-port retry window (#1173).
        private static double _portBusySince = 0.0;

        // If the port has been continuously busy longer than this, _portBusySince is treated as a
        // stale leftover from an abandoned retry and a fresh window is started (#1173).
        private const double PortBusyStaleResetSeconds = 60.0;
        private static int heartbeatSeq = 0;
        private static Dictionary<string, QueuedCommand> commandQueue = new();
        private static readonly List<string> canceledCommandIds = new();
        private static int mainThreadId;
        private static int currentUnityPort = 6400;
        private static bool isAutoConnectMode = false;
        private const ulong MaxFrameBytes = 128UL * 1024 * 1024;

        // Command/frame I/O timeout for the stdio bridge TCP hop. Previously a
        // hardcoded 30s const, which cut off long-running tool calls mid-execution
        // (the client would then reconnect and re-send, causing the bridge to
        // restart on a new port). Now defaults to 5 minutes and is overridable via
        // the UNITY_MCP_STDIO_COMMAND_TIMEOUT_MS environment variable.
        private const int DefaultFrameIOTimeoutMs = 300000;
        private static readonly int FrameIOTimeoutMs = ResolveFrameIOTimeoutMs();
        private static readonly Stopwatch _uptime = Stopwatch.StartNew();
        private static volatile int _consecutiveTimeouts = 0;
        private static bool _processCommandsHooked = false;

        private static int ResolveFrameIOTimeoutMs()
        {
            try
            {
                string raw = Environment.GetEnvironmentVariable("UNITY_MCP_STDIO_COMMAND_TIMEOUT_MS");
                if (!string.IsNullOrWhiteSpace(raw) && int.TryParse(raw.Trim(), out int ms) && ms > 0)
                {
                    return ms;
                }
            }
            catch { /* fall through to default */ }
            return DefaultFrameIOTimeoutMs;
        }

        internal static int ResolveCommandTimeoutMs(string payload, int defaultTimeoutMs) => ResolveCommandTimeoutMs(payload, defaultTimeoutMs, false, out _);

        private static int ResolveCommandTimeoutMs(string payload, int defaultTimeoutMs, bool arbitration, out string requestNonce)
        {
            requestNonce = null;
            try
            {
                JObject command = JObject.Parse(payload);
                if (arbitration && TransportCommandResponse.IsBoundRequest(command["arbitration"] as JObject))
                    requestNonce = (string)command["arbitration"]["request_nonce"];
                if (command["type"]?.Type != JTokenType.String)
                    return defaultTimeoutMs;
                var arguments = command["params"] as JObject ?? new JObject();
                if (string.Equals((string)command["type"], "blender_bridge", StringComparison.Ordinal))
                {
                    var parameters = new ToolParams(arguments);
                    int requestedSeconds = 180;
                    try
                    {
                        requestedSeconds = parameters.GetInt("timeout_seconds", 180) ?? 180;
                    }
                    catch (ArgumentException)
                    {
                        // The handler still reports invalid input; budget resolution must not drop the connection.
                    }
                    int seconds = Math.Max(5, Math.Min(86400, requestedSeconds));
                    return Math.Max(defaultTimeoutMs, seconds * 1000 + 30000);
                }
                JToken requested = arguments["timeout_seconds"];
                if (requested == null || requested.Type == JTokenType.Null)
                    requested = arguments["timeoutSeconds"];
                if (
                    requested == null
                    || requested.Type == JTokenType.Boolean
                    || !double.TryParse(
                        requested.ToString(),
                        System.Globalization.NumberStyles.Float,
                        System.Globalization.CultureInfo.InvariantCulture,
                        out double value
                    )
                    || double.IsNaN(value)
                    || double.IsInfinity(value)
                    || value <= 0
                )
                    return defaultTimeoutMs;
                return Math.Max(defaultTimeoutMs, (int)Math.Ceiling(Math.Min(86400, value) * 1000));
            }
            catch (JsonException)
            {
                return defaultTimeoutMs;
            }
        }

        private static void IoInfo(string s)
        {
            McpLog.Info(s, always: false);
        }

        private static bool IsDebugEnabled()
        {
            if (ownedEndpoint)
                return false;
            try
            {
                return EditorPrefs.GetBool(EditorPrefKeys.DebugLogs, false);
            }
            catch
            {
                return false;
            }
        }

        private static void LogBreadcrumb(string stage)
        {
            if (IsDebugEnabled())
            {
                McpLog.Info($"[{stage}]", always: false);
            }
        }

        public static bool IsRunning => isRunning;

        public static int GetCurrentPort() => currentUnityPort;

        public static bool IsAutoConnectMode() => isAutoConnectMode;

        public static void StartAutoConnect()
        {
            Stop();

            try
            {
                currentUnityPort = PortManager.GetPortWithFallback();
                Start();
                isAutoConnectMode = true;

                TelemetryHelper.RecordBridgeStartup();
            }
            catch (Exception ex)
            {
                McpLog.Error($"Auto-connect failed ({ex.GetType().Name})");
                TelemetryHelper.RecordBridgeConnection(false, ex.Message);
                throw;
            }
        }

        public static bool FolderExists(string path)
        {
            if (string.IsNullOrEmpty(path))
            {
                return false;
            }

            if (path.Equals("Assets", StringComparison.OrdinalIgnoreCase))
            {
                return true;
            }

            string fullPath = Path.Combine(Application.dataPath, path.StartsWith("Assets/") ? path[7..] : path);
            return Directory.Exists(fullPath);
        }

        static StdioBridgeHost()
        {
            try
            {
                mainThreadId = Thread.CurrentThread.ManagedThreadId;
            }
            catch
            {
                mainThreadId = 0;
            }

            if (Application.isBatchMode && string.IsNullOrWhiteSpace(Environment.GetEnvironmentVariable("UNITY_MCP_ALLOW_BATCH")))
            {
                return;
            }
            if (ShouldAutoStartBridge())
            {
                ScheduleInitRetry();
                if (!ensureUpdateHooked)
                {
                    ensureUpdateHooked = true;
                    EditorApplication.update += EnsureStartedOnEditorIdle;
                }
            }
            EditorApplication.quitting += Stop;
            EditorApplication.playModeStateChanged += _ =>
            {
                if (ShouldAutoStartBridge())
                {
                    ScheduleInitRetry();
                }
            };
        }

        private static void InitializeAfterCompilation()
        {
            initScheduled = false;

            if (IsCompiling())
            {
                ScheduleInitRetry();
                return;
            }

            if (!isRunning)
            {
                Start();
                if (!isRunning)
                {
                    ScheduleInitRetry();
                }
            }
        }

        private static void ScheduleInitRetry()
        {
            if (initScheduled)
            {
                return;
            }
            initScheduled = true;
            nextStartAt = EditorApplication.timeSinceStartup + 0.20f;
            if (!ensureUpdateHooked)
            {
                ensureUpdateHooked = true;
                EditorApplication.update += EnsureStartedOnEditorIdle;
            }
            EditorApplication.delayCall += InitializeAfterCompilation;
        }

        private static bool ShouldAutoStartBridge()
        {
            try
            {
                bool useHttpTransport = EditorConfigurationCache.Instance.UseHttpTransport;
                return !useHttpTransport;
            }
            catch
            {
                return true;
            }
        }

        private static void EnsureStartedOnEditorIdle()
        {
            if (IsCompiling())
            {
                return;
            }

            if (isRunning)
            {
                EditorApplication.update -= EnsureStartedOnEditorIdle;
                ensureUpdateHooked = false;
                return;
            }

            if (nextStartAt > 0 && EditorApplication.timeSinceStartup < nextStartAt)
            {
                return;
            }

            if (isStarting)
            {
                return;
            }

            isStarting = true;
            try
            {
                Start();
            }
            finally
            {
                isStarting = false;
            }
            if (isRunning)
            {
                EditorApplication.update -= EnsureStartedOnEditorIdle;
                ensureUpdateHooked = false;
            }
        }

        // Routed through EditorStateCache so a deferred domain reload (issue #1276) does not
        // pin the bridge off: raw EditorApplication.isCompiling stays true for as long as the
        // reload is held, and this gates bridge startup.
        /// <summary>
        /// Number of commands currently queued, read under the queue lock. Diagnostics only —
        /// callers must not reason about individual entries, since the queue mutates from both
        /// the listener tasks and the editor update loop.
        /// </summary>
        internal static int QueuedCommandCount
        {
            get
            {
                lock (lockObj)
                {
                    return commandQueue.Count;
                }
            }
        }

        internal static int AuthenticatedClientCount
        {
            get
            {
                lock (clientsLock)
                    return activeClients.Count;
            }
        }
        internal static int AuthenticatingClientCount
        {
            get
            {
                lock (clientsLock)
                    return authenticatingClients.Count;
            }
        }
        internal static long ReservedInputBytes => inputBudget.ReservedBytes;
        internal static int PendingInputCount => inputBudget.PendingCount;

        /// <summary>
        /// Identifies a raw collision only. It cannot establish that independent clients share intent.
        /// </summary>
        internal static bool IsBrokerResend(string queuedCommandJson, object queuedOwner, string incomingCommandJson, object incomingOwner)
        {
            if (queuedOwner == null || incomingOwner == null)
                return false;
            if (ReferenceEquals(queuedOwner, incomingOwner))
                return false;
            return string.Equals(queuedCommandJson, incomingCommandJson, StringComparison.Ordinal);
        }

        internal enum BrokerRequestDisposition
        {
            Distinct,
            ExistingRequest,
            AmbiguousLegacyOutcome,
        }

        internal static BrokerRequestDisposition ClassifyBrokerRequest(QueuedCommand queued, string commandText, object incomingOwner, string requestNonce)
        {
            if (!IsBrokerResend(queued.CommandJson, queued.Owner, commandText, incomingOwner))
                return BrokerRequestDisposition.Distinct;
            if (requestNonce != null && string.Equals(queued.RequestNonce, requestNonce, StringComparison.Ordinal))
                return BrokerRequestDisposition.ExistingRequest;
            if (requestNonce == null && queued.RequestNonce == null && queued.ConnectionCancellation.IsCancellationRequested)
                return BrokerRequestDisposition.AmbiguousLegacyOutcome;
            return BrokerRequestDisposition.Distinct;
        }

        private static BrokerRequestDisposition FindBrokerResendTarget(
            string commandText,
            object incomingOwner,
            string requestNonce,
            out QueuedCommand existing
        )
        {
            existing = null;
            foreach (var kvp in commandQueue)
            {
                var disposition = ClassifyBrokerRequest(kvp.Value, commandText, incomingOwner, requestNonce);
                if (disposition != BrokerRequestDisposition.Distinct)
                {
                    existing = kvp.Value;
                    return disposition;
                }
            }
            return BrokerRequestDisposition.Distinct;
        }

        private static bool IsCompiling() => EditorStateCache.GetActualIsCompiling();

        public static void Start() => StartBridge(null, null);

        /// <summary>Owned ephemeral QA endpoint; no discovery files or EditorPrefs access.</summary>
        internal static void StartOwned(int port, string token)
        {
            if (port < 0 || port > 65535 || token == null)
                throw new ArgumentException("Invalid owned endpoint");
            StartBridge(port, token);
        }

        private static void StartBridge(int? ownedPort, string ownedToken)
        {
            lock (startStopLock)
            {
                if (isRunning && listener != null)
                {
                    if (!ownedPort.HasValue && IsDebugEnabled())
                    {
                        McpLog.Info($"StdioBridgeHost already running on port {currentUnityPort}");
                    }
                    return;
                }

                Stop();
                lifecycleEpoch++;

                try
                {
                    ownedEndpoint = ownedPort.HasValue;
                    currentUnityPort = ownedPort ?? PortManager.GetPortWithFallback();

                    // Clear any stale "reloading" heartbeat from a previous domain reload.
                    // After reload, static fields reset (isRunning=false), so Stop() above
                    // is a no-op and won't delete the status file. Writing now ensures clients
                    // see reloading=false even if listener creation fails below.
                    WriteHeartbeat(false, "starting");

                    LogBreadcrumb("Start");

                    try
                    {
                        listener = CreateConfiguredListener(currentUnityPort);
                        listener.Start();
                    }
                    catch (SocketException se) when (!ownedEndpoint && se.SocketErrorCode == SocketError.AddressAlreadyInUse)
                    {
                        // The configured port is busy. The usual cause is our own previous listener
                        // whose OS socket has not been released yet after a domain reload: Stop()/
                        // Dispose runs on the main thread, but the kernel frees the bound port a few
                        // hundred ms later (longer on Windows/macOS).
                        //
                        // Do NOT silently switch to a new port on the first conflict — the Python
                        // client stays pinned to the configured port and ends up talking to the
                        // orphan, returning busy/timeout forever (#1173). Keep the configured port
                        // and fail this attempt WITHOUT blocking: the reload handler's async resume
                        // schedule and the editor-idle retry re-invoke Start() on the same port within
                        // ~1s, by which point the OS has released it. Only after the port stays busy
                        // past the fallback window do we treat it as a foreign occupant and switch.
                        double now = EditorApplication.timeSinceStartup;
                        // Start a fresh window on the first conflict, or if a stale timestamp
                        // survived a long idle gap (a real reload + retry resolves in seconds).
                        if (_portBusySince <= 0.0 || (now - _portBusySince) > PortBusyStaleResetSeconds)
                            _portBusySince = now;

                        if (!PortManager.ShouldAbandonBusyPort(now - _portBusySince))
                        {
                            try
                            {
                                listener?.Stop();
                            }
                            catch { }
                            try
                            {
                                listener?.Server?.Dispose();
                            }
                            catch { }
                            listener = null;
                            McpLog.Warn($"Port {currentUnityPort} not released yet after reload; retrying same port.");
                            WriteHeartbeat(true, "port_busy");
                            nextStartAt = now + 0.3; // throttle the editor-idle retry loop
                            // Arm the editor-idle retry even when Start() was called directly
                            // (e.g. StartAutoConnect), not only during reload resume — so a transient
                            // AddressAlreadyInUse can never leave the bridge permanently stopped.
                            if (!ensureUpdateHooked)
                            {
                                ensureUpdateHooked = true;
                                EditorApplication.update += EnsureStartedOnEditorIdle;
                            }
                            return;
                        }

                        int oldPort = currentUnityPort;
                        currentUnityPort = PortManager.DiscoverNewPort();
                        _portBusySince = 0.0;

                        try
                        {
                            EditorPrefs.SetInt(EditorPrefKeys.UnitySocketPort, currentUnityPort);
                        }
                        catch { }

                        McpLog.Warn(
                            $"Port {oldPort} still occupied after {PortManager.BusyPortFallbackWindowSeconds:0.#}s; falling back to port {currentUnityPort} (clients follow via the status file)."
                        );

                        listener = CreateConfiguredListener(currentUnityPort);
                        listener.Start();
                    }

                    _portBusySince = 0.0;
                    currentUnityPort = ((IPEndPoint)listener.LocalEndpoint).Port;
                    var launch = new StdioBridgeAuthentication(ownedToken, !ownedEndpoint);
                    lock (clientsLock)
                    {
                        authentication = launch;
                        commandAdmission = new CancellationTokenSource();
                        isRunning = true;
                    }
                    isAutoConnectMode = false;
                    string platform = Application.platform.ToString();
                    string serverVer = AssetPathUtility.GetPackageVersion();
                    McpLog.Info($"StdioBridgeHost started on port {currentUnityPort}. (OS={platform}, server={serverVer})");
                    cts = new CancellationTokenSource();
                    var launchListener = listener;
                    var launchToken = cts.Token;
                    listenerTask = Task.Run(() => ListenerLoopAsync(launchListener, launch, launchToken));
                    CommandRegistry.Initialize();
                    if (!_processCommandsHooked)
                    {
                        _processCommandsHooked = true;
                        EditorApplication.update += ProcessCommands;
                    }
                    try
                    {
                        EditorApplication.quitting -= Stop;
                    }
                    catch { }
                    try
                    {
                        EditorApplication.quitting += Stop;
                    }
                    catch { }
                    heartbeatSeq++;
                    WriteHeartbeat(false, "ready");
                    nextHeartbeatAt = EditorApplication.timeSinceStartup + 0.5f;
                }
                catch (Exception ex)
                {
                    if (isRunning)
                        Stop();
                    else
                    {
                        listener?.Stop();
                        listener = null;
                        authentication?.Dispose();
                        lock (clientsLock)
                            authentication = null;
                    }
                    McpLog.Error($"Failed to start TCP listener ({ex.GetType().Name})");
                    WriteHeartbeat(false, "start_failed");
                }
            }
        }

        private static TcpListener CreateConfiguredListener(int port)
        {
            var newListener = new TcpListener(IPAddress.Loopback, port);
#if UNITY_EDITOR_OSX
            // SO_REUSEADDR is intentionally NOT set. On macOS it allows multiple
            // processes (including AssetImportWorkers) to bind the same port,
            // causing connections to land on a worker that can't process commands.
            // The ExclusiveAddressUse flag prevents this; port-busy conflicts are
            // handled by the retry/fallback logic in Start() and the reload handler.
            try
            {
                newListener.Server.ExclusiveAddressUse = true;
            }
            catch { }
#endif
            try
            {
                newListener.Server.LingerState = new LingerOption(true, 0);
            }
            catch (Exception) { }
            return newListener;
        }

        public static void Stop()
        {
            Task toWait;
            CancellationTokenSource cancel;
            CancellationTokenSource retiredAdmission;
            StdioBridgeAuthentication launch;
            TcpClient[] toClose;
            long stoppedEpoch;
            bool wasOwned;
            lock (startStopLock)
            {
                if (!isRunning)
                    return;
                stoppedEpoch = ++lifecycleEpoch;
                wasOwned = ownedEndpoint;
                // Detach this launch and all its sockets atomically. Old cleanup never
                // snapshots, disposes or clears a replacement generation's state.
                lock (clientsLock)
                {
                    isRunning = false;
                    retiredAdmission = commandAdmission;
                    commandAdmission = null;
                    // Admission retirement is synchronous and callback-free. Queued work
                    // must be fenced before the asynchronous cancellation worker can run.
                    retiredAdmission?.Cancel();
                    launch = authentication;
                    authentication = null;
                    toClose = activeClients.Concat(authenticatingClients).ToArray();
                    activeClients.Clear();
                    authenticatingClients.Clear();
                }
                cancel = cts;
                cts = null;
                toWait = listenerTask;
                listenerTask = null;
                var stoppedListener = listener;
                listener = null;
                try
                {
                    stoppedListener?.Stop();
                }
                catch { }
            }
            // Cancel runs registered callbacks synchronously. The Editor's stop/reload
            // thread must not execute them, and the source stays owned until they finish.
            if (cancel != null)
                _ = Task.Run(() =>
                {
                    try
                    {
                        cancel.Cancel();
                    }
                    catch { }
                    finally
                    {
                        cancel.Dispose();
                        retiredAdmission?.Dispose();
                    }
                });
            else
                retiredAdmission?.Dispose();
            foreach (var client in toClose)
            {
                try
                {
                    client.Close();
                }
                catch { }
            }
            launch?.Dispose(); // Credential disposal is keyed to its own launch generation.
            if (toWait != null)
            {
                try
                {
                    toWait.Wait(500);
                }
                catch { }
            }
            lock (startStopLock)
            {
                if (lifecycleEpoch != stoppedEpoch)
                    return;
                // The permanently hooked command pump still drains actual old owners.
                try
                {
                    EditorApplication.quitting -= Stop;
                }
                catch { }
                if (wasOwned)
                    return;
                try
                {
                    string dir = Environment.GetEnvironmentVariable("UNITY_MCP_STATUS_DIR");
                    if (string.IsNullOrWhiteSpace(dir))
                        dir = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.UserProfile), ".unity-mcp");
                    string statusFile = Path.Combine(dir, $"unity-mcp-status-{ComputeProjectHash(Application.dataPath)}.json");
                    if (File.Exists(statusFile))
                        File.Delete(statusFile);
                }
                catch (Exception ex)
                {
                    if (IsDebugEnabled())
                        McpLog.Warn($"Failed to delete status file ({ex.GetType().Name})");
                }
            }
        }

        private static async Task ListenerLoopAsync(TcpListener launchListener, StdioBridgeAuthentication launch, CancellationToken token)
        {
            while (isRunning && !token.IsCancellationRequested)
            {
                try
                {
                    TcpClient client = await launchListener.AcceptTcpClientAsync().ConfigureAwait(false);
                    client.Client.SetSocketOption(SocketOptionLevel.Socket, SocketOptionName.KeepAlive, true);

                    // Keep the socket receive timeout at least as long as the command
                    // timeout so it never fires before a long-running tool call completes.
                    client.ReceiveTimeout = Math.Max(60000, FrameIOTimeoutMs);
                    lock (clientsLock)
                    {
                        if (
                            token.IsCancellationRequested
                            || !isRunning
                            || !ReferenceEquals(authentication, launch)
                            || authenticatingClients.Count >= MaxAuthenticatingClients
                        )
                        {
                            client.Close();
                            continue;
                        }
                        authenticatingClients.Add(client);
                    }
                    // The wrapper itself must run even if cancellation arrives now,
                    // so it always disposes/removes this accepted socket.
                    _ = Task.Run(async () =>
                    {
                        try
                        {
                            await HandleClientAsync(client, launch, token).ConfigureAwait(false);
                        }
                        catch (ObjectDisposedException) { }
                        catch (InvalidOperationException) { }
                        finally
                        {
                            lock (clientsLock)
                            {
                                authenticatingClients.Remove(client);
                                activeClients.Remove(client);
                            }
                            client.Dispose();
                        }
                    });
                }
                catch (ObjectDisposedException)
                {
                    if (!isRunning || token.IsCancellationRequested)
                    {
                        break;
                    }
                }
                catch (OperationCanceledException)
                {
                    break;
                }
                catch (Exception ex)
                {
                    if (isRunning && !token.IsCancellationRequested)
                    {
                        if (IsDebugEnabled())
                            McpLog.Error($"Listener error ({ex.GetType().Name})");
                    }
                }
            }
        }

        private static async Task HandleClientAsync(TcpClient client, StdioBridgeAuthentication launch, CancellationToken token)
        {
            using (client)
            using (NetworkStream stream = client.GetStream())
            {
                using var connectionLifetime = CancellationTokenSource.CreateLinkedTokenSource(token);
                StdioFrameWriter writer = null;
                try
                {
                    try
                    {
                        var ep = client.Client?.RemoteEndPoint?.ToString() ?? "unknown";
                        McpLog.Info($"Client connected {ep}", always: false);
                    }
                    catch { }
                    try
                    {
                        client.NoDelay = true;
                    }
                    catch { }
                    StdioAuthenticatedSession session;
                    try
                    {
                        session = await launch
                            .AuthenticateSessionAsync(
                                stream,
                                token,
                                _ =>
                                {
                                    lock (clientsLock)
                                    {
                                        if (
                                            token.IsCancellationRequested
                                            || !isRunning
                                            || !ReferenceEquals(launch, authentication)
                                            || activeClients.Count >= MaxAuthenticatedClients
                                        )
                                            return false;
                                        authenticatingClients.Remove(client);
                                        activeClients.Add(client);
                                        return true;
                                    }
                                }
                            )
                            .ConfigureAwait(false);
                        if (session == null)
                            return;
                        if (IsDebugEnabled())
                            McpLog.Info("Authenticated stdio client admitted", always: false);
                    }
                    catch (Exception ex)
                    {
                        if (IsDebugEnabled())
                            McpLog.Warn($"Stdio authentication refused ({ex.GetType().Name})");
                        return;
                    }
                    writer = new StdioFrameWriter(
                        stream,
                        () =>
                        {
                            try
                            {
                                connectionLifetime.Cancel();
                            }
                            finally
                            {
                                client.Close();
                            }
                        },
                        connectionLifetime.Token
                    );
                    while (isRunning && !connectionLifetime.IsCancellationRequested)
                    {
                        try
                        {
                            await HandleCommandAsync(client, stream, writer, launch, session, connectionLifetime).ConfigureAwait(false);
                        }
                        catch (Exception ex)
                        {
                            LogClientHandlerFailure(ex);
                            break;
                        }
                    }
                }
                finally
                {
                    connectionLifetime.Cancel();
                    if (writer != null)
                        await writer.CloseAsync().ConfigureAwait(false);
                    lock (lockObj)
                    {
                        foreach (var item in commandQueue.Values.Where(item => ReferenceEquals(item.Owner, client)))
                            item.Progress?.Close();
                        var abandoned = commandQueue
                            .Where(item => ReferenceEquals(item.Value.Owner, client) && !item.Value.IsExecuting)
                            .Select(item => item.Key)
                            .ToArray();
                        foreach (var id in abandoned)
                        {
                            commandQueue[id].Tcs.TrySetCanceled(connectionLifetime.Token);
                            commandQueue[id].Timing?.ExecutionDone();
                            ReleaseInput(commandQueue[id]);
                            commandQueue.Remove(id);
                        }
                    }
                    int remaining;
                    lock (clientsLock)
                    {
                        activeClients.Remove(client);
                        authenticatingClients.Remove(client);
                        remaining = activeClients.Count;
                    }
                    McpLog.Info($"Client handler exited (remaining clients: {remaining})", always: false);
                }
            }
        }

        private enum ProgressPrelude
        {
            None,
            Accepted,
            Refused,
        }

        private static ProgressPrelude ReadProgressPrelude(string text, out string id)
        {
            id = null;
            JObject value;
            try
            {
                value = JObject.Parse(text, new JsonLoadSettings { DuplicatePropertyNameHandling = DuplicatePropertyNameHandling.Error });
            }
            catch (JsonException)
            {
                try
                {
                    // A duplicate/malformed optional object must not fall back to
                    // command dispatch merely because strict loading refused it.
                    var malformed = JObject.Parse(text);
                    if (malformed["type"]?.Type == JTokenType.String && (string)malformed["type"] == "command_progress_begin")
                        return ProgressPrelude.Refused;
                }
                catch (JsonException) { }
                // Invalid ordinary business JSON retains the existing error path.
                // Once armed, the caller separately requires a valid business object.
                return ProgressPrelude.None;
            }
            if (value["type"]?.Type != JTokenType.String || (string)value["type"] != "command_progress_begin")
                return ProgressPrelude.None;
            if (
                text.Length > CommandProgressBuffer.MaxFrameBytes
                || System.Text.Encoding.UTF8.GetByteCount(text) > CommandProgressBuffer.MaxFrameBytes
                || value.Count != 3
                || value["version"]?.Type != JTokenType.Integer
                || value["version"].ToString(Formatting.None) != "1"
                || value["id"]?.Type != JTokenType.String
            )
                return ProgressPrelude.Refused;
            string raw = (string)value["id"];
            if (!Guid.TryParseExact(raw, "D", out var parsed) || !string.Equals(parsed.ToString("D"), raw, StringComparison.Ordinal))
                return ProgressPrelude.Refused;
            id = raw;
            return ProgressPrelude.Accepted;
        }

        private static bool IsProgressBusiness(string text)
        {
            try
            {
                var value = JObject.Parse(text);
                return value["type"]?.Type == JTokenType.String && (string)value["type"] != "command_progress_begin";
            }
            catch (JsonException)
            {
                return false;
            }
        }

        private static int RemainingCommandMs(long admittedAt, int timeoutMs)
        {
            long remaining = timeoutMs - (_uptime.ElapsedMilliseconds - admittedAt);
            return (int)Math.Max(0, Math.Min(int.MaxValue, remaining));
        }

        private static void LogReceivedCommand(string commandText)
        {
            try
            {
                if (IsDebugEnabled())
                    McpLog.Info($"Received command frame (characters: {commandText.Length})", always: false);
            }
            catch { }
        }

        private static void LogClientHandlerFailure(Exception exception)
        {
            string message = exception.Message ?? string.Empty;
            bool benign =
                message.IndexOf("Connection closed before reading expected bytes", StringComparison.OrdinalIgnoreCase) >= 0
                || message.IndexOf("Read timed out", StringComparison.OrdinalIgnoreCase) >= 0
                || exception is IOException
                || exception is ObjectDisposedException
                || exception is OperationCanceledException;
            if (benign)
            {
                if (IsDebugEnabled())
                    McpLog.Info("Client handler connection ended", always: false);
            }
            else
                McpLog.Error("Client handler failed");
        }

        private static async Task HandleCommandAsync(
            TcpClient client,
            NetworkStream stream,
            StdioFrameWriter writer,
            StdioBridgeAuthentication launch,
            StdioAuthenticatedSession session,
            CancellationTokenSource connectionLifetime
        )
        {
            CommandPhaseTiming timing = null;
            QueuedCommand owned = null;
            QueuedCommand observed = null;
            InboundFrame frame = null;
            CancellationTokenRegistration cancelQueued = default;
            using var stopProgress = CancellationTokenSource.CreateLinkedTokenSource(connectionLifetime.Token);
            Task progressPump = Task.CompletedTask;
            CancellationTokenSource commandLifetime = null;
            try
            {
                long frameReadStartedAt = _uptime.ElapsedMilliseconds;
                frame = await ReadFrameAsUtf8Async(client, stream, FrameIOTimeoutMs, connectionLifetime.Token).ConfigureAwait(false);
                string commandText = frame.Text;
                var prelude = ReadProgressPrelude(commandText, out string progressId);
                long admittedAt = _uptime.ElapsedMilliseconds;
                if (prelude != ProgressPrelude.None)
                {
                    if (prelude != ProgressPrelude.Accepted || !session.Capabilities.Contains(CommandProgressBuffer.Capability))
                        throw new IOException("Stdio progress association refused");
                    // Exactly one local armed slot. EOF, cancellation or any second
                    // prelude exits this connection without dispatching business work.
                    int associationMs = Math.Min(RemainingCommandMs(frameReadStartedAt, FrameIOTimeoutMs), RemainingCommandMs(admittedAt, FrameIOTimeoutMs));
                    if (associationMs == 0)
                        throw new IOException("Stdio progress association deadline expired");
                    using var associationDeadline = CancellationTokenSource.CreateLinkedTokenSource(connectionLifetime.Token);
                    associationDeadline.CancelAfter(associationMs);
                    commandText = null;
                    frame.Dispose();
                    frame = await ReadFrameAsUtf8Async(client, stream, associationMs, associationDeadline.Token).ConfigureAwait(false);
                    commandText = frame.Text;
                    if (RemainingCommandMs(frameReadStartedAt, FrameIOTimeoutMs) == 0)
                        throw new IOException("Stdio progress association deadline expired");
                    if (!IsProgressBusiness(commandText))
                        throw new IOException("Stdio progress association has no business command");
                }
                if (commandText.Trim() == "ping")
                {
                    using var pingDeadline = CancellationTokenSource.CreateLinkedTokenSource(connectionLifetime.Token);
                    pingDeadline.CancelAfter(FrameIOTimeoutMs);
                    await writer
                        .WriteResultAsync(System.Text.Encoding.UTF8.GetBytes("{\"status\":\"success\",\"result\":{\"message\":\"pong\"}}"), pingDeadline.Token)
                        .ConfigureAwait(false);
                    return;
                }
                LogReceivedCommand(commandText);
                string commandId = Guid.NewGuid().ToString();
                var result = new TaskCompletionSource<string>(TaskCreationOptions.RunContinuationsAsynchronously);
                TaskCompletionSource<string> pending = result;
                timing = CommandPhaseTiming.Create(McpLogRecord.IsEnabled, CommandPhaseTiming.Transport.Stdio);
                int timeoutMs = ResolveCommandTimeoutMs(
                    commandText,
                    FrameIOTimeoutMs,
                    session.Capabilities.Contains(NativeCommandArbitrator.Capability),
                    out string requestNonce
                );
                bool optedIn = prelude == ProgressPrelude.Accepted;
                int remainingMs = optedIn ? RemainingCommandMs(admittedAt, timeoutMs) : timeoutMs;
                if (remainingMs == 0)
                    throw new IOException("Stdio progress command deadline expired");
                commandLifetime = CancellationTokenSource.CreateLinkedTokenSource(connectionLifetime.Token);
                commandLifetime.CancelAfter(remainingMs);
                lock (clientsLock)
                {
                    if (!isRunning || !ReferenceEquals(authentication, launch) || !activeClients.Contains(client) || connectionLifetime.IsCancellationRequested)
                        throw new OperationCanceledException(connectionLifetime.Token);
                    lock (lockObj)
                    {
                        var disposition = FindBrokerResendTarget(commandText, client, requestNonce, out QueuedCommand inFlight);
                        if (disposition == BrokerRequestDisposition.ExistingRequest)
                        {
                            observed = inFlight;
                            Interlocked.Increment(ref observed.IdentifiedResponseWaiters);
                            pending = inFlight.Tcs;
                            timing?.ExecutionDone();
                            McpLog.Info("Suppressed repeated identified stdio command; awaiting its in-flight result.", always: false);
                        }
                        else if (disposition == BrokerRequestDisposition.AmbiguousLegacyOutcome)
                        {
                            result.TrySetResult(
                                "{\"status\":\"error\",\"code\":\"legacy_command_outcome_unknown\",\"error\":\"An identical command from a disconnected legacy client has not settled; its outcome cannot be attributed to this request\"}"
                            );
                            timing?.ExecutionDone();
                        }
                        else if (commandQueue.Count >= 128)
                        {
                            result.TrySetResult("{\"status\":\"error\",\"error\":\"Command queue capacity reached\"}");
                            timing?.ExecutionDone();
                        }
                        else
                        {
                            timing?.Start(CommandPhaseTiming.Phase.Queue);
                            owned = new QueuedCommand
                            {
                                CommandJson = commandText,
                                Tcs = result,
                                IsExecuting = false,
                                EnqueuedAtMs = _uptime.ElapsedMilliseconds,
                                TimeoutMs = timeoutMs,
                                Owner = client,
                                OwnerCancellation = commandLifetime.Token,
                                AdmissionCancellation = commandAdmission.Token,
                                ConnectionCancellation = connectionLifetime.Token,
                                RequestNonce = requestNonce,
                                InputReservation = frame.TakeReservation(),
                                Timing = timing,
                                Progress = optedIn ? new CommandProgressBuffer() : null,
                                ProgressId = optedIn ? progressId : null,
                                Session = session,
                                DeadlineAtMs = optedIn ? admittedAt + timeoutMs : 0,
                            };
                            commandQueue[commandId] = owned;
                        }
                    }
                }
                commandText = null;
                frame.Dispose();
                frame = null;
                if (owned != null)
                    cancelQueued = commandLifetime.Token.Register(() => CancelQueuedCommand(commandId, owned));
                if (owned?.Progress != null)
                    progressPump = PumpStdioProgressAsync(client, writer, launch, session, commandId, owned, commandLifetime.Token, stopProgress.Token);
                try
                {
                    EditorApplication.QueuePlayerLoopUpdate();
                }
                catch { }
                string response;
                using var watchLifetime = CancellationTokenSource.CreateLinkedTokenSource(connectionLifetime.Token);
                Task disconnectWatch = WatchPendingDisconnectAsync(client, pending.Task, connectionLifetime, watchLifetime.Token);
                try
                {
                    using var responseWait = CancellationTokenSource.CreateLinkedTokenSource(connectionLifetime.Token);
                    int waitMs = optedIn ? RemainingCommandMs(admittedAt, timeoutMs) : timeoutMs;
                    var completed = await Task.WhenAny(pending.Task, Task.Delay(waitMs, responseWait.Token)).ConfigureAwait(false);
                    responseWait.Cancel();
                    if (completed == pending.Task)
                    {
                        response = pending.Task.Result;
                        Interlocked.Exchange(ref _consecutiveTimeouts, 0);
                    }
                    else
                    {
                        int timeouts = Interlocked.Increment(ref _consecutiveTimeouts);
                        McpLog.Warn($"Command TCS timed out ({timeouts} consecutive)");
                        response = SerializeTimedResponse(new { status = "error", error = $"Command processing timed out after {timeoutMs} ms" }, timing);
                    }
                }
                catch (Exception ex)
                {
                    response = SerializeTimedResponse(new { status = "error", error = ex.Message }, timing);
                }
                finally
                {
                    if (!pending.Task.IsCompleted)
                        commandLifetime.Cancel();
                    watchLifetime.Cancel();
                    await disconnectWatch.ConfigureAwait(false);
                }
                owned?.Progress?.Close();
                stopProgress.Cancel();
                await progressPump.ConfigureAwait(false);
                timing?.Start(CommandPhaseTiming.Phase.Serialization);
                byte[] responseBytes;
                try
                {
                    responseBytes = System.Text.Encoding.UTF8.GetBytes(response);
                }
                finally
                {
                    timing?.Stop(CommandPhaseTiming.Phase.Serialization);
                }
                using var legacyWrite = CancellationTokenSource.CreateLinkedTokenSource(connectionLifetime.Token);
                // Preserve ordinary no-prelude timeout/result behavior. Only an
                // accepted opt-in shares its original deadline with the final frame.
                if (!optedIn)
                    legacyWrite.CancelAfter(FrameIOTimeoutMs);
                CancellationToken writeDeadline = optedIn ? commandLifetime.Token : legacyWrite.Token;
                timing?.Start(CommandPhaseTiming.Phase.Send);
                await writer.WriteResultAsync(responseBytes, writeDeadline).ConfigureAwait(false);
                timing?.Stop(CommandPhaseTiming.Phase.Send);
                timing?.DeliveryDone(CommandPhaseTiming.Delivery.Sent);
            }
            finally
            {
                if (observed != null)
                    Interlocked.Decrement(ref observed.IdentifiedResponseWaiters);
                frame?.Dispose();
                cancelQueued.Dispose();
                owned?.Progress?.Close();
                stopProgress.Cancel();
                await progressPump.ConfigureAwait(false);
                commandLifetime?.Dispose();
                timing?.DeliveryDone(connectionLifetime.IsCancellationRequested ? CommandPhaseTiming.Delivery.Canceled : CommandPhaseTiming.Delivery.Failed);
            }
        }

        private static bool IsCurrentStdioProgress(
            TcpClient client,
            StdioBridgeAuthentication launch,
            StdioAuthenticatedSession session,
            string commandId,
            QueuedCommand command
        )
        {
            lock (clientsLock)
                if (!isRunning || !ReferenceEquals(authentication, launch) || !activeClients.Contains(client))
                    return false;
            lock (lockObj)
                return commandQueue.TryGetValue(commandId, out var current)
                    && ReferenceEquals(current, command)
                    && ReferenceEquals(command.Owner, client)
                    && ReferenceEquals(command.Session, session)
                    && !command.OwnerCancellation.IsCancellationRequested
                    && !command.Tcs.Task.IsCompleted
                    && !command.Progress.IsClosed;
        }

        private static async Task PumpStdioProgressAsync(
            TcpClient client,
            StdioFrameWriter writer,
            StdioBridgeAuthentication launch,
            StdioAuthenticatedSession session,
            string commandId,
            QueuedCommand command,
            CancellationToken deadline,
            CancellationToken stop
        )
        {
            long lastSent = 0;
            int sent = 0;
            try
            {
                while (!stop.IsCancellationRequested && !deadline.IsCancellationRequested && !command.Progress.IsClosed && sent < 256)
                {
                    await command.Progress.WaitAsync(stop).ConfigureAwait(false);
                    if (lastSent != 0)
                    {
                        int delay = (int)(100 - (_uptime.ElapsedMilliseconds - lastSent));
                        if (delay > 0)
                            await Task.Delay(delay, stop).ConfigureAwait(false);
                    }
                    if (!IsCurrentStdioProgress(client, launch, session, commandId, command))
                        return;
                    if (!command.Progress.TryTake(out var update))
                        continue;
                    var payload = new JObject
                    {
                        ["type"] = "command_progress",
                        ["id"] = command.ProgressId,
                        ["completed"] = update.Completed,
                        ["total"] = update.Total,
                        ["stage"] = "batch_items",
                    };
                    byte[] json = System.Text.Encoding.UTF8.GetBytes(payload.ToString(Formatting.None));
                    if (
                        json.Length > CommandProgressBuffer.MaxFrameBytes
                        || stop.IsCancellationRequested
                        || !IsCurrentStdioProgress(client, launch, session, commandId, command)
                    )
                        return;
                    byte[] frame = new byte[json.Length + 4];
                    frame[0] = (byte)'M';
                    frame[1] = (byte)'C';
                    frame[2] = (byte)'P';
                    frame[3] = (byte)'G';
                    Buffer.BlockCopy(json, 0, frame, 4, json.Length);
                    // A started write joins normally; stop only cancels buffer waits.
                    // Its actual I/O remains bounded by the ORIGINAL command token.
                    if (!await writer.TryWriteProgressAsync(frame, deadline).ConfigureAwait(false))
                        return;
                    sent++;
                    lastSent = _uptime.ElapsedMilliseconds;
                }
            }
            catch (OperationCanceledException) { }
            catch (Exception) { /* Optional reporting cannot replace a tool result or retry work. */ }
        }

        private static async Task WatchPendingDisconnectAsync(
            TcpClient client,
            Task pending,
            CancellationTokenSource connectionLifetime,
            CancellationToken watchStop
        )
        {
            // No frame reader runs while a command result is pending. Polling only
            // in this interval avoids racing a reader that just consumed bytes.
            while (!pending.IsCompleted && !watchStop.IsCancellationRequested)
            {
                try
                {
                    Socket socket = client.Client;
                    if (socket == null || socket.Poll(0, SelectMode.SelectRead) && socket.Available == 0)
                    {
                        connectionLifetime.Cancel();
                        return;
                    }
                }
                catch (SocketException)
                {
                    connectionLifetime.Cancel();
                    return;
                }
                catch (ObjectDisposedException)
                {
                    connectionLifetime.Cancel();
                    return;
                }
                await Task.WhenAny(pending, Task.Delay(50, watchStop)).ConfigureAwait(false);
            }
        }

        private static async Task<byte[]> ReadExactAsync(NetworkStream stream, int count, int timeoutMs, CancellationToken cancel = default)
        {
            cancel.ThrowIfCancellationRequested();
            byte[] buffer = new byte[count];
            int offset = 0;
            var stopwatch = System.Diagnostics.Stopwatch.StartNew();

            while (offset < count)
            {
                int remaining = count - offset;
                int remainingTimeout = timeoutMs <= 0 ? Timeout.Infinite : timeoutMs - (int)stopwatch.ElapsedMilliseconds;

                if (remainingTimeout != Timeout.Infinite && remainingTimeout <= 0)
                {
                    throw new IOException("Read timed out");
                }

                using var cts = CancellationTokenSource.CreateLinkedTokenSource(cancel);
                if (remainingTimeout != Timeout.Infinite)
                {
                    cts.CancelAfter(remainingTimeout);
                }
                using var abortRead = cts.Token.Register(() =>
                {
                    try
                    {
                        stream.Dispose();
                    }
                    catch { }
                });

                try
                {
#if NETSTANDARD2_1 || NET6_0_OR_GREATER
                    int read = await stream.ReadAsync(buffer.AsMemory(offset, remaining), cts.Token).ConfigureAwait(false);
#else
                    int read = await stream.ReadAsync(buffer, offset, remaining, cts.Token).ConfigureAwait(false);
#endif
                    cts.Token.ThrowIfCancellationRequested();
                    if (read == 0)
                    {
                        throw new IOException("Connection closed before reading expected bytes");
                    }
                    offset += read;
                }
                catch (Exception ex) when (cts.IsCancellationRequested)
                {
                    cancel.ThrowIfCancellationRequested();
                    throw new IOException("Read timed out", ex);
                }
            }

            return buffer;
        }

        private static async Task WriteFrameAsync(NetworkStream stream, byte[] payload)
        {
            using var cts = new CancellationTokenSource(FrameIOTimeoutMs);
            // Unity Mono may only check the token before beginning socket I/O.
            // Closing this connection's stream also releases an in-flight write.
            using var abortWrite = cts.Token.Register(() =>
            {
                try
                {
                    stream.Dispose();
                }
                catch { }
            });
            await WriteFrameAsync(stream, payload, cts.Token).ConfigureAwait(false);
            // A disposed Mono socket can report a partially sent buffer as success.
            cts.Token.ThrowIfCancellationRequested();
        }

        private static async Task WriteFrameAsync(NetworkStream stream, byte[] payload, CancellationToken cancel)
        {
            if (payload == null)
            {
                throw new ArgumentNullException(nameof(payload));
            }
            if ((ulong)payload.LongLength > MaxFrameBytes)
            {
                throw new IOException($"Frame too large: {payload.LongLength}");
            }
            byte[] header = new byte[8];
            WriteUInt64BigEndian(header, (ulong)payload.LongLength);
#if NETSTANDARD2_1 || NET6_0_OR_GREATER
            await stream.WriteAsync(header.AsMemory(0, header.Length), cancel).ConfigureAwait(false);
            await stream.WriteAsync(payload.AsMemory(0, payload.Length), cancel).ConfigureAwait(false);
#else
            await stream.WriteAsync(header, 0, header.Length, cancel).ConfigureAwait(false);
            await stream.WriteAsync(payload, 0, payload.Length, cancel).ConfigureAwait(false);
#endif
        }

        private sealed class InboundFrame : IDisposable
        {
            internal string Text;
            private StdioFrameBudget.Lease reservation;

            internal InboundFrame(string text, StdioFrameBudget.Lease reservation)
            {
                Text = text;
                this.reservation = reservation;
            }

            internal StdioFrameBudget.Lease TakeReservation()
            {
                Text = null;
                return Interlocked.Exchange(ref reservation, null);
            }

            public void Dispose()
            {
                Text = null;
                Interlocked.Exchange(ref reservation, null)?.Dispose();
            }
        }

        private static async Task<InboundFrame> ReadFrameAsUtf8Async(TcpClient client, NetworkStream stream, int timeoutMs, CancellationToken cancel)
        {
            // Idle header waits reserve nothing. Header, capacity wait and body share
            // one original I/O deadline rather than restarting it after admission.
            using var deadline = CancellationTokenSource.CreateLinkedTokenSource(cancel);
            deadline.CancelAfter(timeoutMs);
            long startedAt = _uptime.ElapsedMilliseconds;
            byte[] header = await ReadExactAsync(stream, 8, timeoutMs, deadline.Token).ConfigureAwait(false);
            ulong payloadLen = ReadUInt64BigEndian(header);
            if (payloadLen == 0 || payloadLen > MaxFrameBytes)
                throw new IOException($"Invalid framed length: {payloadLen}");
            int count = (int)payloadLen;
            StdioFrameBudget.Lease reservation = null;
            try
            {
                Task<StdioFrameBudget.Lease> admission = inputBudget.AcquireAsync(count, deadline.Token);
                using var watchStop = CancellationTokenSource.CreateLinkedTokenSource(deadline.Token);
                Task disconnect = WatchPendingDisconnectAsync(client, admission, deadline, watchStop.Token);
                try
                {
                    reservation = await admission.ConfigureAwait(false);
                }
                finally
                {
                    watchStop.Cancel();
                    await disconnect.ConfigureAwait(false);
                }
                deadline.Token.ThrowIfCancellationRequested();
                int remaining = RemainingCommandMs(startedAt, timeoutMs);
                if (remaining == 0)
                    throw new IOException("Read timed out");
                byte[] payload = await ReadExactAsync(stream, count, remaining, deadline.Token).ConfigureAwait(false);
                var result = new InboundFrame(System.Text.Encoding.UTF8.GetString(payload), reservation);
                reservation = null;
                return result;
            }
            finally
            {
                reservation?.Dispose();
            }
        }

        private static void ReleaseInput(QueuedCommand command)
        {
            command.CommandJson = null;
            Interlocked.Exchange(ref command.InputReservation, null)?.Dispose();
        }

        private static void CancelQueuedCommand(string id, QueuedCommand expected)
        {
            lock (lockObj)
            {
                if (!commandQueue.TryGetValue(id, out var current) || !ReferenceEquals(current, expected) || current.IsExecuting)
                    return;
                commandQueue.Remove(id);
                current.Tcs.TrySetCanceled(current.OwnerCancellation);
                current.Progress?.Close();
                current.Timing?.ExecutionDone();
                ReleaseInput(current);
            }
        }

        private static ulong ReadUInt64BigEndian(byte[] buffer)
        {
            if (buffer == null || buffer.Length < 8)
                return 0UL;
            return ((ulong)buffer[0] << 56)
                | ((ulong)buffer[1] << 48)
                | ((ulong)buffer[2] << 40)
                | ((ulong)buffer[3] << 32)
                | ((ulong)buffer[4] << 24)
                | ((ulong)buffer[5] << 16)
                | ((ulong)buffer[6] << 8)
                | buffer[7];
        }

        private static void WriteUInt64BigEndian(byte[] dest, ulong value)
        {
            if (dest == null || dest.Length < 8)
            {
                throw new ArgumentException("Destination buffer too small for UInt64");
            }
            dest[0] = (byte)(value >> 56);
            dest[1] = (byte)(value >> 48);
            dest[2] = (byte)(value >> 40);
            dest[3] = (byte)(value >> 32);
            dest[4] = (byte)(value >> 24);
            dest[5] = (byte)(value >> 16);
            dest[6] = (byte)(value >> 8);
            dest[7] = (byte)(value);
        }

        private static bool UsesNativeArbitration(QueuedCommand queued) =>
            queued.Session != null && queued.Session.Capabilities.Contains(NativeCommandArbitrator.Capability);

        private static void ProcessCommands()
        {
            if (!isRunning)
                return;
            if (Interlocked.Exchange(ref processingCommands, 1) == 1)
                return;
            try
            {
                double now = EditorApplication.timeSinceStartup;
                if (now >= nextHeartbeatAt)
                {
                    WriteHeartbeat(false);
                    nextHeartbeatAt = now + 0.5f;
                }

                KeyValuePair<string, QueuedCommand> work = default;
                lock (lockObj)
                {
                    // Avoid cancellation-scan allocations on idle editor updates.
                    if (commandQueue.Count == 0)
                        return;

                    canceledCommandIds.Clear();
                    bool hasExecuting = false;
                    foreach (var item in commandQueue)
                    {
                        if (item.Value.IsExecuting)
                            hasExecuting = true;
                        else if (item.Value.OwnerCancellation.IsCancellationRequested || item.Value.AdmissionCancellation.IsCancellationRequested)
                            canceledCommandIds.Add(item.Key);
                    }
                    foreach (var id in canceledCommandIds)
                    {
                        commandQueue[id].Tcs.TrySetCanceled(commandQueue[id].OwnerCancellation);
                        commandQueue[id].Timing?.ExecutionDone();
                        ReleaseInput(commandQueue[id]);
                        commandQueue.Remove(id);
                    }
                    canceledCommandIds.Clear();
                    if (canceledCommandIds.Capacity > 256)
                        canceledCommandIds.Capacity = 256;
                    // Early exit inside lock to prevent per-frame List allocations (GitHub issue #577)
                    if (commandQueue.Count == 0)
                    {
                        return;
                    }

                    foreach (var kvp in commandQueue)
                    {
                        var queued = kvp.Value;
                        if (queued.IsExecuting)
                            continue;
                        // Old peers retain FIFO through actual settlement. Negotiated peers
                        // reach native admission promptly: conflicts receive a no-start receipt,
                        // and audited control can observe/cancel an owner on another connection.
                        if (hasExecuting && !UsesNativeArbitration(queued))
                            continue;
                        queued.IsExecuting = true;
                        work = kvp;
                        break;
                    }
                }

                if (work.Value != null)
                {
                    string id = work.Key;
                    QueuedCommand queuedCommand = work.Value;
                    string commandText = queuedCommand.CommandJson;
                    TaskCompletionSource<string> tcs = queuedCommand.Tcs;

                    if (string.IsNullOrWhiteSpace(commandText))
                    {
                        var emptyResponse = new { status = "error", error = "Empty command received" };
                        tcs.SetResult(SerializeTimedResponse(emptyResponse, queuedCommand.Timing));
                        lock (lockObj)
                        {
                            commandQueue.Remove(id);
                            ReleaseInput(queuedCommand);
                        }
                        queuedCommand.Timing?.ExecutionDone();
                        return;
                    }

                    commandText = commandText.Trim();
                    if (commandText == "ping")
                    {
                        var pingResponse = new { status = "success", result = new { message = "pong" } };
                        tcs.SetResult(SerializeTimedResponse(pingResponse, queuedCommand.Timing));
                        lock (lockObj)
                        {
                            commandQueue.Remove(id);
                            ReleaseInput(queuedCommand);
                        }
                        queuedCommand.Timing?.ExecutionDone();
                        return;
                    }

                    if (!IsValidJson(commandText))
                    {
                        var invalidJsonResponse = new
                        {
                            status = "error",
                            error = "Invalid JSON format",
                            receivedText = commandText.Length > 50 ? commandText[..50] + "..." : commandText,
                        };
                        tcs.SetResult(SerializeTimedResponse(invalidJsonResponse, queuedCommand.Timing));
                        lock (lockObj)
                        {
                            commandQueue.Remove(id);
                            ReleaseInput(queuedCommand);
                        }
                        queuedCommand.Timing?.ExecutionDone();
                        return;
                    }

                    ExecuteQueuedCommand(id, queuedCommand);
                }
            }
            finally
            {
                Interlocked.Exchange(ref processingCommands, 0);
            }
        }

        private sealed class CommandCancellation
        {
            private readonly object gate = new();
            private readonly CancellationTokenSource source = new();
            private readonly CancellationTokenRegistration parent;
            private readonly Timer deadline;
            private Task cancellation;
            private bool finished;

            internal CancellationToken Token => source.Token;

            internal CommandCancellation(CancellationToken owner, int remainingMs)
            {
                // Linking the execution source directly lets an upstream Cancel run
                // arbitrary handler callbacks and makes callback settlement unobservable.
                parent = owner.Register(RequestCancellation);
                if (remainingMs == 0)
                    RequestCancellation();
                else
                    deadline = new Timer(_ => RequestCancellation(), null, remainingMs, Timeout.Infinite);
            }

            private void RequestCancellation()
            {
                lock (gate)
                {
                    if (finished || cancellation != null)
                        return;
                    cancellation = Task.Run(() => source.Cancel());
                }
            }

            internal async Task CompleteAsync()
            {
                deadline?.Dispose();
                parent.Dispose();
                Task pending;
                lock (gate)
                {
                    finished = true;
                    pending = cancellation;
                }
                try
                {
                    if (pending != null)
                        await pending.ConfigureAwait(false);
                }
                catch (Exception) { /* Cancellation callback failures do not abandon the owner. */ }
                finally
                {
                    source.Dispose();
                }
            }
        }

        private static void ExecuteQueuedCommand(string commandId, QueuedCommand queuedCommand)
        {
            string payload = queuedCommand.CommandJson.Trim();
            TaskCompletionSource<string> completionSource = queuedCommand.Tcs;
            int commandTimeoutMs = queuedCommand.TimeoutMs > 0 ? queuedCommand.TimeoutMs : ResolveCommandTimeoutMs(payload, FrameIOTimeoutMs);
            SynchronizationContext editorContext = SynchronizationContext.Current;
            async void Runner()
            {
                TransportCommandOperation operation = null;
                int remainingMs =
                    queuedCommand.DeadlineAtMs == 0
                        ? commandTimeoutMs
                        : (int)Math.Max(0, Math.Min(int.MaxValue, queuedCommand.DeadlineAtMs - _uptime.ElapsedMilliseconds));
                var cancellation = new CommandCancellation(queuedCommand.OwnerCancellation, remainingMs);
                try
                {
                    queuedCommand.OwnerCancellation.ThrowIfCancellationRequested();
                    queuedCommand.AdmissionCancellation.ThrowIfCancellationRequested();
                    if (remainingMs == 0)
                        throw new OperationCanceledException(cancellation.Token);
                    var command = TransportJson.DeserializeCommand(payload);
                    if (queuedCommand.Session == null || !queuedCommand.Session.Capabilities.Contains(NativeCommandArbitrator.Capability))
                        command.arbitration = null;
                    operation = TransportCommandDispatcher.ExecuteTimedCommandAsync(
                        command,
                        cancellation.Token,
                        TransportMode.Stdio,
                        queuedCommand.Timing,
                        queuedCommand.Progress,
                        queuedCommand.AdmissionCancellation,
                        queuedCommand.OwnerCancellation
                    );
                    command = null;
                    // Dispatch above runs on the Editor thread. Below, only frozen responses,
                    // timing, buffers and queue ownership are touched; no Unity API access.
                    var response = await operation.Response.ConfigureAwait(false);
                    queuedCommand.Progress?.Close();
                    queuedCommand.Timing?.Start(CommandPhaseTiming.Phase.Serialization);
                    string json;
                    try
                    {
                        json = response.ToJson();
                    }
                    finally
                    {
                        queuedCommand.Timing?.Stop(CommandPhaseTiming.Phase.Serialization);
                    }
                    completionSource.TrySetResult(json);
                }
                catch (OperationCanceledException)
                {
                    queuedCommand.Progress?.Close();
                    var timeoutResponse = new { status = "error", error = $"Command processing timed out after {commandTimeoutMs} ms" };
                    completionSource.TrySetResult(SerializeTimedResponse(timeoutResponse, queuedCommand.Timing));
                }
                catch (Exception ex)
                {
                    queuedCommand.Progress?.Close();
                    PostEditorDiagnostic(editorContext, "Command processing failed", true);
                    var response = new
                    {
                        status = "error",
                        error = ex.Message,
                        receivedText = payload?.Length > 50 ? payload[..50] + "..." : payload,
                    };
                    completionSource.TrySetResult(SerializeTimedResponse(response, queuedCommand.Timing));
                }
                finally
                {
                    queuedCommand.Progress?.Close();
                    if (operation != null)
                    {
                        try
                        {
                            await operation.Completion.ConfigureAwait(false);
                        }
                        catch (Exception ex)
                        {
                            PostEditorDiagnostic(editorContext, $"Stdio command settlement: {ex.GetType().Name}", false);
                        }
                    }
                    // The queue and its input lease own both execution and callbacks.
                    await cancellation.CompleteAsync().ConfigureAwait(false);
                    lock (lockObj)
                    {
                        commandQueue.Remove(commandId);
                        queuedCommand.Timing?.ExecutionDone();
                        payload = null;
                        ReleaseInput(queuedCommand);
                    }
                }
            }

            Runner();
        }

        private static void PostEditorDiagnostic(SynchronizationContext editorContext, string message, bool error)
        {
            if (editorContext == null)
                return;
            try
            {
                editorContext.Post(
                    _ =>
                    {
                        if (error)
                            McpLog.Error(message);
                        else
                            McpLog.Warn(message);
                    },
                    null
                );
            }
            catch { /* A retiring Editor context cannot prevent managed ownership cleanup. */ }
        }

        private static string SerializeTimedResponse(object response, CommandPhaseTiming timing)
        {
            timing?.Start(CommandPhaseTiming.Phase.Serialization);
            try
            {
                return JsonConvert.SerializeObject(response);
            }
            finally
            {
                timing?.Stop(CommandPhaseTiming.Phase.Serialization);
            }
        }

        private static object InvokeOnMainThreadWithTimeout(Func<object> func, int timeoutMs)
        {
            if (func == null)
                return null;
            try
            {
                if (mainThreadId == 0)
                {
                    try
                    {
                        return func();
                    }
                    catch (Exception ex)
                    {
                        throw new InvalidOperationException($"Main thread handler error: {ex.Message}", ex);
                    }
                }
                try
                {
                    if (Thread.CurrentThread.ManagedThreadId == mainThreadId)
                    {
                        return func();
                    }
                }
                catch { }

                object result = null;
                Exception captured = null;
                var tcs = new TaskCompletionSource<bool>(TaskCreationOptions.RunContinuationsAsynchronously);
                EditorApplication.delayCall += () =>
                {
                    try
                    {
                        result = func();
                    }
                    catch (Exception ex)
                    {
                        captured = ex;
                    }
                    finally
                    {
                        try
                        {
                            tcs.TrySetResult(true);
                        }
                        catch { }
                    }
                };

                bool completed = tcs.Task.Wait(timeoutMs);
                if (!completed)
                {
                    return null;
                }
                if (captured != null)
                {
                    throw new InvalidOperationException($"Main thread handler error: {captured.Message}", captured);
                }
                return result;
            }
            catch (Exception ex)
            {
                throw new InvalidOperationException($"Failed to invoke on main thread: {ex.Message}", ex);
            }
        }

        private static bool IsValidJson(string text)
        {
            if (string.IsNullOrWhiteSpace(text))
            {
                return false;
            }

            text = text.Trim();
            if ((text.StartsWith("{") && text.EndsWith("}")) || (text.StartsWith("[") && text.EndsWith("]")))
            {
                try
                {
                    JToken.Parse(text);
                    return true;
                }
                catch
                {
                    return false;
                }
            }

            return false;
        }

        public static void WriteHeartbeat(bool reloading, string reason = null)
        {
            if (ownedEndpoint)
                return;
            try
            {
                string dir = Environment.GetEnvironmentVariable("UNITY_MCP_STATUS_DIR");
                if (string.IsNullOrWhiteSpace(dir))
                {
                    dir = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.UserProfile), ".unity-mcp");
                }
                Directory.CreateDirectory(dir);
                string filePath = Path.Combine(dir, $"unity-mcp-status-{ComputeProjectHash(Application.dataPath)}.json");

                string projectName = "Unknown";
                try
                {
                    string projectPath = Application.dataPath;
                    if (!string.IsNullOrEmpty(projectPath))
                    {
                        projectPath = projectPath.TrimEnd('/', '\\');
                        if (projectPath.EndsWith("Assets", StringComparison.OrdinalIgnoreCase))
                        {
                            projectPath = projectPath.Substring(0, projectPath.Length - 6).TrimEnd('/', '\\');
                        }
                        projectName = Path.GetFileName(projectPath);
                        if (string.IsNullOrEmpty(projectName))
                        {
                            projectName = "Unknown";
                        }
                    }
                }
                catch { }

                bool projectScopedTools = EditorPrefs.GetBool(
                    EditorPrefKeys.ProjectScopedToolsLocalHttp,
                    false // must match McpToolsSection toggle default so UI and heartbeat agree
                );

                var payload = new
                {
                    unity_port = currentUnityPort,
                    reloading,
                    reason = reason ?? (reloading ? "reloading" : "ready"),
                    seq = heartbeatSeq,
                    project_path = Application.dataPath,
                    project_name = projectName,
                    unity_version = Application.unityVersion,
                    last_heartbeat = DateTime.UtcNow.ToString("O"),
                    project_scoped_tools = projectScopedTools,
                };
                File.WriteAllText(filePath, JsonConvert.SerializeObject(payload), new System.Text.UTF8Encoding(false));
            }
            catch (Exception) { }
        }

        private static string ComputeProjectHash(string input)
        {
            try
            {
                using var sha1 = System.Security.Cryptography.SHA1.Create();
                byte[] bytes = System.Text.Encoding.UTF8.GetBytes(input ?? string.Empty);
                byte[] hashBytes = sha1.ComputeHash(bytes);
                var sb = new System.Text.StringBuilder();
                foreach (byte b in hashBytes)
                {
                    sb.Append(b.ToString("x2"));
                }
                return sb.ToString()[..8];
            }
            catch
            {
                return "default";
            }
        }
    }
}
