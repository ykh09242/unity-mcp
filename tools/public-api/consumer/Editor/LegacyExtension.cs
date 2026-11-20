using System;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Editor.Services;
using MCPForUnity.Editor.Services.Server;

namespace IndependentExtension
{
    public sealed class LegacyExtension : IProcessTerminator
    {
        public bool Terminate(int pid) => false;

        public ServerManagementService CreateService(IProcessDetector detector) =>
            new ServerManagementService(processDetector: detector, processTerminator: this);

        public Func<bool> StopHandler(ServerManagementService service) => service.StopManagedLocalHttpServer;

        // Existing extensions are allowed to reference the legacy helpers. Bind the
        // old calls without touching the filesystem or terminating any process.
#pragma warning disable CS0618
        public Func<int, bool> BindTerminator(IProcessDetector detector) => new ProcessTerminator(detector).Terminate;

        public static bool BindLegacyStore()
        {
            Action<string, string> save = McpJobStateStore.SaveState<string>;
            Func<string, string> load = McpJobStateStore.LoadState<string>;
            Action<string> clear = McpJobStateStore.ClearState;
            return save != null && load != null && clear != null;
        }
#pragma warning restore CS0618
    }
}
