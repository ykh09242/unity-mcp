using System;
using System.Collections.Generic;
using System.Linq;
using MCPForUnity.Editor.Clients;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Editor.Models;

namespace MCPForUnity.Editor.Services
{
    /// <summary>
    /// Implementation of client configuration service
    /// </summary>
    public class ClientConfigurationService : IClientConfigurationService
    {
        private readonly List<IMcpClientConfigurator> configurators;
        private readonly Func<bool> getUseHttpTransport;
        private readonly Action<bool> setUseHttpTransport;
        private readonly Action prepareLocalServer;
        private readonly Action<string> logTransportChange;

        public ClientConfigurationService()
            : this(
                McpClientRegistry.All,
                () => EditorConfigurationCache.Instance.UseHttpTransport,
                value => EditorConfigurationCache.Instance.SetUseHttpTransport(value),
                PrepareLocalServer,
                message => McpLog.Info(message)
            ) { }

        internal ClientConfigurationService(
            IEnumerable<IMcpClientConfigurator> configurators,
            Func<bool> getUseHttpTransport,
            Action<bool> setUseHttpTransport,
            Action prepareLocalServer,
            Action<string> logTransportChange
        )
        {
            this.configurators = configurators.ToList();
            this.getUseHttpTransport = getUseHttpTransport;
            this.setUseHttpTransport = setUseHttpTransport;
            this.prepareLocalServer = prepareLocalServer;
            this.logTransportChange = logTransportChange;
        }

        public IReadOnlyList<IMcpClientConfigurator> GetAllClients() => configurators;

        public void ConfigureClient(IMcpClientConfigurator configurator)
        {
            prepareLocalServer();
            ConfigureWithTransportCoercion(configurator);
        }

        public ClientConfigurationSummary ConfigureAllDetectedClients()
        {
            prepareLocalServer();

            var summary = new ClientConfigurationSummary();
            foreach (var configurator in configurators)
            {
                if (!configurator.IsInstalled)
                {
                    summary.SkippedCount++;
                    continue;
                }
                try
                {
                    // Always re-run configuration so core fields stay current
                    configurator.CheckStatus(attemptAutoRewrite: false);
                    ConfigureWithTransportCoercion(configurator);
                    summary.SuccessCount++;
                    summary.Messages.Add($"✓ {configurator.DisplayName}: Configured successfully");
                }
                catch (Exception ex)
                {
                    summary.FailureCount++;
                    summary.Messages.Add($"⚠ {configurator.DisplayName}: {ex.Message}");
                }
            }

            return summary;
        }

        private static void PrepareLocalServer()
        {
            // Clean stale build artifacts so deleted modules cannot be auto-discovered.
            if (AssetPathUtility.IsLocalServerPath())
                AssetPathUtility.CleanLocalServerBuildArtifacts();
        }

        private void ConfigureWithTransportCoercion(IMcpClientConfigurator configurator)
        {
            bool originalHttp = getUseHttpTransport();
            try
            {
                CoerceTransportFor(configurator);
                configurator.Configure();
            }
            finally
            {
                setUseHttpTransport(originalHttp);
            }
        }

        public bool CheckClientStatus(IMcpClientConfigurator configurator, bool attemptAutoRewrite = true)
        {
            var previous = configurator.Status;
            var current = configurator.CheckStatus(attemptAutoRewrite);
            return current != previous;
        }

        private void CoerceTransportFor(IMcpClientConfigurator configurator)
        {
            var supported = configurator.SupportedTransports;
            if (supported == null || supported.Count == 0)
                return;

            bool currentlyHttp = getUseHttpTransport();
            var requested = currentlyHttp ? ConfiguredTransport.Http : ConfiguredTransport.Stdio;

            // Accept any HTTP variant (Http, HttpRemote) when the user wants HTTP — a client that
            // only supports HttpRemote should not get coerced to stdio just because Http isn't
            // explicitly listed.
            if (SupportsRequested(supported, requested))
                return;

            // Fall back in the direction of the user's intent: if they wanted HTTP, prefer any
            // HTTP variant the client does support; otherwise prefer stdio. Honors the
            // configurator's declared order when more than one option remains.
            ConfiguredTransport chosen = PickFallback(supported, requested);
            bool needHttp = IsHttpVariant(chosen);
            if (getUseHttpTransport() != needHttp)
            {
                setUseHttpTransport(needHttp);
                logTransportChange($"[{configurator.DisplayName}] auto-selected {chosen} transport (client does not support {requested}).");
            }
        }

        private static bool IsHttpVariant(ConfiguredTransport t) => t == ConfiguredTransport.Http || t == ConfiguredTransport.HttpRemote;

        private static bool SupportsRequested(IReadOnlyList<ConfiguredTransport> supported, ConfiguredTransport requested)
        {
            if (requested == ConfiguredTransport.Http)
            {
                foreach (var t in supported)
                    if (IsHttpVariant(t))
                        return true;
                return false;
            }
            return supported.Contains(requested);
        }

        private static ConfiguredTransport PickFallback(IReadOnlyList<ConfiguredTransport> supported, ConfiguredTransport requested)
        {
            if (requested == ConfiguredTransport.Http)
            {
                foreach (var t in supported)
                    if (IsHttpVariant(t))
                        return t;
            }
            else
            {
                foreach (var t in supported)
                    if (t == ConfiguredTransport.Stdio)
                        return t;
            }
            return supported[0];
        }
    }
}
