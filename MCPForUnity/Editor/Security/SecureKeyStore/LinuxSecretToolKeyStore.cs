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
        private readonly Func<ProcessStartInfo, int> deleteCommand;
        private readonly Func<ProcessStartInfo, string, (int code, string stdout, string stderr)> runCommand;

        internal LinuxSecretToolKeyStore(Func<ProcessStartInfo, int> deleteCommand = null)
            : this(deleteCommand, null) { }

        internal LinuxSecretToolKeyStore(
            Func<ProcessStartInfo, int> deleteCommand,
            Func<ProcessStartInfo, string, (int code, string stdout, string stderr)> runCommand
        )
        {
            this.runCommand = runCommand ?? ((info, input) => KeyStoreProcess.Run(info, input));
            this.deleteCommand = deleteCommand;
        }

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
                var result = Execute(psi);
                if (IsConfirmedAbsence(result))
                    return false;
                if (result.code != 0)
                    throw new InvalidOperationException();
                apiKey = (result.stdout ?? string.Empty).TrimEnd('\n', '\r');
                return !string.IsNullOrEmpty(apiKey);
            }
            catch
            {
                throw new InvalidOperationException("Credential lookup could not be confirmed. Check secret-service access and retry.");
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
            var psi = NewPsi(redirectIn: true);
            psi.ArgumentList.Add("store");
            psi.ArgumentList.Add("--label=MCPForUnity AssetGen");
            psi.ArgumentList.Add("service");
            psi.ArgumentList.Add(Service);
            psi.ArgumentList.Add("account");
            psi.ArgumentList.Add(providerId);
            if (Execute(psi, apiKey).code != 0)
                throw new InvalidOperationException("Credential storage could not be confirmed. Check secret-service access and retry.");
        }

        public void Delete(string providerId)
        {
            if (string.IsNullOrEmpty(providerId))
                return;
            var psi = NewPsi();
            psi.ArgumentList.Add("clear");
            psi.ArgumentList.Add("service");
            psi.ArgumentList.Add(Service);
            psi.ArgumentList.Add("account");
            psi.ArgumentList.Add(providerId);
            try
            {
                // The legacy delete-only seam has no stderr, so only zero confirms success.
                var result = deleteCommand == null ? Execute(psi) : (code: deleteCommand(psi), stdout: (string)null, stderr: "unconfirmed");
                if (result.code != 0 && !IsConfirmedAbsence(result))
                    throw new InvalidOperationException();
            }
            catch
            {
                throw new InvalidOperationException("Credential removal could not be confirmed. Check secret-service access and retry.");
            }
        }

        private static bool IsConfirmedAbsence((int code, string stdout, string stderr) result) =>
            // secret-tool uses 1 for both no match and failure; failures emit stderr.
            result.code == 1
            && string.IsNullOrEmpty(result.stdout)
            && string.IsNullOrEmpty(result.stderr);

        private (int code, string stdout, string stderr) Execute(ProcessStartInfo info, string input = null)
        {
            try
            {
                return runCommand(info, input);
            }
            catch
            {
                throw new InvalidOperationException("Credential operation could not be confirmed. Check secret-service access and retry.");
            }
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
