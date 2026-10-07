using System;
using System.IO;
using System.Runtime.InteropServices;
using MCPForUnity.Editor.Tools;
using MCPForUnity.Editor.Tools.Build;
using Newtonsoft.Json.Linq;
using NUnit.Framework;

namespace MCPForUnityTests.Editor.Tools
{
    public class BuildOutputCleanupTests
    {
        private string _root;

        [SetUp]
        public void SetUp()
        {
            _root = Path.Combine(Path.GetTempPath(), "mcp-build-cleanup-" + Guid.NewGuid().ToString("N"));
            Directory.CreateDirectory(Path.Combine(_root, "Builds", "Fixture", "Nested"));
            File.WriteAllText(Path.Combine(_root, "Builds", "Fixture", "Game.exe"), "game");
            File.WriteAllText(Path.Combine(_root, "Builds", "Fixture", "Nested", "data.bin"), "data");
            Directory.CreateDirectory(Path.Combine(_root, "Assets"));
            File.WriteAllText(Path.Combine(_root, "Assets", "keep.txt"), "source");
        }

        [TearDown]
        public void TearDown()
        {
            if (Directory.Exists(_root))
                Directory.Delete(_root, true);
        }

        [TestCase("Builds/Fixture")]
        [TestCase("Builds\\Fixture")]
        public void Preview_ReportsOwnedOutputAndPreservesFiles(string path)
        {
            // Given a selected output in an isolated project fixture.
            // When previewing cleanup.
            var result = JObject.FromObject(BuildOutputCleaner.Clean(_root, path, true));
            // Then content and source remain, with bounded useful counts.
            Assert.IsTrue(result.Value<bool>("success"), result.ToString());
            Assert.IsTrue(result["data"].Value<bool>("dry_run"));
            Assert.AreEqual(2, result["data"].Value<int>("file_count"));
            Assert.AreEqual(2, result["data"].Value<int>("directory_count"));
            Assert.AreEqual(8, result["data"].Value<long>("total_bytes"));
            Assert.AreEqual(0, result["data"].Value<int>("deleted_entries"));
            Assert.IsTrue(File.Exists(Path.Combine(_root, "Builds", "Fixture", "Game.exe")));
            Assert.IsTrue(File.Exists(Path.Combine(_root, "Assets", "keep.txt")));
        }

        [TestCase(false)]
        [TestCase(true)]
        public void ExplicitDeletion_RemovesOnlySelectedOutput(bool absolute)
        {
            // Given an explicit relative or absolute output descendant.
            string path = absolute ? Path.Combine(_root, "Builds", "Fixture") : "Builds/Fixture";
            // When deletion is requested explicitly.
            var result = JObject.FromObject(BuildOutputCleaner.Clean(_root, path, false));
            // Then only the fixture output disappears, including its empty directories.
            Assert.IsTrue(result.Value<bool>("success"), result.ToString());
            Assert.AreEqual(4, result["data"].Value<int>("deleted_entries"));
            Assert.IsFalse(Directory.Exists(Path.Combine(_root, "Builds", "Fixture")));
            Assert.IsTrue(File.Exists(Path.Combine(_root, "Assets", "keep.txt")));
            Assert.IsTrue(Directory.Exists(Path.Combine(_root, "Builds")));
        }

        [TestCase(null)]
        [TestCase("")]
        [TestCase("Builds")]
        [TestCase("Builds/")]
        [TestCase(".")]
        [TestCase("Assets")]
        [TestCase("Library")]
        [TestCase("Temp")]
        [TestCase("BuildsOther/Fixture")]
        [TestCase("Builds/../Assets")]
        [TestCase("Builds/Fixture/../../Assets")]
        [TestCase("Builds/Fixture:stream")]
        [TestCase("Builds/Fixture.")]
        public void UnsafeSelection_RefusesWithoutDeleting(string path)
        {
            // Given an absent, broad, escaping or ambiguous selection.
            // When actual cleanup is requested.
            var result = JObject.FromObject(BuildOutputCleaner.Clean(_root, path, false));
            // Then it fails before changing any fixture content.
            Assert.IsFalse(result.Value<bool>("success"), result.ToString());
            Assert.IsTrue(File.Exists(Path.Combine(_root, "Builds", "Fixture", "Game.exe")));
            Assert.IsTrue(File.Exists(Path.Combine(_root, "Assets", "keep.txt")));
        }

        [Test]
        public void ActiveBuild_RefusesCleanup()
        {
            // Given a pending or running build.
            // When its output might be cleaned.
            var result = JObject.FromObject(BuildOutputCleaner.Clean(_root, "Builds/Fixture", false, true));
            // Then nothing is deleted.
            Assert.IsFalse(result.Value<bool>("success"));
            StringAssert.Contains("pending or running", result.Value<string>("error"));
            Assert.IsTrue(File.Exists(Path.Combine(_root, "Builds", "Fixture", "Game.exe")));
        }

        [Test]
        public void MissingOutput_IsAnIdempotentNoOp()
        {
            // Given a permitted output that does not exist.
            // When explicitly deleting it.
            var result = JObject.FromObject(BuildOutputCleaner.Clean(_root, "Builds/Missing", false));
            // Then cleanup succeeds without touching another selection.
            Assert.IsTrue(result.Value<bool>("success"), result.ToString());
            Assert.IsFalse(result["data"].Value<bool>("exists"));
            Assert.AreEqual(0, result["data"].Value<int>("deleted_entries"));
        }

        [Test]
        public void LargeOutput_RefusesBeforeAnyDeletion()
        {
            // Given an output beyond the scan budget.
            for (int i = 0; i <= BuildOutputCleaner.MaxEntries; i++)
                File.WriteAllText(Path.Combine(_root, "Builds", "Fixture", "entry-" + i), "x");
            // When requesting deletion.
            var result = JObject.FromObject(BuildOutputCleaner.Clean(_root, "Builds/Fixture", false));
            // Then even the original files remain.
            Assert.IsFalse(result.Value<bool>("success"), result.ToString());
            Assert.IsTrue(File.Exists(Path.Combine(_root, "Builds", "Fixture", "Game.exe")));
            Assert.IsTrue(File.Exists(Path.Combine(_root, "Builds", "Fixture", "entry-0")));
        }

        [Test]
        public void Preview_BoundsReturnedEntries()
        {
            // Given a moderate output with more entries than the response preview budget.
            for (int i = 0; i < 110; i++)
                File.WriteAllText(Path.Combine(_root, "Builds", "Fixture", "entry-" + i), "x");
            // When previewing it.
            var result = JObject.FromObject(BuildOutputCleaner.Clean(_root, "Builds/Fixture", true));
            // Then counts cover the selection but the returned path sample is bounded.
            Assert.IsTrue(result.Value<bool>("success"), result.ToString());
            Assert.AreEqual(112, result["data"].Value<int>("file_count"));
            Assert.AreEqual(100, ((JArray)result["data"]["entries"]).Count);
            Assert.IsTrue(result["data"].Value<bool>("entries_truncated"));
        }

        [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
        private static extern bool CreateSymbolicLinkW(string link, string target, int flags);

        [DllImport("libc", SetLastError = true)]
        private static extern int symlink(string target, string link);

        [TestCase(false, false)]
        [TestCase(false, true)]
        [TestCase(true, false)]
        [TestCase(true, true)]
        public void LinkedOutput_RefusesBeforeTouchingTarget(bool linkedRoot, bool dangling)
        {
            // Given a link either inside the selection or at Builds itself.
            string builds = Path.Combine(_root, "Builds");
            string link = linkedRoot ? builds : Path.Combine(builds, "Fixture", "linked");
            string original = Path.Combine(_root, "OriginalBuilds");
            if (linkedRoot)
                Directory.Move(builds, original);
            string target = Path.Combine(_root, dangling ? "MissingTarget" : "Assets");
            bool created = Path.DirectorySeparatorChar == '\\' ? CreateSymbolicLinkW(link, target, 1 | 2) : symlink(target, link) == 0;
            if (!created)
            {
                if (linkedRoot)
                    Directory.Move(original, builds);
                Assert.Ignore("Symbolic-link fixture unavailable: " + Marshal.GetLastWin32Error());
            }
            try
            {
                // When deletion is requested through or containing the link.
                var result = JObject.FromObject(BuildOutputCleaner.Clean(_root, linkedRoot ? "Builds/Child" : "Builds/Fixture", false));
                // Then source remains and the operation fails before the first deletion.
                Assert.IsFalse(result.Value<bool>("success"), result.ToString());
                Assert.IsTrue(File.Exists(Path.Combine(_root, "Assets", "keep.txt")));
                if (!linkedRoot)
                    Assert.IsTrue(File.Exists(Path.Combine(builds, "Fixture", "Game.exe")));
            }
            finally
            {
                // Remove the link itself before the isolated fixture's recursive teardown.
                if (Path.DirectorySeparatorChar == '\\')
                    Directory.Delete(link, false);
                else
                    File.Delete(link);
                if (linkedRoot)
                    Directory.Move(original, builds);
            }
        }

        [TestCase("false")]
        [TestCase(0)]
        public void InvalidNativeDryRun_RefusesInsteadOfDeleting(object value)
        {
            var result = JObject.FromObject(
                ManageBuild.HandleCommand(
                    new JObject
                    {
                        ["action"] = "clean_output",
                        ["output_path"] = "Builds/MCP-never-delete",
                        ["dry_run"] = JToken.FromObject(value),
                    }
                )
            );
            Assert.IsFalse(result.Value<bool>("success"));
            StringAssert.Contains("must be a boolean", result.Value<string>("error"));
        }
    }
}
