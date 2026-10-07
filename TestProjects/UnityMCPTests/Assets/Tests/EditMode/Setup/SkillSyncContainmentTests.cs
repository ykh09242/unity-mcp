using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Runtime.InteropServices;
using System.Text;
using MCPForUnity.Editor.Setup;
using NUnit.Framework;

namespace MCPForUnityTests.Editor.Setup
{
    [TestFixture]
    public class SkillSyncContainmentTests
    {
        private const BindingFlags Internal = BindingFlags.Static | BindingFlags.NonPublic;
        private string _root,
            _install,
            _outside;
        private readonly List<(string path, bool directory)> _links = new();

        [SetUp]
        public void SetUp()
        {
            _links.Clear();
            _root = Path.Combine(Path.GetTempPath(), "UnityMcpSkillLinks-" + Guid.NewGuid().ToString("N"));
            _install = Path.Combine(_root, "install");
            _outside = Path.Combine(_root, "outside");
            Directory.CreateDirectory(_install);
            Directory.CreateDirectory(_outside);
            File.WriteAllText(Path.Combine(_install, "SKILL.md"), "old");
            File.WriteAllText(Path.Combine(_outside, "sentinel.md"), "outside sentinel");
        }

        [TearDown]
        public void TearDown()
        {
            // Unlink each known fixture before any recursive cleanup.
            foreach (var link in _links.AsEnumerable().Reverse())
            {
                if (link.directory)
                    Directory.Delete(link.path, false);
                else
                    File.Delete(link.path);
            }
            string temp = Path.GetFullPath(Path.GetTempPath()).TrimEnd(Path.DirectorySeparatorChar) + Path.DirectorySeparatorChar;
            Assert.IsTrue(Path.GetFullPath(_root).StartsWith(temp, StringComparison.OrdinalIgnoreCase));
            Assert.IsTrue(Path.GetFileName(_root).StartsWith("UnityMcpSkillLinks-", StringComparison.Ordinal));
            Directory.Delete(_root, true);
        }

        [TestCase("file")]
        [TestCase("directory")]
        [TestCase("junction")]
        [TestCase("dangling-file")]
        [TestCase("dangling-directory")]
        [TestCase("marker")]
        [TestCase("root")]
        public void ListFiles_PreexistingLinks_RejectsBeforeReadingOrAdoption(string kind)
        {
            string path = Path.Combine(_install, kind == "marker" ? ".unity-mcp-skill-sync" : "references");
            bool directory = kind == "directory" || kind == "junction" || kind == "dangling-directory" || kind == "root";
            string target = directory ? _outside : Path.Combine(_outside, "sentinel.md");
            if (kind.StartsWith("dangling", StringComparison.Ordinal))
                target = Path.Combine(_outside, "absent");
            if (kind == "root")
            {
                Directory.Delete(_install, true);
                path = _install;
            }
            Link(path, target, directory, kind == "junction");
            Assert.Throws<InvalidOperationException>(() => Invoke("ListFiles", _install));
            Assert.AreEqual("outside sentinel", File.ReadAllText(Path.Combine(_outside, "sentinel.md")));
        }

        [Test]
        public void BuildPlan_LinkSwapAfterListing_RejectsBeforeHashRead()
        {
            var local = (Dictionary<string, string>)Invoke("ListFiles", _install);
            string skill = Path.Combine(_install, "SKILL.md");
            File.Delete(skill);
            Link(skill, Path.Combine(_outside, "sentinel.md"), false);
            var remote = new Dictionary<string, string> { ["SKILL.md"] = Hash(Encoding.UTF8.GetBytes("new")) };
            Assert.Throws<InvalidOperationException>(() => Invoke("BuildPlan", remote, local, StringComparer.Ordinal));
        }

        [TestCase(false)]
        [TestCase(true)]
        public void ApplyPlan_LinkSwapAfterPlanning_RejectsBeforeDownload(bool deletion)
        {
            var remote = new Dictionary<string, string> { ["SKILL.md"] = Hash(Encoding.UTF8.GetBytes("new")) };
            string old = Path.Combine(_install, "obsolete.md");
            File.WriteAllText(old, "obsolete");
            object plan = Plan(remote);
            string path = deletion ? old : Path.Combine(_install, "SKILL.md");
            File.Delete(path);
            Link(path, Path.Combine(_outside, "sentinel.md"), false);
            int downloads = 0;
            Assert.Throws<InvalidOperationException>(() =>
                Apply(
                    plan,
                    remote,
                    _ =>
                    {
                        downloads++;
                        return Encoding.UTF8.GetBytes("new");
                    }
                )
            );
            Assert.AreEqual(0, downloads);
            Assert.AreEqual("outside sentinel", File.ReadAllText(Path.Combine(_outside, "sentinel.md")));
        }

        [TestCase(false)]
        [TestCase(true)]
        public void ApplyPlan_FinalLinkSwapDuringDownload_RejectsBeforeAnyMutation(bool deletion)
        {
            var remote = new Dictionary<string, string> { ["SKILL.md"] = Hash(Encoding.UTF8.GetBytes("new")) };
            string old = Path.Combine(_install, "obsolete.md");
            File.WriteAllText(old, "obsolete");
            object plan = Plan(remote);
            int downloads = 0;
            Assert.Throws<InvalidOperationException>(() =>
                Apply(
                    plan,
                    remote,
                    _ =>
                    {
                        downloads++;
                        string path = deletion ? old : Path.Combine(_install, "SKILL.md");
                        File.Delete(path);
                        Link(path, Path.Combine(_outside, "sentinel.md"), false);
                        return Encoding.UTF8.GetBytes("new");
                    }
                )
            );
            Assert.AreEqual(1, downloads);
            Assert.AreEqual("outside sentinel", File.ReadAllText(Path.Combine(_outside, "sentinel.md")));
            if (deletion)
                Assert.AreEqual("old", File.ReadAllText(Path.Combine(_install, "SKILL.md")));
            else
                Assert.AreEqual("obsolete", File.ReadAllText(old));
        }

        [TestCase(false)]
        [TestCase(true)]
        public void ApplyPlan_FinalDirectoryOrRootSwap_RejectsBeforeAnyMutation(bool root)
        {
            Directory.CreateDirectory(Path.Combine(_install, "references"));
            File.WriteAllText(Path.Combine(_install, "references", "guide.md"), "old guide");
            var remote = new Dictionary<string, string>
            {
                ["SKILL.md"] = Hash(Encoding.UTF8.GetBytes("old")),
                ["references/guide.md"] = Hash(Encoding.UTF8.GetBytes("new")),
            };
            object plan = Plan(remote);
            string moved = Path.Combine(_root, "saved");
            Assert.Throws<InvalidOperationException>(() =>
                Apply(
                    plan,
                    remote,
                    _ =>
                    {
                        string swapped = root ? _install : Path.Combine(_install, "references");
                        Directory.Move(swapped, moved);
                        Link(swapped, _outside, true);
                        return Encoding.UTF8.GetBytes("new");
                    }
                )
            );
            Assert.AreEqual("outside sentinel", File.ReadAllText(Path.Combine(_outside, "sentinel.md")));
            Assert.AreEqual("old guide", File.ReadAllText(Path.Combine(moved, root ? "references/guide.md" : "guide.md")));
            Assert.IsFalse(File.Exists(Path.Combine(_outside, "guide.md")));
        }

        [Test]
        public void ResolveInstallPath_LinkedAncestor_Rejects()
        {
            string linked = Path.Combine(_root, "ancestor");
            Link(linked, _outside, true);
            Assert.Throws<InvalidOperationException>(() => Invoke("ResolveAndValidateInstallPath", Path.Combine(linked, "future", "install")));
        }

        [Test]
        public void ListFiles_LocalFileCountLimit_Rejects()
        {
            for (int i = 0; i < 4096; i++)
                File.WriteAllText(Path.Combine(_install, "f" + i), "");
            Assert.Throws<IOException>(() => Invoke("ListFiles", _install));
        }

        [Test]
        public void ApplyPlan_CaseOnlyRename_UsesDetectedVolumeComparison()
        {
            File.WriteAllText(Path.Combine(_install, "Foo.md"), "old case file");
            var remote = new Dictionary<string, string>
            {
                ["SKILL.md"] = Hash(Encoding.UTF8.GetBytes("old")),
                ["foo.md"] = Hash(Encoding.UTF8.GetBytes("new case file")),
            };
            var comparison = (StringComparison)Invoke("GetPathComparison", _install);
            var comparer = (StringComparer)Invoke("GetPathComparer", comparison);
            object plan = Invoke("BuildPlan", remote, Invoke("ListFiles", _install), comparer);
            var method = typeof(SkillSyncService)
                .GetMethods(Internal)
                .Single(m => m.Name == "ApplyPlan" && m.GetParameters()[0].ParameterType == typeof(string));
            Func<string, byte[]> download = _ => Encoding.UTF8.GetBytes("new case file");
            try
            {
                method.Invoke(null, new object[] { _install, plan, remote, comparison, download, null });
            }
            catch (TargetInvocationException e)
            {
                throw e.InnerException ?? e;
            }
            Assert.AreEqual("new case file", File.ReadAllText(Path.Combine(_install, "foo.md")));
            Assert.IsFalse(Directory.EnumerateFiles(_install, ".mcp-case-probe-*").Any());
        }

        [Test]
        public void ListFiles_TotalDirectoryEntryLimit_Rejects()
        {
            for (int i = 0; i < 8192; i++)
                Directory.CreateDirectory(Path.Combine(_install, "d" + i));
            Assert.Throws<InvalidOperationException>(() => Invoke("ListFiles", _install));
        }

        [Test]
        public void ListFiles_ExcessiveDirectoryDepth_Rejects()
        {
            string path = _install;
            for (int i = 0; i < 65; i++)
            {
                path = Path.Combine(path, "d");
                Directory.CreateDirectory(path);
            }
            Assert.Throws<InvalidOperationException>(() => Invoke("ListFiles", _install));
        }

        private object Plan(Dictionary<string, string> remote) => Invoke("BuildPlan", remote, Invoke("ListFiles", _install), StringComparer.Ordinal);

        private void Apply(object plan, Dictionary<string, string> remote, Func<string, byte[]> download)
        {
            var method = typeof(SkillSyncService)
                .GetMethods(Internal)
                .Single(m => m.Name == "ApplyPlan" && m.GetParameters()[0].ParameterType == typeof(string));
            try
            {
                method.Invoke(null, new object[] { _install, plan, remote, StringComparison.Ordinal, download, null });
            }
            catch (TargetInvocationException e)
            {
                throw e.InnerException ?? e;
            }
        }

        private static object Invoke(string name, params object[] args)
        {
            try
            {
                return typeof(SkillSyncService).GetMethod(name, Internal).Invoke(null, args);
            }
            catch (TargetInvocationException e)
            {
                throw e.InnerException ?? e;
            }
        }

        private static string Hash(byte[] bytes) =>
            (string)
                typeof(SkillSyncService).GetMethod("ComputeGitBlobSha1", Internal, null, new[] { typeof(byte[]) }, null).Invoke(null, new object[] { bytes });

        [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
        [return: MarshalAs(UnmanagedType.I1)]
        private static extern bool CreateSymbolicLink(string path, string target, int flags);

        [DllImport("libc", SetLastError = true)]
        private static extern int symlink(string target, string path);

        private void Link(string path, string target, bool directory, bool junction = false)
        {
            Assert.IsTrue(Path.GetFullPath(path).StartsWith(_root + Path.DirectorySeparatorChar, StringComparison.Ordinal));
            Assert.IsTrue(Path.GetFullPath(target).StartsWith(_root + Path.DirectorySeparatorChar, StringComparison.Ordinal));
            if (junction)
            {
                if (Path.DirectorySeparatorChar != '\\')
                    Assert.Ignore("Windows junction fixture.");
                using var process = Process.Start(
                    new ProcessStartInfo("cmd.exe", "/d /c mklink /J \"" + path + "\" \"" + target + "\"")
                    {
                        UseShellExecute = false,
                        CreateNoWindow = true,
                        RedirectStandardOutput = true,
                        RedirectStandardError = true,
                    }
                );
                Assert.IsTrue(process.WaitForExit(5000));
                Assert.AreEqual(0, process.ExitCode, "Owned junction fixture creation failed.");
            }
            else if (Path.DirectorySeparatorChar == '\\')
            {
                if (!CreateSymbolicLink(path, target, (directory ? 1 : 0) | 2))
                    Assert.Ignore("Symbolic-link fixture unavailable: " + Marshal.GetLastWin32Error());
            }
            else
                Assert.AreEqual(0, symlink(target, path), "Owned symlink fixture creation failed.");
            _links.Add((path, directory));
        }
    }
}
