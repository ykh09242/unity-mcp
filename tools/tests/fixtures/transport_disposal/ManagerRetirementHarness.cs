using System;
using System.Threading.Tasks;
using MCPForUnity.Editor.Services;
using MCPForUnity.Editor.Services.Transport;

internal static class ManagerRetirementHarness
{
    private sealed class Client : IMcpTransportClient, IDisposable
    {
        public readonly TaskCompletionSource<bool> Start = new TaskCompletionSource<bool>();
        public TaskCompletionSource<bool> PendingVerify;
        public int Disposals;
        public bool IsConnected { get; private set; } = true;
        public string TransportName => "owned";
        public TransportState State => IsConnected ? TransportState.Connected(TransportName) : TransportState.Disconnected(TransportName);

        public Task<bool> StartAsync() => Start.Task;

        public Task StopAsync() => throw new Exception("Disposable clients must retain their terminal disposal semantics.");

        public Task<bool> VerifyAsync() => PendingVerify?.Task ?? Task.FromResult(IsConnected);

        public Task ReregisterToolsAsync() => Task.CompletedTask;

        public void Dispose()
        {
            Disposals++;
            IsConnected = false;
        }
    }

    private sealed class AsyncStopClient : IMcpTransportClient
    {
        public readonly TaskCompletionSource<bool> Stop = new TaskCompletionSource<bool>();
        public int Stops;
        public bool IsConnected => Stops == 0;
        public string TransportName => "async-stop";
        public TransportState State => TransportState.Connected(TransportName);

        public Task<bool> StartAsync() => Task.FromResult(true);

        public Task StopAsync()
        {
            Stops++;
            return Stop.Task;
        }

        public Task<bool> VerifyAsync() => Task.FromResult(IsConnected);

        public Task ReregisterToolsAsync() => Task.CompletedTask;
    }

    private static void Check(bool value, string message)
    {
        if (!value)
            throw new Exception(message);
    }

    private static async Task Main(string[] args)
    {
        if (args.Length != 0 && args[0] != "retirement")
        {
            await ConnectionLifecycleHarness.RunAsync(args[0]);
            Console.WriteLine("PASS manager and locator " + args[0]);
            return;
        }
        var client = new Client();
        var old = new TransportManager();
        old.Configure(() => client, () => client);
        MCPServiceLocator.Register(old);
        Task<bool> start = old.StartAsync(TransportMode.Http);
        old.GetState(TransportMode.Stdio);
        client.PendingVerify = new TaskCompletionSource<bool>();
        Task<bool> verification = old.VerifyAsync(TransportMode.Http);
        MCPServiceLocator.Register(old);
        Check(client.Disposals == 0, "Same-manager registration must preserve ownership.");
        var next = new TransportManager();
        var nextClient = new Client();
        next.Configure(() => nextClient, () => nextClient);
        MCPServiceLocator.Register(next);
        Check(client.Disposals == 1 && !client.IsConnected, "Replacement must retire its previous owner exactly once.");
        Check(!old.HasConnectionIntent(TransportMode.Http), "Replacement must retire pending start intent.");
        Check(!old.GetState(TransportMode.Stdio).IsConnected, "Retired status queries must not create another client.");
        try
        {
            old.StartAsync(TransportMode.Http);
            throw new Exception("Retired manager accepted a new start.");
        }
        catch (ObjectDisposedException) { }
        client.Start.SetResult(true);
        Check(!await start, "Late completion revived a retired manager.");
        client.PendingVerify.SetResult(true);
        Check(!await verification && !old.IsRunning(TransportMode.Http), "Late verification revived a retired manager.");
        next.GetState(TransportMode.Stdio);
        MCPServiceLocator.Reset();
        MCPServiceLocator.Reset();
        Check(nextClient.Disposals == 1 && MCPServiceLocator.ExistingTransportManager == null, "Reset must retire once and clear ownership.");

        var asyncClient = new AsyncStopClient();
        var asyncManager = new TransportManager();
        asyncManager.Configure(() => asyncClient, () => asyncClient);
        await asyncManager.StartAsync(TransportMode.Http);
        MCPServiceLocator.Register(asyncManager);
        MCPServiceLocator.Register(new TransportManager());
        Check(asyncClient.Stops == 1 && !asyncClient.Stop.Task.IsCompleted, "Replacement must initiate async stop without blocking.");
        Check(!asyncManager.IsRunning(TransportMode.Http), "Retired manager must remain disconnected while cleanup is pending.");
        asyncClient.Stop.SetResult(true);
        MCPServiceLocator.Reset();
        Console.WriteLine("PASS manager and locator retirement");
    }
}

namespace MCPForUnity.Editor.Helpers
{
    internal static class McpLog
    {
        internal static void Warn(string message) => Console.Error.WriteLine(message);

        internal static void Error(string message) => Console.Error.WriteLine(message);

        internal static void Debug(string message) { }
    }

    internal static class HttpEndpointUtility
    {
        internal static string RedactUrls(string value) => value;
    }

    internal sealed class EditorConfigurationCache
    {
        internal static readonly EditorConfigurationCache Instance = new EditorConfigurationCache();
        internal event Action<string> OnConfigurationChanged;
        internal string HttpTransportScope,
            HttpRemoteBaseUrl,
            HttpBaseUrl;
        internal bool UseHttpTransport;
    }
}

namespace MCPForUnity.Editor.Services
{
    internal static class HttpAutoStartHandler
    {
        internal static void CancelPendingReconnect() { }
    }

    internal static class StdioBridgeReloadHandler
    {
        internal static void CancelPendingResume() { }
    }

    public interface IClientConfigurationService { }

    public class ClientConfigurationService : IClientConfigurationService { }

    public interface IPathResolverService { }

    public class PathResolverService : IPathResolverService { }

    public interface ITestRunnerService { }

    public class TestRunnerService : ITestRunnerService { }

    public interface IPackageUpdateService { }

    public class PackageUpdateService : IPackageUpdateService { }

    public interface IPlatformService { }

    public class PlatformService : IPlatformService { }

    public interface IToolDiscoveryService { }

    public class ToolDiscoveryService : IToolDiscoveryService { }

    public interface IToolConsentService { }

    public class ToolConsentService : IToolConsentService { }

    public interface IResourceDiscoveryService { }

    public class ResourceDiscoveryService : IResourceDiscoveryService { }

    public interface IServerManagementService { }

    public class ServerManagementService : IServerManagementService { }

    public interface IPackageDeploymentService { }

    public class PackageDeploymentService : IPackageDeploymentService { }
}

namespace MCPForUnity.Editor.Services.Transport.Transports
{
    internal static class StdioBridgeHost
    {
        internal static int GetCurrentPort() => 6400;

        internal static bool IsAutoConnectMode() => false;

        internal static void Stop() { }
    }

    public class StdioTransportClient : IMcpTransportClient
    {
        public bool IsConnected => false;
        public string TransportName => "stdio";
        public TransportState State => TransportState.Disconnected(TransportName);

        public Task<bool> StartAsync() => Task.FromResult(false);

        public Task StopAsync() => Task.CompletedTask;

        internal Task StopForReloadAsync() => Task.CompletedTask;

        public Task<bool> VerifyAsync() => Task.FromResult(false);

        public Task ReregisterToolsAsync() => Task.CompletedTask;
    }

    public class WebSocketTransportClient : StdioTransportClient
    {
        public WebSocketTransportClient(IToolDiscoveryService tools) { }

        public void ForceStop() { }
    }
}
