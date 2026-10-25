using System;
using System.IO;
using System.Linq;
using System.Reflection;
using MCPForUnity.Editor.Clients.Configurators;
using MCPForUnity.Editor.Constants;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Editor.Models;
using MCPForUnity.Editor.Services;
using MCPForUnity.Editor.Services.Server;
using MCPForUnity.External.Tommy;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEditor;
using EditorConfigCache = MCPForUnity.Editor.Services.EditorConfigurationCache;

namespace MCPForUnityTests.Editor.Helpers
{
    // Per-client MCP config-format coverage.
    //
    // Issue #1120: Kilo Code v7.0.33+ moved its MCP config from the VS Code extension's
    // globalStorage/mcp_settings.json (mcpServers / type:"http" or "streamableHttp" / disabled)
    // to a CLI-style kilo.jsonc under ~/.config/kilo whose schema (https://app.kilo.ai/config.json)
    // uses an "mcp" container, type:"remote" for HTTP servers, and an "enabled" flag. Writing the
    // old format left the server showing as "stdio" + disabled. These tests pin the new Kilo format
    // while guarding that Cline keeps "streamableHttp" and generic clients keep plain "http".
    public class ClientConfigFormatTests : TransportPreferenceTestBase
    {
        private const string UseHttpTransportPrefKey = EditorPrefKeys.UseHttpTransport;

        private bool _hadHttpTransport;
        private bool _originalHttpTransport;
        private string _originalScope;
        private bool _hadScope;
        private string _originalTokenFile;
        private string _originalToken;
        private string _tokenPath;
        private Func<bool> _originalCodexCapabilityProbe;
        private string _profileKey;
        private string _originalProfile;
        private bool _hadProfile;

        [SetUp]
        public void SetUp()
        {
            _profileKey = "MCPForUnity.StartupToolProfile." + ProjectIdentityUtility.GetProjectHash();
            _hadProfile = EditorPrefs.HasKey(_profileKey);
            _originalProfile = EditorPrefs.GetString(_profileKey, "");
            EditorPrefs.DeleteKey(_profileKey);
            _originalCodexCapabilityProbe = CodexHttpAuth.SupportsHeadersHelper;
            CodexHttpAuth.SupportsHeadersHelper = () => true;
            _hadHttpTransport = EditorPrefs.HasKey(UseHttpTransportPrefKey);
            _originalHttpTransport = EditorPrefs.GetBool(UseHttpTransportPrefKey, true);
            _hadScope = EditorPrefs.HasKey(EditorPrefKeys.HttpTransportScope);
            _originalScope = EditorPrefs.GetString(EditorPrefKeys.HttpTransportScope, "local");
            _originalTokenFile = Environment.GetEnvironmentVariable("UNITY_MCP_LOCAL_AUTH_TOKEN_FILE");
            _originalToken = Environment.GetEnvironmentVariable("UNITY_MCP_LOCAL_AUTH_TOKEN");
            _tokenPath = Path.GetTempFileName();
            File.WriteAllText(_tokenPath, "first-test-launch");
            Environment.SetEnvironmentVariable("UNITY_MCP_LOCAL_AUTH_TOKEN_FILE", _tokenPath);
            Environment.SetEnvironmentVariable("UNITY_MCP_LOCAL_AUTH_TOKEN", null);
            EditorPrefs.SetString(EditorPrefKeys.HttpTransportScope, "local");

            // Force HTTP transport so the remote/streamableHttp branch is exercised.
            EditorPrefs.SetBool(UseHttpTransportPrefKey, true);
            EditorConfigCache.Instance.Refresh();
        }

        [TearDown]
        public void TearDown()
        {
            if (_hadProfile)
                EditorPrefs.SetString(_profileKey, _originalProfile);
            else
                EditorPrefs.DeleteKey(_profileKey);
            CodexHttpAuth.SupportsHeadersHelper = _originalCodexCapabilityProbe;
            Environment.SetEnvironmentVariable("UNITY_MCP_LOCAL_AUTH_TOKEN_FILE", _originalTokenFile);
            Environment.SetEnvironmentVariable("UNITY_MCP_LOCAL_AUTH_TOKEN", _originalToken);
            File.Delete(_tokenPath);
            if (_hadScope)
                EditorPrefs.SetString(EditorPrefKeys.HttpTransportScope, _originalScope);
            else
                EditorPrefs.DeleteKey(EditorPrefKeys.HttpTransportScope);
            if (_hadHttpTransport)
                EditorPrefs.SetBool(UseHttpTransportPrefKey, _originalHttpTransport);
            else
                EditorPrefs.DeleteKey(UseHttpTransportPrefKey);
            EditorConfigCache.Instance.Refresh();
        }

        [Test]
        public void KiloCodeConfigurator_DeclaresNewKiloFormat()
        {
            var client = new KiloCodeConfigurator().Client;

            Assert.AreEqual("mcp", client.ServerContainerKey, "Kilo Code's new schema nests servers under an \"mcp\" container, not \"mcpServers\"");
            Assert.AreEqual("remote", client.HttpTypeValue, "Kilo Code's new schema uses type:\"remote\" for HTTP servers");
            Assert.AreEqual("local", client.StdioTypeValue, "Kilo Code's new schema uses type:\"local\" for stdio servers");
            Assert.AreEqual("https://app.kilo.ai/config.json", client.SchemaUrl, "Kilo Code config should declare the kilo.jsonc $schema");
            Assert.IsTrue(client.DefaultUnityFields.ContainsKey("enabled"), "Kilo Code uses an \"enabled\" flag instead of \"disabled\"");
            Assert.AreEqual(true, client.DefaultUnityFields["enabled"], "Kilo Code must default enabled:true so the server is active");
            Assert.IsFalse(client.DefaultUnityFields.ContainsKey("disabled"), "Kilo Code's new schema must not write the legacy \"disabled\" field");

            foreach (var path in new[] { client.windowsConfigPath, client.macConfigPath, client.linuxConfigPath })
            {
                Assert.AreEqual("kilo.jsonc", System.IO.Path.GetFileName(path), "Kilo Code config must target kilo.jsonc, not mcp_settings.json");
            }
        }

        [TestCase("")]
        [TestCase("core")]
        [TestCase("full")]
        [TestCase("minimal")]
        public void StartupProfile_StdioBuildersUseSameExplicitChoice(string profile)
        {
            // Given: this project's explicit startup choice does not change Unity tool toggles.
            EditorPrefs.SetBool(UseHttpTransportPrefKey, false);
            EditorPrefs.SetString(_profileKey, profile);
            EditorConfigCache.Instance.Refresh();
            // When: generic JSON, Codex manual/update TOML and specialized client snippets are generated.
            var json = JObject.Parse(ConfigJsonBuilder.BuildManualConfigJson("uv", new McpClient()));
            string[] expected = json["mcpServers"]["unityMCP"]["args"].ToObject<string[]>();
            foreach (string document in new[] { CodexConfigHelper.BuildCodexServerBlock("uv"), CodexConfigHelper.UpsertCodexServerBlock("", "uv") })
            {
                using var reader = new StringReader(document);
                var args = TOML.Parse(reader)["mcp_servers"]["unityMCP"]["args"].AsArray.Children.Select(node => node.AsString.Value).ToArray();
                CollectionAssert.AreEqual(expected, args);
            }
            var openClaw = JObject.Parse(new OpenClawConfigurator().GetManualSnippet());
            CollectionAssert.AreEqual(expected, openClaw.SelectToken("plugins.entries.openclaw-mcp-bridge.config.servers.unityMCP.args").ToObject<string[]>());
            var openCode = JObject.Parse(new OpenCodeConfigurator().GetManualSnippet());
            CollectionAssert.AreEqual(expected, openCode["mcp"]["unityMCP"]["command"].ToObject<string[]>().Skip(1));
            // Then: no flag preserves server/env defaults; explicit choices occur once after server args.
            Assert.AreEqual(string.IsNullOrEmpty(profile) ? 0 : 1, expected.Count(arg => arg == "--tool-profile"));
            if (!string.IsNullOrEmpty(profile))
                CollectionAssert.AreEqual(new[] { "--tool-profile", profile }, expected.Skip(expected.Length - 2));
        }

        [Test]
        public void StartupProfile_HttpClientConfigurationDoesNotChange()
        {
            // Given: the Editor's local HTTP server is configured independently of URL-only clients.
            var client = new McpClient();
            string beforeJson = ConfigJsonBuilder.BuildManualConfigJson("uv", client);
            string beforeToml = CodexConfigHelper.BuildCodexServerBlock("uv");
            EditorPrefs.SetString(_profileKey, "full");
            // When/Then: selecting the startup profile does not add flags to client URL/auth config.
            Assert.AreEqual(beforeJson, ConfigJsonBuilder.BuildManualConfigJson("uv", client));
            Assert.AreEqual(beforeToml, CodexConfigHelper.BuildCodexServerBlock("uv"));
        }

        [Test]
        public void StartupProfile_LocalHttpLaunchUsesExplicitChoice()
        {
            // Given: the local server launch uses the same project startup choice.
            EditorPrefs.SetString(_profileKey, "full");
            var builder = new ServerCommandBuilder();
            // When: the Editor constructs the HTTP server command without launching it.
            Assert.IsTrue(builder.TryBuildCommand(out _, out var args, out var display, out var error), error);
            // Then: the server receives the explicit choice without changing the client endpoint.
            StringAssert.Contains(" --tool-profile full", args);
            StringAssert.Contains(" --tool-profile full", display);
        }

        [TestCase("json")]
        [TestCase("toml")]
        public void StartupProfile_CapturedConfigurationRetainsChoiceAndChangesFingerprint(string format)
        {
            // Given: a configuration preview captured the default stdio launch arguments.
            EditorPrefs.SetBool(UseHttpTransportPrefKey, false);
            EditorConfigCache.Instance.Refresh();
            var capture = typeof(ClientConfigurationChangeService).GetMethod("Capture", BindingFlags.Static | BindingFlags.NonPublic);
            var initial = (ClientConfigurationInputs)capture.Invoke(null, new object[] { format });
            string fingerprint = initial.Fingerprint();
            // When: the project selects Full after capture, the current context must become different.
            StartupToolProfile.Selection = "full";
            var current = (ClientConfigurationInputs)capture.Invoke(null, new object[] { format });
            // Then: stale-context detection can reject that preview, and captured generation uses its original choice.
            Assert.AreNotEqual(fingerprint, current.Fingerprint());
            CollectionAssert.DoesNotContain(initial.Args, "--tool-profile");
            CollectionAssert.AreEqual(new[] { "--tool-profile", "full" }, current.Args.Skip(current.Args.Length - 2));
            if (format == "toml")
            {
                StringAssert.DoesNotContain("--tool-profile", CodexConfigHelper.UpsertCodexServerBlock("", "uv", initial));
                StringAssert.Contains("--tool-profile", CodexConfigHelper.UpsertCodexServerBlock("", "uv", current));
            }
            else
            {
                var root = ConfigJsonBuilder.ApplyUnityServerToExistingConfig(new JObject(), "uv", new McpClient(), initial, true);
                CollectionAssert.AreEqual(initial.Args, root["mcpServers"]["unityMCP"]["args"].ToObject<string[]>());
            }
        }

        [Test]
        public void StartupProfile_InvalidStoredOrRequestedValueCannotBecomeAnArgument()
        {
            // Given: malformed persisted text cannot be used as a command fragment.
            EditorPrefs.SetString(_profileKey, "full --other-flag");
            // When/Then: reading preserves server defaults, and an invalid setter cannot overwrite it.
            Assert.AreEqual("", StartupToolProfile.Selection);
            Assert.Throws<ArgumentException>(() => StartupToolProfile.Selection = "full --other-flag");
            Assert.AreEqual("full --other-flag", EditorPrefs.GetString(_profileKey));
        }

        [Test]
        public void StartupProfile_SelectionDoesNotChangeAnotherProjectPreference()
        {
            // Given: an unrelated synthetic project has an independent startup choice.
            string other = StartupToolProfile.PreferenceKey("owned-test-" + Guid.NewGuid().ToString("N"));
            EditorPrefs.SetString(other, "minimal");
            try
            {
                // When: this project chooses Full.
                StartupToolProfile.Selection = "full";
                // Then: the unrelated scoped preference remains intact.
                Assert.AreEqual("full", StartupToolProfile.Selection);
                Assert.AreEqual("minimal", EditorPrefs.GetString(other));
                Assert.AreNotEqual(other, _profileKey);
            }
            finally
            {
                EditorPrefs.DeleteKey(other);
            }
        }

        [Test]
        public void BuildManualConfigJson_ForKiloCode_UsesMcpContainerRemoteTypeAndEnabled()
        {
            var client = new KiloCodeConfigurator().Client;

            var root = JObject.Parse(ConfigJsonBuilder.BuildManualConfigJson(uvPath: null, client));

            Assert.AreEqual("https://app.kilo.ai/config.json", (string)root["$schema"], "Kilo Code config should include the kilo.jsonc $schema at the root");
            Assert.IsNull(root["mcpServers"], "Kilo Code must not use the legacy \"mcpServers\" container");

            var unity = (JObject)root.SelectToken("mcp.unityMCP");
            Assert.NotNull(unity, "Expected mcp.unityMCP node");
            Assert.AreEqual("remote", (string)unity["type"], "Kilo Code HTTP config must use type:remote, not type:http/streamableHttp");
            Assert.AreEqual(true, (bool)unity["enabled"], "Kilo Code config must set enabled:true");
            Assert.IsNull(unity["disabled"], "Kilo Code must not write the legacy disabled field");
            Assert.IsNotNull(unity["url"], "HTTP transport should set a url");
            Assert.IsNull(unity["command"], "HTTP transport should not include a command");
        }

        [Test]
        public void ApplyUnityServerToExistingConfig_ForKiloCode_RewritesStaleStdioToRemote()
        {
            var client = new KiloCodeConfigurator().Client;

            // Simulate a stale local (stdio) entry that Kilo would have shown as the wrong transport.
            var root = new JObject
            {
                ["mcp"] = new JObject
                {
                    ["unityMCP"] = new JObject
                    {
                        ["command"] = "uvx",
                        ["args"] = new JArray("unity-mcp-server"),
                        ["type"] = "local",
                    },
                },
            };

            var result = ConfigJsonBuilder.ApplyUnityServerToExistingConfig(root, uvPath: null, client);

            Assert.AreEqual("https://app.kilo.ai/config.json", (string)result["$schema"], "Rewrite should add the kilo.jsonc $schema when missing");
            var unity = (JObject)result.SelectToken("mcp.unityMCP");
            Assert.NotNull(unity, "Expected mcp.unityMCP node");
            Assert.AreEqual("remote", (string)unity["type"], "Existing config should be rewritten to type:remote for Kilo Code");
            Assert.AreEqual(true, (bool)unity["enabled"], "Existing config should gain enabled:true");
            Assert.IsNull(unity["command"], "HTTP transport should remove the stale stdio command");
        }

        [Test]
        public void BuildManualConfigJson_ForCline_StillUsesStreamableHttpInMcpServers()
        {
            var client = new ClineConfigurator().Client;

            var root = JObject.Parse(ConfigJsonBuilder.BuildManualConfigJson(uvPath: null, client));

            Assert.IsNull(root["$schema"], "Cline must not write a $schema");
            var unity = (JObject)root.SelectToken("mcpServers.unityMCP");
            Assert.NotNull(unity, "Expected mcpServers.unityMCP node");
            Assert.AreEqual("streamableHttp", (string)unity["type"], "Cline must keep type:streamableHttp after the name-check was replaced with a flag");
        }

        [Test]
        public void BuildManualConfigJson_ForGenericHttpClient_UsesPlainHttpType()
        {
            // A client without a type override must continue to receive the generic type:http.
            var client = new McpClient { name = "Cursor" };

            var root = JObject.Parse(ConfigJsonBuilder.BuildManualConfigJson(uvPath: null, client));
            var unity = (JObject)root.SelectToken("mcpServers.unityMCP");

            Assert.NotNull(unity, "Expected mcpServers.unityMCP node");
            Assert.AreEqual("http", (string)unity["type"], "Clients without HttpTypeValue should keep the generic type:http");
        }

        [Test]
        public void ApplyUnityServerToExistingConfig_ReplacesPreviousLaunchCredential()
        {
            // Given a client config created for an earlier server launch.
            var client = new McpClient { name = "Cursor" };
            var root = JObject.Parse(ConfigJsonBuilder.BuildManualConfigJson(null, client));
            File.WriteAllText(_tokenPath, "second-test-launch");

            // When the user configures the client for the current launch.
            root = ConfigJsonBuilder.ApplyUnityServerToExistingConfig(root, null, client);

            // Then the native HTTP header contains the current credential.
            Assert.AreEqual("second-test-launch", (string)root.SelectToken("mcpServers.unityMCP.headers.X-Unity-MCP-Token"));
        }

        [Test]
        public void ReadLocalAuthToken_OnReconnect_ReadsNewLaunchCredential()
        {
            var endpoint = new Uri("ws://127.0.0.1:8080/hub/plugin");
            Assert.AreEqual("first-test-launch", HttpEndpointUtility.ReadLocalAuthToken(endpoint));
            File.WriteAllText(_tokenPath, "second-test-launch");
            Assert.AreEqual("second-test-launch", HttpEndpointUtility.ReadLocalAuthToken(endpoint));
        }

        [TestCase(false)]
        [TestCase(true)]
        public void CodexConfiguration_LooksUpLocalAuthWithoutEmbeddingToken(bool updateExisting)
        {
            string text = updateExisting ? CodexConfigHelper.UpsertCodexServerBlock("", null) : CodexConfigHelper.BuildCodexServerBlock(null);
            using var reader = new StringReader(text);
            var root = MCPForUnity.External.Tommy.TOML.Parse(reader);
            Assert.IsNotEmpty(root["mcp_servers"]["unityMCP"]["http_headers_helper"].AsString.Value);
            StringAssert.DoesNotContain("first-test-launch", text);
        }

        [TestCase("url")]
        [TestCase("serverUrl")]
        [TestCase("httpUrl")]
        public void SwitchingToStdioRemovesEveryHttpUrlAlias(string urlProperty)
        {
            var client = new McpClient { name = "Synthetic", HttpUrlProperty = urlProperty };
            var root = JObject.Parse(ConfigJsonBuilder.BuildManualConfigJson(null, client));
            EditorConfigCache.Instance.SetUseHttpTransport(false);

            var result = ConfigJsonBuilder.ApplyUnityServerToExistingConfig(root, null, client);
            var entry = (JObject)result["mcpServers"]["unityMCP"];
            Assert.IsNull(entry["url"]);
            Assert.IsNull(entry["serverUrl"]);
            Assert.IsNull(entry["httpUrl"]);
            Assert.IsNull(entry["headers"]);
            Assert.IsNotNull(entry["command"]);
            Assert.IsNotNull(entry["args"]);
        }

        [TestCase(false)]
        [TestCase(true)]
        public void ClientSpecificJsonRewritePersistsRotatedToken(bool openClaw)
        {
            string path = _tokenPath + ".json";
            bool hadLock = EditorPrefs.HasKey(EditorPrefKeys.LockCursorConfig);
            bool originalLock = EditorPrefs.GetBool(EditorPrefKeys.LockCursorConfig, false);
            try
            {
                EditorPrefs.SetBool(EditorPrefKeys.LockCursorConfig, false);
                MCPForUnity.Editor.Clients.IMcpClientConfigurator configurator = openClaw ? new OwnedOpenClaw(path) : new OwnedOpenCode(path);
                configurator.Configure();
                Assert.AreEqual(McpStatus.Configured, configurator.Status);
                File.WriteAllText(_tokenPath, "second-test-launch");
                configurator.Configure();

                string entryPath = openClaw ? "plugins.entries.openclaw-mcp-bridge.config.servers.unityMCP" : "mcp.unityMCP";
                var entry = JObject.Parse(File.ReadAllText(path)).SelectToken(entryPath);
                Assert.AreEqual("second-test-launch", (string)entry["headers"]["X-Unity-MCP-Token"]);
                Assert.AreEqual(McpStatus.Configured, configurator.Status);
            }
            finally
            {
                File.Delete(path);
                if (hadLock)
                {
                    EditorPrefs.SetBool(EditorPrefKeys.LockCursorConfig, originalLock);
                }
                else
                {
                    EditorPrefs.DeleteKey(EditorPrefKeys.LockCursorConfig);
                }
            }
        }

        [Test]
        public void OpenClawStatusNeverRepairsAnUnownedEndpoint()
        {
            string path = _tokenPath + ".json";
            var doc = new JObject
            {
                ["plugins"] = new JObject
                {
                    ["entries"] = new JObject
                    {
                        ["openclaw-mcp-bridge"] = new JObject
                        {
                            ["enabled"] = true,
                            ["config"] = new JObject
                            {
                                ["timeout"] = 123,
                                ["retries"] = 4,
                                ["servers"] = new JObject
                                {
                                    ["unityMCP"] = new JObject
                                    {
                                        ["enabled"] = true,
                                        ["transport"] = "http",
                                        ["url"] = "http://localhost:54321/mcp",
                                    },
                                },
                            },
                        },
                    },
                },
            };
            File.WriteAllText(path, doc.ToString());
            var original = File.ReadAllBytes(path);
            var mtime = File.GetLastWriteTimeUtc(path);
            try
            {
                var configurator = new OwnedOpenClaw(path);
                Assert.AreEqual(McpStatus.IncorrectPath, configurator.CheckStatus(), configurator.Client.configStatus);
                Assert.AreEqual(McpStatus.IncorrectPath, configurator.CheckStatus(false));
                CollectionAssert.AreEqual(original, File.ReadAllBytes(path));
                Assert.AreEqual(mtime, File.GetLastWriteTimeUtc(path));
            }
            finally
            {
                File.Delete(path);
            }
        }

        [TestCase(false)]
        [TestCase(true)]
        public void UnregisterHonorsLockBeforeAnyFileMutation(bool toml)
        {
            WithOwnedConfiguration(
                toml,
                (configurator, path) =>
                {
                    EditorPrefs.SetBool(EditorPrefKeys.LockCursorConfig, true);
                    var before = File.ReadAllBytes(path);
                    Assert.Throws<InvalidOperationException>(configurator.Unregister);
                    CollectionAssert.AreEqual(before, File.ReadAllBytes(path));
                }
            );
        }

        [TestCase(false)]
        [TestCase(true)]
        public void UnregisterPreservesBomAndUnrelatedSettings(bool toml)
        {
            WithOwnedConfiguration(
                toml,
                (configurator, path) =>
                {
                    configurator.Unregister();
                    var bytes = File.ReadAllBytes(path);
                    CollectionAssert.AreEqual(new byte[] { 239, 187, 191 }, new[] { bytes[0], bytes[1], bytes[2] });
                    StringAssert.Contains("other-owned-value", File.ReadAllText(path));
                    StringAssert.DoesNotContain("unityMCP", File.ReadAllText(path));
                    Assert.AreEqual(McpStatus.NotConfigured, configurator.Status);
                }
            );
        }

        [TestCase(false)]
        [TestCase(true)]
        public void UnregisterFinalComparisonPreservesConcurrentChanges(bool failWrite)
        {
            WithOwnedConfiguration(
                false,
                (configurator, path) =>
                {
                    var service = MCPForUnity.Editor.Services.ClientConfigurationChangeService.Shared;
                    var field = service
                        .GetType()
                        .GetField("beforeCommitComparison", System.Reflection.BindingFlags.Instance | System.Reflection.BindingFlags.NonPublic);
                    var original = field.GetValue(service);
                    var expected = File.ReadAllBytes(path);
                    try
                    {
                        field.SetValue(
                            service,
                            new Action(() =>
                            {
                                if (failWrite)
                                    throw new IOException("synthetic write failure");
                                File.AppendAllText(path, " ");
                                expected = File.ReadAllBytes(path);
                            })
                        );
                        Assert.Throws<InvalidOperationException>(configurator.Unregister);
                        CollectionAssert.AreEqual(expected, File.ReadAllBytes(path));
                        CollectionAssert.AreEquivalent(new[] { path }, Directory.GetFiles(Path.GetDirectoryName(path), Path.GetFileName(path) + "*"));
                    }
                    finally
                    {
                        field.SetValue(service, original);
                    }
                }
            );
        }

        [TestCase("duplicate")]
        [TestCase("encoding")]
        [TestCase("large")]
        public void JsonUnregisterRefusesAmbiguousOrUnboundedInput(string kind)
        {
            WithOwnedConfiguration(
                false,
                (configurator, path) =>
                {
                    if (kind == "duplicate")
                        File.WriteAllText(path, "{\"mcpServers\":{\"unityMCP\":{},\"unityMCP\":{}}}");
                    if (kind == "encoding")
                        File.WriteAllBytes(path, new byte[] { 0xff, 0xfe, 0x00 });
                    if (kind == "large")
                        File.WriteAllText(path, new string(' ', 1024 * 1024 + 1));
                    var bytes = File.ReadAllBytes(path);
                    Assert.Throws<InvalidOperationException>(configurator.Unregister);
                    CollectionAssert.AreEqual(bytes, File.ReadAllBytes(path));
                }
            );
        }

        [TestCase(false)]
        [TestCase(true)]
        public void LockedConfigureDoesNotCreateMissingParentDirectory(bool toml)
        {
            string parent = _tokenPath + "-missing-parent";
            string path = Path.Combine(parent, "config");
            bool hadLock = EditorPrefs.HasKey(EditorPrefKeys.LockCursorConfig);
            bool originalLock = EditorPrefs.GetBool(EditorPrefKeys.LockCursorConfig, false);
            try
            {
                EditorPrefs.SetBool(EditorPrefKeys.LockCursorConfig, true);
                MCPForUnity.Editor.Clients.IMcpClientConfigurator configurator = toml ? new OwnedCodex(path) : new OwnedJson(path);
                Assert.Throws<InvalidOperationException>(configurator.Configure);
                Assert.IsFalse(Directory.Exists(parent));
            }
            finally
            {
                if (Directory.Exists(parent))
                    Directory.Delete(parent);
                if (hadLock)
                    EditorPrefs.SetBool(EditorPrefKeys.LockCursorConfig, originalLock);
                else
                    EditorPrefs.DeleteKey(EditorPrefKeys.LockCursorConfig);
            }
        }

        private void WithOwnedConfiguration(bool toml, Action<MCPForUnity.Editor.Clients.IMcpClientConfigurator, string> action)
        {
            string path = _tokenPath + (toml ? ".toml" : ".json");
            bool hadLock = EditorPrefs.HasKey(EditorPrefKeys.LockCursorConfig);
            bool originalLock = EditorPrefs.GetBool(EditorPrefKeys.LockCursorConfig, false);
            try
            {
                EditorPrefs.SetBool(EditorPrefKeys.LockCursorConfig, false);
                string text = toml
                    ? "theme = \"other-owned-value\"\n[mcp_servers.unityMCP]\nurl = \"http://localhost:54321/mcp\"\n"
                    : "{\"other\":\"other-owned-value\",\"mcpServers\":{\"unityMCP\":{\"url\":\"http://localhost:54321/mcp\"}}}";
                File.WriteAllText(path, text, new System.Text.UTF8Encoding(true));
                MCPForUnity.Editor.Clients.IMcpClientConfigurator configurator = toml ? new OwnedCodex(path) : new OwnedJson(path);
                action(configurator, path);
            }
            finally
            {
                File.Delete(path);
                if (hadLock)
                    EditorPrefs.SetBool(EditorPrefKeys.LockCursorConfig, originalLock);
                else
                    EditorPrefs.DeleteKey(EditorPrefKeys.LockCursorConfig);
            }
        }

        private sealed class OwnedJson : MCPForUnity.Editor.Clients.JsonFileMcpConfigurator
        {
            private readonly string path;

            public OwnedJson(string path)
                : base(new McpClient { name = "Owned JSON" })
            {
                this.path = path;
            }

            public override string GetConfigPath() => path;
        }

        private sealed class OwnedCodex : MCPForUnity.Editor.Clients.CodexMcpConfigurator
        {
            private readonly string path;

            public OwnedCodex(string path)
                : base(new McpClient { name = "Owned Codex" })
            {
                this.path = path;
            }

            public override string GetConfigPath() => path;
        }

        private sealed class OwnedOpenCode : OpenCodeConfigurator
        {
            private readonly string path;

            public OwnedOpenCode(string path)
            {
                this.path = path;
            }

            public override string GetConfigPath() => path;
        }

        private sealed class OwnedOpenClaw : OpenClawConfigurator
        {
            private readonly string path;

            public OwnedOpenClaw(string path)
            {
                this.path = path;
            }

            public override string GetConfigPath() => path;
        }

        [Test]
        public void ReadLocalAuthToken_MissingFile_ReturnsNoCredential()
        {
            File.Delete(_tokenPath);
            Assert.AreEqual(string.Empty, HttpEndpointUtility.ReadLocalAuthToken(new Uri("http://localhost:8080")));
        }
    }
}
