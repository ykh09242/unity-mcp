using System;
using System.Collections.Generic;
using System.IO;
using System.IO.Compression;
using System.Linq;
using System.Reflection;
using System.Runtime.InteropServices;
using System.Text;
using MCPForUnity.Editor.Services.AssetGen;
using MCPForUnity.Editor.Services.AssetGen.Import;
using MCPForUnity.Editor.Tools.AssetGen;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEditor;
using UnityEngine;
using UnityEngine.TestTools;

namespace MCPForUnityTests.Editor.AssetGen
{
    public class ModelArchiveSelectionTests
    {
        private string root;
        private string output;
        private static readonly HashSet<string> Allowed = new(StringComparer.OrdinalIgnoreCase) { ".fbx", ".obj", ".gltf", ".glb", ".mtl", ".png" };

        [SetUp]
        public void SetUp()
        {
            root = "Assets/__model_archive_selection_" + Guid.NewGuid().ToString("N");
            output = root + "/Output";
            Directory.CreateDirectory(root);
        }

        [TearDown]
        public void TearDown()
        {
            string full = Path.GetFullPath(root);
            Assert.IsTrue(full.StartsWith(Path.GetFullPath(Application.dataPath) + Path.DirectorySeparatorChar, StringComparison.OrdinalIgnoreCase));
            if (AssetDatabase.IsValidFolder(root))
                AssetDatabase.DeleteAsset(root);
            if (Directory.Exists(full))
                Directory.Delete(full, true);
            if (File.Exists(full + ".meta"))
                File.Delete(full + ".meta");
        }

        internal static byte[] ArchiveBytes(params (string path, byte[] bytes)[] entries)
        {
            using var bytes = new MemoryStream();
            using (var archive = new ZipArchive(bytes, ZipArchiveMode.Create, true))
                foreach (var entry in entries)
                {
                    using Stream target = archive.CreateEntry(entry.path, System.IO.Compression.CompressionLevel.NoCompression).Open();
                    target.Write(entry.bytes, 0, entry.bytes.Length);
                }
            return bytes.ToArray();
        }

        private static byte[] Text(string value) => Encoding.UTF8.GetBytes(value);

        private string Zip(params (string path, byte[] bytes)[] entries)
        {
            string path = root + "/source.zip";
            File.WriteAllBytes(path, ArchiveBytes(entries));
            return path;
        }

        private JObject Import(string source, string selector = null, string name = "chosen")
        {
            var parameters = new JObject
            {
                ["sourcePath"] = source,
                ["name"] = name,
                ["outputFolder"] = output,
                ["targetSize"] = 0f,
            };
            if (selector != null)
                parameters["archiveModelPath"] = selector;
            return JObject.FromObject(ImportModelFile.HandleCommand(parameters));
        }

        private static string Obj(int width = 1, string material = null) =>
            (material == null ? "" : "mtllib model.mtl\n")
            + "o OwnedTriangle\nv 0 0 0\nv "
            + width
            + " 0 0\nv 0 1 0\nvt 0 0\nvt 1 0\nvt 0 1\n"
            + (material == null ? "" : "usemtl " + material + "\n")
            + "f 1/1 2/2 3/3\n";

        private static byte[] Png(Color color)
        {
            var texture = new Texture2D(2, 2, TextureFormat.RGBA32, false);
            try
            {
                texture.SetPixels(new[] { color, color, color, color });
                texture.Apply();
                return texture.EncodeToPNG();
            }
            finally
            {
                UnityEngine.Object.DestroyImmediate(texture);
            }
        }

        [TestCase(false)]
        [TestCase(true)]
        public void ExplicitObj_SelectsNonFirstModelWithExtractedMaterialTexture(bool reverse)
        {
            string material = "Owned_" + Path.GetFileName(root);
            var entries = new[]
            {
                ("a_preview/model.obj", Text(Obj())),
                ("z_main/model.obj", Text(Obj(3, material))),
                ("z_main/model.mtl", Text("newmtl " + material + "\nKd 1 1 1\nmap_Kd textures/owned.png\n")),
                ("z_main/textures/owned.png", Png(Color.green)),
                ("lod/model.obj", Text(Obj(2))),
            };
            if (reverse)
                Array.Reverse(entries);
            string source = Zip(entries);
            byte[] original = File.ReadAllBytes(source);
            // A real same-name source-side decoy must not satisfy extracted texture binding.
            Directory.CreateDirectory(root + "/textures");
            File.WriteAllBytes(root + "/textures/owned.png", Png(Color.red));
            JObject result = Import(source, "z_main/model.obj");
            Assert.IsTrue((bool)result["success"], result.ToString());
            string expected = output + "/chosen/z_main/model.obj";
            Assert.AreEqual(expected, (string)result["data"]["asset_path"]);
            var model = AssetDatabase.LoadAssetAtPath<GameObject>(expected);
            Mesh mesh = model.GetComponentInChildren<MeshFilter>(true).sharedMesh;
            Assert.Greater(mesh.GetIndexCount(0), 0u);
            Assert.That(mesh.bounds.size.x, Is.EqualTo(3f).Within(.001f));
            Material bound = model.GetComponentInChildren<MeshRenderer>(true).sharedMaterials.Single(m => m.name == material);
            Assert.IsNotNull(bound.mainTexture, "A material without a bound texture is not dependency evidence.");
            Assert.AreEqual(output + "/chosen/z_main/textures/owned.png", AssetDatabase.GetAssetPath(bound.mainTexture));
            JObject evidence = (JObject)result["data"]["archive_selection"];
            Assert.AreEqual("z_main/model.obj", (string)evidence["selected_path"]);
            Assert.AreEqual(expected, (string)evidence["selected_asset_path"]);
            Assert.AreEqual("complete", (string)evidence["extraction"]);
            Assert.IsTrue((bool)evidence["candidates_complete"]);
            CollectionAssert.AreEqual(new[] { "a_preview/model.obj", "lod/model.obj", "z_main/model.obj" }, evidence["candidates"].Values<string>().ToArray());
            CollectionAssert.AreEqual(original, File.ReadAllBytes(source));
        }

        [TestCase(false)]
        [TestCase(true)]
        public void DefaultSelection_IsOrdinalWithinBuiltInGroup(bool reverse)
        {
            var entries = new[] { ("z.obj", Text(Obj(3))), ("a.obj", Text(Obj(2))) };
            if (reverse)
                Array.Reverse(entries);
            JObject result = Import(Zip(entries));
            Assert.IsTrue((bool)result["success"], result.ToString());
            StringAssert.EndsWith("/a.obj", (string)result["data"]["asset_path"]);
            Assert.AreEqual("default", (string)result["data"]["archive_selection"]["mode"]);
        }

        [TestCase("model.obj")]
        [TestCase("MAIN/model.obj")]
        [TestCase("absent.obj")]
        public void MissingExactPath_DoesNotExtractAndReturnsCompleteCandidates(string selector)
        {
            string source = Zip(("main/model.obj", Text(Obj())), ("lod/model.obj", Text(Obj(2))));
            byte[] before = File.ReadAllBytes(source);
            JObject result = Import(source, selector);
            Assert.IsFalse((bool)result["success"], result.ToString());
            Assert.AreEqual("archive_model_not_found", (string)result["data"]["model_import_error_code"]);
            Assert.AreEqual(output + "/chosen.zip", (string)result["data"]["asset_path"]);
            Assert.AreEqual("not_started", (string)result["data"]["archive_selection"]["extraction"]);
            CollectionAssert.AreEqual(
                new[] { "lod/model.obj", "main/model.obj" },
                result["data"]["archive_selection"]["candidates"].Values<string>().ToArray()
            );
            Assert.IsFalse(Directory.Exists(output + "/chosen"));
            CollectionAssert.AreEqual(before, File.ReadAllBytes(source));
        }

        [TestCase("")]
        [TestCase(" ")]
        [TestCase("../outside.obj")]
        [TestCase("./model.obj")]
        [TestCase("nested//model.obj")]
        [TestCase("/model.obj")]
        [TestCase("C:/model.obj")]
        [TestCase("//host/model.obj")]
        [TestCase("model.obj/")]
        [TestCase("texture.png")]
        [TestCase("bad\nmodel.obj")]
        public void InvalidSelector_RejectsBeforeStaging(string selector)
        {
            string source = Zip(("model.obj", Text(Obj())));
            JObject result = Import(source, selector);
            Assert.IsFalse((bool)result["success"], result.ToString());
            Assert.AreEqual("archive_model_path_invalid", (string)result["data"]["model_import_error_code"]);
            Assert.IsFalse(Directory.Exists(output));
            Assert.IsTrue(File.Exists(source));
        }

        [Test]
        public void SelectorForNonArchive_RejectsBeforeStaging()
        {
            string source = root + "/model.obj";
            File.WriteAllText(source, Obj());
            JObject result = Import(source, "model.obj");
            Assert.IsFalse((bool)result["success"], result.ToString());
            Assert.AreEqual("archive_selector_requires_zip", (string)result["data"]["model_import_error_code"]);
            Assert.IsFalse(Directory.Exists(output));
        }

        [TestCase("model.obj", "model.obj")]
        [TestCase("Model.obj", "model.obj")]
        [TestCase("nested/model.obj", "nested\\model.obj")]
        [TestCase("nested/model.obj", "./nested//model.obj")]
        [TestCase("parent.obj", "parent.obj/child.obj")]
        [TestCase("parent.obj", "parent.obj/")]
        public void ArchiveDestinationConflict_RefusesBeforeExtracting(string first, string second)
        {
            JObject result = Import(Zip((first, Text(Obj())), (second, second.EndsWith("/") ? Array.Empty<byte>() : Text(Obj(2)))));
            Assert.IsFalse((bool)result["success"], result.ToString());
            Assert.AreEqual("archive_entry_conflict", (string)result["data"]["model_import_error_code"]);
            Assert.IsFalse(Directory.Exists(output + "/chosen"));
            Assert.IsTrue(File.Exists(output + "/chosen.zip"));
        }

        [Test]
        public void SelectedCorruptModel_DoesNotFallBackToUsableSibling()
        {
            bool previous = LogAssert.ignoreFailingMessages;
            try
            {
                LogAssert.ignoreFailingMessages = true;
                JObject result = Import(Zip(("a_good.obj", Text(Obj())), ("z_bad.obj", Text("not a model"))), "z_bad.obj");
                Assert.IsFalse((bool)result["success"], result.ToString());
                Assert.AreEqual("unusable_model", (string)result["data"]["model_import_error_code"]);
                StringAssert.EndsWith("/z_bad.obj", (string)result["data"]["asset_path"]);
                Assert.AreEqual("complete", (string)result["data"]["archive_selection"]["extraction"]);
            }
            finally
            {
                LogAssert.ignoreFailingMessages = previous;
            }
        }

        [Test]
        public void ExplicitGlb_ReportsActualMissingImporterWithoutBuiltInFallback()
        {
            JObject result = Import(Zip(("a_good.obj", Text(Obj())), ("model.glb", ModelImportPipelineTests.ContainerOnlyGlb())), "model.glb");
            Assert.IsFalse((bool)result["success"], result.ToString());
            Assert.AreEqual("missing_gltf_importer", (string)result["data"]["model_import_error_code"]);
            Assert.AreEqual("model.glb", (string)result["data"]["archive_selection"]["selected_path"]);
        }

        private static string Select(string[] entries, string requested, JObject evidence, int candidates = 128, int bytes = 512, int depth = 32)
        {
            Type type = typeof(ModelImportPipeline).Assembly.GetType("MCPForUnity.Editor.Services.AssetGen.Import.ModelArchiveSelection");
            Assert.IsNotNull(type, "The bounded archive catalog must replace filesystem enumeration.");
            MethodInfo method = type.GetMethod("Select", BindingFlags.Static | BindingFlags.NonPublic);
            Assert.IsNotNull(method);
            using var stream = new MemoryStream(ArchiveBytes(entries.Select(p => (p, Text("fixture"))).ToArray()));
            using var archive = new ZipArchive(stream, ZipArchiveMode.Read);
            try
            {
                return (string)method.Invoke(null, new object[] { archive, Allowed, requested, evidence, candidates, bytes, depth });
            }
            catch (TargetInvocationException error)
            {
                throw error.InnerException;
            }
        }

        [TestCase(null, "models/main.fbx")]
        [TestCase("preview.obj", "preview.obj")]
        [TestCase("models/main.fbx", "models/main.fbx")]
        [TestCase("lod/model.obj", "lod/model.obj")]
        public void MixedCatalog_UsesExactChoiceOrBuiltInOrdinalDefault(string requested, string expected)
        {
            var evidence = new JObject();
            string selected = Select(new[] { "z_optional.gltf", "preview.obj", "models/main.fbx", "lod/model.obj" }, requested, evidence);
            // lod/model.obj precedes models/main.fbx when selection is omitted; no "main" heuristic.
            Assert.AreEqual(requested == null ? "lod/model.obj" : expected, selected);
            CollectionAssert.AreEqual(
                new[] { "lod/model.obj", "models/main.fbx", "preview.obj", "z_optional.gltf" },
                evidence["candidates"].Values<string>().ToArray()
            );
        }

        [TestCase(2, 2, true)]
        [TestCase(3, 2, false)]
        public void CandidateLimit_IsExactAndNeverSelectsPartialList(int count, int limit, bool valid)
        {
            var evidence = new JObject();
            string[] paths = Enumerable.Range(0, count).Select(i => "model" + i + ".obj").ToArray();
            if (valid)
                Assert.AreEqual("model0.obj", Select(paths, null, evidence, limit));
            else
            {
                Exception failure = Assert.Catch(() => Select(paths, null, evidence, limit));
                Assert.AreEqual("archive_selection_limit", (string)evidence["selection_error_code"], failure.ToString());
                Assert.IsFalse((bool)evidence["candidates_complete"]);
                Assert.AreEqual(0, ((JArray)evidence["candidates"]).Count);
            }
        }

        [TestCase("nested/model.obj", 16, 2, true)]
        [TestCase("nested/model.obj", 15, 2, false)]
        [TestCase("nested/model.obj", 16, 1, false)]
        [TestCase("é.obj", 6, 1, true)]
        [TestCase("é.obj", 5, 1, false)]
        public void PathLimits_CountUtf8BytesAndSegments(string path, int bytes, int depth, bool valid)
        {
            var evidence = new JObject();
            if (valid)
                Assert.AreEqual(path, Select(new[] { path }, null, evidence, 128, bytes, depth));
            else
            {
                Assert.Catch(() => Select(new[] { path }, null, evidence, 128, bytes, depth));
                Assert.AreEqual("archive_selection_limit", (string)evidence["selection_error_code"]);
            }
        }

        [Test]
        public void GenuineAsciiFbx_FeasibilityAndExplicitMixedArchiveImport()
        {
            string source = root + "/feasibility.fbx";
            File.WriteAllText(source, AsciiFbx);
            AssetDatabase.ImportAsset(source, ImportAssetOptions.ForceSynchronousImport);
            Assert.IsInstanceOf<ModelImporter>(AssetImporter.GetAtPath(source));
            var model = AssetDatabase.LoadAssetAtPath<GameObject>(source);
            Assert.IsNotNull(model, "A genuine FBX fixture must import before archive selection can claim native FBX coverage.");
            MeshFilter filter = model.GetComponentInChildren<MeshFilter>(true);
            Assert.IsNotNull(filter, "The standalone FBX prerequisite must produce real mesh geometry before archive selection is tested.");
            Mesh mesh = filter.sharedMesh;
            Assert.IsNotNull(mesh);
            Assert.Greater(mesh.vertexCount, 0);
            Assert.Greater(mesh.GetIndexCount(0), 0u);
            JObject result = Import(Zip(("a_preview.obj", Text(Obj())), ("main/model.fbx", Text(AsciiFbx)), ("lod/model.obj", Text(Obj(2)))), "main/model.fbx");
            Assert.IsTrue((bool)result["success"], result.ToString());
            StringAssert.EndsWith("/main/model.fbx", (string)result["data"]["asset_path"]);
            Assert.IsNotNull(
                AssetDatabase.LoadAssetAtPath<GameObject>((string)result["data"]["asset_path"]).GetComponentInChildren<MeshFilter>(true).sharedMesh
            );
        }

        [TestCase("lod/model.obj", 2f)]
        [TestCase("main/model.obj", 3f)]
        public void ExactDuplicateBasename_ImportsRequestedGeometry(string selector, float expectedWidth)
        {
            JObject result = Import(Zip(("main/model.obj", Text(Obj(3))), ("lod/model.obj", Text(Obj(2)))), selector);
            Assert.IsTrue((bool)result["success"], result.ToString());
            var model = AssetDatabase.LoadAssetAtPath<GameObject>((string)result["data"]["asset_path"]);
            Assert.That(model.GetComponentInChildren<MeshFilter>(true).sharedMesh.bounds.size.x, Is.EqualTo(expectedWidth).Within(.001f));
            Assert.AreEqual(selector, (string)result["data"]["archive_selection"]["selected_path"]);
        }

        [TestCase("../unsafe.cs")]
        [TestCase("C:/unsafe.cs")]
        public void UnsafeSkippedEntry_RejectsBeforeAnyExtraction(string entry)
        {
            string source = Zip(("model.obj", Text(Obj())), (entry, Text("inert test payload")));
            JObject result = Import(source);
            Assert.AreEqual("archive_extraction_failed", (string)result["data"]["model_import_error_code"]);
            Assert.IsFalse(Directory.Exists(output + "/chosen"));
            Assert.IsTrue(File.Exists(source));
        }

        [Test]
        public void DefaultGlbOnly_ReportsMissingImporterAndCompleteSelection()
        {
            JObject result = Import(Zip(("model.glb", ModelImportPipelineTests.ContainerOnlyGlb())));
            Assert.AreEqual("missing_gltf_importer", (string)result["data"]["model_import_error_code"]);
            Assert.AreEqual("model.glb", (string)result["data"]["archive_selection"]["selected_path"]);
            Assert.IsTrue((bool)result["data"]["archive_selection"]["candidates_complete"]);
            Assert.AreEqual("complete", (string)result["data"]["archive_selection"]["extraction"]);
        }

        [Test]
        public void CandidateLimit_ProductionBoundaryRetainsZipWithoutExtraction()
        {
            string source = Zip(Enumerable.Range(0, 129).Select(i => ("model" + i + ".obj", Text(Obj()))).ToArray());
            JObject result = Import(source);
            Assert.AreEqual("archive_selection_limit", (string)result["data"]["model_import_error_code"]);
            Assert.IsFalse((bool)result["data"]["archive_selection"]["candidates_complete"]);
            Assert.IsEmpty((JArray)result["data"]["archive_selection"]["candidates"]);
            Assert.IsFalse(Directory.Exists(output + "/chosen"));
            Assert.IsTrue(File.Exists(output + "/chosen.zip"));
        }

        [Test]
        public void ArchiveNormalization_PreservesSelectedPathAndSourceIdentity()
        {
            string source = Zip(("selected.obj", Text(Obj(4))));
            AssetDatabase.ImportAsset(source, ImportAssetOptions.ForceSynchronousImport);
            string originalGuid = AssetDatabase.AssetPathToGUID(source);
            byte[] originalBytes = File.ReadAllBytes(source);
            var job = new AssetGenJob { TargetSize = 2f, ArchiveModelPath = "selected.obj" };
            MethodInfo method = typeof(ModelImportPipeline).GetMethod("ImportInto", BindingFlags.NonPublic | BindingFlags.Static);
            Assert.IsNotNull(method);
            var result = (AssetGenJob)method.Invoke(null, new object[] { job, source, true });
            Assert.AreEqual(AssetGenJobState.Done, result.State, result.Error);
            Assert.AreEqual(root + "/source/selected.obj", (string)result.ArchiveSelection["selected_asset_path"]);
            Assert.AreEqual(result.AssetPath, (string)result.ArchiveSelection["selected_asset_path"]);
            var model = AssetDatabase.LoadAssetAtPath<GameObject>(result.AssetPath);
            Assert.That(model.GetComponentInChildren<MeshFilter>(true).sharedMesh.bounds.size.x, Is.EqualTo(2f).Within(.002f));
            CollectionAssert.AreEqual(originalBytes, File.ReadAllBytes(source));
            Assert.AreEqual(originalGuid, AssetDatabase.AssetPathToGUID(source));
        }

        private static string OutputFor(string source, int attempts)
        {
            MethodInfo method = typeof(ModelImportPipeline).GetMethod("FindArchiveOutput", BindingFlags.Static | BindingFlags.NonPublic);
            Assert.IsNotNull(method);
            return (string)method.Invoke(null, new object[] { source, attempts });
        }

        [TestCase("file")]
        [TestCase("directory")]
        [TestCase("metadata")]
        public void OutputCollision_PreservesExistingBytesAndUsesBoundedSuffix(string kind)
        {
            string stem = root + "/occupied";
            string sentinel = kind == "metadata" ? stem + ".meta" : stem;
            if (kind == "directory")
            {
                Directory.CreateDirectory(stem);
                sentinel = stem + "/sentinel.txt";
            }
            File.WriteAllText(sentinel, "owned original");
            Assert.AreEqual(stem + "_1", OutputFor(stem + ".zip", 2));
            var failure = Assert.Throws<TargetInvocationException>(() => OutputFor(stem + ".zip", 1));
            Assert.AreEqual(
                "archive_output_conflict",
                failure.InnerException.GetType().GetProperty("ErrorCode", BindingFlags.Instance | BindingFlags.NonPublic).GetValue(failure.InnerException)
            );
            Assert.AreEqual("owned original", File.ReadAllText(sentinel));
            Assert.IsFalse(Directory.Exists(stem + "_1"));
        }

        [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
        [return: MarshalAs(UnmanagedType.I1)]
        private static extern bool CreateSymbolicLinkW(string link, string target, int flags);

        [DllImport("libc", SetLastError = true)]
        private static extern int symlink(string target, string link);

        [TestCase(false)]
        [TestCase(true)]
        public void OutputDanglingLink_RefusesWithoutFollowingOrCreatingTarget(bool metadata)
        {
            string stem = root + "/occupied";
            string link = Path.GetFullPath(stem + (metadata ? ".meta" : ""));
            string target = Path.GetFullPath(root + "/owned-missing-target");
            bool created = false;
            AssetDatabase.DisallowAutoRefresh();
            try
            {
                created = Application.platform == RuntimePlatform.WindowsEditor ? CreateSymbolicLinkW(link, target, 2) : symlink(target, link) == 0;
                if (!created)
                    Assert.Ignore("Symlink creation unavailable: " + Marshal.GetLastWin32Error());
                var failure = Assert.Throws<TargetInvocationException>(() => OutputFor(stem + ".zip", 2));
                Assert.AreEqual(
                    "archive_output_conflict",
                    failure.InnerException.GetType().GetProperty("ErrorCode", BindingFlags.Instance | BindingFlags.NonPublic).GetValue(failure.InnerException)
                );
                Assert.IsFalse(File.Exists(target));
                Assert.IsFalse(Directory.Exists(stem + "_1"));
                Assert.AreNotEqual((FileAttributes)0, File.GetAttributes(link) & FileAttributes.ReparsePoint);
            }
            finally
            {
                if (created)
                    File.Delete(link);
                AssetDatabase.AllowAutoRefresh();
            }
        }

        // Owned ASCII FBX geometry and connections; this is not OBJ data with an FBX suffix.
        private const string AsciiFbx =
            @"; FBX 7.4.0 project file
FBXHeaderExtension: {
 FBXHeaderVersion: 1003
 FBXVersion: 7400
 Creator: ""GEN05 owned fixture""
}
GlobalSettings: {
 Version: 1000
 Properties70: {
  P: ""UpAxis"", ""int"", ""Integer"", """",1
  P: ""UpAxisSign"", ""int"", ""Integer"", """",1
  P: ""FrontAxis"", ""int"", ""Integer"", """",2
  P: ""FrontAxisSign"", ""int"", ""Integer"", """",-1
  P: ""CoordAxis"", ""int"", ""Integer"", """",0
  P: ""CoordAxisSign"", ""int"", ""Integer"", """",1
  P: ""UnitScaleFactor"", ""double"", ""Number"", """",1
 }
}
Documents: {
 Count: 1
 Document: 3000, ""OwnedScene"", ""Scene"" {
  RootNode: 0
 }
}
Definitions: {
 Version: 100
 Count: 2
 ObjectType: ""Model"" {
  Count: 1
 }
 ObjectType: ""Geometry"" {
  Count: 1
 }
}
Objects: {
 Geometry: 1000, ""Geometry::OwnedTriangle"", ""Mesh"" {
  Vertices: *9 {
   a: 0,0,0,300,0,0,0,100,0
  }
  PolygonVertexIndex: *3 {
   a: 0,1,-3
  }
  GeometryVersion: 124
  LayerElementNormal: 0 {
   Version: 101
   Name: """"
   MappingInformationType: ""ByPolygonVertex""
   ReferenceInformationType: ""Direct""
   Normals: *9 {
    a: 0,0,1,0,0,1,0,0,1
   }
  }
  Layer: 0 {
   Version: 100
   LayerElement: {
    Type: ""LayerElementNormal""
    TypedIndex: 0
   }
  }
 }
 Model: 2000, ""Model::OwnedMain"", ""Mesh"" {
  Version: 232
  Properties70: {
   P: ""Lcl Translation"", ""Lcl Translation"", """", ""A"",0,0,0
   P: ""Lcl Rotation"", ""Lcl Rotation"", """", ""A"",0,0,0
   P: ""Lcl Scaling"", ""Lcl Scaling"", """", ""A"",1,1,1
  }
  Shading: T
  Culling: ""CullingOff""
 }
}
Connections: {
 C: ""OO"",1000,2000
 C: ""OO"",2000,0
}
";
    }
}
