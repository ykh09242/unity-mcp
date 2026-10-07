using System;
using System.IO;
using System.IO.Compression;
using System.Reflection;
using MCPForUnity.Editor.Services.AssetGen.Import;
using MCPForUnity.Editor.Tools.AssetGen;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEditor;
using UnityEngine;

namespace MCPForUnityTests.Editor.AssetGen
{
    /// <summary>
    /// Drives the import_model_file handler with on-disk fixture files (no network, no provider).
    /// Covers missing source, unsupported extension, and a real OBJ import that yields an asset GUID.
    /// </summary>
    public class ImportModelFileHandlerTests
    {
        private string _tempDir;
        private string TestFolder;
        private string _sourceFolder;

        [SetUp]
        public void SetUp()
        {
            _tempDir = Path.Combine(Path.GetTempPath(), "mcp_imf_" + Guid.NewGuid().ToString("N"));
            Directory.CreateDirectory(_tempDir);
            TestFolder = "Assets/__import_model_file_test_" + Guid.NewGuid().ToString("N");
            _sourceFolder = TestFolder + "/Sources";
            Directory.CreateDirectory(Path.Combine(Path.GetDirectoryName(Application.dataPath), _sourceFolder));
        }

        [TearDown]
        public void TearDown()
        {
            if (AssetDatabase.IsValidFolder(TestFolder))
                AssetDatabase.DeleteAsset(TestFolder);
            string ownedAssetsFolder = Path.GetFullPath(Path.Combine(Path.GetDirectoryName(Application.dataPath), TestFolder));
            Assert.IsTrue(
                ownedAssetsFolder.StartsWith(Path.GetFullPath(Application.dataPath) + Path.DirectorySeparatorChar, StringComparison.OrdinalIgnoreCase),
                "cleanup must remain within Assets"
            );
            if (Directory.Exists(ownedAssetsFolder))
                Directory.Delete(ownedAssetsFolder, true);
            try
            {
                if (Directory.Exists(_tempDir))
                    Directory.Delete(_tempDir, true);
            }
            catch { /* ignore */ }
        }

        private static JObject Call(JObject p) => JObject.Parse(JsonConvert.SerializeObject(ImportModelFile.HandleCommand(p)));

        private string WriteCubeObj()
        {
            string path = _sourceFolder + "/cube.obj";
            File.WriteAllText(
                Path.Combine(Path.GetDirectoryName(Application.dataPath), path),
                "o Cube\n"
                    + "v 0 0 0\nv 1 0 0\nv 1 1 0\nv 0 1 0\nv 0 0 1\nv 1 0 1\nv 1 1 1\nv 0 1 1\n"
                    + "f 1 2 3 4\nf 5 6 7 8\nf 1 2 6 5\nf 2 3 7 6\nf 3 4 8 7\nf 4 1 5 8\n"
            );
            return path;
        }

        [Test]
        public void MissingSource_ReturnsError()
        {
            JObject resp = Call(new JObject { ["sourcePath"] = _sourceFolder + "/nope.obj" });
            Assert.AreEqual(false, (bool)resp["success"]);
            StringAssert.Contains("not found", ((string)resp["error"]).ToLowerInvariant());
        }

        [Test]
        public void UnsupportedExtension_ReturnsError()
        {
            string txt = _sourceFolder + "/readme.txt";
            File.WriteAllText(Path.Combine(Path.GetDirectoryName(Application.dataPath), txt), "hi");
            JObject resp = Call(new JObject { ["sourcePath"] = txt });
            Assert.AreEqual(false, (bool)resp["success"]);
            StringAssert.Contains("unsupported", ((string)resp["error"]).ToLowerInvariant());
        }

        [TestCase(".glb")]
        [TestCase(".gltf")]
        public void MissingGltfast_DoesNotStageDirectModel(string extension)
        {
            var availability = typeof(ModelImportPipeline).GetField("_gltfastAvailable", BindingFlags.Static | BindingFlags.NonPublic);
            Assert.IsNotNull(availability);
            object previous = availability.GetValue(null);
            string outputFolder = TestFolder + "/missing_gltf_" + Guid.NewGuid().ToString("N");
            string absoluteOutput = Path.Combine(Path.GetDirectoryName(Application.dataPath), outputFolder);
            string source = _sourceFolder + "/source" + extension;
            string absoluteSource = Path.Combine(Path.GetDirectoryName(Application.dataPath), source);
            File.WriteAllBytes(absoluteSource, new byte[] { 0 });
            try
            {
                availability.SetValue(null, false);
                JObject response = Call(new JObject { ["sourcePath"] = source, ["outputFolder"] = outputFolder });
                Assert.AreEqual(false, (bool)response["success"]);
                StringAssert.Contains("glTFast", (string)response["error"]);
                Assert.IsFalse(Directory.Exists(absoluteOutput), "rejected input must not create a staging folder");
                Assert.IsTrue(File.Exists(absoluteSource), "source must remain intact");
            }
            finally
            {
                availability.SetValue(null, previous);
            }
        }

        [Test]
        public void ImportsObj_ReturnsAssetPathAndGuid()
        {
            string obj = WriteCubeObj();
            JObject resp = Call(
                new JObject
                {
                    ["sourcePath"] = obj,
                    ["name"] = "TestCube",
                    ["outputFolder"] = TestFolder,
                }
            );
            Assert.AreEqual(true, (bool)resp["success"], resp.ToString());
            string assetPath = (string)resp["data"]["asset_path"];
            StringAssert.StartsWith(TestFolder, assetPath);
            Assert.IsFalse(string.IsNullOrEmpty((string)resp["data"]["asset_guid"]));
            Assert.IsTrue(File.Exists(assetPath), "imported file should exist under Assets");
        }

        private string WriteArchive(string name, string entryName, string contents)
        {
            string path = _sourceFolder + "/" + name + ".zip";
            using (var stream = File.Create(Path.Combine(Path.GetDirectoryName(Application.dataPath), path)))
            using (var archive = new ZipArchive(stream, ZipArchiveMode.Create))
            using (var writer = new StreamWriter(archive.CreateEntry(entryName).Open()))
                writer.Write(contents);
            return path;
        }

        [TestCase("empty")]
        [TestCase("blocked_entries")]
        [TestCase("invalid_archive")]
        [TestCase("directory_only")]
        [TestCase("traversal_after_directory")]
        public void FailedArchive_RemovesOnlyEmptyExtractionFolder(string kind)
        {
            string source = _sourceFolder + "/source.zip";
            string absoluteSource = Path.Combine(Path.GetDirectoryName(Application.dataPath), source);
            if (kind == "invalid_archive")
                File.WriteAllBytes(absoluteSource, new byte[] { 1, 2, 3 });
            else
            {
                using var stream = File.Create(absoluteSource);
                using var archive = new ZipArchive(stream, ZipArchiveMode.Create);
                if (kind == "directory_only" || kind == "traversal_after_directory")
                    archive.CreateEntry("New/Empty/");
                if (kind == "traversal_after_directory")
                    archive.CreateEntry("../outside.obj");
                if (kind == "blocked_entries")
                {
                    using var writer = new StreamWriter(archive.CreateEntry("nested/unsafe.cs").Open());
                    writer.Write("blocked executable source");
                }
            }

            JObject response = Call(
                new JObject
                {
                    ["sourcePath"] = source,
                    ["name"] = "failed_bundle",
                    ["outputFolder"] = TestFolder,
                }
            );

            Assert.AreEqual(false, (bool)response["success"], response.ToString());
            string extracted = TestFolder + "/failed_bundle";
            Assert.IsFalse(Directory.Exists(extracted), "empty extraction folder must roll back");
            Assert.IsFalse(File.Exists(extracted + ".meta"));
            Assert.IsFalse(AssetDatabase.IsValidFolder(extracted));
            Assert.IsTrue(File.Exists(source), "source archive must remain intact");
            Assert.IsTrue(File.Exists(TestFolder + "/failed_bundle.zip"), "staged bytes are partial output, not an empty folder");
        }

        [TestCase(false)]
        [TestCase(true)]
        public void ImportsModel_DoesNotImportUnrelatedPendingFile(bool archive)
        {
            AssetDatabase.ImportAsset(TestFolder, ImportAssetOptions.ImportRecursive | ImportAssetOptions.ForceSynchronousImport);
            string pending = TestFolder + "/unrelated.txt";
            AssetDatabase.DisallowAutoRefresh();
            try
            {
                File.WriteAllText(pending, "unrelated pending asset");
                string source = WriteCubeObj();
                if (archive)
                    source = WriteArchive("model", "nested/cube.obj", File.ReadAllText(source));
                JObject response = Call(new JObject { ["sourcePath"] = source, ["outputFolder"] = TestFolder + "/New/Nested" });

                Assert.AreEqual(true, (bool)response["success"], response.ToString());
                Assert.IsNotNull(AssetDatabase.LoadAssetAtPath<GameObject>((string)response["data"]["asset_path"]));
                Assert.IsEmpty(
                    AssetDatabase.AssetPathToGUID(pending, AssetPathToGUIDOptions.OnlyExistingAssets),
                    "targeted imports must not scan unrelated pending files"
                );
                Assert.IsFalse(File.Exists(pending + ".meta"));
                Assert.AreEqual("unrelated pending asset", File.ReadAllText(pending));
            }
            finally
            {
                File.Delete(pending);
                AssetDatabase.AllowAutoRefresh();
            }
        }

        [Test]
        public void ArchiveWithoutModel_DoesNotSelectExistingSiblingModel()
        {
            string folder = Path.Combine(Path.GetDirectoryName(Application.dataPath), TestFolder, "bundle");
            Directory.CreateDirectory(folder);
            string existing = Path.Combine(folder, "old.obj");
            string original = File.ReadAllText(Path.Combine(Path.GetDirectoryName(Application.dataPath), WriteCubeObj()));
            File.WriteAllText(existing, original);
            string source = WriteArchive("textures", "materials.mtl", "newmtl Unused\n");
            JObject response = Call(
                new JObject
                {
                    ["sourcePath"] = source,
                    ["name"] = "bundle",
                    ["outputFolder"] = TestFolder,
                }
            );
            Assert.AreEqual(false, (bool)response["success"], response.ToString());
            StringAssert.Contains("no model file", (string)response["error"]);
            Assert.AreEqual(original, File.ReadAllText(existing));
            Assert.IsTrue(File.Exists(Path.Combine(folder + "_1", "materials.mtl")));
        }

        [TestCase(false)]
        [TestCase(true)]
        public void ArchiveStemCollision_ImportsOnlyNewContentsAndPreservesExisting(bool fileCollision)
        {
            string stem = Path.Combine(Path.GetDirectoryName(Application.dataPath), TestFolder, "bundle");
            Directory.CreateDirectory(Path.GetDirectoryName(stem));
            string existing;
            if (fileCollision)
                existing = stem;
            else
            {
                Directory.CreateDirectory(stem);
                existing = Path.Combine(stem, "new.obj");
            }
            File.WriteAllText(existing, "existing file must remain intact");
            string source = WriteArchive("new_model", "new.obj", File.ReadAllText(Path.Combine(Path.GetDirectoryName(Application.dataPath), WriteCubeObj())));
            JObject response = Call(
                new JObject
                {
                    ["sourcePath"] = source,
                    ["name"] = "bundle",
                    ["outputFolder"] = TestFolder,
                }
            );
            Assert.AreEqual(true, (bool)response["success"], response.ToString());
            Assert.AreEqual(TestFolder + "/bundle_1/new.obj", (string)response["data"]["asset_path"]);
            Assert.AreEqual("existing file must remain intact", File.ReadAllText(existing));
        }

        [Test]
        public void AbsoluteSourceWithinAssets_ImportsNormally()
        {
            string source = Path.Combine(Path.GetDirectoryName(Application.dataPath), WriteCubeObj());
            JObject response = Call(new JObject { ["sourcePath"] = source, ["outputFolder"] = TestFolder });
            Assert.AreEqual(true, (bool)response["success"], response.ToString());
            Assert.IsTrue(File.Exists(source), "import must preserve the source");
        }

        [Test]
        public void AbsoluteExternalSource_DoesNotCreateOutputOrCopyBytes()
        {
            string source = Path.Combine(_tempDir, "external.obj");
            File.WriteAllText(source, "owned external source");
            string outputFolder = TestFolder + "/Rejected";
            JObject response = Call(new JObject { ["sourcePath"] = source, ["outputFolder"] = outputFolder });
            Assert.AreEqual(false, (bool)response["success"]);
            StringAssert.Contains("source_path", (string)response["error"]);
            Assert.IsFalse(Directory.Exists(Path.Combine(Path.GetDirectoryName(Application.dataPath), outputFolder)));
            Assert.AreEqual("owned external source", File.ReadAllText(source));
        }

        [TestCase("Assets/../external.obj")]
        [TestCase("C:external.obj")]
        [TestCase("relative.obj")]
        public void MalformedSource_DoesNotCreateOutput(string source)
        {
            string outputFolder = TestFolder + "/Rejected";
            JObject response = Call(new JObject { ["sourcePath"] = source, ["outputFolder"] = outputFolder });
            Assert.AreEqual(false, (bool)response["success"]);
            Assert.IsFalse(Directory.Exists(Path.Combine(Path.GetDirectoryName(Application.dataPath), outputFolder)));
        }

        [Test]
        public void OutputFolderTraversal_ReturnsError_AndDoesNotStageOutsideAssets()
        {
            string obj = WriteCubeObj();
            string escapeFolder = "Library/__import_model_file_escape_" + Guid.NewGuid().ToString("N");
            string escapedAbs = Path.Combine(Path.GetDirectoryName(Application.dataPath), escapeFolder, "Escaped.obj");
            try
            {
                JObject resp = Call(
                    new JObject
                    {
                        ["sourcePath"] = obj,
                        ["name"] = "Escaped",
                        ["outputFolder"] = "Assets/../" + escapeFolder,
                    }
                );

                Assert.AreEqual(false, (bool)resp["success"]);
                StringAssert.Contains("output_folder", ((string)resp["error"]).ToLowerInvariant());
                Assert.IsFalse(File.Exists(escapedAbs), "handler must not stage files outside Assets");
            }
            finally
            {
                string dir = Path.GetDirectoryName(escapedAbs);
                try
                {
                    if (Directory.Exists(dir))
                        Directory.Delete(dir, true);
                }
                catch { /* ignore */ }
            }
        }
    }
}
