using System;
using System.Collections.Generic;
using System.Linq;
using MCPForUnity.Editor.Clients;

namespace MCPForUnity.Editor.Services
{
    /// <summary>Bounded, domain-local evidence from one configuration operation. Contains no saved settings or file contents.</summary>
    public sealed class ClientConfigurationAttempt
    {
        internal const int MaxCatalog = 512;
        internal const int MaxFailures = 128;
        private readonly object owner;
        private readonly ClientConfigurationTarget[] catalog;
        internal readonly ClientConfigurationTarget[] FailedTargets;
        public IReadOnlyList<ClientConfigurationFailure> Failures { get; }
        public int RemainingFailureCount { get; }
        public string UnavailableReason { get; }
        public bool CanRetry => RemainingFailureCount > 0 && string.IsNullOrEmpty(UnavailableReason);

        internal ClientConfigurationAttempt(
            object owner,
            ClientConfigurationTarget[] catalog,
            List<ClientConfigurationTarget> failures,
            List<ClientConfigurationFailure> rows,
            int remaining,
            string unavailableReason
        )
        {
            this.owner = owner;
            this.catalog = catalog ?? Array.Empty<ClientConfigurationTarget>();
            FailedTargets = failures.ToArray();
            Failures = Array.AsReadOnly(rows.ToArray());
            RemainingFailureCount = remaining;
            UnavailableReason = unavailableReason;
        }

        internal bool OwnedBy(object candidate) => ReferenceEquals(owner, candidate);

        internal bool MatchesCatalog(IReadOnlyList<IMcpClientConfigurator> current)
        {
            if (current.Count != catalog.Length)
                return false;
            try
            {
                for (int i = 0; i < catalog.Length; i++)
                    if (!ReferenceEquals(current[i], catalog[i].Client) || !string.Equals(current[i].Id, catalog[i].Id, StringComparison.Ordinal))
                        return false;
                return true;
            }
            catch (Exception)
            {
                return false;
            }
        }

        internal ClientConfigurationTarget[] CopyCatalog() => (ClientConfigurationTarget[])catalog.Clone();

        internal static string BoundedDisplay(string value, int limit)
        {
            if (string.IsNullOrEmpty(value))
                return string.Empty;
            return new string(value.Where(c => !char.IsControl(c)).Take(limit).ToArray());
        }
    }

    public sealed class ClientConfigurationFailure
    {
        public string ClientId { get; }
        public string DisplayName { get; }
        public string FailureCode { get; }

        internal ClientConfigurationFailure(string clientId, string displayName, string failureCode)
        {
            ClientId = clientId;
            DisplayName = ClientConfigurationAttempt.BoundedDisplay(displayName, 128);
            FailureCode = ClientConfigurationAttempt.BoundedDisplay(failureCode, 80);
        }
    }

    public sealed class ClientConfigurationRetryResult
    {
        public string Disposition { get; }
        public ClientConfigurationSummary Summary { get; }
        public ClientConfigurationAttempt Attempt { get; }

        internal ClientConfigurationRetryResult(string disposition, ClientConfigurationSummary summary, ClientConfigurationAttempt attempt)
        {
            Disposition = disposition;
            Summary = summary;
            Attempt = attempt;
        }
    }

    internal sealed class ClientConfigurationTarget
    {
        internal IMcpClientConfigurator Client { get; }
        internal string Id { get; }

        internal ClientConfigurationTarget(IMcpClientConfigurator client, string id)
        {
            Client = client;
            Id = id;
        }
    }
}
