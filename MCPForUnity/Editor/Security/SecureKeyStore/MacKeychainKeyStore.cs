using System;
using System.Diagnostics;

namespace MCPForUnity.Editor.Security
{
    /// <summary>
    /// macOS Keychain-backed key store via /usr/bin/security generic passwords.
    /// Service = MCPForUnity.AssetGen, account = provider id.
    /// </summary>
    internal sealed class MacKeychainKeyStore : ISecureKeyStore
    {
        private const string Security = "/usr/bin/security";
        private const string Service = SecureKeyStoreConstants.ServiceName;
        private readonly Func<string[], int> deleteCommand;

        internal MacKeychainKeyStore(Func<string[], int> deleteCommand = null) => this.deleteCommand = deleteCommand ?? (args => Run(args).code);

        public bool Has(string providerId) => TryGet(providerId, out _);

        public bool TryGet(string providerId, out string apiKey)
        {
            apiKey = null;
            if (string.IsNullOrEmpty(providerId))
                return false;
            (int code, string stdout, _) = Run("find-generic-password", "-s", Service, "-a", providerId, "-w");
            if (code != 0)
                return false;
            apiKey = (stdout ?? string.Empty).TrimEnd('\n', '\r');
            return !string.IsNullOrEmpty(apiKey);
        }

        public void Set(string providerId, string apiKey)
        {
            if (string.IsNullOrEmpty(providerId))
                return;
            if (string.IsNullOrEmpty(apiKey))
            {
                Delete(providerId);
                return;
            }
            // -U overwrites an existing item for this service/account.
            Run("add-generic-password", "-U", "-s", Service, "-a", providerId, "-w", apiKey);
        }

        public void Delete(string providerId)
        {
            if (string.IsNullOrEmpty(providerId))
                return;
            if (deleteCommand(new[] { "delete-generic-password", "-s", Service, "-a", providerId }) != 0)
                throw new InvalidOperationException("Credential removal could not be confirmed. The key may already be absent or Keychain access unavailable.");
        }

        private static (int code, string stdout, string stderr) Run(params string[] args)
        {
            try
            {
                var psi = new ProcessStartInfo(Security)
                {
                    UseShellExecute = false,
                    CreateNoWindow = true,
                    RedirectStandardOutput = true,
                    RedirectStandardError = true,
                };
                foreach (string a in args)
                    psi.ArgumentList.Add(a);
                return KeyStoreProcess.Run(psi);
            }
            catch (Exception e)
            {
                return (-1, null, e.Message);
            }
        }
    }
}
