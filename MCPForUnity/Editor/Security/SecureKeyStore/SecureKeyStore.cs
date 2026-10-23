using System;
using UnityEngine;

namespace MCPForUnity.Editor.Security
{
    /// <summary>
    /// Entry point for at-rest provider key storage. Selects the best OS secure store for
    /// the current platform and layers the read-only environment-variable override on top.
    /// Keys never transit the MCP bridge; only the C# editor side reads them, at call time.
    /// </summary>
    public static class SecureKeyStore
    {
        private static ISecureKeyStore _current;

        public static ISecureKeyStore Current => _current ??= Build();

        /// <summary>Test seam: substitute an in-memory or temp-dir store.</summary>
        internal static void OverrideForTests(ISecureKeyStore store) => _current = store;

        /// <summary>Test seam: clear the cached instance.</summary>
        internal static void ResetForTests() => _current = null;

        private static ISecureKeyStore Build() => new EnvOverlayKeyStore(SelectPlatform());

        private static ISecureKeyStore SelectPlatform()
        {
            switch (Application.platform)
            {
                case RuntimePlatform.OSXEditor:
                    return new MacKeychainKeyStore();
                case RuntimePlatform.WindowsEditor:
                    return new WindowsCredentialKeyStore();
                case RuntimePlatform.LinuxEditor:
                    return LinuxSecretToolKeyStore.IsAvailable() ? (ISecureKeyStore)new LinuxSecretToolKeyStore() : new EncryptedFileKeyStore();
                default:
                    return new EncryptedFileKeyStore();
            }
        }
    }

    /// <summary>
    /// Wraps a platform store so an <c>MCPFORUNITY_&lt;PROVIDER&gt;_API_KEY</c> environment
    /// variable takes precedence on reads. Writes always go to the underlying store.
    /// </summary>
    internal sealed class EnvOverlayKeyStore : ISecureKeyStore
    {
        private readonly ISecureKeyStore _inner;
        private readonly Func<string, string> environmentLookup;

        public EnvOverlayKeyStore(ISecureKeyStore inner, Func<string, string> environmentLookup = null)
        {
            _inner = inner;
            this.environmentLookup = environmentLookup ?? Environment.GetEnvironmentVariable;
        }

        private bool TryEnvironment(string providerId, out string key)
        {
            key = string.IsNullOrEmpty(providerId) ? null : environmentLookup(EnvKeyOverride.EnvVarName(providerId));
            return !string.IsNullOrEmpty(key);
        }

        internal bool HasStoredKey(string providerId) => _inner.Has(providerId);

        internal bool HasEnvironmentKey(string providerId) => TryEnvironment(providerId, out _);

        public bool TryGet(string providerId, out string apiKey)
        {
            if (TryEnvironment(providerId, out apiKey))
                return true;
            return _inner.TryGet(providerId, out apiKey);
        }

        public bool Has(string providerId) => HasEnvironmentKey(providerId) || HasStoredKey(providerId);

        public void Set(string providerId, string apiKey) => _inner.Set(providerId, apiKey);

        public void Delete(string providerId) => _inner.Delete(providerId);
    }
}
