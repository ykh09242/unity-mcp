using System;
using System.Collections.Generic;
using System.IO;
using MCPForUnity.Editor.Clients;
using MCPForUnity.Editor.Clients.Configurators;
using MCPForUnity.Editor.Constants;
using MCPForUnity.Editor.Models;
using MCPForUnity.Editor.Services;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEditor;

namespace MCPForUnityTests.Editor.Clients
{
    public class ManualStdioPreservationTests : TransportPreferenceTestBase
    {
        private string directory;
        private bool hadAutoRegister;
        private bool autoRegister;
        private bool hadLock;
        private bool configLock;
        private bool hadForceRefresh;
        private bool forceRefresh;
        private readonly Dictionary<string, string> savedPaths = new();

        [SetUp]
        public void SetUp()
        {
            directory = Path.Combine(Path.GetTempPath(), "UnityMCPManualConfig", Guid.NewGuid().ToString("N"));
            Directory.CreateDirectory(directory);
            hadAutoRegister = EditorPrefs.HasKey(EditorPrefKeys.AutoRegisterEnabled);
            autoRegister = EditorPrefs.GetBool(EditorPrefKeys.AutoRegisterEnabled, true);
            hadLock = EditorPrefs.HasKey(EditorPrefKeys.LockCursorConfig);
            configLock = EditorPrefs.GetBool(EditorPrefKeys.LockCursorConfig, false);
            hadForceRefresh = EditorPrefs.HasKey(EditorPrefKeys.DevModeForceServerRefresh);
            forceRefresh = EditorPrefs.GetBool(EditorPrefKeys.DevModeForceServerRefresh, false);
            EditorPrefs.SetBool(EditorPrefKeys.AutoRegisterEnabled, true);
            EditorPrefs.SetBool(EditorPrefKeys.UseHttpTransport, false);
            EditorConfigurationCache.Instance.Refresh();
        }

        [TearDown]
        public void TearDown()
        {
            if (hadAutoRegister)
                EditorPrefs.SetBool(EditorPrefKeys.AutoRegisterEnabled, autoRegister);
            else
                EditorPrefs.DeleteKey(EditorPrefKeys.AutoRegisterEnabled);
            if (hadLock)
                EditorPrefs.SetBool(EditorPrefKeys.LockCursorConfig, configLock);
            else
                EditorPrefs.DeleteKey(EditorPrefKeys.LockCursorConfig);
            if (hadForceRefresh)
                EditorPrefs.SetBool(EditorPrefKeys.DevModeForceServerRefresh, forceRefresh);
            else
                EditorPrefs.DeleteKey(EditorPrefKeys.DevModeForceServerRefresh);
            foreach (var pair in savedPaths)
                if (pair.Value == null)
                    EditorPrefs.DeleteKey(pair.Key);
                else
                    EditorPrefs.SetString(pair.Key, pair.Value);
            savedPaths.Clear();
            EditorPrefs.DeleteKey(JsonFileMcpConfigurator.GeneratedOwnershipKey(Path.Combine(directory, "client.json")));
            Directory.Delete(directory, true);
        }

        [TestCase("python", "['server.py']")]
        [TestCase("uv", "['tool','run','mcp-for-unity']")]
        [TestCase("wrapper", "[]")]
        [TestCase("wrapper", null)]
        public void AutomaticStatusCheckPreservesManualCommands(string command, string args)
        {
            var entry = new JObject
            {
                ["command"] = command,
                ["env"] = new JObject { ["SYNTHETIC_OPTION"] = "custom" },
            };
            if (args != null)
                entry["args"] = JArray.Parse(args);
            var configurator = Write(entry, out string original);
            Assert.AreEqual(McpStatus.Configured, configurator.CheckStatus(true));
            Assert.AreEqual(ConfiguredTransport.Stdio, configurator.Client.configuredTransport);
            Assert.AreEqual(original, File.ReadAllText(configurator.GetConfigPath()));
        }

        [Test]
        public void SelectedHttpTransportDoesNotOverwriteManualStdio()
        {
            var configurator = Write(new JObject { ["command"] = "wrapper" }, out string original);
            EditorPrefs.SetBool(EditorPrefKeys.UseHttpTransport, true);
            EditorConfigurationCache.Instance.Refresh();
            Assert.AreEqual(McpStatus.IncorrectPath, configurator.CheckStatus(true));
            Assert.AreEqual(original, File.ReadAllText(configurator.GetConfigPath()));
        }

        [Test]
        public void AutoRegistrationDisabledPreservesGeneratedEntry()
        {
            var configurator = ConfigureOwnedGenerated();
            string original = File.ReadAllText(configurator.GetConfigPath());
            EditorPrefs.SetBool(EditorPrefKeys.AutoRegisterEnabled, false);
            EditorPrefs.SetBool(EditorPrefKeys.UseHttpTransport, true);
            SavePath(EditorPrefKeys.GitUrlOverride, "https://synthetic.example.test/server.zip");
            EditorConfigurationCache.Instance.Refresh();
            Assert.AreEqual(McpStatus.VersionMismatch, configurator.CheckStatus(true));
            Assert.AreEqual(original, File.ReadAllText(configurator.GetConfigPath()));
        }

        [Test]
        public void LegacyOrManuallyPinnedUvConfigIsPreservedWithoutAnOwnershipFingerprint()
        {
            var entry = GeneratedEntry();
            Assert.IsTrue(JsonFileMcpConfigurator.IsGeneratedStdioEntry(entry));
            var configurator = Write(entry, out string original);
            SavePath(EditorPrefKeys.GitUrlOverride, "https://synthetic.example.test/server.zip");
            Assert.AreEqual(McpStatus.VersionMismatch, configurator.CheckStatus(true));
            Assert.AreEqual(
                original,
                File.ReadAllText(configurator.GetConfigPath()),
                "Matching the generated shape does not authorize replacing a manual version pin"
            );
        }

        [Test]
        public void ExplicitConfigureStillReplacesManualCommandInTemporaryFile()
        {
            var configurator = Write(new JObject { ["command"] = "wrapper" }, out _);
            string executable = Path.Combine(directory, "uvx.exe");
            File.WriteAllText(executable, "synthetic executable — never executed");
            File.WriteAllText(Path.Combine(directory, "pyproject.toml"), "[project]\nname='synthetic'\n");
            SavePath(EditorPrefKeys.UvxPathOverride, executable);
            SavePath(EditorPrefKeys.GitUrlOverride, directory);
            EditorPrefs.SetBool(EditorPrefKeys.LockCursorConfig, false);
            configurator.Configure();
            var entry = JObject.Parse(File.ReadAllText(configurator.GetConfigPath()))["mcpServers"]["unityMCP"];
            Assert.AreEqual(executable, (string)entry["command"]);
            CollectionAssert.Contains(entry["args"].ToObject<string[]>(), "--from");
        }

        [Test]
        public void CustomArgumentsAndEnvironmentAreNotRecognizedAsGenerated()
        {
            var entry = GeneratedEntry();
            ((JArray)entry["args"]).Add("--custom");
            Assert.IsFalse(JsonFileMcpConfigurator.IsGeneratedStdioEntry(entry));
            entry = GeneratedEntry();
            entry["env"] = new JObject { ["SYNTHETIC_OPTION"] = "custom" };
            Assert.IsFalse(JsonFileMcpConfigurator.IsGeneratedStdioEntry(entry));
        }

        [Test]
        public void GeneratedPinnedUrlMatchIsConfiguredWithoutRewriting()
        {
            string source = "https://github.com/ykh09242/unity-mcp/archive/0000000000000000000000000000000000000000.zip#subdirectory=Server";
            SavePath(EditorPrefKeys.GitUrlOverride, source);
            var entry = GeneratedEntry();
            string executable = Path.Combine(directory, "uvx.exe");
            File.WriteAllText(executable, "inert launcher fixture; never executed");
            SavePath(EditorPrefKeys.UvxPathOverride, executable);
            entry["command"] = executable;
            ((JArray)entry["args"])[3] = source;
            var configurator = Write(entry, out string original);
            Assert.AreEqual(McpStatus.Configured, configurator.CheckStatus(true));
            Assert.AreEqual(original, File.ReadAllText(configurator.GetConfigPath()));
        }

        [Test]
        public void ExplicitlyGeneratedEntryCanAutomaticallyFollowSelectedSource()
        {
            var configurator = ConfigureOwnedGenerated();
            string next = "https://github.com/ykh09242/unity-mcp/archive/1111111111111111111111111111111111111111.zip#subdirectory=Server";
            EditorPrefs.SetString(EditorPrefKeys.GitUrlOverride, next);
            Assert.AreEqual(McpStatus.Configured, configurator.CheckStatus(true));
            var args = JObject.Parse(File.ReadAllText(configurator.GetConfigPath()))["mcpServers"]["unityMCP"]["args"].ToObject<string[]>();
            CollectionAssert.Contains(args, next);
        }

        [Test]
        public void EditingAnOwnedGeneratedEntryRevokesAutomaticRewrite()
        {
            var configurator = ConfigureOwnedGenerated();
            var root = JObject.Parse(File.ReadAllText(configurator.GetConfigPath()));
            var args = (JArray)root["mcpServers"]["unityMCP"]["args"];
            for (int index = 0; index < args.Count - 1; index++)
                if ((string)args[index] == "--from")
                    args[index + 1] = "mcpforunity==0.2.0";
            string manual = root.ToString();
            File.WriteAllText(configurator.GetConfigPath(), manual);
            Assert.AreEqual(McpStatus.VersionMismatch, configurator.CheckStatus(true));
            Assert.AreEqual(manual, File.ReadAllText(configurator.GetConfigPath()));
        }

        [TestCase("missing", McpStatus.IncorrectPath)]
        [TestCase("override", McpStatus.IncorrectPath)]
        [TestCase("valid", McpStatus.Configured)]
        public void OpenCodeGeneratedStatusValidatesExecutableWithoutRunningOrRewriting(string condition, McpStatus expected)
        {
            // Given: an owned generated entry and inert launchers that cannot execute.
            string source = "https://github.com/ykh09242/unity-mcp/archive/0000000000000000000000000000000000000000.zip#subdirectory=Server";
            string executable = Path.Combine(directory, "uvx.exe");
            File.WriteAllText(executable, "inert launcher fixture; never executed");
            SavePath(EditorPrefKeys.GitUrlOverride, source);
            SavePath(EditorPrefKeys.UvxPathOverride, executable);
            var entry = new JObject
            {
                ["type"] = "local",
                ["command"] = new JArray(executable, "--python", ">=3.11", "--from", source, "mcp-for-unity", "--transport", "stdio"),
                ["enabled"] = true,
            };
            string path = Path.Combine(directory, "opencode.json");
            string original = new JObject { ["mcp"] = new JObject { ["unityMCP"] = entry } }.ToString();
            File.WriteAllText(path, original);
            string generatedEntry = OpenCodeConfigurator.GeneratedStdioEntry(entry);
            Assert.IsNotNull(generatedEntry, "The fixture must exercise generated-launcher validation rather than manual command status.");
            GeneratedClientConfigOwnership.Record("OpenCode", path, generatedEntry);
            if (condition == "missing")
                File.Delete(executable);
            else if (condition == "override")
            {
                string replacement = Path.Combine(directory, "selected-uvx.exe");
                File.WriteAllText(replacement, "inert replacement; never executed");
                EditorPrefs.SetString(EditorPrefKeys.UvxPathOverride, replacement);
            }
            var configurator = new SyntheticOpenCodeConfigurator(path);
            try
            {
                // When: ordinary status inspection allows the existing automatic-refresh path.
                Assert.AreEqual(expected, configurator.CheckStatus(true));
                // Then: invalid launchers retain recovery details and the config is untouched.
                if (expected == McpStatus.IncorrectPath)
                    StringAssert.Contains(condition == "missing" ? "missing" : "selected override", configurator.Client.configStatus);
                Assert.AreEqual(original, File.ReadAllText(path));
            }
            finally
            {
                EditorPrefs.DeleteKey(GeneratedClientConfigOwnership.Key("OpenCode", path));
            }
        }

        private sealed class SyntheticOpenCodeConfigurator : OpenCodeConfigurator
        {
            private readonly string path;

            public SyntheticOpenCodeConfigurator(string path) => this.path = path;

            public override string GetConfigPath() => path;
        }

        private SyntheticConfigurator ConfigureOwnedGenerated()
        {
            string executable = Path.Combine(directory, "uvx.exe");
            File.WriteAllText(executable, "synthetic executable — never executed");
            SavePath(EditorPrefKeys.UvxPathOverride, executable);
            SavePath(
                EditorPrefKeys.GitUrlOverride,
                "https://github.com/ykh09242/unity-mcp/archive/0000000000000000000000000000000000000000.zip#subdirectory=Server"
            );
            EditorPrefs.SetBool(EditorPrefKeys.LockCursorConfig, false);
            EditorPrefs.SetBool(EditorPrefKeys.DevModeForceServerRefresh, true);
            var configurator = Write(new JObject { ["command"] = "wrapper" }, out _);
            configurator.Configure();
            Assert.IsTrue(EditorPrefs.HasKey(JsonFileMcpConfigurator.GeneratedOwnershipKey(configurator.GetConfigPath())));
            return configurator;
        }

        private static JObject GeneratedEntry() =>
            new JObject
            {
                ["command"] = "uvx",
                ["args"] = new JArray("--python", ">=3.11", "--from", "mcpforunity==0.1.0", "mcp-for-unity", "--transport", "stdio"),
            };

        private void SavePath(string key, string value)
        {
            if (!savedPaths.ContainsKey(key))
                savedPaths[key] = EditorPrefs.HasKey(key) ? EditorPrefs.GetString(key) : null;
            EditorPrefs.SetString(key, value);
        }

        private SyntheticConfigurator Write(JObject entry, out string original)
        {
            string path = Path.Combine(directory, "client.json");
            original = new JObject { ["mcpServers"] = new JObject { ["unityMCP"] = entry } }.ToString();
            File.WriteAllText(path, original);
            return new SyntheticConfigurator(
                new McpClient
                {
                    name = "Synthetic",
                    windowsConfigPath = path,
                    macConfigPath = path,
                    linuxConfigPath = path,
                }
            );
        }

        private sealed class SyntheticConfigurator : JsonFileMcpConfigurator
        {
            public SyntheticConfigurator(McpClient client)
                : base(client) { }
        }
    }

    public class OpenCodeManualStatusTests
    {
        [TestCase(false, McpStatus.IncorrectPath)]
        [TestCase(true, McpStatus.Configured)]
        public void OpenCodeManualWrapperChecksExplicitPathWithoutExecuting(bool exists, McpStatus expected)
        {
            string directory = Path.Combine(Path.GetTempPath(), "OpenCodeManualStatus", Guid.NewGuid().ToString("N"));
            Directory.CreateDirectory(directory);
            try
            {
                const string source = "synthetic-package-source";
                string executable = Path.Combine(directory, "wrapper");
                if (exists)
                    File.WriteAllText(executable, "inert wrapper");
                var entry = new JObject
                {
                    ["type"] = "local",
                    ["command"] = new JArray(executable, "--from", source, "mcp-for-unity"),
                    ["enabled"] = true,
                    ["custom"] = true,
                };
                Assert.IsNull(OpenCodeConfigurator.GeneratedStdioEntry(entry));
                Assert.AreEqual(expected, OpenCodeConfigurator.ValidateManualStdio(entry, source, out string detail));
                if (exists)
                    Assert.AreEqual(OpenCodeConfigurator.ManualSourceOnlyStatus, detail);
                else
                    StringAssert.Contains("does not exist", detail);
                Assert.AreEqual(McpStatus.IncorrectPath, OpenCodeConfigurator.ValidateManualStdio(entry, "different-source", out _));
            }
            finally
            {
                Directory.Delete(directory, true);
            }
        }
    }
}
