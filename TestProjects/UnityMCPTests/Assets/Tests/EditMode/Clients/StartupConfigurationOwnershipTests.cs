using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Reflection;
using MCPForUnity.Editor.Clients;
using MCPForUnity.Editor.Clients.Configurators;
using MCPForUnity.Editor.Constants;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Editor.Migrations;
using MCPForUnity.Editor.Models;
using MCPForUnity.Editor.Services;
using MCPForUnityTests.Editor.Helpers;
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
        private object registry;
        private string claudeConfigDirectory;

        [SetUp]
        public void SetUp()
        {
            directory = Path.Combine(Path.GetTempPath(), "UnityMCPStartupOwnership_" + Guid.NewGuid().ToString("N"));
            Directory.CreateDirectory(directory);
            registry = typeof(McpClientRegistry).GetField("cached", BindingFlags.Static | BindingFlags.NonPublic).GetValue(null);
            claudeConfigDirectory = Environment.GetEnvironmentVariable("CLAUDE_CONFIG_DIR");
            Environment.SetEnvironmentVariable("CLAUDE_CONFIG_DIR", directory);
            SetString(EditorPrefKeys.ClientProjectDirOverride, directory);
            SetString(EditorPrefKeys.LastStdIoUpgradeVersion, "");
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
            typeof(McpClientRegistry).GetField("cached", BindingFlags.Static | BindingFlags.NonPublic).SetValue(null, registry);
            Environment.SetEnvironmentVariable("CLAUDE_CONFIG_DIR", claudeConfigDirectory);
            EditorPrefs.DeleteKey(GeneratedClientConfigOwnership.Key("Claude", Path.Combine(directory, ".claude.json") + "|" + Path.GetFullPath(directory)));
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

        [TestCase(null, false, true)]
        [TestCase(null, true, true)]
        [TestCase("true", false, true)]
        [TestCase("true", true, true)]
        [TestCase("false", false, false)]
        [TestCase("false", true, false)]
        [TestCase("'false'", false, false)]
        [TestCase("'false'", true, false)]
        [TestCase("0", false, false)]
        [TestCase("0", true, false)]
        [TestCase("[]", false, false)]
        [TestCase("[]", true, false)]
        public void CodexStdioStatusHonorsActivationWithoutRewriting(string enabled, bool attemptAutoRewrite, bool configured)
        {
            var client = (OwnedCodex)Create("codex");
            client.Configure();
            string document = File.ReadAllText(client.GetConfigPath());
            if (enabled != null)
                document = document.Replace("[mcp_servers.unityMCP]", "[mcp_servers.unityMCP]\nenabled = " + enabled);
            File.WriteAllText(client.GetConfigPath(), document);
            byte[] before = File.ReadAllBytes(client.GetConfigPath());

            Assert.AreEqual(configured ? McpStatus.Configured : McpStatus.IncorrectPath, client.CheckStatus(attemptAutoRewrite));
            CollectionAssert.AreEqual(before, File.ReadAllBytes(client.GetConfigPath()));
            if (!configured)
                StringAssert.Contains("Enable it in the config file", client.StatusDetail);
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

        [TestCase("json", false)]
        [TestCase("codex", false)]
        [TestCase("json", true)]
        [TestCase("codex", true)]
        public void GeneratedExecutableMissingOrMovedRequiresExplicitRepair(string kind, bool moved)
        {
            var client = Create(kind);
            client.Configure();
            Assert.AreEqual(McpStatus.Configured, client.CheckStatus(false));
            string original = File.ReadAllText(client.GetConfigPath());
            string executable = Path.Combine(directory, "uvx.exe");
            if (moved)
            {
                string nextDirectory = Path.Combine(directory, "moved");
                Directory.CreateDirectory(nextDirectory);
                string replacement = Path.Combine(nextDirectory, "uvx.exe");
                File.Move(executable, replacement);
                SetString(EditorPrefKeys.UvxPathOverride, replacement);
            }
            else
                File.Delete(executable);
            Assert.AreEqual(McpStatus.IncorrectPath, client.CheckStatus(true));
            StringAssert.Contains("Configure", ((McpClientConfiguratorBase)client).Client.configStatus);
            Assert.AreEqual(original, File.ReadAllText(client.GetConfigPath()), "Status must offer repair without changing the launch command.");
            if (moved)
            {
                client.Configure();
                Assert.AreEqual(McpStatus.Configured, client.CheckStatus(false));
                StringAssert.Contains("moved", File.ReadAllText(client.GetConfigPath()));
            }
        }

        [TestCase("json")]
        [TestCase("codex")]
        public void GeneratedExecutableMustFollowChangedOverrideEvenWhenOldFileStillExists(string kind)
        {
            var client = Create(kind);
            client.Configure();
            string replacementDirectory = Path.Combine(directory, "override");
            Directory.CreateDirectory(replacementDirectory);
            string replacement = Path.Combine(replacementDirectory, "uvx.exe");
            File.WriteAllText(replacement, "inert fixture; never executed");
            SetString(EditorPrefKeys.UvxPathOverride, replacement);
            Assert.AreEqual(McpStatus.IncorrectPath, client.CheckStatus(false));
            client.Configure();
            Assert.AreEqual(McpStatus.Configured, client.CheckStatus(false));
            StringAssert.Contains("override", File.ReadAllText(client.GetConfigPath()));
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

        [TestCase("manual")]
        [TestCase("legacy")]
        [TestCase("edited")]
        [TestCase("disabled")]
        [TestCase("http-selected")]
        public void MigrationPreservesEntriesWithoutAutomaticSourceRefreshPermission(string fixture)
        {
            var client = Create("json");
            client.Configure();
            string document = File.ReadAllText(client.GetConfigPath());
            if (fixture == "manual")
                document = UnownedDocument("json", "manual");
            else if (fixture == "legacy")
                EditorPrefs.DeleteKey(GeneratedClientConfigOwnership.Key("Json", client.GetConfigPath()));
            else if (fixture == "edited")
                document = document.Replace(FirstSource, "mcpforunity==0.1.0");
            else if (fixture == "disabled")
                EditorPrefs.SetBool(EditorPrefKeys.AutoRegisterEnabled, false);
            else
            {
                EditorPrefs.SetBool(EditorPrefKeys.UseHttpTransport, true);
                EditorConfigurationCache.Instance.Refresh();
            }
            File.WriteAllText(client.GetConfigPath(), document);
            EditorPrefs.SetString(EditorPrefKeys.GitUrlOverride, NextSource);
            RunMigration(client);
            Assert.AreEqual(document, File.ReadAllText(client.GetConfigPath()));
        }

        [Test]
        public void MigrationRefreshesOwnedEntryAndRetriesAnEntirelyFailedAttempt()
        {
            var client = Create("json");
            client.Configure();
            EditorPrefs.SetString(EditorPrefKeys.GitUrlOverride, NextSource);
            EditorPrefs.SetBool(EditorPrefKeys.LockCursorConfig, true);
            RunMigration(client);
            Assert.AreEqual("", EditorPrefs.GetString(EditorPrefKeys.LastStdIoUpgradeVersion));
            StringAssert.Contains(FirstSource, File.ReadAllText(client.GetConfigPath()));
            EditorPrefs.SetBool(EditorPrefKeys.LockCursorConfig, false);
            RunMigration(client);
            StringAssert.Contains(NextSource, File.ReadAllText(client.GetConfigPath()));
            Assert.AreEqual(
                StdIoVersionMigration.GetUpgradeIdentity(AssetPathUtility.GetPackageVersion(), NextSource),
                EditorPrefs.GetString(EditorPrefKeys.LastStdIoUpgradeVersion)
            );
        }

        private static void RunMigration(IMcpClientConfigurator client)
        {
            typeof(McpClientRegistry)
                .GetField("cached", BindingFlags.Static | BindingFlags.NonPublic)
                .SetValue(null, new List<IMcpClientConfigurator> { client });
            typeof(StdIoVersionMigration).GetMethod("RunMigrationIfNeeded", BindingFlags.Static | BindingFlags.NonPublic).Invoke(null, null);
        }

        [TestCase("codex", false)]
        [TestCase("opencode", false)]
        [TestCase("codex", true)]
        [TestCase("opencode", true)]
        public void PreviewApplyEstablishesOwnershipIncludingUnchangedExplicitConfirmation(string kind, bool unchanged)
        {
            var client = Create(kind);
            var service = ChangeService();
            if (unchanged)
            {
                client.Configure();
                EditorPrefs.DeleteKey(GeneratedClientConfigOwnership.Key(kind == "codex" ? "Codex" : "OpenCode", client.GetConfigPath()));
            }
            var preview = service.Prepare(client);
            Assert.IsTrue(preview.CanApply, preview.Code);
            Assert.AreEqual(unchanged ? "unchanged" : "applied", service.Apply(preview, client).Code);
            EditorPrefs.SetString(EditorPrefKeys.GitUrlOverride, NextSource);
            Assert.AreEqual(McpStatus.Configured, client.CheckStatus(true));
            StringAssert.Contains(NextSource, File.ReadAllText(client.GetConfigPath()));
        }

        [TestCase("codex", false)]
        [TestCase("opencode", false)]
        [TestCase("codex", true)]
        [TestCase("opencode", true)]
        public void RestoreReinstatesPriorOwnershipAndProtectsManualEntries(string kind, bool owned)
        {
            var client = Create(kind);
            if (owned)
                client.Configure();
            else
                File.WriteAllText(client.GetConfigPath(), UnownedDocument(kind, "manual"));
            string key = GeneratedClientConfigOwnership.Key(kind == "codex" ? "Codex" : "OpenCode", client.GetConfigPath());
            string originalOwnership = EditorPrefs.HasKey(key) ? EditorPrefs.GetString(key) : null;
            EditorPrefs.SetString(EditorPrefKeys.GitUrlOverride, NextSource);
            var service = ChangeService();
            var applied = service.Apply(service.Prepare(client), client);
            Assert.AreEqual("applied", applied.Code);
            Assert.IsTrue(EditorPrefs.HasKey(key));
            Assert.AreEqual("restored", service.Restore(applied.Receipt, client).Code);
            Assert.AreEqual(originalOwnership, EditorPrefs.HasKey(key) ? EditorPrefs.GetString(key) : null);
            string restored = File.ReadAllText(client.GetConfigPath());
            client.CheckStatus(true);
            if (owned)
                StringAssert.Contains(NextSource, File.ReadAllText(client.GetConfigPath()));
            else
                Assert.AreEqual(restored, File.ReadAllText(client.GetConfigPath()));
        }

        [TestCase("codex")]
        [TestCase("opencode")]
        public void ApplyRejectsOwnershipChangesSincePreview(string kind)
        {
            var client = Create(kind);
            client.Configure();
            var service = ChangeService();
            EditorPrefs.SetString(EditorPrefKeys.GitUrlOverride, NextSource);
            var preview = service.Prepare(client);
            string key = GeneratedClientConfigOwnership.Key(kind == "codex" ? "Codex" : "OpenCode", client.GetConfigPath());
            EditorPrefs.SetString(key, "concurrent-ownership-change");
            byte[] before = File.ReadAllBytes(client.GetConfigPath());
            Assert.AreEqual("stale_context", service.Apply(preview, client).Code);
            CollectionAssert.AreEqual(before, File.ReadAllBytes(client.GetConfigPath()));
            Assert.AreEqual("concurrent-ownership-change", EditorPrefs.GetString(key));
        }

        [TestCase("codex")]
        [TestCase("opencode")]
        public void FinalOwnershipRacePreservesNewerReceiptAndExistingFile(string kind)
        {
            var client = Create(kind);
            client.Configure();
            var service = ChangeService();
            EditorPrefs.SetString(EditorPrefKeys.GitUrlOverride, NextSource);
            var preview = service.Prepare(client);
            string key = GeneratedClientConfigOwnership.Key(kind == "codex" ? "Codex" : "OpenCode", client.GetConfigPath());
            byte[] before = File.ReadAllBytes(client.GetConfigPath());
            typeof(ClientConfigurationChangeService)
                .GetField("beforeCommitComparison", BindingFlags.Instance | BindingFlags.NonPublic)
                .SetValue(service, new Action(() => EditorPrefs.SetString(key, "newer-ownership")));
            Assert.AreEqual("stale_file", service.Apply(preview, client).Code);
            CollectionAssert.AreEqual(before, File.ReadAllBytes(client.GetConfigPath()));
            Assert.AreEqual("newer-ownership", EditorPrefs.GetString(key));
        }

        [TestCase("codex")]
        [TestCase("opencode")]
        public void FailedApplyOrRestorePreservesFileAndOwnershipAsOneTransaction(string kind)
        {
            var client = Create(kind);
            client.Configure();
            string before = File.ReadAllText(client.GetConfigPath());
            string key = GeneratedClientConfigOwnership.Key(kind == "codex" ? "Codex" : "OpenCode", client.GetConfigPath());
            string ownership = EditorPrefs.GetString(key);
            EditorPrefs.SetString(EditorPrefKeys.GitUrlOverride, NextSource);
            var service = ChangeService();
            var preview = service.Prepare(client);
            var hook = typeof(ClientConfigurationChangeService).GetField("beforeCommitComparison", BindingFlags.Instance | BindingFlags.NonPublic);
            hook.SetValue(service, new Action(() => throw new IOException("injected transaction failure")));
            Assert.AreEqual("write_failed", service.Apply(preview, client).Code);
            Assert.AreEqual(before, File.ReadAllText(client.GetConfigPath()));
            Assert.AreEqual(ownership, EditorPrefs.GetString(key));
            hook.SetValue(service, null);
            var applied = service.Apply(service.Prepare(client), client);
            Assert.AreEqual("applied", applied.Code);
            string written = File.ReadAllText(client.GetConfigPath());
            string writtenOwnership = EditorPrefs.GetString(key);
            Assert.AreNotEqual(ownership, writtenOwnership);
            hook.SetValue(service, new Action(() => throw new IOException("injected restore failure")));
            Assert.AreEqual("write_failed", service.Restore(applied.Receipt, client).Code);
            Assert.AreEqual(written, File.ReadAllText(client.GetConfigPath()));
            Assert.AreEqual(writtenOwnership, EditorPrefs.GetString(key));
        }

        [TestCase("manual")]
        [TestCase("legacy")]
        [TestCase("edited")]
        [TestCase("disabled")]
        public void ClaudeAutomaticEntryPointPreservesUnownedEditedAndDisabledRegistrations(string fixture)
        {
            string path = Path.Combine(directory, ".claude.json");
            var entry = new JObject
            {
                ["type"] = "stdio",
                ["command"] = "uvx",
                ["args"] = new JArray("--from", FirstSource, "mcp-for-unity", "--transport", "stdio"),
            };
            if (fixture == "manual")
                entry["command"] = "wrapper";
            if (fixture == "edited")
            {
                GeneratedClientConfigOwnership.Record("Claude", path + "|" + Path.GetFullPath(directory), entry.ToString(Newtonsoft.Json.Formatting.None));
                ((JArray)entry["args"]).Add("--manual");
            }
            File.WriteAllText(
                path,
                new JObject { ["projects"] = new JObject { [directory] = new JObject { ["mcpServers"] = new JObject { ["UnityMCP"] = entry } } } }.ToString()
            );
            if (fixture == "disabled")
            {
                GeneratedClientConfigOwnership.Record("Claude", path + "|" + Path.GetFullPath(directory), entry.ToString(Newtonsoft.Json.Formatting.None));
                EditorPrefs.SetBool(EditorPrefKeys.AutoRegisterEnabled, false);
            }
            var cli = new MemoryCli(directory);
            byte[] before = File.ReadAllBytes(path);
            cli.CheckStatusWithProjectDir(directory, false, "inert-cli", UnityEngine.Application.platform, false, NextSource, true);
            Assert.AreEqual(0, cli.Calls);
            CollectionAssert.AreEqual(before, File.ReadAllBytes(path));
        }

        [TestCase(false)]
        [TestCase(true)]
        public void ClaudeOwnedAutomaticRefreshUsesRealRegistrationAndRetriesFailure(bool failFirst)
        {
            var cli = new MemoryCli(directory);
            cli.Configure();
            string path = Path.Combine(directory, ".claude.json");
            string key = GeneratedClientConfigOwnership.Key("Claude", path + "|" + Path.GetFullPath(directory));
            Assert.IsTrue(EditorPrefs.HasKey(key));
            string originalOwnership = EditorPrefs.GetString(key);
            cli.Source = NextSource;
            cli.FailNext = failFirst;
            cli.CheckStatusWithProjectDir(directory, false, "inert-cli", UnityEngine.Application.platform, false, NextSource, true);
            if (failFirst)
            {
                StringAssert.Contains(FirstSource, File.ReadAllText(path));
                Assert.AreEqual(originalOwnership, EditorPrefs.GetString(key));
                cli.FailNext = false;
                cli.CheckStatusWithProjectDir(directory, false, "inert-cli", UnityEngine.Application.platform, false, NextSource, true);
            }
            Assert.AreEqual(McpStatus.Configured, cli.Status);
            StringAssert.Contains(NextSource, File.ReadAllText(path));
            Assert.AreNotEqual(originalOwnership, EditorPrefs.GetString(key));
        }

        [TestCase(false)]
        [TestCase(true)]
        public void CapturedClaudeRegistrationPublishesOnlyItsUnchangedSuccessfulSnapshot(bool edited)
        {
            var cli = new MemoryCli(directory);
            System.Threading.Tasks.Task.Run(() => cli.Configure()).GetAwaiter().GetResult();
            string path = Path.Combine(directory, ".claude.json");
            string key = GeneratedClientConfigOwnership.Key("Claude", path + "|" + Path.GetFullPath(directory));
            Assert.IsFalse(EditorPrefs.HasKey(key), "Background configuration must not write EditorPrefs.");
            if (edited)
                File.WriteAllText(path, File.ReadAllText(path).Replace("uvx", "wrapper"));
            typeof(ClaudeCliMcpConfigurator).GetMethod("CompleteConfigurationOwnership", BindingFlags.Instance | BindingFlags.NonPublic).Invoke(cli, null);
            Assert.AreEqual(!edited, EditorPrefs.HasKey(key));
            byte[] before = File.ReadAllBytes(path);
            cli.Source = NextSource;
            cli.CheckStatusWithProjectDir(directory, false, "inert-cli", UnityEngine.Application.platform, false, NextSource, true);
            if (edited)
                CollectionAssert.AreEqual(before, File.ReadAllBytes(path));
            else
                StringAssert.Contains(NextSource, File.ReadAllText(path));
        }

        [Test]
        public void ClaudeCompletionPreservesNewerOwnedEntryObservedBeforeCapture()
        {
            string identity = Path.Combine(directory, ".claude.json") + "|" + Path.GetFullPath(directory);
            var cli = new MemoryCli(directory);
            cli.AfterAdd = path =>
            {
                var document = JObject.Parse(File.ReadAllText(path));
                var newer = document["projects"][directory]["mcpServers"]["UnityMCP"];
                newer["args"][1] = NextSource;
                File.WriteAllText(path, document.ToString());
                GeneratedClientConfigOwnership.Record("Claude", identity, newer.ToString(Newtonsoft.Json.Formatting.None));
            };
            cli.Configure();
            string config = File.ReadAllText(Path.Combine(directory, ".claude.json"));
            var observed = JObject.Parse(config)["projects"][directory]["mcpServers"]["UnityMCP"];
            Assert.IsTrue(GeneratedClientConfigOwnership.Owns("Claude", identity, observed.ToString(Newtonsoft.Json.Formatting.None)));
            StringAssert.Contains(NextSource, config);
        }

        [Test]
        public void ClaudeOwnershipUsesObservedJsonWhenCliReordersFields()
        {
            var cli = new MemoryCli(directory) { ReorderOutput = true };
            cli.Configure();
            string path = Path.Combine(directory, ".claude.json");
            var observed = JObject.Parse(File.ReadAllText(path))["projects"][directory]["mcpServers"]["UnityMCP"];
            Assert.IsTrue(
                GeneratedClientConfigOwnership.Owns("Claude", path + "|" + Path.GetFullPath(directory), observed.ToString(Newtonsoft.Json.Formatting.None))
            );
            cli.Source = NextSource;
            Assert.AreEqual(
                McpStatus.Configured,
                cli.CheckStatusWithProjectDir(directory, false, "inert-cli", UnityEngine.Application.platform, false, NextSource, true)
            );
            StringAssert.Contains(NextSource, File.ReadAllText(path));
        }

        [TestCase("json")]
        [TestCase("codex")]
        [TestCase("opencode")]
        public void FileRefreshPreservesManualReplacementAfterStatusRead(string kind)
        {
            var client = Create(kind);
            client.Configure();
            string path = client.GetConfigPath();
            string format =
                kind == "codex" ? "Codex"
                : kind == "opencode" ? "OpenCode"
                : "Json";
            string receipt = GeneratedClientConfigOwnership.Receipt(format, path);
            string manual = UnownedDocument(kind, "manual");
            // Resolving this local source emits a synchronous log during status validation,
            // after the selected entry was read and before its writer reads the file.
            Directory.CreateDirectory(Path.Combine(directory, "Server"));
            File.WriteAllText(Path.Combine(directory, "Server", "pyproject.toml"), "");
            EditorPrefs.SetString(EditorPrefKeys.GitUrlOverride, directory);
            DuringLog("Auto-corrected server source override", () => File.WriteAllText(path, manual), () => client.CheckStatus(true));
            Assert.AreEqual(manual, File.ReadAllText(path));
            Assert.AreEqual(receipt, GeneratedClientConfigOwnership.Receipt(format, path));
        }

        [TestCase("json", false)]
        [TestCase("codex", false)]
        [TestCase("opencode", false)]
        [TestCase("json", true)]
        public void FileRefreshRechecksOwnershipAtWrite(string kind, bool migration)
        {
            var client = Create(kind);
            client.Configure();
            string path = client.GetConfigPath();
            string format =
                kind == "codex" ? "Codex"
                : kind == "opencode" ? "OpenCode"
                : "Json";
            byte[] original = File.ReadAllBytes(path);
            EditorPrefs.SetString(EditorPrefKeys.GitUrlOverride, NextSource);
            var previous = MCPServiceLocator.Paths;
            var paths = new SyntheticClientPaths { UvxPath = Path.Combine(directory, "uvx.exe") };
            // JSON and Codex first resolve the existing executable during status. All
            // three resolve the executable while constructing the replacement.
            int remaining = kind == "opencode" ? 1 : 2;
            paths.OnResolveUvx = () =>
            {
                if (--remaining == 0)
                    GeneratedClientConfigOwnership.RestoreReceipt(format, path, "newer-receipt");
            };
            try
            {
                MCPServiceLocator.Register(paths);
                if (migration)
                    RunMigration(client);
                else
                    client.CheckStatus(true);
            }
            finally
            {
                MCPServiceLocator.Register(previous);
            }
            Assert.LessOrEqual(remaining, 0, "Expected the replacement builder to resolve the fixture executable.");
            CollectionAssert.AreEqual(original, File.ReadAllBytes(path));
            Assert.AreEqual("newer-receipt", GeneratedClientConfigOwnership.Receipt(format, path));
            if (migration)
                Assert.AreEqual("", EditorPrefs.GetString(EditorPrefKeys.LastStdIoUpgradeVersion));
        }

        [TestCase("entry")]
        [TestCase("receipt")]
        [TestCase("disabled")]
        public void ClaudeAutomaticRefreshRefusesChangedAuthorizationBeforeCli(string change)
        {
            var cli = new MemoryCli(directory);
            cli.Configure();
            string path = Path.Combine(directory, ".claude.json");
            string identity = path + "|" + Path.GetFullPath(directory);
            string receipt = GeneratedClientConfigOwnership.Receipt("Claude", identity);
            string document = File.ReadAllText(path);
            if (change == "entry")
                document = document.Replace("uvx", "manual-wrapper");
            int calls = cli.Calls;
            cli.Source = NextSource;
            DuringLog(
                "Re-registering...",
                () =>
                {
                    if (change == "entry")
                        File.WriteAllText(path, document);
                    else if (change == "disabled")
                        EditorPrefs.SetBool(EditorPrefKeys.AutoRegisterEnabled, false);
                    else
                        GeneratedClientConfigOwnership.RestoreReceipt("Claude", identity, "newer-receipt");
                },
                () => cli.CheckStatusWithProjectDir(directory, false, "inert-cli", UnityEngine.Application.platform, false, NextSource, true)
            );
            Assert.AreEqual(calls, cli.Calls);
            Assert.AreEqual(document, File.ReadAllText(path));
            Assert.AreEqual(change == "receipt" ? "newer-receipt" : receipt, GeneratedClientConfigOwnership.Receipt("Claude", identity));
        }

        [TestCase("json")]
        [TestCase("codex")]
        [TestCase("opencode")]
        public void AutomaticFileRefreshRefusesTransportChangedDuringStdioBuild(string kind)
        {
            var client = Create(kind);
            client.Configure();
            string path = client.GetConfigPath();
            string format =
                kind == "codex" ? "Codex"
                : kind == "opencode" ? "OpenCode"
                : "Json";
            string receipt = GeneratedClientConfigOwnership.Receipt(format, path);
            byte[] original = File.ReadAllBytes(path);
            EditorPrefs.SetString(EditorPrefKeys.GitUrlOverride, NextSource);
            var previous = MCPServiceLocator.Paths;
            var paths = new SyntheticClientPaths { UvxPath = Path.Combine(directory, "uvx.exe") };
            // The innermost stdio builder resolves uvx after selecting its transport.
            // JSON and TOML first resolve it during status and again in the outer writer.
            int remaining = kind == "opencode" ? 1 : 3;
            paths.OnResolveUvx = () =>
            {
                if (--remaining == 0)
                    SelectLocalHttp();
            };
            string tokenFile = Environment.GetEnvironmentVariable("UNITY_MCP_LOCAL_AUTH_TOKEN_FILE");
            try
            {
                Environment.SetEnvironmentVariable("UNITY_MCP_LOCAL_AUTH_TOKEN_FILE", Path.Combine(directory, "absent-fixture-token"));
                MCPServiceLocator.Register(paths);
                client.CheckStatus(true);
            }
            finally
            {
                MCPServiceLocator.Register(previous);
                Environment.SetEnvironmentVariable("UNITY_MCP_LOCAL_AUTH_TOKEN_FILE", tokenFile);
            }
            Assert.LessOrEqual(remaining, 0, "The fixture must switch selection during the stdio build.");
            CollectionAssert.AreEqual(original, File.ReadAllBytes(path));
            Assert.AreEqual(receipt, GeneratedClientConfigOwnership.Receipt(format, path));
        }

        [TestCase(false)]
        [TestCase(true)]
        public void ClaudeAutomaticRefreshBindsTransportToItsDispatch(bool afterDispatch)
        {
            var cli = new MemoryCli(directory);
            cli.Configure();
            string path = Path.Combine(directory, ".claude.json");
            byte[] original = File.ReadAllBytes(path);
            string identity = path + "|" + Path.GetFullPath(directory);
            string receipt = GeneratedClientConfigOwnership.Receipt("Claude", identity);
            int calls = cli.Calls;
            cli.Source = NextSource;
            DuringLog(afterDispatch ? "Successfully registered" : "Re-registering...", SelectLocalHttp, () =>
                cli.CheckStatusWithProjectDir(directory, false, "inert-cli", UnityEngine.Application.platform, false, NextSource, true)
            );
            if (afterDispatch)
            {
                StringAssert.Contains(NextSource, File.ReadAllText(path));
                Assert.AreEqual(ConfiguredTransport.Stdio, cli.ConfiguredTransport);
            }
            else
            {
                Assert.AreEqual(calls, cli.Calls);
                CollectionAssert.AreEqual(original, File.ReadAllBytes(path));
                Assert.AreEqual(receipt, GeneratedClientConfigOwnership.Receipt("Claude", identity));
            }
        }

        private void SelectLocalHttp()
        {
            SetString(EditorPrefKeys.HttpTransportScope, "local");
            EditorPrefs.SetBool(EditorPrefKeys.UseHttpTransport, true);
            EditorConfigurationCache.Instance.Refresh();
        }

        [TestCase(false)]
        [TestCase(true)]
        public void ClaudeCompletedAutomaticAddPublishesOnlyAgainstItsPriorReceipt(bool superseded)
        {
            var cli = new MemoryCli(directory);
            cli.Configure();
            string path = Path.Combine(directory, ".claude.json");
            string identity = path + "|" + Path.GetFullPath(directory);
            cli.Source = NextSource;
            cli.AfterAdd = _ =>
            {
                // The CLI already wrote its exact stdio entry. Its caller has not
                // yet observed the result or published ownership.
                SelectLocalHttp();
                if (superseded)
                    GeneratedClientConfigOwnership.RestoreReceipt("Claude", identity, "newer-receipt");
            };
            cli.CheckStatusWithProjectDir(directory, false, "inert-cli", UnityEngine.Application.platform, false, NextSource, true);
            cli.AfterAdd = null;
            var written = JObject.Parse(File.ReadAllText(path))["projects"][directory]["mcpServers"]["UnityMCP"];
            StringAssert.Contains(NextSource, written.ToString());
            if (superseded)
                Assert.AreEqual("newer-receipt", GeneratedClientConfigOwnership.Receipt("Claude", identity));
            else
            {
                Assert.IsTrue(GeneratedClientConfigOwnership.Owns("Claude", identity, written.ToString(Newtonsoft.Json.Formatting.None)));
                Assert.AreEqual(McpStatus.Configured, cli.Status);
                Assert.AreEqual(ConfiguredTransport.Stdio, cli.ConfiguredTransport);
            }
            EditorPrefs.SetBool(EditorPrefKeys.UseHttpTransport, false);
            EditorConfigurationCache.Instance.Refresh();
            int calls = cli.Calls;
            cli.Source = FirstSource;
            cli.CheckStatusWithProjectDir(directory, false, "inert-cli", UnityEngine.Application.platform, false, FirstSource, true);
            Assert.AreEqual(superseded ? calls : calls + 2, cli.Calls);
            StringAssert.Contains(superseded ? NextSource : FirstSource, File.ReadAllText(path));
        }

        [TestCase(false)]
        [TestCase(true)]
        public void ClaudeValueReceiptCannotAuthorizeRenamedRegistration(bool renameDuringAutomaticAdd)
        {
            var cli = new MemoryCli(directory);
            cli.Configure();
            string path = Path.Combine(directory, ".claude.json");
            string identity = path + "|" + Path.GetFullPath(directory);
            string originalReceipt = GeneratedClientConfigOwnership.Receipt("Claude", identity);
            // Older versions recorded the value fingerprint alone. Keep this fixture
            // independent of the representation used by a new explicit registration.
            var first = JObject.Parse(File.ReadAllText(path))["projects"][directory]["mcpServers"]["UnityMCP"];
            string legacy = GeneratedClientConfigOwnership.EntryReceipt(first.ToString(Newtonsoft.Json.Formatting.None));
            if (!renameDuringAutomaticAdd)
                GeneratedClientConfigOwnership.RestoreReceipt("Claude", identity, legacy);

            void Rename(string file)
            {
                var doc = JObject.Parse(File.ReadAllText(file));
                var servers = (JObject)doc["projects"][directory]["mcpServers"];
                var entry = servers["UnityMCP"].DeepClone();
                servers.Remove("UnityMCP");
                servers["unitymcp"] = entry;
                File.WriteAllText(file, doc.ToString());
            }

            cli.Source = NextSource;
            if (renameDuringAutomaticAdd)
            {
                cli.AfterAdd = Rename;
                cli.CheckStatusWithProjectDir(directory, false, "inert-cli", UnityEngine.Application.platform, false, NextSource, true);
                cli.AfterAdd = null;
                StringAssert.Contains(NextSource, File.ReadAllText(path));
                cli.Source = FirstSource;
            }
            else
                Rename(path);
            string renamed = File.ReadAllText(path);
            StringAssert.Contains("\"unitymcp\"", renamed);
            int calls = cli.Calls;
            cli.CheckStatusWithProjectDir(directory, false, "inert-cli", UnityEngine.Application.platform, false, cli.Source, true);
            Assert.AreEqual(calls, cli.Calls, "A differently named registration must not be removed.");
            Assert.AreEqual(renamed, File.ReadAllText(path));
            Assert.AreEqual(renameDuringAutomaticAdd ? originalReceipt : legacy, GeneratedClientConfigOwnership.Receipt("Claude", identity));
        }

        private static void DuringLog(string marker, Action edit, Action run)
        {
            bool edited = false;
            void OnLog(string message, string trace, UnityEngine.LogType type)
            {
                if (edited || !message.Contains(marker))
                    return;
                edited = true;
                edit();
            }
            UnityEngine.Application.logMessageReceived += OnLog;
            try
            {
                run();
            }
            finally
            {
                UnityEngine.Application.logMessageReceived -= OnLog;
            }
            Assert.IsTrue(edited, "Expected the synchronous configuration callback.");
        }

        private sealed class MemoryCli : ClaudeCliMcpConfigurator
        {
            private readonly string project;
            internal int Calls;
            internal string Source = FirstSource;
            internal bool FailNext;
            internal bool ReorderOutput;
            internal Action<string> AfterAdd;

            internal MemoryCli(string project)
                : base(new McpClient { name = "Owned test Claude" }) => this.project = project;

            public override void Configure() =>
                ConfigureWithCapturedValues(
                    project,
                    "inert-cli",
                    null,
                    false,
                    null,
                    "uvx",
                    null,
                    "mcp-for-unity",
                    "",
                    null,
                    ConfiguredTransport.Stdio,
                    false,
                    capturedFromArguments: new[] { "--from", Source }
                );

            protected override bool RunCli(
                string executable,
                string arguments,
                string directory,
                out string stdout,
                out string stderr,
                int timeout,
                string pathPrepend
            )
            {
                Calls++;
                stdout = stderr = "";
                string path = Path.Combine(project, ".claude.json");
                var root = File.Exists(path) ? JObject.Parse(File.ReadAllText(path)) : new JObject { ["projects"] = new JObject() };
                if (arguments.StartsWith("mcp remove ", StringComparison.Ordinal))
                    ((JObject)root["projects"]?[project]?["mcpServers"])?.Remove("UnityMCP");
                else
                {
                    JObject entry = null;
                    foreach (string source in new[] { FirstSource, NextSource })
                    {
                        var candidate = new JObject
                        {
                            ["type"] = "stdio",
                            ["command"] = "uvx",
                            ["args"] = new JArray("--from", source, "mcp-for-unity"),
                        };
                        // Automatic status uses the main-thread builder and the same
                        // inert executable/profile preferences as the file fixtures.
                        var refreshed = new JObject
                        {
                            ["type"] = "stdio",
                            ["command"] = Path.Combine(project, "uvx.exe"),
                            ["args"] = new JArray("--no-cache", "--refresh", "--python", ">=3.11", "--from", source, "mcp-for-unity"),
                        };
                        if (arguments == ClaudeHttpAuth.BuildRegistrationArguments(refreshed))
                            candidate = refreshed;
                        var reordered = new JObject
                        {
                            ["args"] = candidate["args"].DeepClone(),
                            ["type"] = "stdio",
                            ["command"] = candidate["command"].DeepClone(),
                        };
                        if (
                            arguments != ClaudeHttpAuth.BuildRegistrationArguments(candidate)
                            && arguments != ClaudeHttpAuth.BuildRegistrationArguments(reordered)
                        )
                            continue;
                        if (FailNext && source == NextSource)
                            return false;
                        entry = ReorderOutput ? reordered : candidate;
                    }
                    Assert.IsNotNull(entry, "Unexpected fake CLI request");
                    root["projects"][project] = new JObject { ["mcpServers"] = new JObject { ["UnityMCP"] = entry } };
                }
                File.WriteAllText(path, root.ToString());
                if (arguments.StartsWith("mcp add-json ", StringComparison.Ordinal))
                    AfterAdd?.Invoke(path);
                return true;
            }
        }

        private static ClientConfigurationChangeService ChangeService() =>
            new ClientConfigurationChangeService(
                format =>
                    (ClientConfigurationInputs)
                        typeof(ClientConfigurationChangeService)
                            .GetMethod("Capture", BindingFlags.Static | BindingFlags.NonPublic)
                            .Invoke(null, new object[] { format }),
                client => new ClientConfigurationFileTarget(
                    client,
                    client.Id,
                    client.GetConfigPath(),
                    client is CodexMcpConfigurator ? "toml" : "json",
                    ((McpClientConfiguratorBase)client).Client
                )
            );

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
            if (!strings.ContainsKey(key))
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
            internal string StatusDetail => client.configStatus;

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
