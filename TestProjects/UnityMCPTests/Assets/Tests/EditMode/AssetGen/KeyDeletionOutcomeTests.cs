using System;
using System.Diagnostics;
using System.IO;
using System.Reflection;
using MCPForUnity.Editor.Security;
using NUnit.Framework;

namespace MCPForUnityTests.Editor.AssetGen
{
    [Parallelizable(ParallelScope.None)]
    public sealed class KeyDeletionOutcomeTests
    {
        private sealed class Store : ISecureKeyStore
        {
            public bool Present = true;

            public bool Has(string id) => Present;

            public bool TryGet(string id, out string key)
            {
                key = null;
                throw new InvalidOperationException("Do not read keys");
            }

            public void Set(string id, string key) => Assert.Fail("Do not write keys");

            public void Delete(string id) => Present = false;
        }

        [Test]
        public void RemovingSavedKeyKeepsEnvironmentOverrideSeparate()
        {
            var constructor = typeof(EnvOverlayKeyStore).GetConstructor(new[] { typeof(ISecureKeyStore), typeof(Func<string, string>) });
            Assert.IsNotNull(constructor);
            var inner = new Store();
            var overlay = (EnvOverlayKeyStore)constructor.Invoke(new object[] { inner, (Func<string, string>)(_ => "synthetic-override") });
            overlay.Delete("fixture");
            Assert.IsFalse(inner.Present);
            Assert.IsTrue(overlay.Has("fixture"));
            var saved = typeof(EnvOverlayKeyStore).GetMethod("HasStoredKey", BindingFlags.Instance | BindingFlags.NonPublic);
            Assert.IsNotNull(saved);
            Assert.IsFalse((bool)saved.Invoke(overlay, new object[] { "fixture" }));
        }

        [TestCase("windows", 5)]
        [TestCase("mac", -1)]
        [TestCase("linux", -1)]
        public void FailedPlatformDeletionCannotReturnSuccess(string platform, int code)
        {
            int calls = 0;
            ISecureKeyStore store;
            if (platform == "windows")
            {
                var constructor = typeof(WindowsCredentialKeyStore).GetConstructor(
                    BindingFlags.Instance | BindingFlags.NonPublic,
                    null,
                    new[] { typeof(string), typeof(Func<string, int>) },
                    null
                );
                Assert.IsNotNull(constructor);
                store = (ISecureKeyStore)
                    constructor.Invoke(
                        new object[]
                        {
                            "MCP.Test",
                            (Func<string, int>)(
                                _ =>
                                {
                                    calls++;
                                    return code;
                                }
                            ),
                        }
                    );
            }
            else if (platform == "mac")
            {
                var constructor = typeof(MacKeychainKeyStore).GetConstructor(
                    BindingFlags.Instance | BindingFlags.NonPublic,
                    null,
                    new[] { typeof(Func<string[], int>) },
                    null
                );
                Assert.IsNotNull(constructor);
                store = (ISecureKeyStore)
                    constructor.Invoke(
                        new object[]
                        {
                            (Func<string[], int>)(
                                _ =>
                                {
                                    calls++;
                                    return code;
                                }
                            ),
                        }
                    );
            }
            else
            {
                var constructor = typeof(LinuxSecretToolKeyStore).GetConstructor(
                    BindingFlags.Instance | BindingFlags.NonPublic,
                    null,
                    new[] { typeof(Func<ProcessStartInfo, int>) },
                    null
                );
                Assert.IsNotNull(constructor);
                store = (ISecureKeyStore)
                    constructor.Invoke(
                        new object[]
                        {
                            (Func<ProcessStartInfo, int>)(
                                _ =>
                                {
                                    calls++;
                                    return code;
                                }
                            ),
                        }
                    );
            }
            var error = Assert.Throws<InvalidOperationException>(() => store.Delete("fixture"));
            Assert.AreEqual(1, calls);
            Assert.That(error.Message, Does.Not.Contain("fixture"));
        }

        [Test]
        public void FileDeletionFailureIsNotSwallowed()
        {
            string root = Path.Combine(Path.GetTempPath(), "mcp-key-delete-" + Guid.NewGuid().ToString("N"));
            try
            {
                var store = new EncryptedFileKeyStore(root);
                Directory.CreateDirectory(Path.Combine(root, "key_fixture.bin"));
                Assert.Catch(() => store.Delete("fixture"));
            }
            finally
            {
                if (Directory.Exists(root))
                    Directory.Delete(root, true);
            }
        }
    }
}
