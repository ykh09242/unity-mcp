using System;
using System.IO;
using System.IO.Compression;
using System.Linq;
using System.Reflection;
using System.Threading;
using System.Threading.Tasks;
using MCPForUnity.Editor.Setup;
using NUnit.Framework;

namespace MCPForUnityTests.Editor.Setup
{
    public class RoslynCompatibilityTests
    {
        private string root;

        [SetUp]
        public void SetUp() => root = Path.Combine(Path.GetTempPath(), "roslyn-preflight-" + Guid.NewGuid().ToString("N"));

        [TearDown]
        public void TearDown()
        {
            if (Directory.Exists(root))
                Directory.Delete(root, true);
        }

        private static AssemblyName Identity(string name, string version, string token = "b03f5f7f11d50a3a") =>
            new AssemblyName($"{name}, Version={version}, Culture=neutral, PublicKeyToken={token}");

        [Test]
        public void ExistingPublicInstallerSignatureRemainsAvailable()
        {
            Assert.That(
                typeof(RoslynInstaller).GetMethod(nameof(RoslynInstaller.InstallAsync), new[] { typeof(bool), typeof(CancellationToken) }),
                Is.Not.Null
            );
        }

        [TestCase("Microsoft.CodeAnalysis", "4.11.0.0", "31bf3856ad364e35")]
        [TestCase("Microsoft.CodeAnalysis.CSharp", "4.11.0.0", "31bf3856ad364e35")]
        [TestCase("System.Collections.Immutable", "7.0.0.0", "b03f5f7f11d50a3a")]
        [TestCase("System.Reflection.Metadata", "7.0.0.0", "b03f5f7f11d50a3a")]
        [TestCase("System.Runtime.CompilerServices.Unsafe", "4.0.0.0", "b03f5f7f11d50a3a")]
        [TestCase("System.Runtime.CompilerServices.Unsafe", "7.0.0.0", "b03f5f7f11d50a3a")]
        [TestCase("System.Runtime.CompilerServices.Unsafe", "6.0.0.0", "null")]
        public void ConflictingLoadedIdentityFailsWithOriginAndRemediation(string name, string version, string token)
        {
            string origin = Path.Combine(root, "external-package", name + ".dll");
            var error = Assert.Throws<IOException>(() => RoslynAssemblyCompatibility.GetProvided(root, new[] { (Identity(name, version, token), origin) }));
            Assert.That(error.Message, Does.Contain(name).And.Contain(version).And.Contain(origin).And.Contain("restart"));
            Assert.That(Directory.Exists(root), Is.False);
        }

        [Test]
        public void CompatibleExternalSetIsProvidedWithoutCreatingInstallerFolder()
        {
            var loaded = new[]
            {
                (Identity("Microsoft.CodeAnalysis", "4.12.0.0", "31bf3856ad364e35"), "packages/codeanalysis.dll"),
                (Identity("Microsoft.CodeAnalysis.CSharp", "4.12.0.0", "31bf3856ad364e35"), "packages/csharp.dll"),
                (Identity("System.Collections.Immutable", "8.0.0.0"), "packages/immutable.dll"),
                (Identity("System.Reflection.Metadata", "8.0.0.0"), "packages/metadata.dll"),
                (Identity("System.Runtime.CompilerServices.Unsafe", "6.0.0.0"), "packages/unsafe.dll"),
            };
            var provided = RoslynAssemblyCompatibility.GetProvided(root, loaded);
            Assert.That(provided.Count, Is.EqualTo(5));
            Assert.That(provided, Does.Contain("Microsoft.CodeAnalysis.dll"));
            Assert.That(Directory.Exists(root), Is.False);
        }

        [Test]
        public void InstallerOwnedLocationIsValidatedButNotTreatedAsExternal()
        {
            var provided = RoslynAssemblyCompatibility.GetProvided(
                root,
                new[] { (Identity("System.Runtime.CompilerServices.Unsafe", "6.0.0.0"), Path.Combine(root, "System.Runtime.CompilerServices.Unsafe.dll")) }
            );
            Assert.That(provided, Is.Empty);
        }

        [Test]
        public void CompatibleExternalAssemblyDoesNotHideADuplicateInstallerFile()
        {
            Directory.CreateDirectory(root);
            string local = Path.Combine(root, "System.Runtime.CompilerServices.Unsafe.dll");
            File.WriteAllText(local, "preserve");
            Assert.Throws<IOException>(() =>
                RoslynAssemblyCompatibility.GetProvided(root, new[] { (Identity("System.Runtime.CompilerServices.Unsafe", "6.0.0.0"), "packages/unsafe.dll") })
            );
            Assert.That(File.ReadAllText(local), Is.EqualTo("preserve"));
        }

        [Test]
        public void UnrelatedLoadedAssemblyIsIgnored()
        {
            Assert.That(RoslynAssemblyCompatibility.GetProvided(root, new[] { (Identity("Other.Package", "1.0.0.0"), "packages/other.dll") }), Is.Empty);
        }

        private static (AssemblyName identity, string location)[] CompatibleSet() =>
            new[]
            {
                (Identity("Microsoft.CodeAnalysis", "4.12.0.0", "31bf3856ad364e35"), "packages/codeanalysis.dll"),
                (Identity("Microsoft.CodeAnalysis.CSharp", "4.12.0.0", "31bf3856ad364e35"), "packages/csharp.dll"),
                (Identity("System.Collections.Immutable", "8.0.0.0"), "packages/immutable.dll"),
                (Identity("System.Reflection.Metadata", "8.0.0.0"), "packages/metadata.dll"),
                (Identity("System.Runtime.CompilerServices.Unsafe", "6.0.0.0"), "packages/unsafe.dll"),
            };

        private static byte[] Package()
        {
            using var stream = new MemoryStream();
            using (var zip = new ZipArchive(stream, ZipArchiveMode.Create, true))
                foreach (var assembly in CompatibleSet())
                {
                    using var writer = new StreamWriter(zip.CreateEntry("lib/netstandard2.0/" + assembly.identity.Name + ".dll").Open());
                    writer.Write("fixture");
                }
            return stream.ToArray();
        }

        [Test]
        public void ConflictingInventoryStopsBeforeDownloadOrCreatingFolder()
        {
            int downloads = 0;
            var conflict = new[] { (Identity("System.Runtime.CompilerServices.Unsafe", "4.0.0.0"), "packages/old-unsafe.dll") };
            var error = Assert.ThrowsAsync<IOException>(async () =>
                await RoslynInstaller.InstallFromPackagesAsync(
                    root,
                    _ =>
                    {
                        downloads++;
                        return Task.FromResult(Package());
                    },
                    (_, _, _) => { },
                    assemblyInventory: () => conflict
                )
            );
            Assert.That(downloads, Is.Zero);
            Assert.That(Directory.Exists(root), Is.False);
            Assert.That(error.Message, Does.Contain("old-unsafe.dll"));
        }

        [Test]
        public async Task CompatibleExternalSetSkipsDownloadPublicationAndRefresh()
        {
            int downloads = 0,
                publications = 0,
                refreshes = 0;
            await RoslynInstaller.InstallFromPackagesAsync(
                root,
                _ =>
                {
                    downloads++;
                    return Task.FromResult(Package());
                },
                (_, _, _) => { },
                publication: publish =>
                {
                    publications++;
                    publish();
                },
                refresh: () => refreshes++,
                assemblyInventory: CompatibleSet
            );
            Assert.That(downloads + publications + refreshes, Is.Zero);
            Assert.That(Directory.Exists(root), Is.False);
        }

        [Test]
        public async Task PartialExternalSetInstallsOnlyTheMissingDependency()
        {
            int downloads = 0;
            await RoslynInstaller.InstallFromPackagesAsync(
                root,
                url =>
                {
                    downloads++;
                    Assert.That(url, Does.Contain("system.runtime.compilerservices.unsafe"));
                    return Task.FromResult(Package());
                },
                (_, _, _) => { },
                assemblyInventory: () => CompatibleSet().Take(4)
            );
            Assert.That(downloads, Is.EqualTo(1));
            Assert.That(Directory.GetFiles(root, "*.dll").Select(Path.GetFileName), Is.EquivalentTo(new[] { "System.Runtime.CompilerServices.Unsafe.dll" }));
            Assert.That(RoslynInstaller.UninstallOwned(root), Is.EqualTo(1));
        }

        [TestCase(false)]
        [TestCase(true)]
        public async Task FullOwnedManifestSupportsMovingOneDependencyToAnExternalPackage(bool withMetadata)
        {
            const string name = "System.Runtime.CompilerServices.Unsafe.dll";
            string original = Path.Combine(root, name);
            await RoslynInstaller.InstallFromPackagesAsync(
                root,
                _ => Task.FromResult(Package()),
                (_, _, _) => { },
                refresh: withMetadata ? () => File.WriteAllText(original + ".meta", "owned metadata") : (Action)null,
                assemblyInventory: () => Array.Empty<(AssemblyName, string)>()
            );
            string external = Path.Combine(root, "external-package", name);
            Directory.CreateDirectory(Path.GetDirectoryName(external));
            File.Move(original, external);
            if (withMetadata)
                File.Move(original + ".meta", external + ".meta");
            int downloads = 0;
            await RoslynInstaller.InstallFromPackagesAsync(
                root,
                url =>
                {
                    downloads++;
                    Assert.That(url, Does.Not.Contain("system.runtime.compilerservices.unsafe"));
                    return Task.FromResult(Package());
                },
                (_, _, _) => { },
                assemblyInventory: () => new[] { (Identity("System.Runtime.CompilerServices.Unsafe", "6.0.0.0"), external) }
            );
            Assert.That(downloads, Is.EqualTo(4));
            Assert.That(File.Exists(original), Is.False);
            Assert.That(File.ReadAllText(external), Is.EqualTo("fixture"));
            Assert.That(RoslynInstaller.UninstallOwned(root), Is.EqualTo(4));
            Assert.That(File.ReadAllText(external), Is.EqualTo("fixture"));
            if (withMetadata)
                Assert.That(File.ReadAllText(external + ".meta"), Is.EqualTo("owned metadata"));
        }

        [Test]
        public void CompatibilityIsRecheckedInsideThePublicationGate()
        {
            bool changed = false;
            Assert.ThrowsAsync<IOException>(async () =>
                await RoslynInstaller.InstallFromPackagesAsync(
                    root,
                    _ => Task.FromResult(Package()),
                    (_, _, _) => { },
                    publication: publish =>
                    {
                        changed = true;
                        publish();
                    },
                    assemblyInventory: () =>
                        changed
                            ? new[] { (Identity("System.Runtime.CompilerServices.Unsafe", "4.0.0.0"), "packages/appeared.dll") }
                            : Array.Empty<(AssemblyName, string)>()
                )
            );
            Assert.That(Directory.GetFiles(root, "*.dll"), Is.Empty);
            Assert.That(Directory.GetDirectories(root, ".mcp-roslyn-stage-*"), Is.Empty);
        }
    }
}
