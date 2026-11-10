using System;
using System.Collections.Generic;
using System.IO;
using System.Text;
using MCPForUnity.Editor.Clients;
using MCPForUnity.Editor.Clients.Configurators;
using MCPForUnity.Editor.Constants;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Editor.Models;
using MCPForUnity.Editor.Services;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEditor;

namespace MCPForUnityTests.Editor.Helpers
{
    public class ConfigJsonIntegrityTests : TransportPreferenceTestBase
    {
        [TestCase("{\"X-API-Key\":\"synthetic-current\"}", true)]
        [TestCase("{\"x-api-key\":\"synthetic-current\"}", true)]
        [TestCase("{\"X-API-Key\":\"synthetic-stale\"}", false)]
        [TestCase("{\"X-API-Key\":123}", false)]
        [TestCase("{\"X-API-Key\":\"synthetic-current\",\"x-api-key\":\"duplicate\"}", false)]
        [TestCase("{}", false)]
        [TestCase("[]", false)]
        public void ManagedHttpHeadersAreComparedWithoutLeakingCredentials(string json, bool expected)
        {
            var headers = new Dictionary<string, string> { ["X-API-Key"] = "synthetic-current" };
            Assert.AreEqual(expected, ConfigJsonBuilder.TryValidateAuthHeaders(JToken.Parse(json), headers, out string reason));
            StringAssert.DoesNotContain("synthetic-current", reason ?? string.Empty);
            StringAssert.DoesNotContain("synthetic-stale", reason ?? string.Empty);
        }

        [TestCase("headersHelper")]
        [TestCase("http_headers_helper")]
        public void CustomAuthenticationProviderIsNotOverwritten(string field)
        {
            var root = JObject.Parse("{\"mcpServers\":{\"unityMCP\":{\"" + field + "\":\"custom-provider\"}}}");
            var original = root.DeepClone();
            Assert.Throws<InvalidOperationException>(() => ConfigJsonBuilder.ApplyUnityServerToExistingConfig(root, null, new McpClient()));
            Assert.IsTrue(JToken.DeepEquals(original, root));
        }

        [TestCase("{broken")]
        [TestCase("null")]
        [TestCase("[]")]
        [TestCase("{\"mcp\":[]}")]
        public void OpenCodePreservesInvalidExistingConfiguration(string json)
        {
            WithOwnedRoot(root =>
            {
                string path = Owned(root, "opencode.json");
                File.WriteAllText(path, json);
                var configurator = new OwnedOpenCode(path);
                Assert.Throws<InvalidOperationException>(() => configurator.Configure());
                Assert.AreEqual(McpStatus.Error, configurator.Status);
                Assert.AreEqual(json, File.ReadAllText(path));
            });
        }

        [Test]
        public void InvalidOpenCodeConfigurationIsReportedAndRetainedForSelectiveRetry()
        {
            WithOwnedRoot(root =>
            {
                string path = Owned(root, "opencode.json");
                const string invalid = "{broken";
                File.WriteAllText(path, invalid);
                var failed = new OwnedOpenCode(path);
                var successful = new SuccessfulClient();
                var service = new ClientConfigurationService(new IMcpClientConfigurator[] { successful, failed }, () => false, _ => { }, () => { }, _ => { });

                var summary = service.ConfigureAllDetectedClients();

                Assert.AreEqual(1, summary.SuccessCount);
                Assert.AreEqual(1, summary.FailureCount);
                Assert.AreEqual(McpStatus.Error, failed.Status);
                Assert.IsTrue(summary.Messages.Exists(message => message.Contains("OpenCode") && message.Contains("not a JSON object")));
                Assert.IsTrue(summary.Attempt.CanRetry);
                Assert.AreEqual(1, summary.Attempt.Failures.Count);
                Assert.AreEqual(failed.Id, summary.Attempt.Failures[0].ClientId);

                var retry = service.RetryFailedClients(summary.Attempt);
                Assert.AreEqual(0, retry.Summary.SuccessCount);
                Assert.AreEqual(1, retry.Summary.FailureCount);
                Assert.AreEqual(2, failed.ConfigureCalls);
                Assert.AreEqual(1, successful.ConfigureCalls);
                Assert.AreEqual(invalid, File.ReadAllText(path));
                Assert.Throws<InvalidOperationException>(() => service.ConfigureClient(failed));
            });
        }

        private sealed class SuccessfulClient : McpClientConfiguratorBase
        {
            public int ConfigureCalls;

            public SuccessfulClient()
                : base(new McpClient { name = "Synthetic Success" }) { }

            public override bool IsInstalled => true;

            public override string GetConfigPath() => null;

            public override McpStatus CheckStatus(bool attemptAutoRewrite = true) => Status;

            public override void Configure()
            {
                ConfigureCalls++;
                client.SetStatus(McpStatus.Configured);
            }

            public override string GetManualSnippet() => "";

            public override IList<string> GetInstallationSteps() => Array.Empty<string>();
        }

        private sealed class OwnedOpenCode : OpenCodeConfigurator
        {
            private readonly string path;
            public int ConfigureCalls;

            public OwnedOpenCode(string path)
            {
                this.path = path;
            }

            public override string GetConfigPath() => path;

            public override void Configure()
            {
                ConfigureCalls++;
                base.Configure();
            }
        }

        [TestCase("mcpServers", "[]")]
        [TestCase("mcpServers", "null")]
        [TestCase("mcpServers", "false")]
        [TestCase("mcpServers", "\"keep\"")]
        [TestCase("servers", "[]")]
        [TestCase("servers", "null")]
        [TestCase("mcp", "[]")]
        [TestCase("mcp", "null")]
        public void InvalidExistingContainerIsRejectedBeforeTransportOrDocumentChanges(string key, string value)
        {
            var root = JObject.Parse("{\"unrelated\":{\"zero\":0,\"false\":false},\"" + key + "\":" + value + "}");
            var original = root.DeepClone();
            var unrelated = root["unrelated"];
            var client = new McpClient
            {
                IsVsCodeLayout = key == "servers",
                ServerContainerKey = key,
                SchemaUrl = "https://example.invalid/synthetic-schema",
            };

            Assert.Throws<FormatException>(() => ConfigJsonBuilder.ApplyUnityServerToExistingConfig(root, null, client));
            Assert.IsTrue(JToken.DeepEquals(original, root));
            Assert.AreSame(unrelated, root["unrelated"]);
            Assert.IsNull(root["$schema"]);
        }

        [TestCase("[]")]
        [TestCase("null")]
        [TestCase("false")]
        [TestCase("\"keep\"")]
        public void InvalidExistingUnityEntryIsRejectedBeforeTransportOrSiblingChanges(string value)
        {
            var root = JObject.Parse("{\"mcpServers\":{\"other\":{\"zero\":0},\"unityMCP\":" + value + "}}");
            var original = root.DeepClone();
            var other = root["mcpServers"]["other"];

            Assert.Throws<FormatException>(() =>
                ConfigJsonBuilder.ApplyUnityServerToExistingConfig(root, null, new McpClient { SchemaUrl = "https://example.invalid/synthetic-schema" })
            );
            Assert.IsTrue(JToken.DeepEquals(original, root));
            Assert.AreSame(other, root["mcpServers"]["other"]);
            Assert.IsNull(root["$schema"]);
        }

        [TestCase(false)]
        [TestCase(true)]
        public void AtomicWriterPreservesUnrelatedSidecarsForExistingOrNewTarget(bool existing)
        {
            WithOwnedRoot(root =>
            {
                string path = Owned(root, "synthetic.json");
                if (existing)
                    File.WriteAllText(path, "original");
                File.WriteAllText(Owned(root, "synthetic.json.tmp"), "unrelated temp");
                File.WriteAllText(Owned(root, "synthetic.json.backup"), "unrelated backup");

                McpConfigurationHelper.WriteAtomicFile(path, "updated");

                Assert.AreEqual("updated", File.ReadAllText(path));
                Assert.AreEqual("unrelated temp", File.ReadAllText(Owned(root, "synthetic.json.tmp")));
                Assert.AreEqual("unrelated backup", File.ReadAllText(Owned(root, "synthetic.json.backup")));
                Assert.AreEqual(3, Directory.GetFiles(root).Length, "Successful attempt removes only its own sidecars.");
            });
        }

        [Test]
        public void MissingParentWriteFailureDoesNotCreateTargetOrParent()
        {
            WithOwnedRoot(root =>
            {
                string absentParent = Owned(root, "missing-parent");
                string path = Owned(root, "missing-parent/synthetic.json");
                Assert.IsFalse(Directory.Exists(absentParent));
                // Without a parent, temp creation fails before any target mutation.
                Assert.Throws<Exception>(() => McpConfigurationHelper.WriteAtomicFile(path, "updated"));
                Assert.IsFalse(File.Exists(path));
                Assert.IsFalse(Directory.Exists(absentParent));
                Assert.AreEqual(0, Directory.GetFiles(root).Length);
            });
        }

        [TestCase(false, false)]
        [TestCase(false, true)]
        [TestCase(true, false)]
        [TestCase(true, true)]
        public void OrdinaryConfigurationPreservesConcurrentEditOrCreation(bool toml, bool missing)
        {
            WithHttpConfiguration(() =>
                WithOwnedRoot(root =>
                {
                    // Given an existing snapshot (including its BOM), or an expected absent file.
                    string path = Owned(root, toml ? "client.toml" : "client.json");
                    if (!missing)
                        File.WriteAllText(path, toml ? "model = \"original\"\r\n" : "{\"model\":\"original\"}\r\n", Encoding.Unicode);
                    byte[] concurrent = Encoding.UTF8.GetBytes(toml ? "# concurrent\r\nmodel = \"edited\"\r\n" : "{\"model\":\"edited\"}\r\n");
                    File.WriteAllText(path + ".tmp", "unrelated temp");
                    File.WriteAllText(path + ".backup", "unrelated backup");
                    Action edit = () => File.WriteAllBytes(path, concurrent);

                    // When another writer edits or creates the file after this operation read it.
                    Assert.Throws<McpConfigurationHelper.ConfigurationConflictException>(() => ConfigureOrdinary(path, toml, edit));

                    // Then the concurrent bytes survive, and only this attempt's sidecars are removed.
                    CollectionAssert.AreEqual(concurrent, File.ReadAllBytes(path));
                    Assert.AreEqual("unrelated temp", File.ReadAllText(path + ".tmp"));
                    Assert.AreEqual("unrelated backup", File.ReadAllText(path + ".backup"));
                    Assert.AreEqual(3, Directory.GetFiles(root).Length);
                })
            );
        }

        [TestCase(false, false)]
        [TestCase(false, true)]
        [TestCase(true, false)]
        [TestCase(true, true)]
        public void OrdinaryConfigurationWritesAbsentOrUnchangedBomSnapshot(bool toml, bool missing)
        {
            WithHttpConfiguration(() =>
                WithOwnedRoot(root =>
                {
                    // Given an absent file or an unchanged UTF-16 document with unrelated settings.
                    string path = Owned(root, toml ? "client.toml" : "client.json");
                    if (!missing)
                        File.WriteAllText(path, toml ? "model = \"keep\"\r\n" : "{\"model\":\"keep\"}\r\n", Encoding.Unicode);

                    // When ordinary configuration merges its owned server entry.
                    Assert.AreEqual("Configured successfully", ConfigureOrdinary(path, toml, null));

                    // Then new and existing documents remain valid and unrelated values survive.
                    string document = File.ReadAllText(path);
                    if (toml)
                    {
                        Assert.IsTrue(CodexConfigHelper.TryParseCodexServer(document, out _, out _, out string url));
                        Assert.AreEqual("http://127.0.0.1:8080/mcp", url);
                        if (!missing)
                            Assert.AreEqual("keep", CodexConfigHelper.ParseConfiguration(document)["model"].AsString.Value);
                    }
                    else
                    {
                        var json = JObject.Parse(document);
                        Assert.AreEqual("http://127.0.0.1:8080/mcp", (string)json["mcpServers"]["unityMCP"]["url"]);
                        if (!missing)
                            Assert.AreEqual("keep", (string)json["model"]);
                    }
                    Assert.AreEqual(1, Directory.GetFiles(root).Length);
                })
            );
        }

        private static string ConfigureOrdinary(string path, bool toml, Action beforeComparison) =>
            toml
                ? McpConfigurationHelper.ConfigureCodexClient(path, new McpClient(), beforeComparison)
                : McpConfigurationHelper.WriteMcpConfiguration(path, new McpClient(), beforeComparison);

        private static void WithHttpConfiguration(Action test)
        {
            bool hadLock = EditorPrefs.HasKey(EditorPrefKeys.LockCursorConfig);
            bool originalLock = EditorPrefs.GetBool(EditorPrefKeys.LockCursorConfig, false);
            bool hadScope = EditorPrefs.HasKey(EditorPrefKeys.HttpTransportScope);
            string scope = EditorPrefs.GetString(EditorPrefKeys.HttpTransportScope, "local");
            bool hadUrl = EditorPrefs.HasKey(EditorPrefKeys.HttpBaseUrl);
            string url = EditorPrefs.GetString(EditorPrefKeys.HttpBaseUrl, "");
            string token = Environment.GetEnvironmentVariable("UNITY_MCP_LOCAL_AUTH_TOKEN");
            var capability = CodexHttpAuth.SupportsHeadersHelper;
            try
            {
                EditorPrefs.SetBool(EditorPrefKeys.LockCursorConfig, false);
                EditorPrefs.SetBool(EditorPrefKeys.UseHttpTransport, true);
                EditorPrefs.SetString(EditorPrefKeys.HttpTransportScope, "local");
                EditorPrefs.SetString(EditorPrefKeys.HttpBaseUrl, "http://127.0.0.1:8080");
                Environment.SetEnvironmentVariable("UNITY_MCP_LOCAL_AUTH_TOKEN", "synthetic-integrity-token");
                CodexHttpAuth.SupportsHeadersHelper = () => true;
                EditorConfigurationCache.Instance.Refresh();
                test();
            }
            finally
            {
                if (hadLock)
                    EditorPrefs.SetBool(EditorPrefKeys.LockCursorConfig, originalLock);
                else
                    EditorPrefs.DeleteKey(EditorPrefKeys.LockCursorConfig);
                if (hadScope)
                    EditorPrefs.SetString(EditorPrefKeys.HttpTransportScope, scope);
                else
                    EditorPrefs.DeleteKey(EditorPrefKeys.HttpTransportScope);
                if (hadUrl)
                    EditorPrefs.SetString(EditorPrefKeys.HttpBaseUrl, url);
                else
                    EditorPrefs.DeleteKey(EditorPrefKeys.HttpBaseUrl);
                Environment.SetEnvironmentVariable("UNITY_MCP_LOCAL_AUTH_TOKEN", token);
                CodexHttpAuth.SupportsHeadersHelper = capability;
                EditorConfigurationCache.Instance.Refresh();
            }
        }

        private static void WithOwnedRoot(Action<string> test)
        {
            string temp = Path.GetFullPath(Path.GetTempPath()).TrimEnd(Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar);
            string root = Path.GetFullPath(Path.Combine(temp, "UnityMCPConfigIntegrity_" + Guid.NewGuid().ToString("N")));
            Assert.IsTrue(root.StartsWith(temp + Path.DirectorySeparatorChar, StringComparison.OrdinalIgnoreCase));
            Directory.CreateDirectory(root);
            try
            {
                test(root);
            }
            finally
            {
                string resolved = Path.GetFullPath(root);
                Assert.IsTrue(resolved.StartsWith(temp + Path.DirectorySeparatorChar, StringComparison.OrdinalIgnoreCase));
                Assert.IsTrue(Path.GetFileName(resolved).StartsWith("UnityMCPConfigIntegrity_", StringComparison.Ordinal));
                if (Directory.Exists(resolved))
                    Directory.Delete(resolved, true);
            }
        }

        private static string Owned(string root, string relative)
        {
            string path = Path.GetFullPath(Path.Combine(root, relative));
            Assert.IsTrue(path.StartsWith(Path.GetFullPath(root) + Path.DirectorySeparatorChar, StringComparison.OrdinalIgnoreCase));
            return path;
        }
    }
}
