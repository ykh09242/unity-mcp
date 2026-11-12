using System;
using System.Runtime.InteropServices;
using System.Text;

namespace MCPForUnity.Editor.Security
{
    /// <summary>
    /// Windows Credential Manager-backed key store (DPAPI-protected by the OS) via advapi32
    /// Cred* APIs. Target = "MCPForUnity.AssetGen:&lt;provider&gt;", blob = UTF-8 key bytes.
    /// </summary>
    internal sealed class WindowsCredentialKeyStore : ISecureKeyStore
    {
        private const int CRED_TYPE_GENERIC = 1;
        private const int CRED_PERSIST_LOCAL_MACHINE = 2;
        private readonly string targetNamespace;
        private readonly Func<string, int> deleteCredential;
        internal delegate bool ReadCredential(string target, out IntPtr credential, out int error);
        private readonly ReadCredential readCredential;
        private readonly Action<IntPtr> freeCredential;

        internal WindowsCredentialKeyStore(string targetNamespace = SecureKeyStoreConstants.ServiceName, Func<string, int> deleteCredential = null)
            : this(targetNamespace, deleteCredential, null, null) { }

        internal WindowsCredentialKeyStore(
            string targetNamespace,
            Func<string, int> deleteCredential,
            ReadCredential readCredential,
            Action<IntPtr> freeCredential
        )
        {
            this.targetNamespace = targetNamespace;
            this.deleteCredential = deleteCredential ?? (target => CredDelete(target, CRED_TYPE_GENERIC, 0) ? 0 : Marshal.GetLastWin32Error());
            this.readCredential = readCredential ?? ReadNative;
            this.freeCredential = freeCredential ?? CredFree;
        }

        private string Target(string providerId) => targetNamespace + ":" + providerId;

        [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)]
        internal struct CREDENTIAL
        {
            public int Flags;
            public int Type;
            public string TargetName;
            public string Comment;
            public System.Runtime.InteropServices.ComTypes.FILETIME LastWritten;
            public int CredentialBlobSize;
            public IntPtr CredentialBlob;
            public int Persist;
            public int AttributeCount;
            public IntPtr Attributes;
            public string TargetAlias;
            public string UserName;
        }

        [DllImport("advapi32.dll", SetLastError = true, CharSet = CharSet.Unicode, EntryPoint = "CredReadW")]
        private static extern bool CredRead(string target, int type, int reservedFlag, out IntPtr credentialPtr);

        [DllImport("advapi32.dll", SetLastError = true, CharSet = CharSet.Unicode, EntryPoint = "CredWriteW")]
        private static extern bool CredWrite([In] ref CREDENTIAL credential, int flags);

        [DllImport("advapi32.dll", SetLastError = true, CharSet = CharSet.Unicode, EntryPoint = "CredDeleteW")]
        private static extern bool CredDelete(string target, int type, int flags);

        [DllImport("advapi32.dll", EntryPoint = "CredFree")]
        private static extern void CredFree(IntPtr buffer);

        private static bool ReadNative(string target, out IntPtr credential, out int error)
        {
            bool found = CredRead(target, CRED_TYPE_GENERIC, 0, out credential);
            error = found ? 0 : Marshal.GetLastWin32Error();
            return found;
        }

        public bool Has(string providerId) => TryGet(providerId, out _);

        public bool TryGet(string providerId, out string apiKey)
        {
            apiKey = null;
            if (string.IsNullOrEmpty(providerId))
                return false;
            try
            {
                if (!readCredential(Target(providerId), out IntPtr ptr, out int error))
                {
                    const int ErrorNotFound = 1168;
                    if (error == ErrorNotFound)
                        return false;
                    throw new InvalidOperationException();
                }
                try
                {
                    var cred = Marshal.PtrToStructure<CREDENTIAL>(ptr);
                    if (cred.CredentialBlobSize <= 0 || cred.CredentialBlob == IntPtr.Zero)
                        return false;
                    byte[] bytes = new byte[cred.CredentialBlobSize];
                    Marshal.Copy(cred.CredentialBlob, bytes, 0, cred.CredentialBlobSize);
                    apiKey = Encoding.UTF8.GetString(bytes);
                    return !string.IsNullOrEmpty(apiKey);
                }
                finally
                {
                    // Only successful CredRead calls transfer ownership of a buffer.
                    freeCredential(ptr);
                }
            }
            catch
            {
                apiKey = null;
                throw new InvalidOperationException("Credential lookup could not be confirmed. Check secure-store access and retry.");
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
            byte[] blob = Encoding.UTF8.GetBytes(apiKey);
            IntPtr blobPtr = Marshal.AllocHGlobal(blob.Length);
            try
            {
                Marshal.Copy(blob, 0, blobPtr, blob.Length);
                var cred = new CREDENTIAL
                {
                    Type = CRED_TYPE_GENERIC,
                    TargetName = Target(providerId),
                    CredentialBlobSize = blob.Length,
                    CredentialBlob = blobPtr,
                    Persist = CRED_PERSIST_LOCAL_MACHINE,
                    UserName = providerId,
                };
                if (!CredWrite(ref cred, 0))
                {
                    throw new InvalidOperationException("CredWrite failed (Win32 " + Marshal.GetLastWin32Error() + ")");
                }
            }
            finally
            {
                Marshal.FreeHGlobal(blobPtr);
            }
        }

        public void Delete(string providerId)
        {
            if (string.IsNullOrEmpty(providerId))
                return;
            int error = deleteCredential(Target(providerId));
            const int ErrorNotFound = 1168;
            if (error != 0 && error != ErrorNotFound)
                throw new InvalidOperationException("Credential removal could not be confirmed. Check secure-store access.");
        }
    }
}
