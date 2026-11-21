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
    public class ClientConfigurationService : IClientConfigurationService, IClientConfigurationRetryService
    {
        private readonly List<IMcpClientConfigurator> configurators;
        private readonly Func<bool> getUseHttpTransport;
        private readonly Action<bool> setUseHttpTransport;
        private readonly Action prepareLocalServer;
        private readonly Action<string> logTransportChange;
        private readonly object retryOwner = new object();
        private ClientConfigurationAttempt currentAttempt;

        public ClientConfigurationService()
            : this(
                McpClientRegistry.All,
                () => EditorConfigurationCache.Instance.UseHttpTransport,
                value => EditorConfigurationCache.Instance.SetUseHttpTransport(value),
                () => { },
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
            DiscardConfigurationAttempt();
            prepareLocalServer();
            ConfigureWithTransportCoercion(configurator);
        }

        public ClientConfigurationSummary ConfigureAllDetectedClients()
        {
            DiscardConfigurationAttempt();
            prepareLocalServer();
            var summary = new ClientConfigurationSummary();
            var catalog = CaptureCatalog(out string unavailable);
            var failed = new List<ClientConfigurationTarget>();
            var rows = new List<ClientConfigurationFailure>();
            for (int i = 0; i < configurators.Count; i++)
            {
                var target = catalog?[i];
                var failure = ConfigureDetected(configurators[i], target, summary);
                if (failure != null)
                    RetainFailure(target, failure, failed, rows);
            }
            if (summary.FailureCount > ClientConfigurationAttempt.MaxFailures)
                unavailable = "snapshot_limit";
            summary.Attempt = PublishAttempt(catalog, failed, rows, summary.FailureCount, unavailable);
            return summary;
        }

        public void DiscardConfigurationAttempt() => currentAttempt = null;

        public ClientConfigurationRetryResult RetryFailedClients(ClientConfigurationAttempt attempt)
        {
            var summary = new ClientConfigurationSummary();
            if (attempt == null || !ReferenceEquals(attempt, currentAttempt) || !attempt.OwnedBy(retryOwner))
                return new ClientConfigurationRetryResult("stale_attempt", summary, null);
            if (!attempt.CanRetry)
            {
                if (attempt.UnavailableReason != null)
                    return new ClientConfigurationRetryResult(attempt.UnavailableReason, summary, null);
                return new ClientConfigurationRetryResult(
                    attempt.RemainingFailureCount == 0 ? "no_failures" : "manual_verification_required",
                    summary,
                    attempt.RemainingFailureCount == 0 ? null : attempt
                );
            }
            if (!attempt.MatchesCatalog(configurators))
            {
                DiscardConfigurationAttempt();
                return new ClientConfigurationRetryResult("catalog_changed", summary, null);
            }

            // The old token becomes stale before any potentially mutating preparation or client callback.
            DiscardConfigurationAttempt();
            var catalog = attempt.CopyCatalog();
            var failed = new List<ClientConfigurationTarget>();
            var rows = new List<ClientConfigurationFailure>();
            try
            {
                prepareLocalServer();
            }
            catch (Exception)
            {
                failed.AddRange(attempt.FailedTargets);
                rows.AddRange(attempt.Failures);
                summary.Attempt = PublishAttempt(catalog, failed, rows, attempt.RemainingFailureCount, null);
                return new ClientConfigurationRetryResult("preparation_failed", summary, summary.Attempt);
            }

            rows.AddRange(attempt.Failures.Where(failure => !failure.CanRetry));
            foreach (var target in attempt.FailedTargets)
            {
                var failure = ConfigureDetected(target.Client, target, summary, retrying: true);
                if (failure != null)
                    RetainFailure(target, failure, failed, rows);
            }
            summary.Attempt = PublishAttempt(catalog, failed, rows, rows.Count, null);
            return new ClientConfigurationRetryResult("completed", summary, summary.Attempt);
        }

        private ClientConfigurationFailure ConfigureDetected(
            IMcpClientConfigurator configurator,
            ClientConfigurationTarget target,
            ClientConfigurationSummary summary,
            bool retrying = false
        )
        {
            string clientId = target?.Id ?? string.Empty;
            string stage = "detection";
            bool requiresStdio;
            try
            {
                if (!configurator.SupportsAutoConfigure)
                {
                    summary.SkippedCount++;
                    return null;
                }
                if (!configurator.IsInstalled)
                {
                    summary.SkippedCount++;
                    return retrying ? new ClientConfigurationFailure(clientId, SafeDisplay(configurator), "unavailable:not_installed") : null;
                }
                stage = "status";
                configurator.CheckStatus(attemptAutoRewrite: false);
                stage = "configure";
                requiresStdio = ConfigureWithTransportCoercion(configurator);
                stage = "validation";
                var status = configurator.Status;
                if (!IsConfigurationComplete(status))
                {
                    summary.FailureCount++;
                    string display = SafeDisplay(configurator);
                    summary.Messages.Add(
                        $"⚠ {display}: Configuration validation is incomplete. Check connection settings and client instructions, then retry."
                    );
                    return new ClientConfigurationFailure(clientId, display, "validation:" + status);
                }
            }
            catch (Exception ex)
            {
                summary.FailureCount++;
                string display = SafeDisplay(configurator);
                summary.Messages.Add($"⚠ {display}: {ex.Message}");
                // Claude failures supply bounded, credential-free operation and recovery guidance.
                var explicitFailure = ex as ClaudeCliMcpConfigurator.ConfigurationFailureException;
                return new ClientConfigurationFailure(
                    clientId,
                    display,
                    stage + ":" + ex.GetType().Name,
                    explicitFailure?.CanRetry ?? true,
                    explicitFailure?.Message
                );
            }
            // Presentation metadata cannot turn a completed configuration into a failed retry target.
            summary.SuccessCount++;
            summary.Messages.Add(
                requiresStdio
                    ? $"✓ {SafeDisplay(configurator)}: Configured for stdio. Select this client and choose “Switch to stdio and connect” to use its bridge."
                    : $"✓ {SafeDisplay(configurator)}: Configured successfully"
            );
            return null;
        }

        // Configure may save an entry without validating it. Consume the provider's
        // published status without starting another probe or changing its lifecycle.
        internal static bool IsConfigurationComplete(McpStatus status) =>
            status == McpStatus.Configured || status == McpStatus.Running || status == McpStatus.Connected;

        private ClientConfigurationTarget[] CaptureCatalog(out string unavailable)
        {
            unavailable = null;
            if (configurators.Count > ClientConfigurationAttempt.MaxCatalog)
            {
                unavailable = "catalog_limit";
                return null;
            }
            var entries = new List<ClientConfigurationTarget>();
            var ids = new HashSet<string>(StringComparer.Ordinal);
            try
            {
                foreach (var client in configurators)
                {
                    string id = client.Id;
                    if (string.IsNullOrEmpty(id) || id.Length > 128 || id.Any(char.IsControl) || !ids.Add(id))
                    {
                        unavailable = "invalid_identity";
                        return null;
                    }
                    entries.Add(new ClientConfigurationTarget(client, id));
                }
                return entries.ToArray();
            }
            catch (Exception)
            {
                unavailable = "invalid_identity";
                return null;
            }
        }

        private static string SafeDisplay(IMcpClientConfigurator client)
        {
            try
            {
                return client.DisplayName;
            }
            catch (Exception)
            {
                return "Client";
            }
        }

        private static void RetainFailure(
            ClientConfigurationTarget target,
            ClientConfigurationFailure failure,
            List<ClientConfigurationTarget> failed,
            List<ClientConfigurationFailure> rows
        )
        {
            if (rows.Count == ClientConfigurationAttempt.MaxFailures)
                return;
            if (target != null && failure.CanRetry)
                failed.Add(target);
            rows.Add(failure);
        }

        private ClientConfigurationAttempt PublishAttempt(
            ClientConfigurationTarget[] catalog,
            List<ClientConfigurationTarget> failures,
            List<ClientConfigurationFailure> rows,
            int remaining,
            string unavailable
        )
        {
            currentAttempt = new ClientConfigurationAttempt(retryOwner, catalog, failures, rows, remaining, unavailable);
            return currentAttempt;
        }

        private bool ConfigureWithTransportCoercion(IMcpClientConfigurator configurator)
        {
            bool originalHttp = getUseHttpTransport();
            bool requiresStdio = originalHttp && RequiresStdioBridge(configurator.SupportedTransports);
            if (requiresStdio && configurator is JsonFileMcpConfigurator json && !json.Client.SupportsHttpTransport)
            {
                // The JSON writer already selects stdio from the descriptor. Changing the Editor
                // preference, even temporarily, explicitly retires the user's live HTTP bridge.
                configurator.Configure();
                return true;
            }
            try
            {
                CoerceTransportFor(configurator);
                configurator.Configure();
                return requiresStdio;
            }
            finally
            {
                setUseHttpTransport(originalHttp);
            }
        }

        internal static bool RequiresStdioBridge(IReadOnlyList<ConfiguredTransport> supported) =>
            supported != null && supported.Contains(ConfiguredTransport.Stdio) && !SupportsRequested(supported, ConfiguredTransport.Http);

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
