using System;
using System.Collections.Generic;
using System.IO;
using MCPForUnity.Editor.Clients;
using MCPForUnity.Editor.Constants;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Editor.Models;
using MCPForUnity.Editor.Services;
using NUnit.Framework;
using UnityEditor;

namespace MCPForUnityTests.Editor.Clients
{
    /// <summary>
    /// Covers the regression where JsonFileMcpConfigurator.CheckStatus only recognized
    /// the "url" property and missed "serverUrl" (Antigravity/Windsurf) and "httpUrl"
    /// (Gemini CLI), so clients configured via HTTP looked unconfigured and got rewritten
    /// every startup by the auto-rewrite path.
    /// </summary>
    [TestFixture]
    public class CheckStatusUrlPropertyTests : TransportPreferenceTestBase
    {
        private string _tempDir;
        private readonly Dictionary<string, string> _preferences = new();

        [SetUp]
        public void SetUp()
        {
            _tempDir = Path.Combine(Path.GetTempPath(), "UnityMCPTests", Guid.NewGuid().ToString("N"));
            Directory.CreateDirectory(_tempDir);
            foreach (
                string key in new[]
                {
                    EditorPrefKeys.HttpTransportScope,
                    EditorPrefKeys.HttpRemoteBaseUrl,
                    EditorPrefKeys.HttpBaseUrl,
                    EditorPrefKeys.ApiKey,
                    EditorPrefKeys.GitUrlOverride,
                }
            )
                _preferences[key] = EditorPrefs.HasKey(key) ? EditorPrefs.GetString(key) : null;
            EditorPrefs.SetString(EditorPrefKeys.HttpTransportScope, "local");
            EditorPrefs.SetString(EditorPrefKeys.HttpRemoteBaseUrl, string.Empty);
            EditorPrefs.SetString(EditorPrefKeys.HttpBaseUrl, "http://127.0.0.1:65430");
            EditorPrefs.SetBool(EditorPrefKeys.UseHttpTransport, true);
            EditorConfigurationCache.Instance.Refresh();
        }

        [TearDown]
        public void TearDown()
        {
            try
            {
                if (Directory.Exists(_tempDir))
                    Directory.Delete(_tempDir, true);
            }
            catch { }
            foreach (var pref in _preferences)
                if (pref.Value == null)
                    EditorPrefs.DeleteKey(pref.Key);
                else
                    EditorPrefs.SetString(pref.Key, pref.Value);
            _preferences.Clear();
            EditorConfigurationCache.Instance.Refresh();
        }

        [Test]
        public void CheckStatus_DetectsHttp_WhenUrlProperty() => AssertHttpDetected("url");

        [Test]
        public void CheckStatus_DetectsHttp_WhenServerUrlProperty() => AssertHttpDetected("serverUrl");

        [Test]
        public void CheckStatus_DetectsHttp_WhenHttpUrlProperty() => AssertHttpDetected("httpUrl");

        [TestCase("synthetic-current", true, McpStatus.Configured)]
        [TestCase("synthetic-stale", true, McpStatus.IncorrectPath)]
        [TestCase(null, true, McpStatus.IncorrectPath)]
        [TestCase("synthetic-current", false, McpStatus.IncorrectPath)]
        public void CodexHttpStatusChecksTransportAndToken(string token, bool preferHttp, McpStatus expected)
        {
            EditorPrefs.SetString(EditorPrefKeys.HttpTransportScope, "remote");
            EditorPrefs.SetString(EditorPrefKeys.HttpRemoteBaseUrl, "https://synthetic.example.test");
            EditorPrefs.SetString(EditorPrefKeys.ApiKey, "synthetic-current");
            EditorPrefs.SetBool(EditorPrefKeys.UseHttpTransport, preferHttp);
            EditorConfigurationCache.Instance.Refresh();
            string text =
                "[mcp_servers.unityMCP]\nurl = 'https://synthetic.example.test/mcp'\n"
                + (token == null ? "" : "http_headers = { 'X-API-Key' = '" + token + "' }\n");
            string path = Path.Combine(_tempDir, "codex.toml");
            File.WriteAllText(path, text);
            var codex = new FakeCodexConfigurator(
                new McpClient
                {
                    name = "Codex",
                    windowsConfigPath = path,
                    macConfigPath = path,
                    linuxConfigPath = path,
                }
            );
            Assert.AreEqual(expected, codex.CheckStatus(attemptAutoRewrite: false));
            Assert.AreEqual(text, File.ReadAllText(path), "Read-only validation must not rewrite the file");
            if (expected != McpStatus.Configured)
                StringAssert.DoesNotContain("synthetic-current", codex.Client.configStatus);
        }

        [Test]
        public void CodexStdioSourceMatchCannotHideSelectedHttpTransport()
        {
            EditorPrefs.SetString(EditorPrefKeys.GitUrlOverride, "https://synthetic.example.test/server.zip");
            EditorPrefs.SetBool(EditorPrefKeys.UseHttpTransport, true);
            EditorConfigurationCache.Instance.Refresh();
            string path = Path.Combine(_tempDir, "codex.toml");
            File.WriteAllText(
                path,
                "[mcp_servers.unityMCP]\ncommand = 'uvx'\nargs = ['--from', '" + AssetPathUtility.GetMcpServerPackageSource() + "', 'mcp-for-unity']\n"
            );
            var codex = new FakeCodexConfigurator(
                new McpClient
                {
                    name = "Codex",
                    windowsConfigPath = path,
                    macConfigPath = path,
                    linuxConfigPath = path,
                }
            );
            string original = File.ReadAllText(path);
            Assert.AreEqual(McpStatus.IncorrectPath, codex.CheckStatus(attemptAutoRewrite: false));
            Assert.AreEqual(ConfiguredTransport.Stdio, codex.Client.configuredTransport);
            Assert.AreEqual(original, File.ReadAllText(path), "Status must not rewrite the manual stdio entry");
        }

        [TestCase("synthetic-current", true, McpStatus.Configured)]
        [TestCase("synthetic-stale", true, McpStatus.IncorrectPath)]
        [TestCase(null, true, McpStatus.IncorrectPath)]
        [TestCase("synthetic-current", false, McpStatus.IncorrectPath)]
        public void JsonHttpStatusChecksTransportAndToken(string token, bool preferHttp, McpStatus expected)
        {
            EditorPrefs.SetString(EditorPrefKeys.HttpTransportScope, "remote");
            EditorPrefs.SetString(EditorPrefKeys.HttpRemoteBaseUrl, "https://synthetic.example.test");
            EditorPrefs.SetString(EditorPrefKeys.ApiKey, "synthetic-current");
            EditorPrefs.SetBool(EditorPrefKeys.UseHttpTransport, preferHttp);
            EditorConfigurationCache.Instance.Refresh();
            string text =
                "{\"mcpServers\":{\"unityMCP\":{\"httpUrl\":\"https://synthetic.example.test/mcp\""
                + (token == null ? "" : ",\"headers\":{\"X-API-Key\":\"" + token + "\"}")
                + "}}}";
            string path = Path.Combine(_tempDir, "client.json");
            File.WriteAllText(path, text);
            var client = new FakeJsonConfigurator(
                new McpClient
                {
                    name = "Synthetic",
                    windowsConfigPath = path,
                    macConfigPath = path,
                    linuxConfigPath = path,
                }
            );

            Assert.AreEqual(expected, client.CheckStatus(attemptAutoRewrite: false));
            Assert.AreEqual(text, File.ReadAllText(path));
        }

        // Prerelease packages expect "mcpforunityserver>=0.0.0a0". On Windows, Mono's
        // Path.GetFullPath throws on '>', so a correct stdio config read as VersionMismatch
        // and the auto-rewrite path rewrote it every startup.
        [Test]
        public void CheckStatus_Configured_WhenStdioUsesPrereleasePackageRange()
        {
            const string source = "mcpforunityserver>=0.0.0a0";
            Assert.IsTrue(McpConfigurationHelper.PathsEqual(source, source), "Package specs must match before path parsing");
            bool hadOverride = EditorPrefs.HasKey(EditorPrefKeys.GitUrlOverride);
            string originalOverride = EditorPrefs.GetString(EditorPrefKeys.GitUrlOverride, string.Empty);
            try
            {
                EditorPrefs.SetString(EditorPrefKeys.GitUrlOverride, source);
                EditorPrefs.SetBool(EditorPrefKeys.UseHttpTransport, false);
                EditorConfigurationCache.Instance.Refresh();
                string configPath = Path.Combine(_tempDir, "stdio.json");
                File.WriteAllText(
                    configPath,
                    "{\"mcpServers\":{\"unityMCP\":{\"command\":\"uvx\",\"args\":[\"--prerelease\",\"explicit\",\"--from\",\""
                        + source
                        + "\",\"mcp-for-unity\",\"--transport\",\"stdio\"]}}}"
                );
                var client = new McpClient
                {
                    name = "Fake",
                    windowsConfigPath = configPath,
                    macConfigPath = configPath,
                    linuxConfigPath = configPath,
                };

                var status = new FakeJsonConfigurator(client).CheckStatus(attemptAutoRewrite: false);

                Assert.AreEqual(McpStatus.Configured, status, client.configStatus);
            }
            finally
            {
                if (hadOverride)
                    EditorPrefs.SetString(EditorPrefKeys.GitUrlOverride, originalOverride);
                else
                    EditorPrefs.DeleteKey(EditorPrefKeys.GitUrlOverride);
                EditorConfigurationCache.Instance.Refresh();
            }
        }

        [Test]
        public void CodexStdioStatusMatchesPrereleasePackageRange()
        {
            const string source = "mcpforunityserver>=0.0.0a0";
            EditorPrefs.SetString(EditorPrefKeys.GitUrlOverride, source);
            EditorPrefs.SetBool(EditorPrefKeys.UseHttpTransport, false);
            EditorConfigurationCache.Instance.Refresh();
            string path = Path.Combine(_tempDir, "range.toml");
            string text = "[mcp_servers.unityMCP]\ncommand = 'uvx'\nargs = ['--from', '" + source + "', 'mcp-for-unity']\n";
            File.WriteAllText(path, text);
            var client = new McpClient
            {
                name = "Codex",
                windowsConfigPath = path,
                macConfigPath = path,
                linuxConfigPath = path,
            };
            Assert.AreEqual(McpStatus.Configured, new FakeCodexConfigurator(client).CheckStatus(attemptAutoRewrite: false), client.configStatus);
            Assert.AreEqual(text, File.ReadAllText(path));
        }

        [TestCase(false, "")]
        [TestCase(true, "")]
        [TestCase(false, "ftp://synthetic.example.test")]
        [TestCase(true, "ftp://synthetic.example.test")]
        public void LocalHttpStatusDoesNotValidateUnusedRemoteUrl(bool codex, string remote)
        {
            EditorPrefs.SetString(EditorPrefKeys.HttpRemoteBaseUrl, remote);
            EditorConfigurationCache.Instance.Refresh();
            string path = Path.Combine(_tempDir, codex ? "local.toml" : "local.json");
            string text = codex
                ? "[mcp_servers.unityMCP]\nurl = 'http://127.0.0.1:65431/mcp'\n"
                : "{\"mcpServers\":{\"unityMCP\":{\"url\":\"http://127.0.0.1:65431/mcp\"}}}";
            File.WriteAllText(path, text);
            var client = new McpClient
            {
                name = "Synthetic",
                windowsConfigPath = path,
                macConfigPath = path,
                linuxConfigPath = path,
            };
            McpClientConfiguratorBase configurator = codex ? new FakeCodexConfigurator(client) : new FakeJsonConfigurator(client);
            Assert.AreEqual(McpStatus.IncorrectPath, configurator.CheckStatus(attemptAutoRewrite: false), client.configStatus);
            Assert.AreEqual(ConfiguredTransport.Http, client.configuredTransport);
            Assert.AreEqual(text, File.ReadAllText(path));
        }

        private void AssertHttpDetected(string urlProperty)
        {
            string configPath = Path.Combine(_tempDir, $"{urlProperty}.json");
            File.WriteAllText(configPath, "{\"mcpServers\":{\"unityMCP\":{\"" + urlProperty + "\":\"http://localhost:65535/mcp\"}}}");

            var client = new McpClient
            {
                name = "Fake",
                windowsConfigPath = configPath,
                macConfigPath = configPath,
                linuxConfigPath = configPath,
                HttpUrlProperty = urlProperty,
            };
            var configurator = new FakeJsonConfigurator(client);

            configurator.CheckStatus(attemptAutoRewrite: false);

            Assert.AreNotEqual(
                ConfiguredTransport.Unknown,
                client.configuredTransport,
                $"CheckStatus must recognize the '{urlProperty}' property as an HTTP URL"
            );
            Assert.That(
                client.configuredTransport == ConfiguredTransport.Http || client.configuredTransport == ConfiguredTransport.HttpRemote,
                $"Expected HTTP/HttpRemote transport for '{urlProperty}', got {client.configuredTransport}"
            );
        }

        private sealed class FakeJsonConfigurator : JsonFileMcpConfigurator
        {
            public FakeJsonConfigurator(McpClient client)
                : base(client) { }
        }

        private sealed class FakeCodexConfigurator : CodexMcpConfigurator
        {
            public FakeCodexConfigurator(McpClient client)
                : base(client) { }
        }
    }
}
