using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using MCPForUnity.Editor.Clients;
using MCPForUnity.Editor.Clients.Configurators;
using MCPForUnity.Editor.Constants;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Editor.Models;
using MCPForUnity.Editor.Services;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEditor;

namespace MCPForUnityTests.Editor.Clients
{
    [Parallelizable(ParallelScope.None)]
    public class StartupConfigurationOwnershipTests : TransportPreferenceTestBase
    {
        private const string FirstSource = "https://github.com/ykh09242/unity-mcp/archive/0000000000000000000000000000000000000000.zip#subdirectory=Server";
        private const string NextSource = "https://github.com/ykh09242/unity-mcp/archive/1111111111111111111111111111111111111111.zip#subdirectory=Server";
        private const string FirstGitSource = "git+https://github.com/ykh09242/unity-mcp.git@0000000000000000000000000000000000000000#subdirectory=Server";
        private const string NextGitSource = "git+https://github.com/ykh09242/unity-mcp.git@1111111111111111111111111111111111111111#subdirectory=Server";
        private readonly Dictionary<string, string> strings = new();
        private readonly Dictionary<string, bool?> flags = new();
        private string directory;

        [SetUp]
        public void SetUp()
        {
            directory = Path.Combine(Path.GetTempPath(), "UnityMCPStartupOwnership_" + Guid.NewGuid().ToString("N"));
            Directory.CreateDirectory(directory);
            string executable = Path.Combine(directory, "uvx.exe");
            File.WriteAllText(executable, "synthetic executable; never executed");
            SetString(EditorPrefKeys.UvxPathOverride, executable);
            SetString(EditorPrefKeys.GitUrlOverride, FirstSource);
            SetString(StartupToolProfile.PreferenceKey(ProjectIdentityUtility.GetProjectHash()), "");
            SetBool(EditorPrefKeys.AutoRegisterEnabled, true);
            SetBool(EditorPrefKeys.LockCursorConfig, false);
            SetBool(EditorPrefKeys.DevModeForceServerRefresh, true);
            SetBool(EditorPrefKeys.UseHttpTransport, false);
            EditorConfigurationCache.Instance.Refresh();
        }

        [TearDown]
        public void TearDown()
        {
            foreach (var pair in strings)
                if (pair.Value == null)
                    EditorPrefs.DeleteKey(pair.Key);
                else
                    EditorPrefs.SetString(pair.Key, pair.Value);
            foreach (var pair in flags)
                if (pair.Value.HasValue)
                    EditorPrefs.SetBool(pair.Key, pair.Value.Value);
                else
                    EditorPrefs.DeleteKey(pair.Key);
            strings.Clear();
            flags.Clear();
            foreach (string kind in new[] { "Json", "Codex", "OpenCode" })
            {
                string path = Path.Combine(directory, kind == "Codex" ? "client.toml" : "client.json");
                EditorPrefs.DeleteKey(GeneratedClientConfigOwnership.Key(kind, path));
            }
            string resolved = Path.GetFullPath(directory);
            Assert.IsTrue(resolved.StartsWith(Path.GetFullPath(Path.GetTempPath()), StringComparison.OrdinalIgnoreCase));
            Assert.IsTrue(Path.GetFileName(resolved).StartsWith("UnityMCPStartupOwnership_", StringComparison.Ordinal));
            Directory.Delete(resolved, true);
            EditorConfigurationCache.Instance.Refresh();
        }

        [TestCase("codex")]
        [TestCase("opencode")]
        public void StartupPreservesUnownedManualPinnedDisabledAbsentAndLegacyEntries(string kind)
        {
            var client = Create(kind);
            foreach (string fixture in new[] { "manual", "pinned", "disabled", "absent", "legacy" })
            {
                string original = UnownedDocument(kind, fixture);
                File.WriteAllText(client.GetConfigPath(), original);
                byte[] bytes = File.ReadAllBytes(client.GetConfigPath());
                client.CheckStatus(true);
                CollectionAssert.AreEqual(bytes, File.ReadAllBytes(client.GetConfigPath()), fixture);
            }
            File.Delete(client.GetConfigPath());
            client.CheckStatus(true);
            Assert.IsFalse(File.Exists(client.GetConfigPath()));
        }

        [TestCase("json", "core", false)]
        [TestCase("json", "minimal", false)]
        [TestCase("json", "full", false)]
        [TestCase("codex", "core", false)]
        [TestCase("opencode", "minimal", false)]
        [TestCase("json", "full", true)]
        [TestCase("codex", "full", true)]
        [TestCase("opencode", "full", true)]
        public void ExplicitConfigureRecordsProfileOwnershipAndStartupFollowsSource(string kind, string profile, bool gitSource)
        {
            string first = gitSource ? FirstGitSource : FirstSource;
            string next = gitSource ? NextGitSource : NextSource;
            EditorPrefs.SetString(EditorPrefKeys.GitUrlOverride, first);
            StartupToolProfile.Selection = profile;
            var client = Create(kind);
            File.WriteAllText(client.GetConfigPath(), UnownedDocument(kind, "manual"));
            client.Configure();
            StringAssert.Contains(first, File.ReadAllText(client.GetConfigPath()));
            StringAssert.Contains("--tool-profile", File.ReadAllText(client.GetConfigPath()));
            string format =
                kind == "codex" ? "Codex"
                : kind == "opencode" ? "OpenCode"
                : "Json";
            Assert.IsTrue(EditorPrefs.HasKey(GeneratedClientConfigOwnership.Key(format, client.GetConfigPath())));
            EditorPrefs.SetString(EditorPrefKeys.GitUrlOverride, next);
            Assert.AreEqual(McpStatus.Configured, client.CheckStatus(true));
            StringAssert.Contains(next, File.ReadAllText(client.GetConfigPath()));
        }

        [TestCase("0000000000000000000000000000000000000000", true)]
        [TestCase("ABCDEFabcdef1234567890123456789012345678", true)]
        [TestCase("0000000", false)]
        [TestCase("beta", false)]
        [TestCase("gggggggggggggggggggggggggggggggggggggggg", false)]
        public void RecognizerRequiresTheSupportedFullCommitForkGitSource(string revision, bool accepted)
        {
            string source = "git+https://github.com/ykh09242/unity-mcp.git@" + revision + "#subdirectory=Server";
            var entry = new JObject
            {
                ["command"] = "uvx",
                ["args"] = new JArray("--from", source, "mcp-for-unity", "--transport", "stdio", "--tool-profile", "core"),
            };
            Assert.AreEqual(accepted, JsonFileMcpConfigurator.IsGeneratedStdioEntry(entry));
            ((JArray)entry["args"])[1] = source.Replace("ykh09242", "another-owner");
            Assert.IsFalse(JsonFileMcpConfigurator.IsGeneratedStdioEntry(entry));
            ((JArray)entry["args"])[1] = source.Replace("subdirectory=Server", "subdirectory=Other");
            Assert.IsFalse(JsonFileMcpConfigurator.IsGeneratedStdioEntry(entry));
        }

        [TestCase("json")]
        [TestCase("codex")]
        [TestCase("opencode")]
        public void GeneratedEntryDoesNotRefreshWhileAutomaticRegistrationIsDisabled(string kind)
        {
            var client = Create(kind);
            client.Configure();
            byte[] bytes = File.ReadAllBytes(client.GetConfigPath());
            EditorPrefs.SetBool(EditorPrefKeys.AutoRegisterEnabled, false);
            EditorPrefs.SetString(EditorPrefKeys.GitUrlOverride, NextSource);
            client.CheckStatus(true);
            CollectionAssert.AreEqual(bytes, File.ReadAllBytes(client.GetConfigPath()));
        }

        [TestCase("json", "pin")]
        [TestCase("json", "disabled")]
        [TestCase("json", "custom")]
        [TestCase("json", "suffix")]
        [TestCase("codex", "pin")]
        [TestCase("codex", "disabled")]
        [TestCase("codex", "custom")]
        [TestCase("codex", "suffix")]
        [TestCase("opencode", "pin")]
        [TestCase("opencode", "disabled")]
        [TestCase("opencode", "custom")]
        [TestCase("opencode", "suffix")]
        public void EditingGeneratedEntryRevokesAutomaticRefresh(string kind, string edit)
        {
            StartupToolProfile.Selection = "full";
            var client = Create(kind);
            client.Configure();
            string document = File.ReadAllText(client.GetConfigPath());
            if (edit == "pin")
                document = document.Replace(FirstSource, "mcpforunity==0.1.0");
            else if (edit == "suffix")
                document = document.Replace("full", "unknown-profile");
            else if (kind == "codex")
                document = document.Replace("[mcp_servers.unityMCP]", "[mcp_servers.unityMCP]\n" + (edit == "disabled" ? "enabled = false" : "custom = true"));
            else
            {
                var root = JObject.Parse(document);
                var entry = (JObject)root[kind == "opencode" ? "mcp" : "mcpServers"]["unityMCP"];
                entry[edit == "disabled" ? "enabled" : "custom"] = edit != "disabled";
                document = root.ToString();
            }
            File.WriteAllText(client.GetConfigPath(), document);
            byte[] bytes = File.ReadAllBytes(client.GetConfigPath());
            EditorPrefs.SetString(EditorPrefKeys.GitUrlOverride, NextSource);
            client.CheckStatus(true);
            CollectionAssert.AreEqual(bytes, File.ReadAllBytes(client.GetConfigPath()));
        }

        [TestCase("core", true)]
        [TestCase("minimal", true)]
        [TestCase("full", true)]
        [TestCase("custom", false)]
        [TestCase("FULL", false)]
        public void RecognizerAcceptsOnlySupportedProfileSuffix(string profile, bool accepted)
        {
            var entry = new JObject
            {
                ["command"] = "uvx",
                ["args"] = new JArray("--from", FirstSource, "mcp-for-unity", "--transport", "stdio", "--tool-profile", profile),
            };
            Assert.AreEqual(accepted, JsonFileMcpConfigurator.IsGeneratedStdioEntry(entry));
            ((JArray)entry["args"]).Add("--custom");
            Assert.IsFalse(JsonFileMcpConfigurator.IsGeneratedStdioEntry(entry));
        }

        private IMcpClientConfigurator Create(string kind)
        {
            string path = Path.Combine(directory, kind == "codex" ? "client.toml" : "client.json");
            var descriptor = new McpClient
            {
                name = "Synthetic",
                windowsConfigPath = path,
                macConfigPath = path,
                linuxConfigPath = path,
            };
            return kind == "codex" ? new OwnedCodex(descriptor)
                : kind == "opencode" ? new OwnedOpenCode(path)
                : new OwnedJson(descriptor);
        }

        private static string UnownedDocument(string kind, string fixture)
        {
            if (kind == "codex")
            {
                if (fixture == "absent")
                    return "# preserve\nmodel = 'synthetic'\n";
                string table = fixture == "legacy" ? "mcpServers" : "mcp_servers";
                string launch =
                    fixture == "manual"
                        ? "command = 'python'\nargs = ['owned-custom-wrapper.py']"
                        : "command = 'uvx'\nargs = ['--from', 'mcpforunity==0.1.0', 'mcp-for-unity', '--transport', 'stdio']";
                return "# preserve\n[" + table + ".unityMCP]\n" + launch + (fixture == "disabled" ? "\nenabled = false" : "") + "\n";
            }
            if (fixture == "absent")
                return "{ \"other\": true }\n";
            var command =
                fixture == "manual"
                    ? new JArray("python", "owned-custom-wrapper.py")
                    : new JArray("uvx", "--from", "mcpforunity==0.1.0", "mcp-for-unity", "--transport", "stdio");
            var entry =
                kind == "opencode"
                    ? new JObject
                    {
                        ["type"] = "local",
                        ["command"] = command,
                        ["enabled"] = fixture != "disabled",
                    }
                    : new JObject { ["command"] = command[0].DeepClone(), ["args"] = new JArray(command.Skip(1)) };
            return new JObject { [kind == "opencode" ? "mcp" : "mcpServers"] = new JObject { ["unityMCP"] = entry } }.ToString();
        }

        private void SetString(string key, string value)
        {
            strings[key] = EditorPrefs.HasKey(key) ? EditorPrefs.GetString(key) : null;
            EditorPrefs.SetString(key, value);
        }

        private void SetBool(string key, bool value)
        {
            flags[key] = EditorPrefs.HasKey(key) ? EditorPrefs.GetBool(key) : null;
            EditorPrefs.SetBool(key, value);
        }

        private sealed class OwnedJson : JsonFileMcpConfigurator
        {
            public OwnedJson(McpClient descriptor)
                : base(descriptor) { }
        }

        private sealed class OwnedCodex : CodexMcpConfigurator
        {
            public OwnedCodex(McpClient descriptor)
                : base(descriptor) { }
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
    }
}
