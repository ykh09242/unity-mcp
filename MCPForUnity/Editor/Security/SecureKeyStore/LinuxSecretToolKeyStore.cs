using System;
using System.Diagnostics;

namespace MCPForUnity.Editor.Security
{
    /// <summary>
    /// Linux libsecret-backed key store via the `secret-tool` CLI. The secret value is
    /// passed on stdin (never as a process argument). Falls back to
    /// <see cref="EncryptedFileKeyStore"/> when secret-tool is not installed.
    /// </summary>
    internal sealed class LinuxSecretToolKeyStore : ISecureKeyStore
    {
        private const string Service = SecureKeyStoreConstants.ServiceName;

        internal static bool IsAvailable()
        {
            try
            {
                var psi = NewPsi();
                psi.ArgumentList.Add("--version");
                return KeyStoreProcess.Run(psi, timeoutMs: 2000).code == 0;
            }
            catch
            {
                return false;
            }
        }

        public bool Has(string providerId) => TryGet(providerId, out _);

        public bool TryGet(string providerId, out string apiKey)
        {
            apiKey = null;
            if (string.IsNullOrEmpty(providerId))
                return false;
            try
            {
                var psi = NewPsi();
                psi.ArgumentList.Add("lookup");
                psi.ArgumentList.Add("service");
                psi.ArgumentList.Add(Service);
                psi.ArgumentList.Add("account");
                psi.ArgumentList.Add(providerId);
                var result = KeyStoreProcess.Run(psi);
                if (result.code != 0)
                    return false;
                apiKey = (result.stdout ?? string.Empty).TrimEnd('\n', '\r');
                return !string.IsNullOrEmpty(apiKey);
            }
            catch
            {
                return false;
            }
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
            try
            {
                var psi = NewPsi(redirectIn: true);
                psi.ArgumentList.Add("store");
                psi.ArgumentList.Add("--label=MCPForUnity AssetGen");
                psi.ArgumentList.Add("service");
                psi.ArgumentList.Add(Service);
                psi.ArgumentList.Add("account");
                psi.ArgumentList.Add(providerId);
                KeyStoreProcess.Run(psi, apiKey);
            }
            catch { /* best effort */ }
        }

        public void Delete(string providerId)
        {
            if (string.IsNullOrEmpty(providerId))
                return;
            try
            {
                var psi = NewPsi();
                psi.ArgumentList.Add("clear");
                psi.ArgumentList.Add("service");
                psi.ArgumentList.Add(Service);
                psi.ArgumentList.Add("account");
                psi.ArgumentList.Add(providerId);
                KeyStoreProcess.Run(psi);
            }
            catch { /* best effort */ }
        }

        private static ProcessStartInfo NewPsi(bool redirectIn = false)
        {
            var psi = new ProcessStartInfo("/usr/bin/env")
            {
                UseShellExecute = false,
                CreateNoWindow = true,
                RedirectStandardOutput = true,
                RedirectStandardError = true,
                RedirectStandardInput = redirectIn,
            };
            psi.ArgumentList.Add("secret-tool");
            return psi;
        }
    }
}
