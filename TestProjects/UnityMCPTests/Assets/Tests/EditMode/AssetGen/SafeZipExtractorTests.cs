using System;
using System.IO;
using System.IO.Compression;
using System.Text;
using MCPForUnity.Editor.Services.AssetGen.Import;
using NUnit.Framework;

namespace MCPForUnityTests.Editor.AssetGen
{
    /// <summary>
    /// Verifies <see cref="SafeZipExtractor"/> unpacks normal entries and rejects Zip-Slip
    /// traversal entries. Builds tiny zips in memory (no fixtures, no network).
    /// </summary>
    public class SafeZipExtractorTests
    {
        private string _work;

        [SetUp]
        public void SetUp()
        {
            _work = Path.Combine(Path.GetTempPath(), "mcp_zip_" + Guid.NewGuid().ToString("N"));
            Directory.CreateDirectory(_work);
        }

        [TearDown]
        public void TearDown()
        {
            try
            {
                if (Directory.Exists(_work))
                    Directory.Delete(_work, true);
            }
            catch { /* ignore */ }
        }

        private string MakeZip(string entryName, string content)
        {
            string zipPath = Path.Combine(_work, "in_" + Guid.NewGuid().ToString("N") + ".zip");
            using (var ms = new MemoryStream())
            {
                using (var archive = new ZipArchive(ms, ZipArchiveMode.Create, true))
                {
                    ZipArchiveEntry entry = archive.CreateEntry(entryName);
                    using (Stream s = entry.Open())
                    {
                        byte[] bytes = Encoding.UTF8.GetBytes(content);
                        s.Write(bytes, 0, bytes.Length);
                    }
                }
                File.WriteAllBytes(zipPath, ms.ToArray());
            }
            return zipPath;
        }

        private string MakeMultiZip(params (string name, string content)[] entries)
        {
            string zipPath = Path.Combine(_work, "in_" + Guid.NewGuid().ToString("N") + ".zip");
            using (var ms = new MemoryStream())
            {
                using (var archive = new ZipArchive(ms, ZipArchiveMode.Create, true))
                {
                    foreach (var (name, content) in entries)
                    {
                        ZipArchiveEntry entry = archive.CreateEntry(name);
                        using (Stream s = entry.Open())
                        {
                            byte[] bytes = Encoding.UTF8.GetBytes(content);
                            s.Write(bytes, 0, bytes.Length);
                        }
                    }
                }
                File.WriteAllBytes(zipPath, ms.ToArray());
            }
            return zipPath;
        }

        [Test]
        public void LargeArchiveMetadataStillReachesTheFinalAllowedFile()
        {
            string zip = Path.Combine(_work, "many.zip");
            using (var file = File.Create(zip))
            using (var archive = new ZipArchive(file, ZipArchiveMode.Create))
            {
                for (int i = 0; i < 5000; i++)
                    archive.CreateEntry("unused_" + i + ".txt");
                using var writer = new StreamWriter(archive.CreateEntry("last.obj").Open());
                writer.Write("o complete");
            }
            string output = Path.Combine(_work, "large-output");
            SafeZipExtractor.ExtractTo(zip, output, new System.Collections.Generic.HashSet<string> { ".obj" });
            Assert.AreEqual("o complete", File.ReadAllText(Path.Combine(output, "last.obj")));
            Assert.AreEqual(1, Directory.GetFiles(output).Length);
        }

        [Test]
        public void LargerArchiveBudgetPreservesCompressionRatioProtection()
        {
            string zip = MakeZip("repeated.obj", new string(' ', 1024 * 1024));
            string output = Path.Combine(_work, "compressible-output");
            Assert.Throws<IOException>(() => SafeZipExtractor.ExtractTo(zip, output));
            Assert.IsFalse(Directory.Exists(output));
        }

        [Test]
        public void Allowlist_SkipsDisallowedEntries()
        {
            // A hostile marketplace archive: a valid model plus an editor script + a managed dll.
            string zip = MakeMultiZip(("teapot.obj", "o teapot"), ("Editor/Hack.cs", "// [InitializeOnLoad] arbitrary code"), ("plugins/Evil.dll", "MZ..."));
            string dest = Path.Combine(_work, "out");

            SafeZipExtractor.ExtractTo(zip, dest, new System.Collections.Generic.HashSet<string> { ".obj" });

            Assert.IsTrue(File.Exists(Path.Combine(dest, "teapot.obj")), "allowed model must be written");
            Assert.IsFalse(File.Exists(Path.Combine(dest, "Editor", "Hack.cs")), "disallowed .cs must be skipped");
            Assert.IsFalse(File.Exists(Path.Combine(dest, "plugins", "Evil.dll")), "disallowed .dll must be skipped");
        }

        [Test]
        public void NormalEntry_Extracts()
        {
            string zip = MakeZip("models/teapot.obj", "o teapot");
            string dest = Path.Combine(_work, "out");

            SafeZipExtractor.ExtractTo(zip, dest);

            string extracted = Path.Combine(dest, "models", "teapot.obj");
            Assert.IsTrue(File.Exists(extracted), "expected extracted file to exist");
            Assert.AreEqual("o teapot", File.ReadAllText(extracted));
        }

        [Test]
        public void TraversalEntry_Throws()
        {
            string zip = MakeZip("../evil.txt", "pwned");
            string dest = Path.Combine(_work, "out");

            Assert.Throws<IOException>(() => SafeZipExtractor.ExtractTo(zip, dest));

            string escaped = Path.GetFullPath(Path.Combine(_work, "evil.txt"));
            Assert.IsFalse(File.Exists(escaped), "traversal target must not be written");
        }
    }
}
