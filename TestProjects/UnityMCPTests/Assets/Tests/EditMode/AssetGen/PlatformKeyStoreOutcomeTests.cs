using System;
using System.Runtime.InteropServices;
using System.Text;
using MCPForUnity.Editor.Constants;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Editor.Security;
using MCPForUnityTests.Editor.Helpers;
using NUnit.Framework;

namespace MCPForUnityTests.Editor.AssetGen
{
    public class PlatformKeyStoreOutcomeTests
    {
        private static ISecureKeyStore Backend(string platform, int code, string stdout, string stderr, bool startFailure = false)
        {
            // Every command path is injected; no OS store/process can be touched.
            if (platform == "windows")
                return new WindowsCredentialKeyStore(
                    "MCPForUnity.Test",
                    _ => code,
                    (string target, out IntPtr credential, out int error) =>
                    {
                        credential = IntPtr.Zero;
                        error = code;
                        if (startFailure)
                            throw new InvalidOperationException("synthetic-native-error");
                        return false;
                    },
                    _ => Assert.Fail("Failed/missing reads must not free an unowned buffer.")
                );
            if (platform == "mac")
                return new MacKeychainKeyStore(
                    null,
                    _ => startFailure ? throw new InvalidOperationException("synthetic-start-error") : (code, stdout, stderr),
                    (_, __, ___, ____, _____, ______, _______, ________) => -25300
                );
            return new LinuxSecretToolKeyStore(null, (_, __) =>
                startFailure ? throw new InvalidOperationException("synthetic-start-error") : (code, stdout, stderr)
            );
        }

        [TestCase("mac", 44, "security: SecKeychainSearchCopyNext: The specified item could not be found in the keychain.")]
        [TestCase("linux", 1, "")]
        [TestCase("windows", 1168, "")]
        public void ConfirmedAbsenceIsFalseAndDeletionIsIdempotent(string platform, int code, string stderr)
        {
            var backend = Backend(platform, code, string.Empty, stderr);
            Assert.IsFalse(backend.TryGet("synthetic-id", out string key));
            Assert.IsNull(key);
            Assert.DoesNotThrow(() => backend.Delete("synthetic-id"));
            Assert.DoesNotThrow(() => backend.Delete("synthetic-id"));
        }

        [TestCase("mac", -1, "synthetic-timeout", false)]
        [TestCase("linux", -1, "synthetic-timeout", false)]
        [TestCase("mac", -1, "synthetic-start-error", true)]
        [TestCase("linux", -1, "synthetic-start-error", true)]
        [TestCase("mac", 36, "synthetic-denied", false)]
        [TestCase("linux", 1, "synthetic-service-error", false)]
        [TestCase("linux", 127, "synthetic-not-installed", false)]
        [TestCase("windows", 5, "synthetic-denied", false)]
        [TestCase("windows", 1312, "synthetic-logon-error", false)]
        [TestCase("windows", -1, "synthetic-native-error", true)]
        public void UnavailableReadThrowsAndRemoteMigrationNeverFallsBack(string platform, int code, string stderr, bool startFailure)
        {
            var backend = Backend(platform, code, string.Empty, stderr, startFailure);
            var backendError = Assert.Throws<InvalidOperationException>(() => backend.TryGet("synthetic-id", out _));
            StringAssert.DoesNotContain("synthetic", backendError.ToString());
            var hasError = Assert.Throws<InvalidOperationException>(() => backend.Has("synthetic-id"));
            StringAssert.DoesNotContain("synthetic", hasError.ToString());
            var deleteError = Assert.Throws<InvalidOperationException>(() => backend.Delete("synthetic-id"));
            StringAssert.DoesNotContain("synthetic", deleteError.ToString());
            const string origin = "https://example.test:443";
            string legacy = "synthetic-legacy";
            int deletes = 0;
            var remote = new RemoteApiKeyStorage(() => backend, _ => legacy, _ => deletes++);
            var remoteError = Assert.Throws<RemoteApiKeyStorageException>(() => remote.Get(origin));
            StringAssert.DoesNotContain("synthetic", remoteError.ToString());
            Assert.AreEqual(0, deletes);
            Assert.AreEqual("synthetic-legacy", legacy);
            using var scope = new RemoteKeyStorageTestScope();
            HttpEndpointUtility.RemoteKeys = remote;
            Assert.Throws<RemoteApiKeyStorageException>(() => HttpEndpointUtility.GetRemoteApiKey("https://example.test"));
            HttpEndpointUtility.RemoteKeys = new RemoteApiKeyStorage(
                () => backend,
                _ => string.Empty,
                _ => Assert.Fail("No migration cleanup after lookup failure.")
            );
            Assert.Throws<RemoteApiKeyStorageException>(() => HttpEndpointUtility.GetRemoteApiKey("https://example.test"));
        }

        [TestCase("mac")]
        [TestCase("linux")]
        public void SuccessfulReadReturnsOnlyTheStoredValue(string platform)
        {
            var backend = Backend(platform, 0, "synthetic-key\n", string.Empty);
            Assert.IsTrue(backend.TryGet("synthetic-id", out string key));
            Assert.AreEqual("synthetic-key", key);
        }

        [TestCase(false)]
        [TestCase(true)]
        public void WindowsSuccessfulReadOwnsAndFreesItsCredentialBuffer(bool emptyBlob)
        {
            byte[] bytes = Encoding.UTF8.GetBytes("synthetic-windows-key");
            IntPtr blob = Marshal.AllocHGlobal(bytes.Length);
            IntPtr pointer = Marshal.AllocHGlobal(Marshal.SizeOf<WindowsCredentialKeyStore.CREDENTIAL>());
            Marshal.Copy(bytes, 0, blob, bytes.Length);
            Marshal.StructureToPtr(
                new WindowsCredentialKeyStore.CREDENTIAL { CredentialBlob = blob, CredentialBlobSize = emptyBlob ? 0 : bytes.Length },
                pointer,
                false
            );
            int frees = 0;
            bool released = false;
            try
            {
                var backend = new WindowsCredentialKeyStore(
                    "MCPForUnity.Test",
                    _ => throw new InvalidOperationException("Do not delete during read."),
                    (string target, out IntPtr credential, out int error) =>
                    {
                        StringAssert.StartsWith("MCPForUnity.Test:", target);
                        credential = pointer;
                        error = 0;
                        return true;
                    },
                    credential =>
                    {
                        Assert.AreEqual(pointer, credential);
                        frees++;
                        Marshal.FreeHGlobal(blob);
                        Marshal.FreeHGlobal(pointer);
                        released = true;
                    }
                );
                Assert.AreEqual(!emptyBlob, backend.TryGet("synthetic-id", out string key));
                Assert.AreEqual(emptyBlob ? null : "synthetic-windows-key", key);
                Assert.AreEqual(1, frees);
            }
            finally
            {
                if (!released)
                {
                    Marshal.FreeHGlobal(blob);
                    Marshal.FreeHGlobal(pointer);
                }
            }
        }

        [TestCase("mac")]
        [TestCase("linux")]
        public void GlobalOnlyRemoteClearSucceedsRepeatedlyWithAbsentNativeRecord(string platform)
        {
            using var scope = new RemoteKeyStorageTestScope();
            scope.Preferences[EditorPrefKeys.ApiKey] = "synthetic-global";
            var backend = Backend(platform, platform == "mac" ? 44 : 1, string.Empty, string.Empty);
            HttpEndpointUtility.RemoteKeys = new RemoteApiKeyStorage(
                () => backend,
                key => scope.Preferences.TryGetValue(key, out var value) ? value : string.Empty,
                key => scope.Preferences.Remove(key)
            );
            Assert.DoesNotThrow(() => HttpEndpointUtility.ClearRemoteApiKey("https://example.test"));
            Assert.IsEmpty(scope.Preferences);
            Assert.DoesNotThrow(() => HttpEndpointUtility.ClearRemoteApiKey("https://example.test"));
        }

        [TestCase(false, false)]
        [TestCase(false, true)]
        [TestCase(true, false)]
        [TestCase(true, true)]
        public void MacSearchInitializationFailurePreservesLegacyRecoveryCopies(bool hasLegacy, bool clear)
        {
            using var scope = new RemoteKeyStorageTestScope();
            if (hasLegacy)
            {
                scope.Preferences[EditorPrefKeys.ApiKey] = "synthetic-global";
                scope.Preferences[EditorPrefKeys.RemoteApiKeyPrefix + "https://example.test:443"] = "synthetic-origin";
            }
            int originalCount = scope.Preferences.Count;
            var backend = new MacKeychainKeyStore(null, _ =>
                (
                    44,
                    string.Empty,
                    "security: SecKeychainSearchCreateFromAttributes: synthetic-search-error\nsecurity: SecKeychainSearchCopyNext: The specified item could not be found in the keychain.\n"
                )
            );
            HttpEndpointUtility.RemoteKeys = new RemoteApiKeyStorage(
                () => backend,
                key => scope.Preferences.TryGetValue(key, out var value) ? value : string.Empty,
                key => scope.Preferences.Remove(key)
            );
            if (clear)
                Assert.Throws<RemoteApiKeyStorageException>(() => HttpEndpointUtility.ClearRemoteApiKey("https://example.test"));
            else
                Assert.Throws<RemoteApiKeyStorageException>(() => HttpEndpointUtility.GetRemoteApiKey("https://example.test"));
            Assert.AreEqual(originalCount, scope.Preferences.Count);
        }

        [TestCase(-25300, false)]
        [TestCase(0, false)]
        [TestCase(-25291, false)]
        [TestCase(-25308, false)]
        [TestCase(-1, true)]
        public void MacNativeProbePreservesExactStatusForReadAndClear(int status, bool throws)
        {
            foreach (bool hasLegacy in new[] { false, true })
            foreach (bool clear in new[] { false, true })
            {
                using var scope = new RemoteKeyStorageTestScope();
                const string origin = "https://example.test:443";
                if (hasLegacy)
                {
                    scope.Preferences[EditorPrefKeys.ApiKey] = "synthetic-global";
                    scope.Preferences[EditorPrefKeys.RemoteApiKeyPrefix + origin] = "synthetic-origin";
                }
                int originalCount = scope.Preferences.Count;
                int probes = 0;
                bool validProbeArguments = false;
                var backend = new MacKeychainKeyStore(
                    null,
                    args => args[0] == "add-generic-password" ? (0, string.Empty, string.Empty) : (44, string.Empty, string.Empty),
                    (keychains, serviceLength, service, accountLength, account, passwordLength, passwordData, item) =>
                    {
                        probes++;
                        string expectedAccount = RemoteApiKeyStorage.Identifier(origin);
                        validProbeArguments =
                            keychains == IntPtr.Zero
                            && serviceLength == Encoding.UTF8.GetByteCount(SecureKeyStoreConstants.ServiceName)
                            && Encoding.UTF8.GetString(service) == SecureKeyStoreConstants.ServiceName
                            && accountLength == Encoding.UTF8.GetByteCount(expectedAccount)
                            && Encoding.UTF8.GetString(account) == expectedAccount
                            && passwordLength == IntPtr.Zero
                            && passwordData == IntPtr.Zero
                            && item == IntPtr.Zero;
                        if (throws)
                            throw new InvalidOperationException("synthetic-native-error");
                        return status;
                    }
                );
                HttpEndpointUtility.RemoteKeys = new RemoteApiKeyStorage(
                    () => backend,
                    key => scope.Preferences.TryGetValue(key, out var value) ? value : string.Empty,
                    key => scope.Preferences.Remove(key)
                );
                Action action = clear
                    ? () => HttpEndpointUtility.ClearRemoteApiKey("https://example.test")
                    : () => HttpEndpointUtility.GetRemoteApiKey("https://example.test");
                if (status == -25300 && !throws)
                    Assert.DoesNotThrow(() => action());
                else
                {
                    var error = Assert.Throws<RemoteApiKeyStorageException>(() => action());
                    StringAssert.DoesNotContain("synthetic", error.ToString());
                    Assert.AreEqual(originalCount, scope.Preferences.Count);
                }
                Assert.AreEqual(1, probes);
                Assert.IsTrue(validProbeArguments, "The query must scope exact UTF8 service/account bytes and request no native outputs.");
            }
        }

        [TestCase("fixture-\u00e9")]
        public void MacAbsenceProbeUsesUtf8ByteLengthsForNonAsciiAccounts(string accountName)
        {
            byte[] observedAccount = null;
            uint observedLength = 0;
            bool noOutputs = false;
            var backend = new MacKeychainKeyStore(
                null,
                _ => (44, string.Empty, string.Empty),
                (keychains, serviceLength, service, accountLength, account, passwordLength, passwordData, item) =>
                {
                    observedAccount = account;
                    observedLength = accountLength;
                    noOutputs = passwordLength == IntPtr.Zero && passwordData == IntPtr.Zero && item == IntPtr.Zero;
                    return -25300;
                }
            );
            Assert.IsFalse(backend.TryGet(accountName, out _));
            Assert.AreEqual(accountName, Encoding.UTF8.GetString(observedAccount));
            Assert.AreEqual(Encoding.UTF8.GetByteCount(accountName), observedLength);
            Assert.Greater(observedLength, accountName.Length);
            Assert.IsTrue(noOutputs);
        }
    }
}
