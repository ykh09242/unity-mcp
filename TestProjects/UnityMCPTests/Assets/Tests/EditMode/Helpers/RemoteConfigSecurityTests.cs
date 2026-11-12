using System;
using System.Collections.Generic;
using MCPForUnity.Editor.Constants;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Editor.Models;
using MCPForUnity.Editor.Services;
using Newtonsoft.Json.Linq;
using NUnit.Framework;

namespace MCPForUnityTests.Editor.Helpers
{
    public class RemoteConfigSecurityTests
    {
        private readonly Dictionary<string, string> strings = new();
        private readonly Dictionary<string, bool?> flags = new();
        private RemoteKeyStorageTestScope credentialScope;

        [SetUp]
        public void SetUp()
        {
            credentialScope = new RemoteKeyStorageTestScope();
            foreach (string key in new[] { EditorPrefKeys.HttpTransportScope, EditorPrefKeys.HttpRemoteBaseUrl, EditorPrefKeys.HttpBaseUrl })
                strings[key] = UnityEditor.EditorPrefs.HasKey(key) ? UnityEditor.EditorPrefs.GetString(key) : null;
            foreach (string key in new[] { EditorPrefKeys.UseHttpTransport, EditorPrefKeys.AllowInsecureRemoteHttp, EditorPrefKeys.AllowLanHttpBind })
                flags[key] = UnityEditor.EditorPrefs.HasKey(key) ? UnityEditor.EditorPrefs.GetBool(key) : (bool?)null;
            UnityEditor.EditorPrefs.SetString(EditorPrefKeys.HttpTransportScope, "remote");
            UnityEditor.EditorPrefs.SetString(EditorPrefKeys.HttpRemoteBaseUrl, "http://remote.example.test");
            credentialScope.Preferences[EditorPrefKeys.ApiKey] = "test-only-remote-key";
            UnityEditor.EditorPrefs.SetBool(EditorPrefKeys.UseHttpTransport, true);
            UnityEditor.EditorPrefs.SetBool(EditorPrefKeys.AllowInsecureRemoteHttp, false);
            EditorConfigurationCache.Instance.Refresh();
        }

        [TearDown]
        public void TearDown()
        {
            credentialScope.Dispose();
            foreach (var pair in strings)
                if (pair.Value == null)
                    UnityEditor.EditorPrefs.DeleteKey(pair.Key);
                else
                    UnityEditor.EditorPrefs.SetString(pair.Key, pair.Value);
            foreach (var pair in flags)
                if (!pair.Value.HasValue)
                    UnityEditor.EditorPrefs.DeleteKey(pair.Key);
                else
                    UnityEditor.EditorPrefs.SetBool(pair.Key, pair.Value.Value);
            EditorConfigurationCache.Instance.Refresh();
        }

        [Test]
        public void PlaintextRemoteCannotGenerateHeadersOrClientConfiguration()
        {
            Assert.Throws<InvalidOperationException>(() => HttpEndpointUtility.GetAuthHeaders());
            Assert.Throws<InvalidOperationException>(() => HttpEndpointUtility.GetMcpRpcUrl());
            Assert.Throws<InvalidOperationException>(() => HttpEndpointUtility.GetRemoteMcpRpcUrl());
            Assert.Throws<InvalidOperationException>(() => ConfigJsonBuilder.BuildManualConfigJson(null, new McpClient { name = "Cursor" }));
            Assert.Throws<InvalidOperationException>(() => CodexConfigHelper.BuildCodexServerBlock(null));
        }

        [TestCase("https://remote.example.test", false)]
        [TestCase("http://remote.example.test", true)]
        public void AllowedEndpointKeepsCredentialBoundToConfiguredUrl(string url, bool optIn)
        {
            UnityEditor.EditorPrefs.SetString(EditorPrefKeys.HttpRemoteBaseUrl, url);
            UnityEditor.EditorPrefs.SetBool(EditorPrefKeys.AllowInsecureRemoteHttp, optIn);
            SaveKey(url, "test-only-remote-key");
            var root = JObject.Parse(ConfigJsonBuilder.BuildManualConfigJson(null, new McpClient { name = "Cursor" }));
            Assert.AreEqual(url + "/mcp", (string)root.SelectToken("mcpServers.unityMCP.url"));
            Assert.AreEqual("test-only-remote-key", (string)root.SelectToken("mcpServers.unityMCP.headers.X-API-Key"));
        }

        [TestCase("https://remote.example.test/proxy", "https://remote.example.test/proxy", "")]
        [TestCase("remote.example.test/proxy/mcp/", "https://remote.example.test/proxy", "")]
        [TestCase("https://remote.example.test/proxy?tenant=alpha%2Fbeta", "https://remote.example.test/proxy", "?tenant=alpha%2Fbeta")]
        [TestCase("https://remote.example.test/proxy/mcp?tenant=alpha%2Fbeta", "https://remote.example.test/proxy", "?tenant=alpha%2Fbeta")]
        [TestCase("https://remote.example.test/proxy/MCP/?tenant=alpha/", "https://remote.example.test/proxy", "?tenant=alpha/")]
        [TestCase("https://remote.example.test/proxy%2Fname/mcp?next=/mcp", "https://remote.example.test/proxy%2Fname", "?next=/mcp")]
        [TestCase("https://remote.example.test?tenant=alpha", "https://remote.example.test", "?tenant=alpha")]
        public void RemoteEndpointPathsPreserveQueryAndExistingClientOptions(string input, string prefix, string query)
        {
            HttpEndpointUtility.SaveRemoteBaseUrl(input);
            Assert.AreEqual(prefix + query, HttpEndpointUtility.GetRemoteBaseUrl());
            Assert.AreEqual(prefix + "/mcp" + query, HttpEndpointUtility.GetRemoteMcpRpcUrl());
            Assert.AreEqual(prefix + "/register-tools" + query, HttpEndpointUtility.GetRegisterToolsUrl());

            var root = JObject.Parse("{\"mcpServers\":{\"other\":{\"enabled\":false},\"unityMCP\":{\"timeout\":0,\"headers\":{\"X-Custom\":\"keep\"}}}}");
            var sibling = root["mcpServers"]["other"];
            ConfigJsonBuilder.ApplyUnityServerToExistingConfig(root, null, new McpClient());
            Assert.AreEqual(prefix + "/mcp" + query, (string)root.SelectToken("mcpServers.unityMCP.url"));
            Assert.AreEqual(0, (int)root.SelectToken("mcpServers.unityMCP.timeout"));
            Assert.AreEqual("keep", (string)root.SelectToken("mcpServers.unityMCP.headers.X-Custom"));
            Assert.AreSame(sibling, root["mcpServers"]["other"]);

            string toml = CodexConfigHelper.UpsertCodexServerBlock(
                "model = \"synthetic\"\n[mcp_servers.other]\nenabled = false\n[mcp_servers.unityMCP]\ntool_timeout_sec = 7",
                null
            );
            Assert.IsTrue(CodexConfigHelper.TryParseCodexServer(toml, out _, out _, out string url));
            Assert.AreEqual(prefix + "/mcp" + query, url);
            StringAssert.Contains("tool_timeout_sec = 7", toml);
            StringAssert.Contains("model = \"synthetic\"", toml);
            StringAssert.Contains("enabled = false", toml);
        }

        [TestCase("localhost:8080/proxy/mcp?tenant=alpha", "http://127.0.0.1:8080/proxy", "?tenant=alpha")]
        [TestCase("http://localhost:8080/proxy", "http://127.0.0.1:8080/proxy", "")]
        [TestCase("http://127.0.0.1:8080/proxy/mcp?tenant=alpha", "http://127.0.0.1:8080/proxy", "?tenant=alpha")]
        [TestCase("http://[::1]:8080/proxy/mcp/?next=/mcp", "http://[::1]:8080/proxy", "?next=/mcp")]
        [TestCase("http://localhost:8080/mcp/", "http://127.0.0.1:8080", "")]
        public void LocalEndpointHostNormalizationPreservesPathAndQuery(string input, string prefix, string query)
        {
            UnityEditor.EditorPrefs.SetString(EditorPrefKeys.HttpTransportScope, "local");
            EditorConfigurationCache.Instance.Refresh();
            HttpEndpointUtility.SaveLocalBaseUrl(input);
            Assert.AreEqual(prefix + query, HttpEndpointUtility.GetLocalBaseUrl());
            Assert.AreEqual(prefix + "/mcp" + query, HttpEndpointUtility.GetLocalMcpRpcUrl());
            Assert.AreEqual(prefix + "/register-tools" + query, HttpEndpointUtility.GetRegisterToolsUrl());
            Assert.AreEqual("local", UnityEditor.EditorPrefs.GetString(EditorPrefKeys.HttpTransportScope));
        }

        [TestCase("ftp://127.0.0.1:8080")]
        [TestCase("file://localhost/synthetic")]
        [TestCase("ws://[::1]:8080")]
        [TestCase("wss://localhost:8080")]
        [TestCase("ftp://0.0.0.0:8080")]
        public void LocalLaunchRejectsNonHttpSchemesBeforeHostOrLanAdmission(string url)
        {
            UnityEditor.EditorPrefs.SetBool(EditorPrefKeys.AllowLanHttpBind, true);
            Assert.IsFalse(HttpEndpointUtility.IsHttpLocalUrlAllowedForLaunch(url, out string error));
            StringAssert.Contains("scheme", error);
            Assert.IsTrue(UnityEditor.EditorPrefs.GetBool(EditorPrefKeys.AllowLanHttpBind));
        }

        [TestCase("http://127.0.0.1:8080", false, true)]
        [TestCase("https://localhost:8080", false, true)]
        [TestCase("http://[::1]:8080", false, true)]
        [TestCase("http://0.0.0.0:8080", false, false)]
        [TestCase("http://0.0.0.0:8080", true, true)]
        [TestCase("http://[::]:8080", true, true)]
        [TestCase("http://remote.example.test:8080", true, false)]
        [TestCase("", true, false)]
        public void LocalLaunchPreservesHttpHostAndLanPolicy(string url, bool lan, bool expected)
        {
            UnityEditor.EditorPrefs.SetBool(EditorPrefKeys.AllowLanHttpBind, lan);
            Assert.AreEqual(expected, HttpEndpointUtility.IsHttpLocalUrlAllowedForLaunch(url, out string error));
            if (expected)
                Assert.IsNull(error);
            else
                Assert.IsNotEmpty(error);
        }

        [TestCase("test-only-quote\"backslash\\end")]
        [TestCase("test-only-line\nnext\tcolumn")]
        public void GeneratedJsonAndTomlRoundTripSyntheticHeaderValues(string value)
        {
            UnityEditor.EditorPrefs.SetString(EditorPrefKeys.HttpRemoteBaseUrl, "https://remote.example.test");
            SaveKey("https://remote.example.test", value);
            var json = JObject.Parse(ConfigJsonBuilder.BuildManualConfigJson(null, new McpClient()));
            Assert.AreEqual(value, (string)json.SelectToken("mcpServers.unityMCP.headers.X-API-Key"));
            using var reader = new System.IO.StringReader(CodexConfigHelper.BuildCodexServerBlock(null));
            var toml = MCPForUnity.External.Tommy.TOML.Parse(reader);
            Assert.AreEqual(value, ((MCPForUnity.External.Tommy.TomlString)toml["mcp_servers"]["unityMCP"]["http_headers"]["X-API-Key"]).Value);
        }

        [TestCase("https://remote.example.test/proxy/mcp#synthetic-fragment")]
        [TestCase("https://synthetic-user@remote.example.test/proxy/mcp")]
        [TestCase("wss://remote.example.test/proxy/mcp")]
        [TestCase("https://[broken")]
        public void InvalidRemoteEndpointLeavesExistingDocumentAndSiblingUnchanged(string input)
        {
            UnityEditor.EditorPrefs.SetString(EditorPrefKeys.HttpRemoteBaseUrl, input);
            var root = JObject.Parse("{\"mcpServers\":{\"other\":{\"enabled\":false},\"unityMCP\":{\"command\":\"keep\",\"timeout\":0}}}");
            var original = root.DeepClone();
            var sibling = root["mcpServers"]["other"];
            Assert.Throws<InvalidOperationException>(() => ConfigJsonBuilder.ApplyUnityServerToExistingConfig(root, null, new McpClient()));
            Assert.IsTrue(JToken.DeepEquals(original, root));
            Assert.AreSame(sibling, root["mcpServers"]["other"]);
            Assert.AreEqual(input, UnityEditor.EditorPrefs.GetString(EditorPrefKeys.HttpRemoteBaseUrl));
        }

        private void SaveKey(string url, string value)
        {
            HttpEndpointUtility.SaveRemoteApiKey(url, value);
        }

        [TestCase("http://0.0.0.0:43210")]
        [TestCase("https://0.0.0.0:43210")]
        [TestCase("http://[::]:43210")]
        [TestCase("https://[0:0:0:0:0:0:0:0]:43210")]
        public void RemoteBindOnlyOriginCannotCaptureCredentialsOrGenerateConfiguration(string url)
        {
            UnityEditor.EditorPrefs.SetBool(EditorPrefKeys.AllowInsecureRemoteHttp, true);
            UnityEditor.EditorPrefs.SetString(EditorPrefKeys.HttpRemoteBaseUrl, url);
            string pref = EditorPrefKeys.RemoteApiKeyPrefix + HttpEndpointUtility.GetRemoteOrigin(url);
            strings[pref] = UnityEditor.EditorPrefs.HasKey(pref) ? UnityEditor.EditorPrefs.GetString(pref) : null;
            UnityEditor.EditorPrefs.SetString(pref, "synthetic-bind-only-key");
            Assert.IsFalse(HttpEndpointUtility.IsRemoteUrlAllowed(url, out string error));
            StringAssert.Contains("bind-only", error);
            Assert.Throws<InvalidOperationException>(() => HttpEndpointUtility.CaptureConnectionSettings());
            Assert.Throws<InvalidOperationException>(() => HttpEndpointUtility.GetAuthHeaders());
            Assert.Throws<InvalidOperationException>(() => HttpEndpointUtility.GetMcpRpcUrl());
            Assert.Throws<InvalidOperationException>(() => ConfigJsonBuilder.BuildManualConfigJson(null, new McpClient()));
            Assert.Throws<InvalidOperationException>(() => CodexConfigHelper.BuildCodexServerBlock(null));
        }

        [TestCase("http://0.0.0.0:43210")]
        [TestCase("http://[::]:43210")]
        public void LocalBindOnlyCaptureKeepsExplicitLocalPolicyAndNeverUsesRemoteKeys(string url)
        {
            UnityEditor.EditorPrefs.SetString(EditorPrefKeys.HttpTransportScope, "local");
            UnityEditor.EditorPrefs.SetBool(EditorPrefKeys.AllowLanHttpBind, true);
            HttpEndpointUtility.SaveLocalBaseUrl(url);
            EditorConfigurationCache.Instance.Refresh();
            Assert.IsTrue(HttpEndpointUtility.IsHttpLocalUrlAllowedForLaunch(url, out _));
            var captured = HttpEndpointUtility.CaptureConnectionSettings();
            Assert.IsFalse(captured.Remote);
            Assert.AreEqual(url, captured.BaseUrl);
            Assert.IsEmpty(captured.ApiKey);
        }

        [Test]
        public void LegacyKeyNeedsExplicitOriginApprovalAndCannotFollowChangedApproval()
        {
            string a = "https://legacy-" + Guid.NewGuid().ToString("N") + ".example.test";
            string b = "https://other-" + Guid.NewGuid().ToString("N") + ".example.test";
            UnityEditor.EditorPrefs.SetString(EditorPrefKeys.HttpRemoteBaseUrl, a);
            SaveKey(a, string.Empty);
            Assert.IsEmpty(HttpEndpointUtility.GetAuthHeaders());
            StringAssert.DoesNotContain("test-only-remote-key", ConfigJsonBuilder.BuildManualConfigJson(null, new McpClient()));
            string approvedOrigin = HttpEndpointUtility.GetRemoteOrigin(a);
            UnityEditor.EditorPrefs.SetString(EditorPrefKeys.HttpRemoteBaseUrl, b);
            Assert.IsFalse(HttpEndpointUtility.BindLegacyApiKey(approvedOrigin));
            Assert.IsTrue(HttpEndpointUtility.HasLegacyApiKey);
            Assert.IsEmpty(HttpEndpointUtility.GetAuthHeaders());
            UnityEditor.EditorPrefs.SetString(EditorPrefKeys.HttpRemoteBaseUrl, a);
            Assert.IsTrue(HttpEndpointUtility.BindLegacyApiKey(approvedOrigin));
            Assert.IsFalse(HttpEndpointUtility.HasLegacyApiKey);
            Assert.AreEqual("test-only-remote-key", HttpEndpointUtility.GetAuthHeaders()[AuthConstants.ApiKeyHeader]);
        }

        [Test]
        public void OriginBoundKeyDoesNotFollowHostPortOrSchemeAndReturningToOriginRestoresIt()
        {
            string host = "origin-" + Guid.NewGuid().ToString("N") + ".example.test";
            string a = "https://" + host;
            UnityEditor.EditorPrefs.SetBool(EditorPrefKeys.AllowInsecureRemoteHttp, true);
            SaveKey(a, "synthetic-only-origin-a");
            foreach (string other in new[] { "https://other." + host, a + ":444", "http://" + host })
            {
                UnityEditor.EditorPrefs.SetString(EditorPrefKeys.HttpRemoteBaseUrl, other);
                Assert.IsEmpty(HttpEndpointUtility.GetAuthHeaders());
                StringAssert.DoesNotContain("synthetic-only-origin-a", ConfigJsonBuilder.BuildManualConfigJson(null, new McpClient()));
                StringAssert.DoesNotContain("synthetic-only-origin-a", CodexConfigHelper.BuildCodexServerBlock(null));
            }
            UnityEditor.EditorPrefs.SetString(EditorPrefKeys.HttpRemoteBaseUrl, "https://" + host.ToUpperInvariant() + ":443/proxy?q=synthetic-query");
            Assert.AreEqual("synthetic-only-origin-a", HttpEndpointUtility.GetAuthHeaders()[AuthConstants.ApiKeyHeader]);
        }

        [Test]
        public void CapturedSettingsKeepTheApprovedEndpointAndCredentialAcrossPreferenceChanges()
        {
            string a = "https://snapshot-" + Guid.NewGuid().ToString("N") + ".example.test/proxy?q=synthetic-query";
            UnityEditor.EditorPrefs.SetString(EditorPrefKeys.HttpRemoteBaseUrl, a);
            SaveKey(a, "synthetic-captured-key");
            var captured = HttpEndpointUtility.CaptureConnectionSettings();
            UnityEditor.EditorPrefs.SetString(EditorPrefKeys.HttpRemoteBaseUrl, "https://other.example.test");
            UnityEditor.EditorPrefs.SetString(EditorPrefKeys.HttpTransportScope, "local");
            EditorConfigurationCache.Instance.Refresh();
            Assert.AreEqual(a, captured.BaseUrl);
            Assert.IsTrue(captured.Remote);
            Assert.AreEqual(a.Replace("/proxy?", "/proxy/mcp?"), captured.McpRpcUrl);
            Assert.AreEqual("synthetic-captured-key", captured.GetAuthHeaders()[AuthConstants.ApiKeyHeader]);
        }

        [TestCase("https://synthetic-user:synthetic-password@example.test/proxy?q=synthetic-query#synthetic-fragment")]
        [TestCase("https://example.test/proxy?q=synthetic-query")]
        [TestCase("https://[broken?synthetic-query")]
        [TestCase("https://example.test/proxy?part='synthetic-private-query")]
        [TestCase("https://synthetic'-private-user:synthetic-private-pass@example.test/proxy")]
        [TestCase("https://example.test/proxy?part=\"synthetic-private-query")]
        [TestCase("https://synthetic\"-private-user:synthetic-private-pass@example.test/proxy")]
        [TestCase("https://example.test/proxy?part=<synthetic-private-query>")]
        public void DisplayAndValidationNeverEchoSensitiveUrlComponents(string url)
        {
            StringAssert.DoesNotContain("synthetic", HttpEndpointUtility.GetDisplayUrl(url));
            StringAssert.DoesNotContain("synthetic", HttpEndpointUtility.RedactUrls("Network failed for " + url));
            if (!HttpEndpointUtility.IsRemoteUrlAllowed(url, out string error))
                StringAssert.DoesNotContain("synthetic", error);
        }

        [TestCase("Connection failed: socket closed (1006).")]
        [TestCase("Open https://example.test/proxy for setup; ws://127.0.0.1:8080 remains available.")]
        public void DiagnosticRedactionPreservesUnrelatedTextAndOrdinaryUrls(string message)
        {
            Assert.AreEqual(message, HttpEndpointUtility.RedactUrls(message));
        }

        [TestCase("https://synthetic-user:synthetic-password@example.test/proxy?signature=synthetic-query#synthetic-fragment")]
        [TestCase("https://example.test/proxy?part='synthetic-private-query")]
        [TestCase("https://synthetic'-private-user:synthetic-private-pass@example.test/proxy")]
        [TestCase("https://example.test/proxy?part=\"synthetic-private-query")]
        [TestCase("https://example.test/proxy?part=<synthetic-private-query>")]
        public void NativeConfigLogBoundaryRemovesUrlSecretsWithoutChangingTheActualEndpoint(string endpoint)
        {
            string original = new Uri(endpoint).AbsoluteUri;
            var logs = new List<string>();
            UnityEngine.Application.LogCallback capture = (message, stack, type) => logs.Add(message);
            UnityEngine.Application.logMessageReceived += capture;
            try
            {
                McpLog.Info("Client config endpoint: " + endpoint);
                McpLog.Warn("Configuration failed for " + endpoint);
                Assert.AreEqual(2, logs.Count);
                foreach (string log in logs)
                {
                    StringAssert.DoesNotContain("synthetic", log);
                    StringAssert.Contains("https://example.test/proxy", log);
                }
                Assert.AreEqual(original, new Uri(endpoint).AbsoluteUri);
            }
            finally
            {
                UnityEngine.Application.logMessageReceived -= capture;
            }
        }
    }
}
