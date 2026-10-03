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
            string temp = Path.GetFullPath(Path.GetTempPath()).TrimEnd(Path.DirectorySeparatorChar) + Path.DirectorySeparatorChar;
            Assert.IsTrue(_root.StartsWith(temp, StringComparison.OrdinalIgnoreCase));
            Assert.IsTrue(Path.GetFileName(_root).StartsWith("UnityMcpSkillSyncTests-", StringComparison.Ordinal));
            if (Directory.Exists(_root)) Directory.Delete(_root, true);
        }

        [TestCase(false)]
        [TestCase(true)]
        public void ApplyPlan_FileDirectoryTransition_WritesNewShape(bool wasDirectory)
        {
            string previousPath = wasDirectory ? "notes/deep/guide.md" : "notes";
            string targetPath = wasDirectory ? "notes" : "notes/deep/guide.md";
            Write(previousPath, "old notes");
            var remote = Remote(("SKILL.md", "old skill"), (targetPath, "new notes 한글"));
            Apply(remote, path => remote[path]);
            Assert.AreEqual("new notes 한글", File.ReadAllText(Path.Combine(_install, targetPath)));
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
                    if (wrongHash) return Encoding.UTF8.GetBytes("wrong response bytes");
                    throw new IOException("owned download failure");
                }
                return remote[path];
            }
            if (wrongHash) Assert.Throws<InvalidOperationException>(() => Apply(remote, Download));
            else Assert.Throws<IOException>(() => Apply(remote, Download));
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
            foreach (var file in timestamps) Assert.AreEqual(file.Value, File.GetLastWriteTimeUtc(file.Key));
        }

        [Test]
        public void ApplyPlan_UnsafeChangedPath_RejectsBeforeDownloadOrDeletion()
        {
            var before = Snapshot();
            var remote = Remote(("../escape.md", "must not escape"));
            int downloads = 0;
            Assert.Throws<InvalidOperationException>(() => Apply(remote, path => { downloads++; return remote[path]; }));
            Assert.AreEqual(0, downloads);
            AssertSnapshot(before);
            Assert.IsFalse(File.Exists(Path.Combine(_root, "escape.md")));
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
            foreach (var file in before) CollectionAssert.AreEqual(file.Value, after[file.Key], file.Key);
        }

        private static Dictionary<string, byte[]> Remote(params (string path, string text)[] files)
            => files.ToDictionary(x => x.path, x => Encoding.UTF8.GetBytes(x.text), StringComparer.Ordinal);

        private void Apply(Dictionary<string, byte[]> remote, Func<string, byte[]> download)
        {
            var hash = Service.GetMethod("ComputeGitBlobSha1", StaticInternal, null, new[] { typeof(byte[]) }, null);
            var hashes = remote.ToDictionary(x => x.Key, x => (string)hash.Invoke(null, new object[] { x.Value }), StringComparer.Ordinal);
            var files = (Dictionary<string, string>)Service.GetMethod("ListFiles", StaticInternal).Invoke(null, new object[] { _install });
            var plan = Service.GetMethod("BuildPlan", StaticInternal).Invoke(null, new object[] { hashes, files, StringComparer.Ordinal });
            var apply = Service.GetMethods(StaticInternal).Single(m => m.Name == "ApplyPlan" && m.GetParameters()[0].ParameterType == typeof(string));
            try { apply.Invoke(null, new object[] { _install, plan, hashes, StringComparison.Ordinal, download, null }); }
            catch (TargetInvocationException ex) { throw ex.InnerException ?? ex; }
        }
    }
}
