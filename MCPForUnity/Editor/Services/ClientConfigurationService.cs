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
            foreach (var configurator in configurators)
            {
                string code = ConfigureDetected(configurator, summary);
                if (code != null)
                {
                    var target = catalog?.FirstOrDefault(entry => ReferenceEquals(entry.Client, configurator));
                    RetainFailure(target, configurator, code, failed, rows);
                }
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
                return new ClientConfigurationRetryResult(
                    string.IsNullOrEmpty(attempt.UnavailableReason) ? "no_failures" : attempt.UnavailableReason,
                    summary,
                    null
                );
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

            foreach (var target in attempt.FailedTargets)
            {
                int skippedBefore = summary.SkippedCount;
                string code = ConfigureDetected(target.Client, summary);
                if (code != null || summary.SkippedCount != skippedBefore)
                    RetainFailure(target, target.Client, code ?? "unavailable:not_installed", failed, rows);
            }
            summary.Attempt = PublishAttempt(catalog, failed, rows, failed.Count, null);
            return new ClientConfigurationRetryResult("completed", summary, summary.Attempt);
        }

        private string ConfigureDetected(IMcpClientConfigurator configurator, ClientConfigurationSummary summary)
        {
            string stage = "detection";
            try
            {
                if (!configurator.IsInstalled)
                {
                    summary.SkippedCount++;
                    return null;
                }
                stage = "status";
                configurator.CheckStatus(attemptAutoRewrite: false);
                stage = "configure";
                ConfigureWithTransportCoercion(configurator);
            }
            catch (Exception ex)
            {
                summary.FailureCount++;
                summary.Messages.Add($"⚠ {SafeDisplay(configurator)}: {ex.Message}");
                return stage + ":" + ex.GetType().Name;
            }
            // Presentation metadata cannot turn a completed configuration into a failed retry target.
            summary.SuccessCount++;
            summary.Messages.Add($"✓ {SafeDisplay(configurator)}: Configured successfully");
            return null;
        }

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
            IMcpClientConfigurator client,
            string code,
            List<ClientConfigurationTarget> failed,
            List<ClientConfigurationFailure> rows
        )
        {
            if (rows.Count == ClientConfigurationAttempt.MaxFailures)
                return;
            if (target != null)
                failed.Add(target);
            rows.Add(new ClientConfigurationFailure(target?.Id ?? string.Empty, SafeDisplay(client), code));
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
