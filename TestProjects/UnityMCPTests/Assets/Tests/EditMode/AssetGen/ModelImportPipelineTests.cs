using System;
using System.IO;
using System.IO.Compression;
using System.Reflection;
using System.Text;
using MCPForUnity.Editor.Services.AssetGen;
using MCPForUnity.Editor.Services.AssetGen.Import;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEditor;
using UnityEngine;
using UnityEngine.TestTools;

namespace MCPForUnityTests.Editor.AssetGen
{
    /// <summary>Real owned OBJ fixtures exercise native import; glTF availability is controlled without installing packages.</summary>
    public class ModelImportPipelineTests
    {
        internal const string TriangleObj = "o Triangle\nv 0 0 0\nv 1 0 0\nv 0 1 0\nf 1 2 3\n";
        private string _root;

        [SetUp]
        public void SetUp()
        {
            _root = "Assets/__ModelImportEvidence_" + Guid.NewGuid().ToString("N");
            AssetDatabase.CreateFolder("Assets", Path.GetFileName(_root));
        }

        [TearDown]
        public void TearDown()
        {
            Assert.IsTrue(_root.StartsWith("Assets/__ModelImportEvidence_", StringComparison.Ordinal));
            AssetDatabase.DeleteAsset(_root);
        }

        private string Write(string name, string contents)
        {
            string path = _root + "/" + name;
            File.WriteAllText(path, contents, new UTF8Encoding(false));
            return path;
        }

        internal static byte[] ContainerOnlyGlb()
        {
            byte[] json = Encoding.UTF8.GetBytes("{\"asset\":{\"version\":\"2.0\"}} ");
            using var stream = new MemoryStream();
            using var writer = new BinaryWriter(stream);
            writer.Write(0x46546C67u);
            writer.Write(2u);
            writer.Write((uint)(20 + json.Length));
            writer.Write((uint)json.Length);
            writer.Write(0x4E4F534Au);
            writer.Write(json);
            return stream.ToArray();
        }

        private static string Stage(AssetGenJob job) => (string)JObject.FromObject(job)["ModelImportStage"];

        private static string Code(AssetGenJob job) => (string)JObject.FromObject(job)["ModelImportErrorCode"];

        [TestCase(false)]
        [TestCase(true)]
        public void RealObj_WithIndexedMesh_IsUsable(bool archive)
        {
            string source = Write("triangle.obj", TriangleObj);
            if (archive)
            {
                source = _root + "/bundle.zip";
                using var stream = File.Create(source);
                using var zip = new ZipArchive(stream, ZipArchiveMode.Create);
                using var writer = new StreamWriter(zip.CreateEntry("nested/triangle.obj").Open());
                writer.Write(TriangleObj);
            }

            AssetGenJob result = ModelImportPipeline.ImportInto(Job(archive ? "zip" : "obj"), source);

            Assert.AreEqual(AssetGenJobState.Done, result.State, result.Error);
            Assert.AreEqual("usable", Stage(result));
            Assert.IsNull(Code(result));
            Assert.AreEqual(AssetDatabase.AssetPathToGUID(result.AssetPath), result.AssetGuid);
            Assert.IsNotEmpty(result.AssetGuid);
            GameObject model = AssetDatabase.LoadAssetAtPath<GameObject>(result.AssetPath);
            Assert.IsNotNull(model);
            MeshFilter filter = model.GetComponentInChildren<MeshFilter>(true);
            Assert.IsNotNull(filter);
            Assert.Greater(filter.sharedMesh.vertexCount, 0);
            Assert.Greater(filter.sharedMesh.GetIndexCount(0), 0);
            Assert.AreEqual(TriangleObj, File.ReadAllText(result.AssetPath), "Import must preserve source model bytes.");
        }

        [TestCase(false)]
        [TestCase(true)]
        public void CorruptObj_WithGuid_IsFailedAndRetained(bool archive)
        {
            const string contents = "this is not a model";
            string source = Write("corrupt.obj", contents);
            if (archive)
            {
                source = _root + "/corrupt_bundle.zip";
                using var stream = File.Create(source);
                using var zip = new ZipArchive(stream, ZipArchiveMode.Create);
                using var writer = new StreamWriter(zip.CreateEntry("nested/corrupt.obj").Open());
                writer.Write(contents);
            }
            byte[] before = File.ReadAllBytes(source);
            bool previous = LogAssert.ignoreFailingMessages;
            AssetGenJob result;
            try
            {
                LogAssert.ignoreFailingMessages = true; // Expected native importer diagnostic; assert the actual unusable result below.
                result = ModelImportPipeline.ImportInto(Job(archive ? "zip" : "obj"), source);
            }
            finally
            {
                LogAssert.ignoreFailingMessages = previous;
            }

            string modelPath = archive ? _root + "/corrupt_bundle/nested/corrupt.obj" : source;
            string guid = AssetDatabase.AssetPathToGUID(modelPath);
            Assert.IsNotEmpty(guid, "Establish the registered-file false-success precondition.");
            Assert.IsInstanceOf<ModelImporter>(AssetImporter.GetAtPath(modelPath));
            Assert.AreEqual(AssetGenJobState.Failed, result.State);
            Assert.AreEqual("registered", Stage(result));
            Assert.AreEqual("unusable_model", Code(result));
            Assert.AreEqual(modelPath, result.AssetPath);
            Assert.AreEqual(guid, result.AssetGuid);
            StringAssert.Contains("mesh", result.Error.ToLowerInvariant());
            Assert.AreEqual(contents, File.ReadAllText(modelPath));
            CollectionAssert.AreEqual(before, File.ReadAllBytes(source));
        }

        [Test]
        public void UnsupportedImporter_WithGuid_IsFailedAndRetained()
        {
            string path = Write("model.unknownmodel", TriangleObj);
            AssetGenJob result = ModelImportPipeline.ImportInto(Job("unknownmodel"), path);
            Assert.IsNotEmpty(AssetDatabase.AssetPathToGUID(path), "A GUID alone is not model evidence.");
            Assert.AreEqual(AssetGenJobState.Failed, result.State);
            Assert.AreEqual(path, result.AssetPath);
            Assert.AreEqual(AssetDatabase.AssetPathToGUID(path), result.AssetGuid);
            Assert.AreEqual("registered", Stage(result));
            Assert.AreEqual("unsupported_model_importer", Code(result));
            StringAssert.Contains("importer", result.Error.ToLowerInvariant());
            Assert.AreEqual(TriangleObj, File.ReadAllText(path));
        }

        private static AssetGenJob Job(string format) =>
            new AssetGenJob
            {
                JobId = "test",
                Kind = "model",
                Provider = "tripo",
                State = AssetGenJobState.Importing,
                Format = format,
                TargetSize = 1f,
            };

        [TestCase(false)]
        [TestCase(true)]
        public void ValidGlbContainer_WithoutActiveImporter_IsRegisteredButNotUsable(bool runtimeAvailable)
        {
            var availability = typeof(ModelImportPipeline).GetField("_gltfastAvailable", BindingFlags.Static | BindingFlags.NonPublic);
            object previous = availability.GetValue(null);
            string path = _root + "/container.glb";
            byte[] bytes = ContainerOnlyGlb();
            File.WriteAllBytes(path, bytes);
            Assert.AreEqual("glb", MCPForUnity.Editor.Services.AssetGen.Providers.TripoAdapter.ValidateModelBytes(bytes, "glb"));
            try
            {
                availability.SetValue(null, runtimeAvailable);
                AssetGenJob result = ModelImportPipeline.ImportInto(Job("glb"), path);
                if (AssetImporter.GetAtPath(path)?.GetType() != typeof(AssetImporter))
                    Assert.Ignore("This fixture exercises projects without an active glTF importer.");
                Assert.AreEqual(AssetGenJobState.Failed, result.State);
                Assert.AreEqual("registered", Stage(result));
                Assert.IsNotEmpty(result.AssetGuid);
                Assert.AreEqual("missing_gltf_importer", Code(result));
                Assert.AreEqual(path, result.AssetPath);
                StringAssert.Contains("glTFast", result.Error);
                CollectionAssert.AreEqual(bytes, File.ReadAllBytes(path));
            }
            finally
            {
                availability.SetValue(null, previous);
            }
        }

        [TestCase(false, false)]
        [TestCase(false, true)]
        [TestCase(true, false)]
        [TestCase(true, true)]
        public void Inspection_RequiresIndexedGeometry_IncludingInactiveSkinnedModels(bool skinned, bool indexed)
        {
            var model = new GameObject("owned-validation-root");
            var child = new GameObject("inactive-mesh");
            var mesh = new Mesh();
            try
            {
                child.transform.SetParent(model.transform);
                child.SetActive(false);
                mesh.vertices = new[] { Vector3.zero, Vector3.right, Vector3.up };
                mesh.SetIndices(indexed ? new[] { 0, 1, 2 } : Array.Empty<int>(), MeshTopology.Triangles, 0);
                if (skinned)
                    child.AddComponent<SkinnedMeshRenderer>().sharedMesh = mesh;
                else
                    child.AddComponent<MeshFilter>().sharedMesh = mesh;
                mesh.UploadMeshData(true); // Validation must not need CPU-readable vertex/index arrays.

                Assert.AreEqual(indexed ? null : "unusable_model", ModelImportPipeline.InspectModel(model));
            }
            finally
            {
                UnityEngine.Object.DestroyImmediate(model);
                UnityEngine.Object.DestroyImmediate(mesh);
            }
        }

        [Test]
        public void LargeModelFindsLateGeometryAndMeasuresItsCompleteBounds()
        {
            var model = new GameObject("owned-large-model");
            var mesh = new Mesh();
            try
            {
                for (int i = 0; i < 11000; i++)
                    new GameObject("node").transform.SetParent(model.transform, false);
                var last = model.transform.GetChild(10999);
                last.localPosition = new Vector3(100, 0, 0);
                mesh.vertices = new[] { Vector3.zero, Vector3.right, Vector3.up };
                mesh.triangles = new[] { 0, 1, 2 };
                last.gameObject.AddComponent<MeshFilter>().sharedMesh = mesh;
                Assert.IsNull(ModelImportPipeline.InspectModel(model));
                Assert.IsTrue(ModelHierarchyBounds.TryMeasure(model, Matrix4x4.identity, out Bounds bounds, out string error), error);
                Assert.AreEqual(new Vector3(100.5f, 0.5f, 0), bounds.center);
            }
            finally
            {
                UnityEngine.Object.DestroyImmediate(model);
                UnityEngine.Object.DestroyImmediate(mesh);
            }
        }

        [Test]
        public void Inspection_HasBoundedTraversal_AndStopsAtFirstUsableMesh()
        {
            var model = new GameObject("owned-bounded-root");
            var mesh = new Mesh();
            try
            {
                for (int i = 0; i < 20; i++)
                    new GameObject("empty-" + i).transform.SetParent(model.transform);
                Assert.AreEqual("model_validation_limit", ModelImportPipeline.InspectModel(model, 4));
                mesh.vertices = new[] { Vector3.zero, Vector3.right, Vector3.up };
                mesh.triangles = new[] { 0, 1, 2 };
                model.AddComponent<MeshFilter>().sharedMesh = mesh;
                Assert.IsNull(ModelImportPipeline.InspectModel(model, 2), "A valid first mesh must not traverse the remaining hierarchy.");
            }
            finally
            {
                UnityEngine.Object.DestroyImmediate(model);
                UnityEngine.Object.DestroyImmediate(mesh);
            }
        }

        [Test]
        public void MissingFile_DoesNotClaimSavedOutput()
        {
            var result = ModelImportPipeline.ImportInto(Job("obj"), _root + "/absent.obj");
            Assert.AreEqual(AssetGenJobState.Failed, result.State);
            Assert.IsNull(result.AssetPath);
            Assert.IsNull(Stage(result));
            Assert.AreEqual("model_file_missing", Code(result));
        }

        [Test]
        public void PathOutsideAssets_Fails()
        {
            AssetGenJob result = ModelImportPipeline.ImportInto(Job("fbx"), "/tmp/somewhere/model.fbx");
            Assert.AreEqual(AssetGenJobState.Failed, result.State);
            StringAssert.Contains("Assets", result.Error);
        }

        [Test]
        public void NullPath_Fails()
        {
            AssetGenJob result = ModelImportPipeline.ImportInto(Job("fbx"), null);
            Assert.AreEqual(AssetGenJobState.Failed, result.State);
        }

        [TestCase("generic", ModelImporterAnimationType.Generic)]
        [TestCase("Generic", ModelImporterAnimationType.Generic)]
        [TestCase("humanoid", ModelImporterAnimationType.Human)]
        [TestCase("human", ModelImporterAnimationType.Human)]
        [TestCase(" LEGACY ", ModelImporterAnimationType.Legacy)]
        [TestCase("none", ModelImporterAnimationType.None)]
        [TestCase("", ModelImporterAnimationType.None)]
        [TestCase(null, ModelImporterAnimationType.None)]
        [TestCase("nonsense", ModelImporterAnimationType.None)]
        public void ParseAnimationType_MapsRigMode(string input, ModelImporterAnimationType expected)
        {
            Assert.AreEqual(expected, ModelImportPipeline.ParseAnimationType(input));
        }
    }
}
