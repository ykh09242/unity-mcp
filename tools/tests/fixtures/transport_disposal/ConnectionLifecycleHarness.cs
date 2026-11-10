using System;
using System.Collections.Generic;
using System.Threading;
using System.Threading.Tasks;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Editor.Services;
using MCPForUnity.Editor.Services.Transport;
using UnityEditor;

internal static class ConnectionLifecycleHarness
{
    private sealed class Client : IMcpTransportClient, IDisposable
    {
        internal TaskCompletionSource<bool> PendingStart;
        internal TaskCompletionSource<bool> PendingStop;
        internal TaskCompletionSource<bool> PendingVerify;
        internal int Starts;
        public bool IsConnected { get; private set; }
        public string TransportName => "controlled";
        public TransportState State => IsConnected ? TransportState.Connected(TransportName) : TransportState.Disconnected(TransportName);

        public async Task<bool> StartAsync()
        {
            Starts++;
            bool started = PendingStart == null || await PendingStart.Task;
            IsConnected = started;
            return started;
        }

        public async Task StopAsync()
        {
            IsConnected = false;
            if (PendingStop != null)
                await PendingStop.Task;
        }

        public Task<bool> VerifyAsync() => PendingVerify?.Task ?? Task.FromResult(IsConnected);

        public Task ReregisterToolsAsync() => Task.CompletedTask;

        public void Dispose() => IsConnected = false;
    }

    private sealed class SingleThreadContext : SynchronizationContext
    {
        private readonly Queue<Action> _queue = new Queue<Action>();

        public override void Post(SendOrPostCallback callback, object state)
        {
            lock (_queue)
                _queue.Enqueue(() => callback(state));
        }

        internal void Drain()
        {
            while (true)
            {
                Action next;
                lock (_queue)
                {
                    if (_queue.Count == 0)
                        return;
                    next = _queue.Dequeue();
                }
                next();
            }
        }
    }

    private static void Check(bool value, string message)
    {
        if (!value)
            throw new Exception(message);
    }

    private static TransportManager Manager(Client http, Client stdio = null)
    {
        var manager = new TransportManager();
        manager.Configure(() => http, () => stdio ?? new Client());
        return manager;
    }

    internal static async Task RunAsync(string scenario)
    {
        EditorConfigurationCache.Instance.UseHttpTransport = true;
        switch (scenario)
        {
            case "replacement":
                await CachedBridgeUsesReplacementAsync();
                break;
            case "verification":
                LegacyVerificationReturnsWithoutBlocking();
                break;
            case "verification-compatibility":
                await CompletedVerificationPreservesCompatibilityAsync();
                break;
            case "resume":
                await RetiredFaultPreservesResumeIntentAsync(false);
                break;
            case "resume-owner":
                await RetiredFaultPreservesResumeIntentAsync(true);
                break;
            case "resume-current-fault":
                await CurrentResumeFaultConsumesIntentAsync();
                break;
            default:
                throw new ArgumentException(scenario);
        }
    }

    private static async Task CachedBridgeUsesReplacementAsync()
    {
        // Given a cached default bridge, with its old owner suspended before the target start.
        var oldHttp = new Client();
        var oldStdio = new Client { PendingStop = new TaskCompletionSource<bool>() };
        var old = Manager(oldHttp, oldStdio);
        MCPServiceLocator.Register(old);
        await old.StartAsync(TransportMode.Stdio);
        IBridgeControlService bridge = MCPServiceLocator.Bridge;
        Task<bool> suspended = bridge.StartAsync();
        Check(!suspended.IsCompleted, "The opposing stop must suspend the old transition.");
        var replacementHttp = new Client();
        var replacement = Manager(replacementHttp);

        // When ownership transfers and the old transition finishes afterwards.
        MCPServiceLocator.Register(replacement);
        oldStdio.PendingStop.SetResult(true);

        // Then old intent cannot start either owner, and the same cached bridge uses the new owner.
        Check(!await suspended && oldHttp.Starts == 0 && replacementHttp.Starts == 0, "Retired transition reached a transport start.");
        Check(await bridge.StartAsync() && bridge.IsRunning && replacementHttp.Starts == 1, "Cached bridge did not use its replacement owner.");
        Check(ReferenceEquals(bridge, MCPServiceLocator.Bridge), "Replacement must preserve the cached default bridge.");
        try
        {
            MCPServiceLocator.Register(old);
            throw new Exception("A disposed manager was restored.");
        }
        catch (ObjectDisposedException) { }
        Check(ReferenceEquals(replacement, MCPServiceLocator.TransportManager) && bridge.IsRunning, "Rejected restoration retired the healthy owner.");

        // A fixture finishes with a fresh usable default, rather than restoring its disposed snapshot.
        MCPServiceLocator.Register(new TransportManager());
        var restored = MCPServiceLocator.TransportManager;
        var fresh = new Client();
        restored.Configure(() => fresh, () => new Client());
        Check(await bridge.StartAsync() && bridge.IsRunning, "Fixture teardown left the cached bridge bound to a retired owner.");
        await bridge.StopAsync();
        MCPServiceLocator.Reset();
    }

    private static void LegacyVerificationReturnsWithoutBlocking()
    {
        // Given a probe whose continuation requires one single-thread context.
        Exception failure = null;
        using var returned = new ManualResetEventSlim();
        var thread = new Thread(() =>
        {
            var context = new SingleThreadContext();
            SynchronizationContext.SetSynchronizationContext(context);
            try
            {
                var client = new Client { PendingVerify = new TaskCompletionSource<bool>() };
                MCPServiceLocator.Register(Manager(client));
                var bridge = new BridgeControlService();
                Check(bridge.StartAsync().GetAwaiter().GetResult(), "The controlled transport must connect synchronously.");

                // When the compatibility API sees an incomplete ping.
                BridgeVerificationResult pending = bridge.Verify(0);

                // Then it returns a truthful incomplete result and leaves the context available.
                Check(!pending.Success && !pending.PingSucceeded, "An incomplete probe was reported as successful.");
                client.PendingVerify.SetResult(true);
                context.Drain();
                Check(bridge.Verify(0).Success, "An already completed successful probe must stay compatible.");
                client.PendingVerify = new TaskCompletionSource<bool>();
                Task<BridgeVerificationResult> asynchronous = bridge.VerifyAsync();
                Check(!asynchronous.IsCompleted, "Asynchronous verification must await the probe.");
                client.PendingVerify.SetResult(true);
                context.Drain();
                Check(asynchronous.IsCompleted && asynchronous.Result.Success, "Pumping the owner context must finish asynchronous verification.");
                MCPServiceLocator.Reset();
            }
            catch (Exception error)
            {
                failure = error;
            }
            finally
            {
                returned.Set();
            }
        })
        {
            IsBackground = true,
        };
        thread.Start();
        Check(returned.Wait(TimeSpan.FromSeconds(2)), "Legacy Verify blocked the single-thread context on an incomplete probe.");
        if (failure != null)
            throw failure;
    }

    private static async Task CompletedVerificationPreservesCompatibilityAsync()
    {
        // Given a default bridge with a completed HTTP probe.
        var http = new Client();
        var stdio = new Client();
        MCPServiceLocator.Register(Manager(http, stdio));
        IBridgeControlService bridge = MCPServiceLocator.Bridge;
        await bridge.StartAsync();

        // When callers use legacy verification across completed HTTP and stdio paths.
        Check(bridge.Verify(0).Success, "Completed HTTP success changed.");
        await bridge.StopAsync();
        Check(!bridge.Verify(0).Success, "Completed HTTP failure changed.");
        EditorConfigurationCache.Instance.UseHttpTransport = false;
        await bridge.StartAsync();

        // Then stdio still enforces the port handshake independently of ping success.
        Check(bridge.Verify(6400).Success, "Completed stdio handshake changed.");
        BridgeVerificationResult mismatch = bridge.Verify(6401);
        Check(!mismatch.Success && !mismatch.HandshakeValid && !mismatch.PingSucceeded, "Stdio port mismatch was accepted.");
        MCPServiceLocator.Reset();
    }

    private static async Task RetiredFaultPreservesResumeIntentAsync(bool replaceOwner)
    {
        // Given the final HTTP resume attempt suspended in a real manager start.
        var client = new Client { PendingStart = new TaskCompletionSource<bool>() };
        MCPServiceLocator.Register(Manager(client));
        SessionState.SetBool(HttpBridgeReloadHandler.ResumeSessionKey, true);
        Task resume = HttpBridgeReloadHandler.ResumeHttpWithRetriesAsync(new[] { TimeSpan.Zero });
        Check(!resume.IsCompleted, "The final resume attempt must be suspended.");
        if (replaceOwner)
            MCPServiceLocator.Register(new TransportManager());
        else
            HttpBridgeReloadHandler.CancelPendingResume();
        SessionState.SetBool(HttpBridgeReloadHandler.ResumeSessionKey, true);
        SessionState.SetString(HttpBridgeReloadHandler.ResumeStatusSessionKey, "new-owner-intent");

        // When the retired attempt faults after replacement intent exists.
        client.PendingStart.SetException(new InvalidOperationException("retired-start-failure"));
        await resume;

        // Then it cannot consume or overwrite the newer pending state.
        Check(HttpBridgeReloadHandler.IsResumePending, "Retired exceptional completion consumed newer resume intent.");
        Check(HttpBridgeReloadHandler.ResumeStatus == "new-owner-intent", "Retired exceptional completion overwrote newer resume status.");
        Check(client.Starts == 1, "Retired exceptional completion started another retry.");
        HttpBridgeReloadHandler.CancelPendingResume();
        MCPServiceLocator.Reset();
    }

    private static async Task CurrentResumeFaultConsumesIntentAsync()
    {
        // Given a current final resume attempt waiting for its transport start.
        var client = new Client { PendingStart = new TaskCompletionSource<bool>() };
        MCPServiceLocator.Register(Manager(client));
        SessionState.SetBool(HttpBridgeReloadHandler.ResumeSessionKey, true);
        Task resume = HttpBridgeReloadHandler.ResumeHttpWithRetriesAsync(new[] { TimeSpan.Zero });

        // When that current attempt fails with no replacement intent.
        client.PendingStart.SetException(new InvalidOperationException("current-start-failure"));
        await resume;

        // Then exhaustion still consumes this generation's intent and retains a diagnosis.
        Check(!HttpBridgeReloadHandler.IsResumePending && HttpBridgeReloadHandler.ResumeStatus.Length != 0, "Current failure did not finish resume recovery.");
        Check(client.Starts == 1, "A final current failure retried beyond its schedule.");
        HttpBridgeReloadHandler.CancelPendingResume();
        MCPServiceLocator.Reset();
    }
}

namespace MCPForUnity.Editor.Constants { }

namespace MCPForUnity.Editor.Services
{
    internal static class EditorStateCache
    {
        internal static bool GetActualIsCompiling() => false;
    }
}

namespace MCPForUnity.Editor.Windows
{
    internal static class MCPForUnityEditorWindow
    {
        internal static void RequestHealthVerification() { }
    }
}

namespace UnityEngine
{
    internal static class Application
    {
        internal static bool isBatchMode => true;
    }
}

namespace UnityEditor
{
    internal sealed class InitializeOnLoadAttribute : Attribute { }

    internal static class EditorApplication
    {
        internal static bool isUpdating => false;
        internal static event Action update;
    }

    internal static class AssemblyReloadEvents
    {
        internal static event Action beforeAssemblyReload;
        internal static event Action afterAssemblyReload;
    }

    internal static class EditorPrefs
    {
        internal static void DeleteKey(string key) { }
    }

    internal static class SessionState
    {
        private static readonly Dictionary<string, object> Values = new Dictionary<string, object>();

        internal static bool GetBool(string key, bool fallback) => Values.TryGetValue(key, out object value) ? (bool)value : fallback;

        internal static string GetString(string key, string fallback) => Values.TryGetValue(key, out object value) ? (string)value : fallback;

        internal static void SetBool(string key, bool value) => Values[key] = value;

        internal static void SetString(string key, string value) => Values[key] = value;

        internal static void EraseBool(string key) => Values.Remove(key);

        internal static void EraseString(string key) => Values.Remove(key);
    }
}
