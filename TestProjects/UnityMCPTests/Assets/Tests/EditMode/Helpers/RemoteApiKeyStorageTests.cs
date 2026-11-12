using System;
using System.Collections.Generic;
using MCPForUnity.Editor.Constants;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Editor.Security;
using NUnit.Framework;

namespace MCPForUnityTests.Editor.Helpers
{
    public class RemoteApiKeyStorageTests
    {
        private const string Origin = "https://example.test:443";
        private Dictionary<string, string> preferences;
        private MemoryRemoteKeyStore secure;
        private RemoteApiKeyStorage storage;

        [SetUp]
        public void SetUp()
        {
            preferences = new Dictionary<string, string>();
            secure = new MemoryRemoteKeyStore();
            storage = new RemoteApiKeyStorage(
                () => secure,
                key => preferences.TryGetValue(key, out var value) ? value : string.Empty,
                key => preferences.Remove(key)
            );
        }

        [Test]
        public void SaveUsesOnlyOriginSpecificSecureStorage()
        {
            storage.Save(Origin, "synthetic-key");
            Assert.AreEqual("synthetic-key", storage.Get(Origin));
            Assert.IsEmpty(preferences);
            foreach (string other in new[] { "https://other.test:443", "https://example.test:444", "http://example.test:80" })
                Assert.IsEmpty(storage.Get(other));
            string id = RemoteApiKeyStorage.Identifier(Origin);
            StringAssert.StartsWith("remote-http-", id);
            StringAssert.DoesNotContain("example.test", id);
            Assert.AreEqual(id, secure.LastIdentifier);
        }

        [Test]
        public void OriginLegacyValueMigratesOnlyAfterConfirmedSecureWrite()
        {
            string preference = EditorPrefKeys.RemoteApiKeyPrefix + Origin;
            preferences[preference] = "synthetic-legacy";
            secure.FailWrites = true;
            var error = Assert.Throws<RemoteApiKeyStorageException>(() => storage.Get(Origin));
            StringAssert.DoesNotContain("synthetic", error.ToString());
            Assert.AreEqual("synthetic-legacy", preferences[preference]);
            Assert.IsEmpty(secure.Values);
            secure.FailWrites = false;
            Assert.AreEqual("synthetic-legacy", storage.Get(Origin));
            Assert.IsFalse(preferences.ContainsKey(preference));
            Assert.AreEqual("synthetic-legacy", secure.Values[RemoteApiKeyStorage.Identifier(Origin)]);
        }

        [Test]
        public void ExistingSecureValueWinsOverStalePlaintext()
        {
            storage.Save(Origin, "synthetic-new");
            preferences[EditorPrefKeys.RemoteApiKeyPrefix + Origin] = "synthetic-old";
            Assert.AreEqual("synthetic-new", storage.Get(Origin));
            Assert.IsEmpty(preferences);
        }

        [Test]
        public void SecureReadFailureNeverFallsBackToPlaintext()
        {
            preferences[EditorPrefKeys.RemoteApiKeyPrefix + Origin] = "synthetic-legacy";
            secure.FailReads = true;
            var error = Assert.Throws<RemoteApiKeyStorageException>(() => storage.Get(Origin));
            StringAssert.DoesNotContain("synthetic", error.ToString());
            Assert.AreEqual("synthetic-legacy", preferences[EditorPrefKeys.RemoteApiKeyPrefix + Origin]);
            Assert.IsEmpty(secure.Values);
        }

        [Test]
        public void FailedReplacementKeepsSecureAndLegacyValues()
        {
            storage.Save(Origin, "synthetic-existing");
            preferences[EditorPrefKeys.RemoteApiKeyPrefix + Origin] = "synthetic-legacy";
            secure.FailWrites = true;
            Assert.Throws<RemoteApiKeyStorageException>(() => storage.Save(Origin, "synthetic-replacement"));
            Assert.AreEqual("synthetic-existing", secure.Values[RemoteApiKeyStorage.Identifier(Origin)]);
            Assert.AreEqual("synthetic-legacy", preferences[EditorPrefKeys.RemoteApiKeyPrefix + Origin]);
        }

        [Test]
        public void ExplicitRemovalClearsBothOriginRecordsAndPreservesOtherOrigins()
        {
            const string other = "https://other.test:443";
            storage.Save(Origin, "synthetic-a");
            storage.Save(other, "synthetic-b");
            preferences[EditorPrefKeys.RemoteApiKeyPrefix + Origin] = "synthetic-legacy";
            storage.Save(Origin, string.Empty);
            Assert.IsEmpty(storage.Get(Origin));
            Assert.IsFalse(preferences.ContainsKey(EditorPrefKeys.RemoteApiKeyPrefix + Origin));
            Assert.AreEqual("synthetic-b", storage.Get(other));
        }

        [Test]
        public void FailedRemovalDoesNotDiscardLegacyRecoveryValue()
        {
            storage.Save(Origin, "synthetic-key");
            preferences[EditorPrefKeys.RemoteApiKeyPrefix + Origin] = "synthetic-legacy";
            secure.FailDeletes = true;
            Assert.Throws<RemoteApiKeyStorageException>(() => storage.Save(Origin, string.Empty));
            Assert.IsTrue(preferences.ContainsKey(EditorPrefKeys.RemoteApiKeyPrefix + Origin));
            Assert.IsTrue(secure.Values.ContainsKey(RemoteApiKeyStorage.Identifier(Origin)));
        }

        [Test]
        public void GlobalLegacyValueRequiresBindingAndSurvivesFailedBinding()
        {
            preferences[EditorPrefKeys.ApiKey] = "synthetic-global";
            Assert.IsEmpty(storage.Get(Origin));
            Assert.IsTrue(storage.HasLegacyApiKey);
            secure.FailWrites = true;
            Assert.Throws<RemoteApiKeyStorageException>(() => storage.BindLegacy(Origin));
            Assert.IsTrue(storage.HasLegacyApiKey);
            secure.FailWrites = false;
            Assert.IsTrue(storage.BindLegacy(Origin));
            Assert.IsFalse(storage.HasLegacyApiKey);
            Assert.AreEqual("synthetic-global", storage.Get(Origin));
        }

        [Test]
        public void GlobalPreferenceDeletionFailureIsSanitizedAndKeepsRecoveryCopies()
        {
            preferences[EditorPrefKeys.ApiKey] = "synthetic-global";
            storage = new RemoteApiKeyStorage(
                () => secure,
                key => preferences.TryGetValue(key, out var value) ? value : string.Empty,
                key =>
                {
                    if (key == EditorPrefKeys.ApiKey)
                        throw new InvalidOperationException("synthetic-sensitive-error");
                    preferences.Remove(key);
                }
            );
            var bindError = Assert.Throws<RemoteApiKeyStorageException>(() => storage.BindLegacy(Origin));
            StringAssert.DoesNotContain("synthetic", bindError.ToString());
            Assert.AreEqual("synthetic-global", preferences[EditorPrefKeys.ApiKey]);
            Assert.AreEqual("synthetic-global", secure.Values[RemoteApiKeyStorage.Identifier(Origin)]);
            var clearError = Assert.Throws<RemoteApiKeyStorageException>(() => storage.ClearLegacyApiKey());
            StringAssert.DoesNotContain("synthetic", clearError.ToString());
            Assert.IsTrue(preferences.ContainsKey(EditorPrefKeys.ApiKey));
        }

        [Test]
        public void GlobalLegacyPresenceReadFailureIsSanitized()
        {
            storage = new RemoteApiKeyStorage(
                () => secure,
                _ => throw new InvalidOperationException("synthetic-sensitive-error"),
                _ => Assert.Fail("No deletion")
            );
            var error = Assert.Throws<RemoteApiKeyStorageException>(() => _ = storage.HasLegacyApiKey);
            StringAssert.DoesNotContain("synthetic", error.ToString());
        }
    }

    internal sealed class MemoryRemoteKeyStore : ISecureKeyStore
    {
        internal readonly Dictionary<string, string> Values = new Dictionary<string, string>();
        internal bool FailWrites;
        internal bool FailDeletes;
        internal bool FailReads;
        internal string LastIdentifier;

        public bool TryGet(string id, out string key)
        {
            if (FailReads)
                throw new InvalidOperationException("synthetic-sensitive-error");
            return Values.TryGetValue(id, out key);
        }

        public bool Has(string id) => Values.ContainsKey(id);

        public void Set(string id, string key)
        {
            if (FailWrites)
                throw new InvalidOperationException("synthetic-sensitive-error");
            LastIdentifier = id;
            Values[id] = key;
        }

        public void Delete(string id)
        {
            if (FailDeletes)
                throw new InvalidOperationException("synthetic-sensitive-error");
            Values.Remove(id);
        }
    }

    /// <summary>Replaces all credential access before a fixture can touch user secrets.</summary>
    internal sealed class RemoteKeyStorageTestScope : IDisposable
    {
        private readonly RemoteApiKeyStorage previous = HttpEndpointUtility.RemoteKeys;
        internal readonly Dictionary<string, string> Preferences = new Dictionary<string, string>();
        internal readonly MemoryRemoteKeyStore Secure = new MemoryRemoteKeyStore();

        internal RemoteKeyStorageTestScope()
        {
            HttpEndpointUtility.RemoteKeys = new RemoteApiKeyStorage(
                () => Secure,
                key => Preferences.TryGetValue(key, out var value) ? value : string.Empty,
                key => Preferences.Remove(key)
            );
        }

        public void Dispose() => HttpEndpointUtility.RemoteKeys = previous;
    }
}
