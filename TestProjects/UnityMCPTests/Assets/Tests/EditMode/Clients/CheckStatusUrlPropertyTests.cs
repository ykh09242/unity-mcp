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
                    EditorPrefKeys.ApiKey,
                    EditorPrefKeys.GitUrlOverride,
                }
            )
                _preferences[key] = EditorPrefs.HasKey(key) ? EditorPrefs.GetString(key) : null;
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
            Assert.AreEqual(McpStatus.IncorrectPath, codex.CheckStatus(attemptAutoRewrite: false));
            StringAssert.Contains("transport", codex.Client.configStatus);
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
