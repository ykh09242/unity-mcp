using System;
using System.IO;
using System.IO.Compression;
using System.Linq;
using System.Runtime.InteropServices;
using System.Threading;
using System.Threading.Tasks;
using MCPForUnity.Editor.Setup;
using NUnit.Framework;

namespace MCPForUnityTests.Editor.Setup
{
    public class RoslynOwnershipTests
    {
        private string root;
        private static readonly string[] Names =
        {
            "Microsoft.CodeAnalysis.dll",
            "Microsoft.CodeAnalysis.CSharp.dll",
            "System.Collections.Immutable.dll",
            "System.Reflection.Metadata.dll",
            "System.Runtime.CompilerServices.Unsafe.dll",
        };

        [SetUp]
        public void SetUp()
        {
            root = Path.Combine(Path.GetTempPath(), "roslyn-owned-" + Guid.NewGuid().ToString("N"));
            Directory.CreateDirectory(root);
        }

        [TearDown]
        public void TearDown()
        {
            if (Directory.Exists(root))
                Directory.Delete(root, true);
        }

        private static byte[] Package(string marker, bool missing = false)
        {
            using var output = new MemoryStream();
            using (var zip = new ZipArchive(output, ZipArchiveMode.Create, true))
                foreach (string name in Names.Skip(missing ? 1 : 0))
                {
                    using var writer = new StreamWriter(zip.CreateEntry("lib/netstandard2.0/" + name).Open());
                    writer.Write(marker + name);
                }
            return output.ToArray();
        }

        private void Install(string marker) =>
            RoslynInstaller.InstallFromPackagesAsync(root, _ => Task.FromResult(Package(marker)), (_, _, _) => { }).GetAwaiter().GetResult();

        [Test]
        public void UninstallPreservesUnownedAndModifiedFiles()
        {
            Install("old");
            File.WriteAllText(Path.Combine(root, "user-owned.txt"), "retain");
            File.WriteAllText(Path.Combine(root, Names[1]), "modified");
            File.WriteAllText(Path.Combine(root, Names[1] + ".meta"), "user metadata");
            RoslynInstaller.UninstallOwned(root);
            Assert.That(File.ReadAllText(Path.Combine(root, "user-owned.txt")), Is.EqualTo("retain"));
            Assert.That(File.ReadAllText(Path.Combine(root, Names[1])), Is.EqualTo("modified"));
            Assert.That(File.ReadAllText(Path.Combine(root, Names[1] + ".meta")), Is.EqualTo("user metadata"));
            Assert.IsFalse(File.Exists(Path.Combine(root, Names[0])));
        }

        [Test]
        public void LegacyUnownedFolderIsPreserved()
        {
            File.WriteAllText(Path.Combine(root, Names[0]), "manual");
            RoslynInstaller.UninstallOwned(root);
            Assert.That(File.ReadAllText(Path.Combine(root, Names[0])), Is.EqualTo("manual"));
        }

        [Test]
        public void DestinationMoveFailureRestoresEarlierReplacements()
        {
            if (Environment.OSVersion.Platform != PlatformID.Win32NT)
                Assert.Ignore("Windows file sharing semantics are required.");
            Install("old");
            using var locked = new FileStream(Path.Combine(root, Names[1]), FileMode.Open, FileAccess.Read, FileShare.Read);
            Assert.Catch(() => Install("new"));
            foreach (string name in Names)
                Assert.That(File.ReadAllText(Path.Combine(root, name)), Is.EqualTo("old" + name));
            Assert.IsEmpty(Directory.GetDirectories(root, ".mcp-roslyn-stage-*"));
        }

        [Test]
        public void InstallingOverUnownedDllIsRefused()
        {
            File.WriteAllText(Path.Combine(root, Names[1]), "manual");
            Assert.Throws<IOException>(() => Install("new"));
            Assert.That(File.ReadAllText(Path.Combine(root, Names[1])), Is.EqualTo("manual"));
            Assert.IsFalse(File.Exists(Path.Combine(root, Names[0])));
        }

        [Test]
        public void RealAssemblyValidationRejectsInvalidPayloadBeforePublication()
        {
            Install("old");
            Assert.Catch(() => RoslynInstaller.InstallFromPackagesAsync(root, _ => Task.FromResult(Package("invalid"))).GetAwaiter().GetResult());
            foreach (string name in Names)
                Assert.That(File.ReadAllText(Path.Combine(root, name)), Is.EqualTo("old" + name));
        }

        [Test]
        public void MetadataCreatedDuringRefreshIsOwnedButExistingMetadataIsPreserved()
        {
            File.WriteAllText(Path.Combine(root, Names[1] + ".meta"), "manual metadata");
            RoslynInstaller
                .InstallFromPackagesAsync(
                    root,
                    _ => Task.FromResult(Package("old")),
                    (_, _, _) => { },
                    refresh: () => File.WriteAllText(Path.Combine(root, Names[0] + ".meta"), "generated metadata")
                )
                .GetAwaiter()
                .GetResult();
            Assert.AreEqual(Names.Length, RoslynInstaller.UninstallOwned(root));
            Assert.IsFalse(File.Exists(Path.Combine(root, Names[0] + ".meta")));
            Assert.That(File.ReadAllText(Path.Combine(root, Names[1] + ".meta")), Is.EqualTo("manual metadata"));
        }

        [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
        private static extern bool CreateSymbolicLinkW(string link, string target, int flags);

        [DllImport("libc", SetLastError = true)]
        private static extern int symlink(string target, string link);

        [TestCase(false)]
        [TestCase(true)]
        public void LinkedFinalDllAndAncestorAreRefused(bool directoryLink)
        {
            Install("old");
            string target = directoryLink ? root : Path.Combine(root, "unowned.txt");
            string link = directoryLink ? root + "-link" : Path.Combine(root, Names[0]);
            if (!directoryLink)
            {
                File.WriteAllText(target, "preserve");
                File.Delete(link);
            }
            bool created =
                Environment.OSVersion.Platform == PlatformID.Win32NT
                    ? CreateSymbolicLinkW(link, target, (directoryLink ? 1 : 0) | 2)
                    : symlink(target, link) == 0;
            if (!created)
                Assert.Ignore("Host cannot create this owned link fixture.");
            try
            {
                string folder = directoryLink ? link : root;
                Assert.Catch(() => RoslynInstaller.UninstallOwned(folder));
                Assert.Catch(() =>
                    RoslynInstaller.InstallFromPackagesAsync(folder, _ => Task.FromResult(Package("new")), (_, _, _) => { }).GetAwaiter().GetResult()
                );
                Assert.That(File.ReadAllText(Path.Combine(root, Names[1])), Is.EqualTo("old" + Names[1]));
                if (!directoryLink)
                    Assert.That(File.ReadAllText(target), Is.EqualTo("preserve"));
            }
            finally
            {
                if (directoryLink)
                    Directory.Delete(link, false);
                else
                    File.Delete(link);
            }
        }

        [TestCase("missing")]
        [TestCase("download")]
        [TestCase("validation")]
        [TestCase("cancel")]
        public void FailedInstallKeepsThePreviousCompleteSet(string failure)
        {
            Install("old");
            int count = 0;
            using var cancel = new CancellationTokenSource();
            if (failure == "cancel")
                cancel.Cancel();
            Assert.Catch(() =>
                RoslynInstaller
                    .InstallFromPackagesAsync(
                        root,
                        _ =>
                        {
                            count++;
                            if (failure == "download" && count == 2)
                                throw new IOException("synthetic download failure");
                            return Task.FromResult(Package("new", failure == "missing"));
                        },
                        (path, name, version) =>
                        {
                            if (failure == "validation" && name == Names[1])
                                throw new IOException("synthetic invalid assembly");
                        },
                        cancel.Token
                    )
                    .GetAwaiter()
                    .GetResult()
            );
            foreach (string name in Names)
                Assert.That(File.ReadAllText(Path.Combine(root, name)), Is.EqualTo("old" + name));
        }
    }
}
