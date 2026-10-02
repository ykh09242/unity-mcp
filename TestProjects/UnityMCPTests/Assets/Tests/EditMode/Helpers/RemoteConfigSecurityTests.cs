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

        [SetUp]
        public void SetUp()
        {
            foreach (string key in new[] { EditorPrefKeys.HttpTransportScope, EditorPrefKeys.HttpRemoteBaseUrl, EditorPrefKeys.ApiKey })
                strings[key] = UnityEditor.EditorPrefs.HasKey(key) ? UnityEditor.EditorPrefs.GetString(key) : null;
            foreach (string key in new[] { EditorPrefKeys.UseHttpTransport, EditorPrefKeys.AllowInsecureRemoteHttp })
                flags[key] = UnityEditor.EditorPrefs.HasKey(key) ? UnityEditor.EditorPrefs.GetBool(key) : (bool?)null;
            UnityEditor.EditorPrefs.SetString(EditorPrefKeys.HttpTransportScope, "remote");
            UnityEditor.EditorPrefs.SetString(EditorPrefKeys.HttpRemoteBaseUrl, "http://remote.example.test");
            UnityEditor.EditorPrefs.SetString(EditorPrefKeys.ApiKey, "test-only-remote-key");
            UnityEditor.EditorPrefs.SetBool(EditorPrefKeys.UseHttpTransport, true);
            UnityEditor.EditorPrefs.SetBool(EditorPrefKeys.AllowInsecureRemoteHttp, false);
            EditorConfigurationCache.Instance.Refresh();
        }

        [TearDown]
        public void TearDown()
        {
            foreach (var pair in strings)
                if (pair.Value == null) UnityEditor.EditorPrefs.DeleteKey(pair.Key);
                else UnityEditor.EditorPrefs.SetString(pair.Key, pair.Value);
            foreach (var pair in flags)
                if (!pair.Value.HasValue) UnityEditor.EditorPrefs.DeleteKey(pair.Key);
                else UnityEditor.EditorPrefs.SetBool(pair.Key, pair.Value.Value);
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
            var root = JObject.Parse(ConfigJsonBuilder.BuildManualConfigJson(null, new McpClient { name = "Cursor" }));
            Assert.AreEqual(url + "/mcp", (string)root.SelectToken("mcpServers.unityMCP.url"));
            Assert.AreEqual("test-only-remote-key", (string)root.SelectToken("mcpServers.unityMCP.headers.X-API-Key"));
        }
    }
}
