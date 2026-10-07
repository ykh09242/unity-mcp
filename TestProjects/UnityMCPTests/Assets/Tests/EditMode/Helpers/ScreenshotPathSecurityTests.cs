using System;
using System.IO;
using System.Reflection;
using System.Runtime.InteropServices;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Runtime.Helpers;
using NUnit.Framework;
using UnityEngine;

namespace MCPForUnityTests.Editor.Helpers
{
    public class ScreenshotPathSecurityTests
    {
        [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
        [return: MarshalAs(UnmanagedType.I1)]
        private static extern bool CreateSymbolicLinkW(string link, string target, int flags);

        [DllImport("libc", SetLastError = true)]
        private static extern int symlink(string target, string link);

        private static void Link(string link, string target, bool directory)
        {
            bool created =
                Application.platform == RuntimePlatform.WindowsEditor ? CreateSymbolicLinkW(link, target, (directory ? 1 : 0) | 2) : symlink(target, link) == 0;
            if (!created)
                Assert.Ignore("Owned screenshot link creation unavailable: " + Marshal.GetLastWin32Error());
        }

        [TestCase("../escape.png")]
        [TestCase("..\\escape.png")]
        [TestCase("/tmp/escape.png")]
        [TestCase("C:\\escape.png")]
        [TestCase("file:stream.png")]
        [TestCase("//host/share/escape.png")]
        public void ScreenshotRejectsPathInFilename(string name)
        {
            Assert.Throws<InvalidOperationException>(() => ScreenshotUtility.PrepareCaptureResult(name, 1, true, "Captures", false));
        }

        [Test]
        public void ScreenshotAcceptsSimpleNameInConfiguredFolder()
        {
            var result = ScreenshotUtility.PrepareCaptureResult("security-test", 1, true, "Captures", false);
            Assert.AreEqual("security-test.png", Path.GetFileName(result.FullPath));
            Assert.AreEqual("Captures/security-test.png", result.ProjectRelativePath);
        }

        [Test]
        public void CapturePreparationDoesNotCreateOutputFolders()
        {
            string root = Path.Combine(Path.GetDirectoryName(Application.dataPath), "Temp", "McpCapturePrepare-" + Guid.NewGuid().ToString("N"));
            var prepared = ScreenshotUtility.PrepareCaptureResult("capture", 1, true, root, false);
            Assert.AreEqual("capture.png", Path.GetFileName(prepared.FullPath));
            Assert.IsFalse(Directory.Exists(root), "Path validation must not create folders before an image exists.");

            var prepare = typeof(EditorWindowScreenshotUtility).GetMethod("PrepareCaptureResult", BindingFlags.Static | BindingFlags.NonPublic);
            Assert.IsNotNull(prepare);
            prepare.Invoke(null, new object[] { "capture", 1, true, root });
            Assert.IsFalse(Directory.Exists(root), "Scene View path preparation must also be side-effect free.");
        }

        [TestCase(false)]
        [TestCase(true)]
        public void FailedOutputScopeRemovesOnlyNewEmptyAncestors(bool preservePartialFile)
        {
            string root = Path.Combine(Path.GetDirectoryName(Application.dataPath), "Temp", "McpCaptureFolders-" + Guid.NewGuid().ToString("N"));
            string existing = Path.Combine(root, "Existing");
            string owned = Path.Combine(existing, "New", "Nested");
            string output = Path.Combine(owned, "capture.png");
            Directory.CreateDirectory(existing);
            try
            {
                using (var folders = new OutputFolderScope(root))
                {
                    folders.EnsureParentDirectory(output);
                    if (preservePartialFile)
                        File.WriteAllBytes(output, new byte[] { 1, 2, 3 });
                }
                Assert.IsTrue(Directory.Exists(existing), "A pre-existing empty parent must survive failure.");
                Assert.AreEqual(preservePartialFile, Directory.Exists(owned));
                if (preservePartialFile)
                    CollectionAssert.AreEqual(new byte[] { 1, 2, 3 }, File.ReadAllBytes(output));
                else
                    Assert.IsFalse(Directory.Exists(Path.Combine(existing, "New")));
            }
            finally
            {
                Directory.Delete(root, true);
            }
        }

        [Test]
        public void OutputScopeUnwindsEarlierFoldersAfterLaterParentFailure()
        {
            string root = Path.Combine(Path.GetDirectoryName(Application.dataPath), "Temp", "McpCaptureFailure-" + Guid.NewGuid().ToString("N"));
            Directory.CreateDirectory(root);
            string blocker = Path.Combine(root, "Blocker");
            File.WriteAllText(blocker, "sentinel");
            try
            {
                using (var folders = new OutputFolderScope(root))
                {
                    folders.EnsureParentDirectory(Path.Combine(root, "New", "Nested", "capture.png"));
                    Assert.Throws<IOException>(() => folders.EnsureParentDirectory(Path.Combine(blocker, "capture.png")));
                }
                Assert.IsFalse(Directory.Exists(Path.Combine(root, "New")));
                Assert.AreEqual("sentinel", File.ReadAllText(blocker));
            }
            finally
            {
                Directory.Delete(root, true);
            }
        }

        [Test]
        public void CaptureWriteCreatesFoldersOnlyForValidBytesAndKeepsSuccessfulOutput()
        {
            string projectRoot = Path.GetDirectoryName(Application.dataPath);
            string root = Path.Combine(projectRoot, "Temp", "McpCaptureWrite-" + Guid.NewGuid().ToString("N"));
            string output = Path.Combine(root, "Nested", "capture.png");
            Assert.Throws<ArgumentNullException>(() => ScreenshotUtility.WriteCaptureBytes(output, null));
            Assert.IsFalse(Directory.Exists(root));
            try
            {
                ScreenshotUtility.WriteCaptureBytes(output, new byte[] { 4, 5, 6 });
                CollectionAssert.AreEqual(new byte[] { 4, 5, 6 }, File.ReadAllBytes(output));
            }
            finally
            {
                if (Directory.Exists(root))
                    Directory.Delete(root, true);
            }
        }

        [TestCase(false, false, false)]
        [TestCase(false, false, true)]
        [TestCase(false, true, false)]
        [TestCase(false, true, true)]
        [TestCase(true, false, false)]
        [TestCase(true, false, true)]
        [TestCase(true, true, false)]
        [TestCase(true, true, true)]
        public void ScreenshotWriteRejectsLinkedFinalAndAncestorEntries(bool directory, bool broken, bool unique)
        {
            string root = Path.Combine(Application.dataPath, "__McpScreenshotLinks_" + Guid.NewGuid().ToString("N"));
            string outside = Path.Combine(root, "OwnedOutside");
            string output = Path.Combine(root, "Output");
            string link = Path.Combine(output, directory ? "Linked" : "capture.png");
            Directory.CreateDirectory(output);
            if (!broken)
            {
                Directory.CreateDirectory(outside);
                File.WriteAllText(Path.Combine(outside, "capture.png"), "owned sentinel");
            }
            try
            {
                Link(link, directory ? outside : Path.Combine(outside, "capture.png"), directory);
                string destination = directory ? Path.Combine(link, "capture.png") : link;
                Assert.Catch<Exception>(() => ScreenshotUtility.WriteCaptureBytes(destination, new byte[] { 1, 2, 3 }, unique));
                if (broken)
                    Assert.IsFalse(Directory.Exists(outside), "Rejected link must not create its missing target.");
                else
                    Assert.AreEqual("owned sentinel", File.ReadAllText(Path.Combine(outside, "capture.png")));
            }
            finally
            {
                // Remove only the link itself before recursively removing this exact owned tree.
                if (directory)
                {
                    try
                    {
                        Directory.Delete(link);
                    }
                    catch (DirectoryNotFoundException) { }
                }
                else
                    File.Delete(link);
                StringAssert.StartsWith(Path.GetFullPath(Application.dataPath) + Path.DirectorySeparatorChar, Path.GetFullPath(root));
                Directory.Delete(root, true);
                File.Delete(root + ".meta");
            }
        }

        [Test]
        public void SceneViewPreparationRejectsDanglingRequestedAndSuffixFiles()
        {
            string root = Path.Combine(Application.dataPath, "__McpSceneViewOutput_" + Guid.NewGuid().ToString("N"));
            string missing = Path.Combine(root, "OwnedMissing", "capture.png");
            string requested = Path.Combine(root, "capture.png");
            string suffix = Path.Combine(root, "capture-1.png");
            Directory.CreateDirectory(root);
            var prepare = typeof(EditorWindowScreenshotUtility).GetMethod("PrepareCaptureResult", BindingFlags.Static | BindingFlags.NonPublic);
            Assert.IsNotNull(prepare);
            try
            {
                Link(requested, missing, false);
                Assert.That(
                    Assert.Throws<TargetInvocationException>(() => prepare.Invoke(null, new object[] { "capture", 1, true, root })).InnerException,
                    Is.InstanceOf<InvalidOperationException>().Or.InstanceOf<IOException>()
                );
                File.Delete(requested);
                File.WriteAllText(requested, "ordinary screenshot");
                Link(suffix, missing, false);
                Assert.That(
                    Assert.Throws<TargetInvocationException>(() => prepare.Invoke(null, new object[] { "capture", 1, true, root })).InnerException,
                    Is.InstanceOf<InvalidOperationException>().Or.InstanceOf<IOException>()
                );
                Assert.AreEqual("ordinary screenshot", File.ReadAllText(requested));
                Assert.IsFalse(File.Exists(missing));
            }
            finally
            {
                File.Delete(requested);
                File.Delete(suffix);
                Directory.Delete(root, true);
                File.Delete(root + ".meta");
            }
        }

        [Test]
        public void UniqueCaptureDoesNotOverwriteCollisionAndExplicitOverwriteRemainsSupported()
        {
            string root = Path.Combine(Application.dataPath, "__McpScreenshotCollision_" + Guid.NewGuid().ToString("N"));
            Directory.CreateDirectory(root);
            try
            {
                var first = ScreenshotUtility.PrepareCaptureResult("capture", 1, true, root, false);
                ScreenshotUtility.WriteCaptureBytes(first.FullPath, new byte[] { 1, 2, 3 });
                var next = ScreenshotUtility.PrepareCaptureResult("capture", 1, true, root, false);
                Assert.AreEqual("capture-1.png", Path.GetFileName(next.FullPath));
                File.WriteAllBytes(next.FullPath, new byte[] { 4, 5, 6 }); // concurrent regular-file winner
                Assert.Throws<IOException>(() => ScreenshotUtility.WriteCaptureBytes(next.FullPath, new byte[] { 7, 8, 9 }));
                CollectionAssert.AreEqual(new byte[] { 4, 5, 6 }, File.ReadAllBytes(next.FullPath));
                ScreenshotUtility.WriteCaptureBytes(next.FullPath, new byte[] { 7, 8, 9 }, false);
                CollectionAssert.AreEqual(new byte[] { 7, 8, 9 }, File.ReadAllBytes(next.FullPath));
                Assert.Throws<ArgumentNullException>(() => ScreenshotUtility.WriteCaptureBytes(next.FullPath, null, false));
                CollectionAssert.AreEqual(new byte[] { 7, 8, 9 }, File.ReadAllBytes(next.FullPath));
            }
            finally
            {
                Directory.Delete(root, true);
                File.Delete(root + ".meta");
            }
        }
    }
}
