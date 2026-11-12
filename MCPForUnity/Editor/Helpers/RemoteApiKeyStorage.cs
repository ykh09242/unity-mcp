using System;
using System.Security.Cryptography;
using System.Text;
using MCPForUnity.Editor.Constants;
using MCPForUnity.Editor.Security;

namespace MCPForUnity.Editor.Helpers
{
    internal sealed class RemoteApiKeyStorageException : InvalidOperationException
    {
        internal RemoteApiKeyStorageException()
            : base("Remote API key storage failed. Restore access to the platform secure store and retry. The operation was not confirmed.")
        { }
    }

    /// <summary>Origin-bound secure persistence and lossless migration of legacy EditorPrefs credentials.</summary>
    internal sealed class RemoteApiKeyStorage
    {
        private readonly Func<ISecureKeyStore> secureStore;
        private readonly Func<string, string> readPreference;
        private readonly Action<string> deletePreference;

        internal RemoteApiKeyStorage(Func<ISecureKeyStore> secureStore, Func<string, string> readPreference, Action<string> deletePreference)
        {
            this.secureStore = secureStore;
            this.readPreference = readPreference;
            this.deletePreference = deletePreference;
        }

        internal bool HasLegacyApiKey => !string.IsNullOrEmpty(readPreference(EditorPrefKeys.ApiKey));

        internal static string Identifier(string origin)
        {
            if (string.IsNullOrEmpty(origin))
                throw new ArgumentException("A normalized remote origin is required.");
            using var hash = SHA256.Create();
            return "remote-http-" + BitConverter.ToString(hash.ComputeHash(Encoding.UTF8.GetBytes(origin))).Replace("-", string.Empty).ToLowerInvariant();
        }

        internal string Get(string origin)
        {
            string identifier = Identifier(origin);
            string preference = EditorPrefKeys.RemoteApiKeyPrefix + origin;
            try
            {
                if (secureStore().TryGet(identifier, out string key) && !string.IsNullOrEmpty(key))
                {
                    // A confirmed stored value takes precedence over stale plaintext.
                    deletePreference(preference);
                    return key;
                }
                string legacy = readPreference(preference);
                if (string.IsNullOrEmpty(legacy))
                    return string.Empty;
                Save(origin, legacy);
                return legacy;
            }
            catch
            {
                // Backend errors may contain command output or credentials. Do not propagate them.
                throw new RemoteApiKeyStorageException();
            }
        }

        internal void Save(string origin, string apiKey)
        {
            string identifier = Identifier(origin);
            try
            {
                var store = secureStore();
                if (string.IsNullOrEmpty(apiKey))
                    store.Delete(identifier);
                else
                    store.Set(identifier, apiKey);
                // Never discard the recovery value before secure persistence/removal succeeds.
                deletePreference(EditorPrefKeys.RemoteApiKeyPrefix + origin);
            }
            catch
            {
                throw new RemoteApiKeyStorageException();
            }
        }

        internal bool BindLegacy(string approvedOrigin)
        {
            try
            {
                string legacy = readPreference(EditorPrefKeys.ApiKey);
                if (string.IsNullOrEmpty(legacy))
                    return false;
                Save(approvedOrigin, legacy);
                deletePreference(EditorPrefKeys.ApiKey);
                return true;
            }
            catch
            {
                throw new RemoteApiKeyStorageException();
            }
        }

        internal void ClearLegacyApiKey()
        {
            try
            {
                deletePreference(EditorPrefKeys.ApiKey);
            }
            catch
            {
                throw new RemoteApiKeyStorageException();
            }
        }
    }
}
