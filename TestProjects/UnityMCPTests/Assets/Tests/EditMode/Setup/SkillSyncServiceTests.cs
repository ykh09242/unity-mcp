using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Text;
using MCPForUnity.Editor.Setup;
using NUnit.Framework;

namespace MCPForUnityTests.Editor.Setup
{
    [TestFixture]
    public class SkillSyncServiceTests
    {
        private const BindingFlags StaticInternal = BindingFlags.Static | BindingFlags.NonPublic;
        private static readonly Type Service = typeof(SkillSyncService);
        private string _root;
        private string _install;

        [SetUp]
        public void SetUp()
        {
            _root = Path.GetFullPath(Path.Combine(Path.GetTempPath(), "UnityMcpSkillSyncTests-" + Guid.NewGuid().ToString("N")));
            _install = Path.Combine(_root, "install");
            Directory.CreateDirectory(_install);
            Write(".unity-mcp-skill-sync", "managed-by-unity-mcp-skill-sync");
            Write("SKILL.md", "old skill");
        }

        [TearDown]
        public void TearDown()
        {
            SkillSyncService.BeforeFileOperation = null;
            string temp = Path.GetFullPath(Path.GetTempPath()).TrimEnd(Path.DirectorySeparatorChar) + Path.DirectorySeparatorChar;
            Assert.IsTrue(_root.StartsWith(temp, StringComparison.OrdinalIgnoreCase));
            Assert.IsTrue(Path.GetFileName(_root).StartsWith("UnityMcpSkillSyncTests-", StringComparison.Ordinal));
            if (Directory.Exists(_root))
                Directory.Delete(_root, true);
        }

        [Test]
        public void DescribeResult_ConflictOnlyReportsNoWritesAndAllRetainedFiles()
        {
            var summary = SkillSyncService.DescribeResult(
                new SkillSyncService.SyncResult
                {
                    Success = true,
                    Conflicts = new[] { "references/guide.md" },
                    Preserved = new[] { "personal.md" },
                }
            );
            Assert.That(summary, Does.StartWith("No skill files changed."));
            Assert.That(summary, Does.Contain("Preserved locally: 2"));
            Assert.That(summary, Does.Contain("Review required (1)"));
            Assert.That(summary, Does.Not.Contain("Applied:"));
            Assert.That(summary, Does.Contain("Back up"));
        }

        [Test]
        public void DescribeResult_NoDifferencesReportsUpToDate()
        {
            var result = new SkillSyncService.SyncResult
            {
                Success = true,
                SourceDescription = "Installed package example; installation identity SHA256 diagnostic-identity",
                CommitSha = new string('a', 40),
            };
            Assert.AreEqual("Skills are already up to date.", SkillSyncService.DescribeResult(result));
        }

        [TestCase(false)]
        [TestCase(true)]
        public void DescribeResult_KeepsRecoveryAndConflictsVisibleWhileSourceDetailsAreOptional(bool success)
        {
            var result = new SkillSyncService.SyncResult
            {
                Success = success,
                Error = success ? null : "Could not finish the update.",
                PartialApplication = !success,
                Conflicts = success ? new[] { "references/local.md" } : Array.Empty<string>(),
                RecoveryPath = "example/recovery",
                SourceDescription = "Installed package example; installation identity SHA256 diagnostic-identity",
                CommitSha = new string('a', 40),
            };
            string summary = SkillSyncService.DescribeResult(result);
            Assert.That(summary, Does.Contain("Recovery files retained at: example/recovery"));
            Assert.That(summary, Does.Contain(success ? "references/local.md" : "Some changes remain; recovery is required."));
            Assert.That(summary, Does.Not.Contain(result.SourceDescription));
            Assert.That(summary, Does.Not.Contain(result.CommitSha));
            string diagnostics = SkillSyncService.DescribeResult(result, includeSourceDetails: true);
            Assert.That(diagnostics, Does.StartWith(summary));
            Assert.That(diagnostics, Does.Contain(result.SourceDescription));
            Assert.That(diagnostics, Does.Contain(result.CommitSha));
        }

        [Test]
        public void ApplyPlan_UnknownBaselinePreservesDifferingLocalFile()
        {
            var before = Snapshot();
            var remote = Remote(("SKILL.md", "upstream edit"));

            Apply(remote, path => remote[path], knownBaseline: false);

            AssertSnapshot(before);
        }

        [Test]
        public void ApplyPlan_LocalOnlyAdditionSurvivesSync()
        {
            Write("personal-notes.md", "local notes must survive");
            var before = Snapshot();
            var remote = Remote(("SKILL.md", "old skill"));

            Apply(remote, path => remote[path], knownBaseline: false);

            AssertSnapshot(before);
        }

        [Test]
        public void ApplyPlan_LocalEditAfterPreviewRejectsBeforeDownload()
        {
            var remote = Remote(("SKILL.md", "old skill"), ("references/new.md", "new upstream file"));
            var hash = Service.GetMethod("ComputeGitBlobSha1", StaticInternal, null, new[] { typeof(byte[]) }, null);
            var hashes = remote.ToDictionary(x => x.Key, x => (string)hash.Invoke(null, new object[] { x.Value }), StringComparer.Ordinal);
            var files = (Dictionary<string, string>)Service.GetMethod("ListFiles", StaticInternal).Invoke(null, new object[] { _install });
            var plan = Service.GetMethod("BuildPlan", StaticInternal).Invoke(null, new object[] { hashes, files, StringComparer.Ordinal });
            Write("SKILL.md", "edited locally after preview");
            var before = Snapshot();
            int downloads = 0;
            var apply = Service.GetMethods(StaticInternal).Single(m => m.Name == "ApplyPlan" && m.GetParameters()[0].ParameterType == typeof(string));
            Func<string, byte[]> download = path =>
            {
                downloads++;
                return remote[path];
            };

            var error = Assert.Throws<TargetInvocationException>(() =>
                apply.Invoke(null, new object[] { _install, plan, hashes, StringComparison.Ordinal, download, null })
            );

            Assert.IsInstanceOf<InvalidOperationException>(error.InnerException);
            Assert.AreEqual(0, downloads);
            AssertSnapshot(before);
        }

        [TestCase(false)]
        [TestCase(true)]
        public void ApplyPlan_FileDirectoryTransition_WritesNewShape(bool wasDirectory)
        {
            string previousPath = wasDirectory ? "notes/deep/guide.md" : "notes";
            string targetPath = wasDirectory ? "notes" : "notes/deep/guide.md";
            Write(previousPath, "old notes");
            var remote = Remote(("SKILL.md", "old skill"), (targetPath, "new notes \uD55C\uAE00"));
            Apply(remote, path => remote[path]);
            Assert.AreEqual("new notes \uD55C\uAE00", File.ReadAllText(Path.Combine(_install, targetPath)));
            Assert.IsFalse(File.Exists(Path.Combine(_install, previousPath)));
            Assert.AreEqual("old skill", File.ReadAllText(Path.Combine(_install, "SKILL.md")));
        }

        [Test]
        public void ApplyPlan_EmptyDirectoryAtNewFilePath_DoesNotBlockWrite()
        {
            Directory.CreateDirectory(Path.Combine(_install, "notes/deep"));
            var remote = Remote(("SKILL.md", "old skill"), ("notes", "new file"));
            Apply(remote, path => remote[path]);
            Assert.AreEqual("new file", File.ReadAllText(Path.Combine(_install, "notes")));
        }

        [TestCase(false)]
        [TestCase(true)]
        public void ApplyPlan_LaterDownloadFailureOrHashMismatch_PreservesEveryPreviousByte(bool wrongHash)
        {
            Write("references/guide.md", "old guide");
            Write("obsolete.md", "old obsolete bytes");
            var before = Snapshot();
            var remote = Remote(("SKILL.md", "new skill"), ("references/guide.md", "new guide"));
            int downloads = 0;
            byte[] Download(string path)
            {
                downloads++;
                if (path == "references/guide.md")
                {
                    if (wrongHash)
                        return Encoding.UTF8.GetBytes("wrong response bytes");
                    throw new IOException("owned download failure");
                }
                return remote[path];
            }
            if (wrongHash)
                Assert.Throws<InvalidOperationException>(() => Apply(remote, Download));
            else
                Assert.Throws<IOException>(() => Apply(remote, Download));
            Assert.AreEqual(2, downloads, "The first changed file must have downloaded before the later failure.");
            AssertSnapshot(before);
        }

        [Test]
        public void ApplyPlan_FailedDownloadBeforeTransition_PreservesBlockingOldFile()
        {
            Write("notes", "old file blocking future directory");
            var before = Snapshot();
            var remote = Remote(("SKILL.md", "old skill"), ("notes/guide.md", "new guide"));
            Assert.Throws<IOException>(() => Apply(remote, _ => throw new IOException("owned download failure")));
            AssertSnapshot(before);
        }

        [Test]
        public void ApplyPlan_SuccessAndRepeat_PreservesMarkerAndOutsideFiles()
        {
            Write("obsolete.md", "managed mirror removes this");
            string outside = Path.Combine(_root, "outside.txt");
            File.WriteAllText(outside, "owned outside sentinel");
            var remote = Remote(("SKILL.md", "new skill"), ("references/guide.md", "new guide"));
            Apply(remote, path => remote[path]);
            Assert.IsFalse(File.Exists(Path.Combine(_install, "obsolete.md")));
            Assert.AreEqual("managed-by-unity-mcp-skill-sync", File.ReadAllText(Path.Combine(_install, ".unity-mcp-skill-sync")));
            Assert.AreEqual("owned outside sentinel", File.ReadAllText(outside));
            var timestamps = Directory.GetFiles(_install, "*", SearchOption.AllDirectories).ToDictionary(x => x, File.GetLastWriteTimeUtc);
            Apply(remote, _ => throw new AssertionException("An idempotent sync must not download."));
            foreach (var file in timestamps)
                Assert.AreEqual(file.Value, File.GetLastWriteTimeUtc(file.Key));
        }

        [Test]
        public void ApplyPlan_UnsafeChangedPath_RejectsBeforeDownloadOrDeletion()
        {
            var before = Snapshot();
            var remote = Remote(("../escape.md", "must not escape"));
            int downloads = 0;
            Assert.Throws<InvalidOperationException>(() =>
                Apply(
                    remote,
                    path =>
                    {
                        downloads++;
                        return remote[path];
                    }
                )
            );
            Assert.AreEqual(0, downloads);
            AssertSnapshot(before);
            Assert.IsFalse(File.Exists(Path.Combine(_root, "escape.md")));
        }

        private const string BaselineName = ".unity-mcp-skill-sync-baseline.json";
        private const string Repository = "https://github.com/example/owned-test";
        private static readonly string CommitA = new('a', 40);
        private static readonly string CommitB = new('b', 40);

        private SkillSyncService.RemoteSnapshot RemoteSnapshot(Dictionary<string, byte[]> files, string commit = null) =>
            new(
                commit ?? CommitA,
                ".claude/skills/unity-mcp-skill",
                files.ToDictionary(file => file.Key, file => SkillSyncService.ComputeGitBlobSha1(file.Value))
            );

        private SkillSyncService.SyncPreview Preview(Dictionary<string, byte[]> files, string commit = null) =>
            SkillSyncService.CreatePreview(Repository, _install, "beta", RemoteSnapshot(files, commit));

        private SkillSyncService.SyncResult ApplyPreview(
            SkillSyncService.SyncPreview preview,
            Dictionary<string, byte[]> files,
            string commit = null,
            Func<string, byte[]> download = null
        ) =>
            SkillSyncService.ApplyPreview(preview, () => RemoteSnapshot(files, commit), (path, limit) => download == null ? files[path] : download(path), null);

        private void EstablishBaseline(params (string path, string text)[] files)
        {
            var remote = files.Length == 0 ? Remote(("SKILL.md", "old skill")) : Remote(files);
            var result = ApplyPreview(Preview(remote), remote);
            Assert.IsTrue(result.Success, result.Error);
            Assert.IsEmpty(result.Conflicts);
        }

        [Test]
        public void PreviewApply_CleanInstallUpdateAndNoOp()
        {
            File.Delete(Path.Combine(_install, "SKILL.md"));
            File.Delete(Path.Combine(_install, ".unity-mcp-skill-sync"));
            var remote = Remote(("SKILL.md", "initial"), ("references/guide.md", "guide"));
            var before = Snapshot();
            var preview = Preview(remote);
            Assert.AreEqual(2, preview.Added.Count);
            AssertSnapshot(before);
            Assert.IsTrue(ApplyPreview(preview, remote).Success);
            Assert.IsTrue(File.Exists(Path.Combine(_install, BaselineName)));
            var timestamps = Snapshot().Keys.ToDictionary(path => path, File.GetLastWriteTimeUtc);
            var noOp = ApplyPreview(Preview(remote), remote, download: _ => throw new AssertionException("No-op downloaded content."));
            Assert.IsTrue(noOp.Success, noOp.Error);
            Assert.AreEqual(0, noOp.Added + noOp.Updated + noOp.Deleted);
            foreach (var timestamp in timestamps)
                Assert.AreEqual(timestamp.Value, File.GetLastWriteTimeUtc(timestamp.Key));
            remote["SKILL.md"] = Encoding.UTF8.GetBytes("upstream update");
            var update = ApplyPreview(Preview(remote, CommitB), remote, CommitB);
            Assert.IsTrue(update.Success, update.Error);
            Assert.AreEqual(1, update.Updated);
            Assert.AreEqual("upstream update", File.ReadAllText(Path.Combine(_install, "SKILL.md")));
        }

        [Test]
        public void PreviewApply_UnknownBaselineMigratesOnlyMatchingAndNewFiles()
        {
            Write("personal.md", "local addition");
            var remote = Remote(("SKILL.md", "different upstream"), ("references/new.md", "new upstream"));
            var preview = Preview(remote);
            CollectionAssert.Contains(preview.Conflicts, "SKILL.md");
            CollectionAssert.Contains(preview.Preserved, "personal.md");
            var result = ApplyPreview(preview, remote);
            Assert.IsTrue(result.Success, result.Error);
            Assert.IsTrue(result.PartialApplication);
            Assert.AreEqual(1, result.Added);
            Assert.AreEqual("old skill", File.ReadAllText(Path.Combine(_install, "SKILL.md")));
            Assert.AreEqual("local addition", File.ReadAllText(Path.Combine(_install, "personal.md")));
            CollectionAssert.Contains(Preview(remote).Conflicts, "SKILL.md", "Migration must not silently adopt conflicting bytes.");
            Assert.IsEmpty(Directory.GetDirectories(_root, ".unity-mcp-sync-*"));
        }

        [Test]
        public void PreviewApply_UnbasedOlderFilesStayProtectedAcrossRepeatAndExplainReview()
        {
            EstablishBaseline();
            Write("references/guide.md", "older upstream or personal edit");
            var remote = Remote(("SKILL.md", "old skill"), ("references/guide.md", "new pinned upstream"));
            var before = Snapshot();
            var preview = Preview(remote, CommitB);
            CollectionAssert.AreEqual(new[] { "references/guide.md" }, preview.UnknownBaselineConflicts);
            var result = ApplyPreview(preview, remote, CommitB, _ => throw new AssertionException("Unbased conflicts must not download or overwrite content."));
            Assert.IsTrue(result.Success, result.Error);
            Assert.IsTrue(result.PartialApplication);
            Assert.AreEqual(0, result.Added + result.Updated + result.Deleted);
            string localGuide = Path.Combine(_install, "references", "guide.md");
            CollectionAssert.AreEqual(before[localGuide], File.ReadAllBytes(localGuide));
            var baseline = Newtonsoft.Json.Linq.JObject.Parse(File.ReadAllText(Path.Combine(_install, BaselineName)));
            Assert.IsFalse(baseline["files"].Any(file => (string)file["path"] == "references/guide.md"), "Unknown bytes must not become a baseline.");
            var repeat = Preview(remote, CommitB);
            CollectionAssert.AreEqual(preview.UnknownBaselineConflicts, repeat.UnknownBaselineConflicts);
            var summary = SkillSyncService.DescribeResult(result);
            Assert.That(summary, Does.Contain("No recorded baseline"));
            Assert.That(summary, Does.Contain("Preserved locally: 1"));
            Assert.That(summary, Does.Contain("https://github.com/example/owned-test/tree/" + CommitB + "/.claude/skills/unity-mcp-skill"));
            Assert.That(summary, Does.Contain("preview again"));
        }

        [TestCase(false, false)]
        [TestCase(true, false)]
        [TestCase(false, true)]
        [TestCase(true, true)]
        public void PreviewApply_PreservesLocalEditOrDeletion(bool deleteLocal, bool changeRemote)
        {
            EstablishBaseline();
            if (deleteLocal)
                File.Delete(Path.Combine(_install, "SKILL.md"));
            else
                Write("SKILL.md", "local edit");
            var remote = Remote(("SKILL.md", changeRemote ? "upstream edit" : "old skill"));
            var preview = Preview(remote, CommitB);
            CollectionAssert.Contains(changeRemote ? preview.Conflicts : preview.Preserved, "SKILL.md");
            var result = ApplyPreview(preview, remote, CommitB);
            Assert.IsTrue(result.Success, result.Error);
            Assert.AreEqual(changeRemote, result.PartialApplication);
            Assert.AreEqual(!deleteLocal, File.Exists(Path.Combine(_install, "SKILL.md")));
            if (!deleteLocal)
                Assert.AreEqual("local edit", File.ReadAllText(Path.Combine(_install, "SKILL.md")));
            CollectionAssert.Contains(changeRemote ? Preview(remote, CommitB).Conflicts : Preview(remote, CommitB).Preserved, "SKILL.md");
        }

        [TestCase(false)]
        [TestCase(true)]
        public void PreviewApply_RemoteRemovalDeletesOnlyUnchangedManagedFile(bool edited)
        {
            Write("obsolete.md", "previous upstream");
            EstablishBaseline(("SKILL.md", "old skill"), ("obsolete.md", "previous upstream"));
            Write("personal.md", "not managed");
            if (edited)
                Write("obsolete.md", "local obsolete edit");
            var remote = Remote(("SKILL.md", "old skill"));
            var result = ApplyPreview(Preview(remote, CommitB), remote, CommitB);
            Assert.IsTrue(result.Success, result.Error);
            Assert.AreEqual(edited ? 0 : 1, result.Deleted);
            Assert.AreEqual(edited, File.Exists(Path.Combine(_install, "obsolete.md")));
            if (edited)
                CollectionAssert.Contains(result.Conflicts, "obsolete.md");
            Assert.AreEqual("not managed", File.ReadAllText(Path.Combine(_install, "personal.md")));
        }

        [TestCase("local")]
        [TestCase("baseline")]
        [TestCase("remote")]
        [TestCase("download")]
        public void PreviewApply_DriftRejectsWithoutOverwritingLocalState(string drift)
        {
            EstablishBaseline();
            var remote = Remote(("SKILL.md", "new upstream"));
            var preview = Preview(remote, CommitB);
            if (drift == "local")
                Write("SKILL.md", "edit after preview");
            if (drift == "baseline")
                File.AppendAllText(Path.Combine(_install, BaselineName), " ");
            var before = Snapshot();
            int downloads = 0;
            var result = ApplyPreview(
                preview,
                remote,
                drift == "remote" ? CommitA : CommitB,
                path =>
                {
                    downloads++;
                    if (drift == "download")
                        Write("SKILL.md", "edit while downloading");
                    return remote[path];
                }
            );
            Assert.IsFalse(result.Success);
            Assert.That(result.Error, Does.Contain("changed"));
            Assert.AreEqual(drift == "download" ? 1 : 0, downloads);
            if (drift == "download")
                Assert.AreEqual("edit while downloading", File.ReadAllText(Path.Combine(_install, "SKILL.md")));
            else
                AssertSnapshot(before);
        }

        [TestCase("invalid")]
        [TestCase("source")]
        [TestCase("file_child")]
        [TestCase("parent_case")]
        public void Preview_InvalidOrMismatchedBaselineFailsClosed(string kind)
        {
            EstablishBaseline();
            if (kind == "invalid")
                Write(BaselineName, "not json");
            if (kind == "file_child" || kind == "parent_case")
            {
                var baseline = Newtonsoft.Json.Linq.JObject.Parse(File.ReadAllText(Path.Combine(_install, BaselineName)));
                var files = (Newtonsoft.Json.Linq.JArray)baseline["files"];
                var paths = kind == "file_child" ? new[] { "SKILL.md/child.md" } : new[] { "References/a.md", "references/b.md" };
                foreach (string path in paths)
                    files.Add(new Newtonsoft.Json.Linq.JObject { ["path"] = path, ["hash"] = CommitA });
                Write(BaselineName, baseline.ToString());
            }
            var before = Snapshot();
            Assert.Throws<InvalidOperationException>(() =>
                SkillSyncService.CreatePreview(
                    kind == "source" ? "https://github.com/other/owned-test" : Repository,
                    _install,
                    "beta",
                    RemoteSnapshot(Remote(("SKILL.md", "new upstream")))
                )
            );
            AssertSnapshot(before);
        }

        [TestCase("install")]
        [TestCase("metadata")]
        public void PreviewApply_IntermediateFailureRestoresEveryPreviousByte(string failurePhase)
        {
            Write("references/guide.md", "old guide");
            EstablishBaseline(("SKILL.md", "old skill"), ("references/guide.md", "old guide"));
            var remote = Remote(("SKILL.md", "new skill"), ("references/guide.md", "new guide"));
            var preview = Preview(remote, CommitB);
            var before = Snapshot();
            int installs = 0;
            SkillSyncService.BeforeFileOperation = (phase, path) =>
            {
                if (phase == "install")
                    installs++;
                if (
                    failurePhase == "install" && phase == "install" && installs == 2
                    || failurePhase == "metadata" && phase == "metadata" && path == BaselineName
                )
                    throw new IOException("owned intermediate failure");
            };
            var result = ApplyPreview(preview, remote, CommitB);
            Assert.IsFalse(result.Success);
            Assert.IsTrue(result.RolledBack, result.Error);
            Assert.IsFalse(result.PartialApplication);
            AssertSnapshot(before);
            Assert.IsEmpty(Directory.GetDirectories(_root, ".unity-mcp-sync-*"));
        }

        [Test]
        public void PreviewApply_BlockedRollbackRetainsBackupAndNeverOverwritesNewLocalEdit()
        {
            EstablishBaseline();
            var remote = Remote(("SKILL.md", "new skill"), ("z-new.md", "new file"));
            var preview = Preview(remote, CommitB);
            SkillSyncService.BeforeFileOperation = (phase, path) =>
            {
                if (phase == "install" && path == "z-new.md")
                {
                    Write("SKILL.md", "concurrent local edit");
                    throw new IOException("owned blocked rollback");
                }
            };
            var result = ApplyPreview(preview, remote, CommitB);
            Assert.IsFalse(result.Success);
            Assert.IsFalse(result.RolledBack);
            Assert.IsTrue(result.PartialApplication);
            Assert.IsNotEmpty(result.RecoveryPath);
            Assert.AreEqual("concurrent local edit", File.ReadAllText(Path.Combine(_install, "SKILL.md")));
            AssertRecoveryContains(result.RecoveryPath, "SKILL.md", "old skill");
        }

        [Test]
        public void PreviewApply_EmptyDirectoryCreatedDuringApplyIsRetainedOnRollback()
        {
            EstablishBaseline();
            var remote = Remote(("SKILL.md", "new skill"));
            var preview = Preview(remote, CommitB);
            SkillSyncService.BeforeFileOperation = (phase, path) =>
            {
                if (phase == "install" && path == "SKILL.md")
                {
                    Directory.CreateDirectory(Path.Combine(_install, "SKILL.md"));
                    throw new IOException("owned concurrent shape change");
                }
            };
            var result = ApplyPreview(preview, remote, CommitB);
            Assert.IsFalse(result.Success);
            Assert.IsFalse(result.RolledBack);
            Assert.IsTrue(Directory.Exists(Path.Combine(_install, "SKILL.md")));
            AssertRecoveryContains(result.RecoveryPath, "SKILL.md", "old skill");
        }

        [TestCase(false)]
        [TestCase(true)]
        public void PreviewApply_ParentCaseChangeConflictsWithoutChangingLocalDirectory(bool empty)
        {
            if (empty)
                Directory.CreateDirectory(Path.Combine(_install, "references"));
            else
                Write("references/personal.md", "local reference");
            var remote = Remote(("SKILL.md", "old skill"), ("References/new.md", "upstream reference"));
            var preview = Preview(remote);
            CollectionAssert.Contains(preview.Conflicts, "References/new.md");
            Assert.IsEmpty(preview.Added);
            var result = ApplyPreview(preview, remote, CommitA, _ => throw new AssertionException("Conflicting parent must not download."));
            Assert.IsTrue(result.Success, result.Error);
            Assert.IsTrue(result.PartialApplication);
            Assert.IsFalse(File.Exists(Path.Combine(_install, "references", "new.md")));
            Assert.AreEqual("references", Path.GetFileName(Directory.GetDirectories(_install).Single()));
        }

        [Test]
        public void PreviewApply_RemoteChangesDuringDownloadRejectsBeforeMutation()
        {
            EstablishBaseline();
            var remote = Remote(("SKILL.md", "new skill"));
            var preview = Preview(remote, CommitB);
            var before = Snapshot();
            bool downloaded = false;
            int validations = 0;
            var result = SkillSyncService.ApplyPreview(
                preview,
                () =>
                {
                    validations++;
                    return RemoteSnapshot(remote, downloaded ? CommitA : CommitB);
                },
                (path, limit) =>
                {
                    downloaded = true;
                    return remote[path];
                },
                null
            );
            Assert.IsFalse(result.Success);
            Assert.That(result.Error, Does.Contain("changed"));
            Assert.IsTrue(downloaded);
            Assert.AreEqual(2, validations);
            AssertSnapshot(before);
            Assert.IsEmpty(Directory.GetDirectories(_root, ".unity-mcp-sync-*"));
        }

        private void AssertRecoveryContains(string recoveryRoot, string relativePath, string text)
        {
            Assert.IsNotEmpty(recoveryRoot);
            var mapping = Newtonsoft.Json.Linq.JObject.Parse(File.ReadAllText(Path.Combine(recoveryRoot, "recovery.json")));
            Assert.AreEqual(_install, (string)mapping["installRoot"]);
            var file = mapping["files"].Single(entry => (string)entry["path"] == relativePath);
            string backup = (string)file["backup"];
            Assert.AreEqual(Path.GetFileName(backup), backup);
            string saved = Path.Combine(recoveryRoot, backup);
            Assert.AreEqual(text, File.ReadAllText(saved));
            Assert.AreEqual(SkillSyncService.ComputeGitBlobSha1(saved), (string)file["hash"]);
        }

        private void Write(string relative, string text)
        {
            string path = Path.GetFullPath(Path.Combine(_install, relative));
            Assert.IsTrue(path.StartsWith(_install + Path.DirectorySeparatorChar, StringComparison.OrdinalIgnoreCase));
            Directory.CreateDirectory(Path.GetDirectoryName(path));
            File.WriteAllBytes(path, Encoding.UTF8.GetBytes(text));
        }

        private Dictionary<string, byte[]> Snapshot() => Directory.GetFiles(_install, "*", SearchOption.AllDirectories).ToDictionary(x => x, File.ReadAllBytes);

        private void AssertSnapshot(Dictionary<string, byte[]> before)
        {
            var after = Snapshot();
            CollectionAssert.AreEquivalent(before.Keys, after.Keys);
            foreach (var file in before)
                CollectionAssert.AreEqual(file.Value, after[file.Key], file.Key);
        }

        private static Dictionary<string, byte[]> Remote(params (string path, string text)[] files) =>
            files.ToDictionary(x => x.path, x => Encoding.UTF8.GetBytes(x.text), StringComparer.Ordinal);

        private void Apply(Dictionary<string, byte[]> remote, Func<string, byte[]> download, bool knownBaseline = true)
        {
            try
            {
                var hash = Service.GetMethod("ComputeGitBlobSha1", StaticInternal, null, new[] { typeof(byte[]) }, null);
                var hashes = remote.ToDictionary(x => x.Key, x => (string)hash.Invoke(null, new object[] { x.Value }), StringComparer.Ordinal);
                var files = (Dictionary<string, string>)Service.GetMethod("ListFiles", StaticInternal).Invoke(null, new object[] { _install });
                var baseline = files.ToDictionary(file => file.Key, file => SkillSyncService.ComputeGitBlobSha1(file.Value));
                var plan = knownBaseline
                    ? Service.GetMethod("BuildPlanWithBaseline", StaticInternal).Invoke(null, new object[] { hashes, files, baseline, StringComparer.Ordinal })
                    : Service.GetMethod("BuildPlan", StaticInternal).Invoke(null, new object[] { hashes, files, StringComparer.Ordinal });
                var apply = Service.GetMethods(StaticInternal).Single(m => m.Name == "ApplyPlan" && m.GetParameters()[0].ParameterType == typeof(string));
                apply.Invoke(null, new object[] { _install, plan, hashes, StringComparison.Ordinal, download, null });
            }
            catch (TargetInvocationException ex)
            {
                throw ex.InnerException ?? ex;
            }
        }
    }
}
