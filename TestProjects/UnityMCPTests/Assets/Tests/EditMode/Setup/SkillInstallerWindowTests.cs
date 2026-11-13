using System;
using System.Collections;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Reflection;
using MCPForUnity.Editor.Clients.Configurators;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Editor.Services;
using MCPForUnity.Editor.Setup;
using NUnit.Framework;
using UnityEditor;
using UnityEngine;
using UnityEngine.TestTools;

namespace MCPForUnityTests.Editor.Setup
{
    [Parallelizable(ParallelScope.None)]
    public class SkillInstallerWindowTests
    {
        private const BindingFlags PrivateInstance = BindingFlags.NonPublic | BindingFlags.Instance;

        private sealed class EvidenceInstaller : McpForUnitySkillInstaller
        {
            private void OnEnable() { }

            private void OnDisable() { }

            private void OnGUI() => typeof(McpForUnitySkillInstaller).GetMethod("OnGUI", PrivateInstance).Invoke(this, null);
        }

        [TestCase(true)]
        [TestCase(false)]
        public void CodexHomeUsesOneRootForDetectionConfigurationAndSkills(bool alternate)
        {
            string directory = Path.Combine(Path.GetTempPath(), "codex-home-" + Guid.NewGuid().ToString("N"));
            string profile = Path.Combine(directory, "synthetic-profile");
            string defaultHome = Path.Combine(profile, ".codex");
            string selected = alternate ? Path.Combine(directory, "alternate-home") : defaultHome;
            Directory.CreateDirectory(defaultHome);
            string defaultConfig = Path.Combine(defaultHome, "config.toml");
            const string sentinel = "model = 'synthetic-preserved-model'\n";
            File.WriteAllText(defaultConfig, sentinel);
            try
            {
                string resolved = CodexHome.Resolve(alternate ? selected : null, profile);
                var client = new CodexConfigurator(resolved);
                Assert.AreEqual(!alternate, client.IsInstalled, "Detection must inspect the selected home even when a different profile exists.");
                Directory.CreateDirectory(selected);
                Assert.IsTrue(client.IsInstalled);
                string config = Path.Combine(selected, "config.toml");
                Assert.AreEqual(config, client.GetConfigPath());
                Assert.AreEqual(config, client.Client.windowsConfigPath);
                Assert.AreEqual(config, client.Client.macConfigPath);
                Assert.AreEqual(config, client.Client.linuxConfigPath);
                Assert.AreEqual(config, McpConfigurationHelper.GetClientConfigPath(client.Client), "Generic model-based paths must agree with discovery.");
                var inputs = new ClientConfigurationInputs(false, null, "synthetic-uvx", new[] { "synthetic-server" }, null, null, false, null, null, false);
                string existing = File.Exists(config) ? File.ReadAllText(config) : "";
                byte[] snapshot = File.Exists(config) ? File.ReadAllBytes(config) : null;
                string generated = CodexConfigHelper.UpsertCodexServerBlock(existing, null, inputs);
                McpConfigurationHelper.WriteGuardedConfiguration(config, generated, snapshot, null);
                Assert.IsTrue(CodexConfigHelper.TryParseCodexServer(File.ReadAllText(config), out string command, out _));
                Assert.AreEqual("synthetic-uvx", command);
                string skills = Path.Combine(selected, "skills", "unity-mcp-skill");
                Assert.AreEqual(skills, client.GetSkillInstallPath());
                Assert.AreEqual(skills, McpForUnitySkillInstaller.GetDefaultInstallDir(profile, "codex", resolved));
                Assert.AreEqual(skills, McpForUnitySkillInstaller.ResolveInitialInstallDir("", profile, "codex", resolved));
                Assert.AreEqual(
                    skills,
                    McpForUnitySkillInstaller.ResolveInitialInstallDir(CodexHome.SkillInstallPath(defaultHome), profile, "codex", resolved)
                );
                string customized = Path.Combine(directory, "custom-skills");
                Assert.AreEqual(customized, McpForUnitySkillInstaller.ResolveInitialInstallDir(customized, profile, "codex", resolved));
                Assert.AreEqual(
                    Path.Combine(profile, ".claude", "skills", "unity-mcp-skill"),
                    McpForUnitySkillInstaller.GetDefaultInstallDir(profile, "claude", resolved)
                );
                if (alternate)
                    Assert.AreEqual(sentinel, File.ReadAllText(defaultConfig), "The other profile must remain byte-for-byte unchanged.");
            }
            finally
            {
                Directory.Delete(directory, true);
            }
        }

        [Test]
        public void CodexConfiguratorCapturesInheritedHomeAndDoesNotRetargetExistingOperations()
        {
            string previous = Environment.GetEnvironmentVariable("CODEX_HOME");
            string directory = Path.Combine(Path.GetTempPath(), "codex-capture-" + Guid.NewGuid().ToString("N"));
            try
            {
                string first = Path.Combine(directory, "first");
                string second = Path.Combine(directory, "second");
                Environment.SetEnvironmentVariable("CODEX_HOME", first);
                var client = new CodexConfigurator();
                Environment.SetEnvironmentVariable("CODEX_HOME", second);
                Assert.AreEqual(Path.Combine(first, "config.toml"), client.GetConfigPath());
                Assert.AreEqual(CodexHome.SkillInstallPath(first), client.GetSkillInstallPath());
                Assert.AreEqual(Path.Combine(second, "config.toml"), new CodexConfigurator().GetConfigPath());
                Assert.IsFalse(Directory.Exists(directory), "Resolving homes must not create or read configuration files.");
            }
            finally
            {
                Environment.SetEnvironmentVariable("CODEX_HOME", previous);
            }
        }

        [TestCase(null)]
        [TestCase("")]
        [TestCase(" ")]
        public void CodexHomeFallbackAndHomeRelativePathsUseTheSyntheticProfile(string inherited)
        {
            string profile = Path.Combine(Path.GetTempPath(), "synthetic-profile");
            Assert.AreEqual(Path.Combine(profile, ".codex"), CodexHome.Resolve(inherited, profile));
            Assert.AreEqual(Path.Combine(profile, "alternate"), CodexHome.Resolve("~/alternate", profile));
            Assert.AreEqual(profile, CodexHome.Resolve("~", profile));
        }

        [TestCase(true)]
        [TestCase(false)]
        public void ExplicitCodexHomeCanBeNonexistentOrRelativeWithoutProfileFallback(bool relative)
        {
            string name = "synthetic-codex-absent-" + Guid.NewGuid().ToString("N");
            string inherited = relative ? name : Path.Combine(Path.GetTempPath(), name);
            string profile = Path.Combine(Path.GetTempPath(), "unrelated-synthetic-profile");
            string selected = Path.GetFullPath(inherited);
            string resolved = CodexHome.Resolve(inherited, profile);
            var client = new CodexConfigurator(resolved);
            Assert.AreEqual(selected, resolved);
            Assert.AreEqual(Path.Combine(selected, "config.toml"), client.GetConfigPath());
            Assert.AreEqual(CodexHome.SkillInstallPath(selected), client.GetSkillInstallPath());
            Assert.IsFalse(client.IsInstalled);
            Assert.IsFalse(Directory.Exists(selected));
            Assert.IsFalse(Directory.Exists(Path.Combine(profile, ".codex")));
        }

        [Test]
        public void InvalidExplicitCodexHomeIsRejectedWithoutFallingBack()
        {
            string profile = Path.Combine(Path.GetTempPath(), "synthetic-codex-invalid-" + Guid.NewGuid().ToString("N"));
            Assert.Throws<ArgumentException>(() => CodexHome.Resolve("invalid\0home", profile));
            Assert.IsFalse(Directory.Exists(profile));
        }

        [UnityTest, Explicit("Owned, synthetic installer UI evidence without preference or installation writes.")]
        public IEnumerator InstallerReviewKeepsDiagnosticsAndOutputOptional()
        {
            Assert.IsTrue(Guid.TryParseExact(Environment.GetEnvironmentVariable("UNITY_MCP_NATIVE_QA_NONCE"), "N", out _));
            var window = ScriptableObject.CreateInstance<EvidenceInstaller>();
            try
            {
                void Set(string field, object value) => typeof(McpForUnitySkillInstaller).GetField(field, PrivateInstance).SetValue(window, value);
                Set("_cliType", "codex");
                Set("_installDir", "/example/skills/unity-mcp-skill");
                Set("_repoUrl", "https://example.invalid/unity-mcp");
                Set("_targetBranch", "beta");
                Set("_latest", true);
                window.titleContent = new GUIContent("Unity MCP · skill review evidence");
                window.position = new Rect(30, 30, 480, 600);
                window.ShowUtility();
                string directory = Path.GetFullPath(Path.Combine(Application.dataPath, "../Library/McpTestEvidence/installer-" + Guid.NewGuid().ToString("N")));
                Directory.CreateDirectory(directory);
                foreach (string state in new[] { "idle", "preview", "source-details", "output" })
                {
                    if (state == "preview")
                    {
                        typeof(McpForUnitySkillInstaller)
                            .GetMethod("SetPreview", PrivateInstance)
                            .Invoke(
                                window,
                                new object[] { Preview(new[] { "SKILL.md" }, preserved: new[] { "local.md" }, conflicts: new[] { "references/conflict.md" }) }
                            );
                        Set("_status", "Preview ready. Review paths before applying.");
                    }
                    Set("_sourceDetails", state == "source-details");
                    Set("_showOutput", state == "output");
                    if (state == "output")
                        typeof(McpForUnitySkillInstaller)
                            .GetMethod("AppendLineImmediate", PrivateInstance)
                            .Invoke(window, new object[] { "Synthetic preview complete. Local changes are retained." });
                    for (int i = 0; i < 12; i++)
                    {
                        window.Repaint();
                        yield return null;
                    }
                    Assert.IsNotNull(window.rootVisualElement.panel);
                    string path = Path.Combine(directory, state + ".png");
                    typeof(MCPForUnityTests.EditMode.Windows.OperationActivityTests)
                        .GetMethod("Capture", BindingFlags.NonPublic | BindingFlags.Static)
                        .Invoke(null, new object[] { window, path });
                    TestContext.Out.WriteLine("INSTALLER_UX_CAPTURE " + path);
                }
            }
            finally
            {
                ((IDisposable)typeof(McpForUnitySkillInstaller).GetField("_operation", PrivateInstance).GetValue(window)).Dispose();
                window.Close();
            }
        }

        [Test]
        public void CancellationRetainsOwnershipUntilCompletionAndRejectsDuplicateApply()
        {
            using var state = new SkillInstallerOperation();
            var attempt = state.Begin("Applying safe changes");
            var token = attempt.Token;
            state.RequestCancellation();
            Assert.IsTrue(token.IsCancellationRequested);
            Assert.IsTrue(state.IsRunning);
            Assert.IsTrue(state.CancellationRequested);
            Assert.IsTrue(state.Owns(attempt));
            Assert.IsNull(state.Begin("Duplicate apply"));
            Assert.IsTrue(state.Complete(attempt));
            Assert.IsFalse(state.IsRunning);
            Assert.IsFalse(state.Complete(attempt));
            Assert.IsNotNull(state.Begin("Previewing changes"));
        }

        [Test]
        public void CloseInvalidatesLateCompletionAndLogsWithoutAffectingReplacement()
        {
            using var state = new SkillInstallerOperation();
            var closed = state.Begin("Previewing changes");
            var token = closed.Token;
            state.Dispose();
            Assert.IsTrue(token.IsCancellationRequested);
            Assert.IsFalse(state.Owns(closed));
            var replacement = state.Begin("Applying safe changes");
            Assert.IsFalse(state.Complete(closed));
            Assert.IsTrue(state.Owns(replacement));
            Assert.IsTrue(state.IsRunning);
        }

        [Test]
        public void LargePreviewKeepsEveryPathAcrossPagesAndFiltersWithoutRebuildingOnReads()
        {
            var paths = Enumerable.Range(0, 8192).Select(index => $"references/guide-{index:D5}.md").ToArray();
            var preview = Preview(paths);
            var review = new SkillInstallerReview();
            review.SetPreview(preview);
            Assert.AreEqual(8192, review.MatchCount);
            var seen = new List<string>();
            for (int page = 0; page < review.PageCount; page++)
            {
                review.SetPage(page);
                for (int row = 0; row < review.VisibleCount; row++)
                    seen.Add(review.GetVisiblePath(row));
            }
            CollectionAssert.AreEqual(paths, seen);
            review.SetFilter(SkillInstallerReview.Group.Added, "GUIDE-0819");
            Assert.AreEqual(2, review.MatchCount);
            Assert.AreEqual(0, review.Page);
            Assert.AreEqual(paths[8190], review.GetVisiblePath(0));
            Assert.AreEqual(paths[8191], review.GetVisiblePath(1));
            int builds = review.FilterRevision;
            review.SetPreview(preview);
            review.SetFilter(SkillInstallerReview.Group.Added, "GUIDE-0819");
            review.SetPage(0);
            Assert.AreEqual(2, review.VisibleCount);
            Assert.AreEqual(builds, review.FilterRevision, "Unchanged drawing inputs must reuse the filtered collection.");
            CollectionAssert.AreEqual(paths, preview.Added);
        }

        [Test]
        public void GroupsShowPreservedAndConflictingPathsAndEmptyFilterDoesNotLoseSelection()
        {
            var review = new SkillInstallerReview();
            review.SetPreview(Preview(new[] { "new.md" }, new[] { "updated.md" }, new[] { "removed.md" }, new[] { "local.md" }, new[] { "conflict.md" }));
            Assert.AreEqual(SkillInstallerReview.Group.Conflicts, review.SelectedGroup);
            foreach (SkillInstallerReview.Group group in Enum.GetValues(typeof(SkillInstallerReview.Group)))
            {
                review.SetFilter(group, ".md");
                Assert.AreEqual(1, review.MatchCount);
                Assert.IsNotEmpty(review.GetVisiblePath(0));
            }
            review.SetFilter(SkillInstallerReview.Group.Preserved, "missing");
            Assert.AreEqual(0, review.VisibleCount);
            Assert.AreEqual(1, review.PageCount);
            Assert.AreEqual(SkillInstallerReview.Group.Preserved, review.SelectedGroup);
            review.SetFilter(SkillInstallerReview.Group.Preserved, "");
            Assert.AreEqual("local.md", review.GetVisiblePath(0));
            review.SetPreview(null);
            Assert.AreEqual(0, review.MatchCount);
        }

        private static SkillSyncService.SyncPreview Preview(
            string[] added,
            string[] updated = null,
            string[] deleted = null,
            string[] preserved = null,
            string[] conflicts = null
        )
        {
            var plan = new SkillSyncService.SyncPlan();
            plan.Added.AddRange(added);
            plan.Updated.AddRange(updated ?? Array.Empty<string>());
            plan.Deleted.AddRange(deleted ?? Array.Empty<string>());
            plan.Preserved.AddRange(preserved ?? Array.Empty<string>());
            plan.Conflicts.AddRange(conflicts ?? Array.Empty<string>());
            return new SkillSyncService.SyncPreview(
                SkillSyncSource.Latest("https://github.com/ykh09242/unity-mcp", "beta"),
                "synthetic-install",
                new SkillSyncService.RemoteSnapshot(new string('a', 40), SkillSyncSource.SkillSubdirectory, new Dictionary<string, string>()),
                plan
            );
        }
    }
}
