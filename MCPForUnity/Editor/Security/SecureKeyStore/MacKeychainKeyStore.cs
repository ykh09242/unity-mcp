using System;
using System.Diagnostics;
using System.Runtime.InteropServices;
using System.Text;

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

        // security returns errSecItemNotFound (-25300), truncated to the Unix exit byte.
        private const int ItemNotFoundExitCode = 44;
        private readonly Func<string[], int> deleteCommand;
        private readonly Func<string[], (int code, string stdout, string stderr)> runCommand;
        internal delegate int NativeLookup(
            IntPtr keychains,
            uint serviceLength,
            byte[] service,
            uint accountLength,
            byte[] account,
            IntPtr passwordLength,
            IntPtr passwordData,
            IntPtr item
        );
        private readonly NativeLookup nativeLookup;

        internal MacKeychainKeyStore(Func<string[], int> deleteCommand = null)
            : this(deleteCommand, null, null) { }

        internal MacKeychainKeyStore(Func<string[], int> deleteCommand, Func<string[], (int code, string stdout, string stderr)> runCommand)
            : this(deleteCommand, runCommand, null) { }

        internal MacKeychainKeyStore(
            Func<string[], int> deleteCommand,
            Func<string[], (int code, string stdout, string stderr)> runCommand,
            NativeLookup nativeLookup
        )
        {
            this.runCommand = runCommand ?? Run;
            this.deleteCommand = deleteCommand;
            this.nativeLookup = nativeLookup;
            // Injected command seams must never unexpectedly access a real Keychain.
            if (nativeLookup == null && runCommand == null && deleteCommand == null)
                this.nativeLookup = SecKeychainFindGenericPassword;
        }

        public bool Has(string providerId) => TryGet(providerId, out _);

        public bool TryGet(string providerId, out string apiKey)
        {
            apiKey = null;
            if (string.IsNullOrEmpty(providerId))
                return false;
            var result = Execute(new[] { "find-generic-password", "-s", Service, "-a", providerId, "-w" });
            if (IsConfirmedAbsence(result, providerId))
                return false;
            if (result.code != 0)
                throw new InvalidOperationException("Credential lookup could not be confirmed. Check Keychain access and retry.");
            apiKey = (result.stdout ?? string.Empty).TrimEnd('\n', '\r');
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
            if (Execute(new[] { "add-generic-password", "-U", "-s", Service, "-a", providerId, "-w", apiKey }).code != 0)
                throw new InvalidOperationException("Credential storage could not be confirmed. Check Keychain access and retry.");
        }

        public void Delete(string providerId)
        {
            if (string.IsNullOrEmpty(providerId))
                return;
            try
            {
                var args = new[] { "delete-generic-password", "-s", Service, "-a", providerId };
                var result = deleteCommand == null ? Execute(args) : (code: deleteCommand(args), stdout: (string)null, stderr: (string)null);
                if (result.code != 0 && !IsConfirmedAbsence(result, providerId))
                    throw new InvalidOperationException();
            }
            catch
            {
                throw new InvalidOperationException("Credential removal could not be confirmed. Check Keychain access and retry.");
            }
        }

        private bool IsConfirmedAbsence((int code, string stdout, string stderr) result, string providerId)
        {
            if (result.code != ItemNotFoundExitCode || !string.IsNullOrEmpty(result.stdout))
                return false;
            // Search initialization errors add diagnostics before the CLI's misleading
            // final item-not-found line. Unknown/localized diagnostics fail closed too.
            const string notFoundDiagnostic = "security: SecKeychainSearchCopyNext: The specified item could not be found in the keychain.";
            if (!string.IsNullOrEmpty(result.stderr) && result.stderr.Trim() != notFoundDiagnostic)
                return false;
            try
            {
                if (nativeLookup == null)
                    throw new InvalidOperationException();
                byte[] service = Encoding.UTF8.GetBytes(Service);
                byte[] account = Encoding.UTF8.GetBytes(providerId);
                // CLI 44 also hides SearchCopyNext failures. Preserve the native OSStatus
                // and request neither password data nor an item reference, so no native
                // buffer/CF object ownership is transferred to the caller.
                int status = nativeLookup(IntPtr.Zero, (uint)service.Length, service, (uint)account.Length, account, IntPtr.Zero, IntPtr.Zero, IntPtr.Zero);
                return status == -25300; // errSecItemNotFound only; success means an item exists.
            }
            catch
            {
                throw new InvalidOperationException("Credential lookup could not be confirmed. Check Keychain access and retry.");
            }
        }

        [DllImport("/System/Library/Frameworks/Security.framework/Security")]
        private static extern int SecKeychainFindGenericPassword(
            IntPtr keychains,
            uint serviceLength,
            byte[] service,
            uint accountLength,
            byte[] account,
            IntPtr passwordLength,
            IntPtr passwordData,
            IntPtr item
        );

        private (int code, string stdout, string stderr) Execute(string[] args)
        {
            try
            {
                return runCommand(args);
            }
            catch
            {
                // No process command output or exception text may expose credentials.
                throw new InvalidOperationException("Credential lookup could not be confirmed. Check Keychain access and retry.");
            }
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
