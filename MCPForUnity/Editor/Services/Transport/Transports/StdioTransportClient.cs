using System;
using System.Diagnostics;
using System.Threading;
using System.Threading.Tasks;
using MCPForUnity.Editor.Helpers;

namespace MCPForUnity.Editor.Services.Transport.Transports
{
    /// <summary>
    /// Adapts the existing TCP bridge into the transport abstraction.
    /// </summary>
    public class StdioTransportClient : IMcpTransportClient
    {
        private TransportState _state = TransportState.Disconnected("stdio");
        private readonly Func<CancellationToken, Task<bool>> _startAndWait;
        private readonly Action _stopBridge;
        private CancellationTokenSource _startCancellation;

        public StdioTransportClient()
            : this(StartBridgeAndWaitAsync, StdioBridgeHost.Stop) { }

        internal StdioTransportClient(Func<CancellationToken, Task<bool>> startAndWait, Action stopBridge)
        {
            _startAndWait = startAndWait ?? throw new ArgumentNullException(nameof(startAndWait));
            _stopBridge = stopBridge ?? throw new ArgumentNullException(nameof(stopBridge));
        }

        public bool IsConnected => StdioBridgeHost.IsRunning;
        public string TransportName => "stdio";

        /// <summary>
        /// The bridge host can bind or drop outside StartAsync/StopAsync (editor-idle retry
        /// after a busy-port reload, CI auto-start), so the cached snapshot can go stale in
        /// both directions — and a stop/start cycle between two reads can land on a different
        /// port while staying "connected". Refresh from the live listener whenever
        /// connectivity or the bound port disagrees.
        /// </summary>
        public TransportState State
        {
            get
            {
                bool bridgeRunning = StdioBridgeHost.IsRunning;
                int livePort = StdioBridgeHost.GetCurrentPort();
                if (bridgeRunning != _state.IsConnected || (bridgeRunning && _state.Port != livePort))
                {
                    _state = bridgeRunning ? TransportState.Connected("stdio", port: livePort) : TransportState.Disconnected("stdio", "Bridge not running");
                }
                return _state;
            }
        }

        // Bounded window to wait for the bridge to actually bind after StartAutoConnect. Covers the
        // OS port-release delay after a domain reload (the same port can stay held for a few hundred
        // ms, longer on Windows/macOS), during which Start() defers binding to an editor-idle retry
        // or falls back to a new port once BusyPortFallbackWindowSeconds elapses.
        internal const double ReadyWaitTimeoutSeconds = 300.0;
        private const int ReadyPollIntervalMs = 100;

        // Pure predicate (unit-testable): keep polling while the bridge is not yet ready and the
        // bounded window has not elapsed.
        internal static bool ShouldKeepWaitingForReady(bool bridgeReady, double secondsWaited) => !bridgeReady && secondsWaited < ReadyWaitTimeoutSeconds;

        public async Task<bool> StartAsync()
        {
            RetirePendingStart();
            var cancellation = new CancellationTokenSource();
            _startCancellation = cancellation;
            try
            {
                bool ready = await _startAndWait(cancellation.Token);
                if (!ReferenceEquals(_startCancellation, cancellation) || cancellation.IsCancellationRequested)
                    return false;
                _state = ready
                    ? TransportState.Connected("stdio", port: StdioBridgeHost.GetCurrentPort())
                    : TransportState.Disconnected("stdio", "Bridge readiness budget expired; inspect the editor and retry.");
                return ready;
            }
            catch (Exception ex)
            {
                if (ReferenceEquals(_startCancellation, cancellation))
                    _state = TransportState.Disconnected("stdio", ex.Message);
                return false;
            }
            finally
            {
                if (ReferenceEquals(_startCancellation, cancellation))
                    _startCancellation = null;
                cancellation.Dispose();
            }
        }

        private static async Task<bool> StartBridgeAndWaitAsync(CancellationToken cancellationToken)
        {
            cancellationToken.ThrowIfCancellationRequested();
            double budget = OperationTimeouts.EnvironmentSeconds("UNITY_MCP_STDIO_START_TIMEOUT", ReadyWaitTimeoutSeconds);
            var elapsed = Stopwatch.StartNew();
            StdioBridgeHost.StartAutoConnect();
            while (!StdioBridgeHost.IsRunning)
            {
                cancellationToken.ThrowIfCancellationRequested();
                double remaining = budget - elapsed.Elapsed.TotalSeconds;
                if (remaining <= 0)
                    return false;
                await Task.Delay(TimeSpan.FromSeconds(Math.Min(ReadyPollIntervalMs / 1000.0, remaining)), cancellationToken);
            }
            cancellationToken.ThrowIfCancellationRequested();
            return true;
        }

        private void RetirePendingStart()
        {
            var previous = _startCancellation;
            _startCancellation = null;
            // The suspended StartAsync owns disposal, even if its callback arrives late.
            previous?.Cancel();
        }

        public Task StopAsync()
        {
            RetirePendingStart();
            _stopBridge();
            _state = TransportState.Disconnected("stdio");
            return Task.CompletedTask;
        }

        public Task<bool> VerifyAsync()
        {
            bool running = StdioBridgeHost.IsRunning;
            _state = running
                ? TransportState.Connected("stdio", port: StdioBridgeHost.GetCurrentPort())
                : TransportState.Disconnected("stdio", "Bridge not running");
            return Task.FromResult(running);
        }

        public Task ReregisterToolsAsync()
        {
            // In stdio mode, Python re-syncs tools automatically on reconnection
            // after domain reload. No proactive push mechanism exists over TCP.
            return Task.CompletedTask;
        }
    }
}
