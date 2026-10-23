using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Net;
using System.Net.Http;
using System.Reflection;
using System.Text;
using System.Threading;
using System.Threading.Tasks;
using MCPForUnity.Editor.Setup;
using Newtonsoft.Json.Linq;
using NUnit.Framework;

namespace MCPForUnityTests.Editor.Setup
{
    public class SkillSyncSourceTests
    {
        private const BindingFlags Static = BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Static;
        private const string Repository = "https://github.com/ykh09242/unity-mcp";
        private const string Subdir = ".claude/skills/unity-mcp-skill";
        private static readonly string A = new('a', 40);
        private static readonly string B = new('b', 40);
        private static readonly string Tree = new('c', 40);
        private string _root;
        private string _install;

        [SetUp]
        public void SetUp()
        {
            _root = Path.Combine(Path.GetTempPath(), "UnityMcpSkillSource-" + Guid.NewGuid().ToString("N"));
            Directory.CreateDirectory(_root);
            _install = Path.Combine(_root, "skill");
        }

        [TearDown]
        public void TearDown()
        {
            SkillSyncService.BeforeFileOperation = null;
            if (Directory.Exists(_root))
                Directory.Delete(_root, true);
        }

        private static Type SourceType
        {
            get
            {
                var type = typeof(SkillSyncService).Assembly.GetType("MCPForUnity.Editor.Setup.SkillSyncSource");
                Assert.IsNotNull(type, "Package-matched skill selection must have an explicit source contract.");
                return type;
            }
        }

        private static object Call(Type type, string method, params object[] arguments)
        {
            var target = type.GetMethod(method, Static);
            Assert.IsNotNull(target, method);
            try
            {
                return target.Invoke(null, arguments);
            }
            catch (TargetInvocationException error)
            {
                throw error.InnerException ?? error;
            }
        }

        private static JObject Metadata(string revision) =>
            new JObject
            {
                ["name"] = "com.ykh09242.unity-mcp",
                ["version"] = "1.2.1",
                ["mcpServerSource"] = "https://github.com/ykh09242/unity-mcp/archive/" + B + ".zip#subdirectory=Server",
                ["mcpSkillRevision"] = revision,
            };

        private static object Package(string revision, string identity = "installed-package") => Call(SourceType, "FromPackage", Metadata(revision), identity);

        private static object Latest(string branch = "beta") => Call(SourceType, "Latest", Repository, branch);

        private static string Property(object source, string name) => (string)SourceType.GetProperty(name).GetValue(source);

        private static Func<T> TypedReader<T>(Func<object> read) => () => (T)read();

        private static Delegate Reader(Func<object> read) =>
            (Delegate)typeof(SkillSyncSourceTests).GetMethod(nameof(TypedReader), Static).MakeGenericMethod(SourceType).Invoke(null, new object[] { read });

        private static SkillSyncService.RemoteSnapshot Snapshot(string commit, string text = "skill") =>
            new(commit, Subdir, new Dictionary<string, string> { ["SKILL.md"] = SkillSyncService.ComputeGitBlobSha1(Encoding.UTF8.GetBytes(text)) });

        private SkillSyncService.SyncPreview Preview(object source, string commit, string text = "skill") =>
            (SkillSyncService.SyncPreview)Call(typeof(SkillSyncService), "CreateSourcePreview", source, _install, Snapshot(commit, text));

        private static SkillSyncService.SyncResult Apply(
            SkillSyncService.SyncPreview preview,
            Func<object> read,
            Func<SkillSyncService.RemoteSnapshot> fetch,
            Func<string, long, byte[]> download
        ) => (SkillSyncService.SyncResult)Call(typeof(SkillSyncService), "ApplySourcePreview", preview, Reader(read), fetch, download, null);

        [Test]
        public void InstalledPackagesWithSameUnityAndServerVersionSelectTheirOwnSkillPins()
        {
            Assert.AreEqual(A, Property(Package(A), "Revision"));
            Assert.AreEqual(B, Property(Package(B), "Revision"));
            string description = Property(Package(A, "https://fixture-user:fixture-secret@github.com/ykh09242/unity-mcp"), "Description");
            StringAssert.Contains("1.2.1", description);
            StringAssert.Contains("identity", description);
            StringAssert.Contains("ykh09242/unity-mcp/" + Subdir, description);
            StringAssert.Contains(A, description);
            StringAssert.DoesNotContain("fixture-secret", description);
            var metadata = Metadata(A);
            metadata["version"] = "99.0.0-beta.8";
            metadata["mcpServerSource"] = "independent-server";
            var selected = Call(SourceType, "FromPackage", metadata, "package#main");
            Assert.AreEqual(A, Property(selected, "Revision"));
        }

        [TestCase("null")]
        [TestCase("true")]
        [TestCase("42")]
        [TestCase("\"beta\"")]
        [TestCase("\"aaaaaaaa\"")]
        [TestCase("\"0000000000000000000000000000000000000000\"")]
        [TestCase("\" aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\"")]
        public void MalformedPackagePinHasActionableError(string json)
        {
            var metadata = Metadata(A);
            metadata["mcpSkillRevision"] = JToken.Parse(json);
            string error = Assert.Throws<InvalidOperationException>(() => Call(SourceType, "FromPackage", metadata, "package")).Message;
            StringAssert.Contains("mcpSkillRevision", error);
            StringAssert.Contains("1.2.1", error);
        }

        [Test]
        public void MissingPackagePinNeverFallsBackToServerOrLatest()
        {
            var metadata = Metadata(A);
            metadata.Remove("mcpSkillRevision");
            Assert.Throws<InvalidOperationException>(() => Call(SourceType, "FromPackage", metadata, "package"));
        }

        [TestCase("pin")]
        [TestCase("identity")]
        [TestCase("version")]
        public void InstalledSelectionDriftRejectsBeforeRemoteOrDownload(string changed)
        {
            var source = Package(A);
            var preview = Preview(source, A);
            var metadata = Metadata(changed == "pin" ? B : A);
            if (changed == "version")
                metadata["version"] = "1.2.2";
            var current = Call(SourceType, "FromPackage", metadata, changed == "identity" ? "replacement-package" : "installed-package");
            int requests = 0;
            var result = Apply(
                preview,
                () => current,
                () =>
                {
                    requests++;
                    return Snapshot(A);
                },
                (_, _) => throw new AssertionException("Downloaded after drift.")
            );
            Assert.IsFalse(result.Success);
            StringAssert.Contains("Preview again", result.Error);
            Assert.AreEqual(0, requests);
            Assert.IsFalse(Directory.Exists(preview.InstallPath));
        }

        [TestCase("download")]
        [TestCase("stage")]
        [TestCase("install")]
        public void SameVersionPinChangeBeforeInstallPreservesDestination(string phase)
        {
            var selected = Package(A);
            var current = selected;
            var preview = Preview(selected, A);
            SkillSyncService.BeforeFileOperation = (operation, _) =>
            {
                if (operation == phase)
                    current = Package(B);
            };
            var result = Apply(
                preview,
                () => current,
                () => Snapshot(A),
                (_, _) =>
                {
                    if (phase == "download")
                        current = Package(B);
                    return Encoding.UTF8.GetBytes("skill");
                }
            );
            Assert.IsFalse(result.Success);
            StringAssert.Contains("Preview again", result.Error);
            Assert.IsFalse(File.Exists(Path.Combine(_install, "SKILL.md")));
            Assert.IsFalse(File.Exists(Path.Combine(_install, ".unity-mcp-skill-sync-baseline.json")));
        }

        [TestCase("stage")]
        [TestCase("install")]
        [TestCase("metadata")]
        public void CancelledLatestApplyDoesNotPublishAndRetainsRollbackOwnership(string phase)
        {
            var preview = SkillSyncService.CreatePreview(Repository, _install, "beta", Snapshot(A));
            using var cancellation = new CancellationTokenSource();
            var type = typeof(SkillSyncService).Assembly.GetType("MCPForUnity.Editor.Setup.SkillSyncWaitBudget");
            Assert.IsNotNull(type);
            object budget = Activator.CreateInstance(
                type,
                BindingFlags.NonPublic | BindingFlags.Instance,
                null,
                new object[] { 300d, cancellation.Token, null },
                null
            );
            SkillSyncService.BeforeFileOperation = (operation, _) =>
            {
                if (operation == phase)
                    cancellation.Cancel();
            };
            var method = typeof(SkillSyncService).GetMethod("ApplySourcePreviewWithBudget", Static);
            Assert.IsNotNull(method);
            var result = (SkillSyncService.SyncResult)
                method.Invoke(
                    null,
                    new object[]
                    {
                        preview,
                        (Func<SkillSyncSource>)(() => throw new AssertionException("Latest must not read a package pin.")),
                        (Func<SkillSyncService.RemoteSnapshot>)(() => Snapshot(A)),
                        (Func<string, long, byte[]>)((_, _) => Encoding.UTF8.GetBytes("skill")),
                        null,
                        budget,
                    }
                );
            Assert.IsFalse(result.Success);
            Assert.IsTrue(result.RolledBack);
            Assert.IsFalse(result.PartialApplication);
            Assert.IsFalse(File.Exists(Path.Combine(_install, "SKILL.md")));
            Assert.IsFalse(File.Exists(Path.Combine(_install, ".unity-mcp-skill-sync-baseline.json")));
        }

        [Test]
        public void PackageModeRejectsSnapshotForAnotherCommit()
        {
            Assert.Throws<InvalidOperationException>(() => Preview(Package(A), B));
        }

        [Test]
        public void LegacyBaselineModeTransitionsPreserveConflictsAndLocalOnlyFiles()
        {
            var first = SkillSyncService.CreatePreview(Repository, _install, "beta", Snapshot(A));
            Assert.IsTrue(SkillSyncService.ApplyPreview(first, () => Snapshot(A), (_, _) => Encoding.UTF8.GetBytes("skill"), null).Success);
            string baseline = Path.Combine(_install, ".unity-mcp-skill-sync-baseline.json");
            var legacy = JObject.Parse(File.ReadAllText(baseline));
            legacy["version"] = 1;
            legacy["branch"] = "beta";
            File.WriteAllText(baseline, legacy.ToString());
            File.WriteAllText(Path.Combine(_install, "SKILL.md"), "local edit");
            File.WriteAllText(Path.Combine(_install, "personal.md"), "personal");
            foreach (var source in new[] { Package(B), Latest("main"), Package(A) })
            {
                string commit = Property(source, "Revision") ?? B;
                string text = commit == A ? "skill" : "remote edit";
                var preview = Preview(source, commit, text);
                CollectionAssert.Contains(commit == A ? preview.Preserved : preview.Conflicts, "SKILL.md");
                var result = Apply(preview, () => source, () => Snapshot(commit, text), (_, _) => throw new AssertionException("Conflict download."));
                Assert.IsTrue(result.Success, result.Error);
                Assert.AreEqual("local edit", File.ReadAllText(Path.Combine(_install, "SKILL.md")));
                Assert.AreEqual("personal", File.ReadAllText(Path.Combine(_install, "personal.md")));
                var state = JObject.Parse(File.ReadAllText(baseline));
                Assert.AreEqual(2, state.Value<int>("version"));
                Assert.AreEqual((string)legacy["files"][0]["hash"], (string)state["files"][0]["hash"]);
            }
        }

        private sealed class Handler : HttpMessageHandler
        {
            internal readonly List<string> Requests = new();
            internal string Head = A;
            internal string ReturnedCommit = A;
            internal string ReturnedTree = Tree;
            internal bool MissingSkill;
            internal bool Unavailable;

            protected override Task<HttpResponseMessage> SendAsync(HttpRequestMessage request, CancellationToken token)
            {
                string path = request.RequestUri.AbsolutePath;
                Requests.Add(path);
                string json;
                if (path.Contains("/branches/"))
                    json = new JObject { ["commit"] = new JObject { ["sha"] = Head } }.ToString();
                else if (path.Contains("/git/commits/"))
                    json = new JObject
                    {
                        ["sha"] = ReturnedCommit,
                        ["tree"] = new JObject { ["sha"] = Tree },
                    }.ToString();
                else
                    json = new JObject
                    {
                        ["sha"] = ReturnedTree,
                        ["tree"] = new JArray(
                            new JObject
                            {
                                ["type"] = "blob",
                                ["path"] = Subdir + (MissingSkill ? "/other.md" : "/SKILL.md"),
                                ["sha"] = B,
                                ["size"] = 5,
                            }
                        ),
                    }.ToString();
                return Task.FromResult(
                    new HttpResponseMessage(Unavailable ? HttpStatusCode.NotFound : HttpStatusCode.OK) { Content = new StringContent(json) }
                );
            }
        }

        private static SkillSyncService.RemoteSnapshot Fetch(HttpClient client, object source) =>
            (SkillSyncService.RemoteSnapshot)Call(typeof(SkillSyncService), "FetchSourceSnapshot", client, source, null);

        [Test]
        public void PinnedSnapshotIgnoresMovingBranchAndVerifiesExactCommitAndTree()
        {
            var handler = new Handler { Head = B };
            using var client = new HttpClient(handler);
            Assert.AreEqual(A, Fetch(client, Package(A)).CommitSha);
            Assert.IsFalse(handler.Requests.Any(path => path.Contains("/branches/")));
            CollectionAssert.AreEqual(new[] { "/repos/ykh09242/unity-mcp/git/commits/" + A, "/repos/ykh09242/unity-mcp/git/trees/" + Tree }, handler.Requests);
        }

        [TestCase("commit")]
        [TestCase("tree")]
        [TestCase("missing_skill")]
        [TestCase("unavailable")]
        public void InvalidRemotePinIdentityNeverCreatesInstallation(string failure)
        {
            var handler = new Handler
            {
                ReturnedCommit = failure == "commit" ? B : A,
                ReturnedTree = failure == "tree" ? A : Tree,
                MissingSkill = failure == "missing_skill",
                Unavailable = failure == "unavailable",
            };
            using var client = new HttpClient(handler);
            Assert.Throws<InvalidOperationException>(() => Fetch(client, Package(A)));
            Assert.IsFalse(Directory.Exists(_install));
        }

        [Test]
        public void LatestIsExplicitAndBranchMovementRequiresNewPreview()
        {
            var source = Latest();
            StringAssert.Contains("ykh09242/unity-mcp/" + Subdir, Property(source, "Description"));
            var handler = new Handler();
            using var client = new HttpClient(handler);
            var snapshot = Fetch(client, source);
            var preview = (SkillSyncService.SyncPreview)Call(typeof(SkillSyncService), "CreateSourcePreview", source, _install, snapshot);
            handler.Head = handler.ReturnedCommit = B;
            var result = Apply(preview, () => source, () => Fetch(client, source), (_, _) => throw new AssertionException("Moved branch download."));
            Assert.IsFalse(result.Success);
            StringAssert.Contains("Preview again", result.Error);
            Assert.AreEqual(B, Fetch(client, source).CommitSha);
        }
    }
}
