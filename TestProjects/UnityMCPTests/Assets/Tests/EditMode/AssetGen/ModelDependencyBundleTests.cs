using System;
using System.IO;
using System.Linq;
using System.Runtime.InteropServices;
using System.Text;
using MCPForUnity.Editor.Services.AssetGen.Import;
using MCPForUnity.Editor.Tools.AssetGen;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEditor;
using UnityEngine;

namespace MCPForUnityTests.Editor.AssetGen
{
    public class ModelDependencyBundleTests
    {
        private string root;
        private string sourceRoot;
        private string outputRoot;

        [SetUp]
        public void SetUp()
        {
            root = "Assets/__bundle_" + Guid.NewGuid().ToString("N");
            sourceRoot = root + "/Sources";
            outputRoot = root + "/Output";
            Directory.CreateDirectory(sourceRoot);
        }

        [TearDown]
        public void TearDown()
        {
            string absolute = Path.GetFullPath(root);
            Assert.IsTrue(absolute.StartsWith(Path.GetFullPath(Application.dataPath) + Path.DirectorySeparatorChar, StringComparison.OrdinalIgnoreCase));
            if (AssetDatabase.IsValidFolder(root))
                AssetDatabase.DeleteAsset(root);
            if (Directory.Exists(absolute))
                Directory.Delete(absolute, true);
            if (File.Exists(absolute + ".meta"))
                File.Delete(absolute + ".meta");
        }

        [Test]
        public void SubstantialGltfBundleIncludesEveryDependencyAndRepeatedReference()
        {
            var buffers = new JArray();
            for (int i = 0; i < 300; i++)
                Write("part" + i + ".bin", new byte[] { (byte)i });
            for (int i = 0; i < 2000; i++)
                buffers.Add(new JObject { ["uri"] = "part" + (i % 300) + ".bin", ["byteLength"] = 1 });
            string main = Write(
                "model.gltf",
                new JObject
                {
                    ["asset"] = new JObject { ["version"] = "2.0" },
                    ["buffers"] = buffers,
                }.ToString(Formatting.None)
            );
            var plan = ModelDependencyBundle.Plan(Path.GetFullPath(main));
            Assert.AreEqual(301, plan.Entries.Count);
            Assert.AreEqual(2000, plan.References);
            Assert.AreEqual("part299.bin", plan.Entries[300].RelativePath);
        }

        private JObject Import(string source, string name = "Renamed") =>
            JObject.Parse(
                JsonConvert.SerializeObject(
                    ImportModelFile.HandleCommand(
                        new JObject
                        {
                            ["sourcePath"] = source,
                            ["outputFolder"] = outputRoot,
                            ["name"] = name,
                            ["targetSize"] = 0f,
                        }
                    )
                )
            );

        private string Write(string relative, byte[] bytes)
        {
            string path = sourceRoot + "/" + relative;
            Directory.CreateDirectory(Path.GetDirectoryName(path));
            File.WriteAllBytes(path, bytes);
            return path;
        }

        private string Write(string relative, string text) => Write(relative, new UTF8Encoding(false).GetBytes(text));

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

        private string Obj(string library = "model.mtl") =>
            Write(
                "model.obj",
                "mtllib "
                    + library
                    + "\no OwnedTriangle\nv 0 0 0\nv 1 0 0\nv 0 1 0\nvt 0 0\nvt 1 0\nvt 0 1\nusemtl Owned_"
                    + Path.GetFileName(root)
                    + "\nf 1/1 2/2 3/3\n"
            );

        private void Mtl(string texture) =>
            Write("model.mtl", "newmtl Owned_" + Path.GetFileName(root) + "\nKa 0 0 0\nKd 1 1 1\nKs 0 0 0\nNs 1\nillum 1\nmap_Kd " + texture + "\n");

        private static JObject TriangleDocument(string bufferUri, string imageUri)
        {
            var buffer = new JObject { ["byteLength"] = 42 };
            if (bufferUri != null)
                buffer["uri"] = bufferUri;
            return new JObject
            {
                ["asset"] = new JObject { ["version"] = "2.0" },
                ["buffers"] = new JArray(buffer),
                ["bufferViews"] = new JArray(
                    new JObject
                    {
                        ["buffer"] = 0,
                        ["byteOffset"] = 0,
                        ["byteLength"] = 36,
                        ["target"] = 34962,
                    },
                    new JObject
                    {
                        ["buffer"] = 0,
                        ["byteOffset"] = 36,
                        ["byteLength"] = 6,
                        ["target"] = 34963,
                    }
                ),
                ["accessors"] = new JArray(
                    new JObject
                    {
                        ["bufferView"] = 0,
                        ["componentType"] = 5126,
                        ["count"] = 3,
                        ["type"] = "VEC3",
                        ["min"] = new JArray(0, 0, 0),
                        ["max"] = new JArray(1, 1, 0),
                    },
                    new JObject
                    {
                        ["bufferView"] = 1,
                        ["componentType"] = 5123,
                        ["count"] = 3,
                        ["type"] = "SCALAR",
                    }
                ),
                ["meshes"] = new JArray(
                    new JObject
                    {
                        ["primitives"] = new JArray(
                            new JObject
                            {
                                ["attributes"] = new JObject { ["POSITION"] = 0 },
                                ["indices"] = 1,
                                ["material"] = 0,
                            }
                        ),
                    }
                ),
                ["materials"] = new JArray(new JObject { ["pbrMetallicRoughness"] = new JObject { ["baseColorTexture"] = new JObject { ["index"] = 0 } } }),
                ["textures"] = new JArray(new JObject { ["source"] = 0 }),
                ["images"] = new JArray(new JObject { ["uri"] = imageUri }),
                ["nodes"] = new JArray(new JObject { ["mesh"] = 0 }),
                ["scenes"] = new JArray(new JObject { ["nodes"] = new JArray(0) }),
                ["scene"] = 0,
            };
        }

        private static byte[] TriangleBytes()
        {
            using var stream = new MemoryStream();
            using var writer = new BinaryWriter(stream);
            foreach (float value in new[] { 0f, 0f, 0f, 1f, 0f, 0f, 0f, 1f, 0f })
                writer.Write(value);
            writer.Write((ushort)0);
            writer.Write((ushort)1);
            writer.Write((ushort)2);
            return stream.ToArray();
        }

        private static byte[] Glb(JObject document, byte[] binary)
        {
            byte[] json = Encoding.UTF8.GetBytes(document.ToString(Formatting.None));
            int jsonLength = (json.Length + 3) & ~3;
            int binaryLength = (binary.Length + 3) & ~3;
            using var stream = new MemoryStream();
            using var writer = new BinaryWriter(stream);
            writer.Write(0x46546C67u);
            writer.Write(2u);
            writer.Write((uint)(12 + 8 + jsonLength + 8 + binaryLength));
            writer.Write((uint)jsonLength);
            writer.Write(0x4E4F534Au);
            writer.Write(json);
            for (int i = json.Length; i < jsonLength; i++)
                writer.Write((byte)' ');
            writer.Write((uint)binaryLength);
            writer.Write(0x004E4942u);
            writer.Write(binary);
            for (int i = binary.Length; i < binaryLength; i++)
                writer.Write((byte)0);
            return stream.ToArray();
        }

        [TestCase(false)]
        [TestCase(true)]
        public void ObjBundle_CopiesAndBindsStagedTexture(bool nestedTexture)
        {
            string texture = (nestedTexture ? "Textures/" : "") + "owned_" + Guid.NewGuid().ToString("N") + ".png";
            byte[] png = Png(Color.magenta);
            Write(texture, png);
            Mtl(texture);
            string source = Obj();
            byte[] objBefore = File.ReadAllBytes(source);
            byte[] mtlBefore = File.ReadAllBytes(sourceRoot + "/model.mtl");
            string decoy = root + "/Decoy/" + Path.GetFileName(texture);
            Directory.CreateDirectory(Path.GetDirectoryName(decoy));
            File.WriteAllBytes(decoy, Png(Color.green));
            AssetDatabase.ImportAsset(root + "/Decoy", ImportAssetOptions.ImportRecursive | ImportAssetOptions.ForceSynchronousImport);
            JObject response = Import(source);
            Assert.IsTrue((bool)response["success"], response.ToString());
            string main = (string)response["data"]["asset_path"];
            Assert.AreEqual(outputRoot + "/Renamed/model.obj", main, "the name labels the bundle; the main basename stays intact");
            var bundle = response["data"]["dependency_bundle"];
            Assert.AreEqual("staged", (string)bundle["stage"]);
            Assert.AreEqual("complete", (string)bundle["inspection"]);
            Assert.AreEqual(3, (int)bundle["staged_file_count"]);
            CollectionAssert.AreEqual(objBefore, File.ReadAllBytes(main));
            CollectionAssert.AreEqual(mtlBefore, File.ReadAllBytes(outputRoot + "/Renamed/model.mtl"));
            CollectionAssert.AreEqual(png, File.ReadAllBytes(outputRoot + "/Renamed/" + texture));
            CollectionAssert.AreEqual(objBefore, File.ReadAllBytes(source));
            CollectionAssert.AreEqual(mtlBefore, File.ReadAllBytes(sourceRoot + "/model.mtl"));
            CollectionAssert.AreEqual(png, File.ReadAllBytes(sourceRoot + "/" + texture));
            GameObject model = AssetDatabase.LoadAssetAtPath<GameObject>(main);
            MeshFilter mesh = model.GetComponentInChildren<MeshFilter>(true);
            Assert.IsNotNull(mesh);
            Assert.Greater(mesh.sharedMesh.vertexCount, 0);
            Assert.Greater(mesh.sharedMesh.GetIndexCount(0), 0);
            Material material = model.GetComponentInChildren<MeshRenderer>(true).sharedMaterial;
            Assert.IsNotNull(material);
            Assert.IsNotNull(material.mainTexture, "a non-null/default material is insufficient");
            Assert.AreEqual(
                outputRoot + "/Renamed/" + texture,
                AssetDatabase.GetAssetPath(material.mainTexture),
                "must bind staged texture, not original or decoy"
            );
        }

        [Test]
        public void GltfBundle_CopiesCoreResourcesAndRetainsActualImporterEvidence()
        {
            byte[] binary = TriangleBytes();
            byte[] png = Png(Color.cyan);
            Write("geometry.bin", binary);
            Write("Textures/owned image.png", png);
            string source = Write("triangle.gltf", TriangleDocument("geometry.bin", "Textures/owned%20image.png").ToString(Formatting.None));
            byte[] original = File.ReadAllBytes(source);
            JObject response = Import(source);
            string main = (string)response["data"]?["asset_path"];
            Assert.AreEqual(outputRoot + "/Renamed/triangle.gltf", main, response.ToString());
            CollectionAssert.AreEqual(original, File.ReadAllBytes(main));
            CollectionAssert.AreEqual(original, File.ReadAllBytes(source));
            CollectionAssert.AreEqual(binary, File.ReadAllBytes(outputRoot + "/Renamed/geometry.bin"));
            CollectionAssert.AreEqual(png, File.ReadAllBytes(outputRoot + "/Renamed/Textures/owned image.png"));
            Assert.AreEqual("staged", (string)response["data"]["dependency_bundle"]["stage"]);
            Assert.AreEqual(3, (int)response["data"]["dependency_bundle"]["staged_file_count"]);
            AssetImporter importer = AssetImporter.GetAtPath(main);
            if (importer == null || importer.GetType() == typeof(AssetImporter))
            {
                Assert.IsFalse((bool)response["success"]);
                Assert.AreEqual("missing_gltf_importer", (string)response["data"]["model_import_error_code"]);
                Assert.AreEqual("registered", (string)response["data"]["model_import_stage"]);
                Assert.IsNotEmpty((string)response["data"]["asset_guid"]);
                return;
            }
            Assert.IsTrue((bool)response["success"], "an installed but failing importer cannot be reported as missing: " + response);
            GameObject model = AssetDatabase.LoadAssetAtPath<GameObject>(main);
            Assert.IsNotNull(model.GetComponentInChildren<MeshFilter>(true)?.sharedMesh);
            var material = model.GetComponentInChildren<Renderer>(true).sharedMaterial;
            Assert.IsNotNull(material.mainTexture);
            Assert.AreEqual(outputRoot + "/Renamed/Textures/owned image.png", AssetDatabase.GetAssetPath(material.mainTexture));
        }

        [Test]
        public void EmbeddedGlb_MaterialExtensionIsPreservedAndInspectionIsLimited()
        {
            JObject doc = TriangleDocument(null, "data:image/png;base64," + Convert.ToBase64String(Png(Color.blue)));
            doc["extensionsUsed"] = new JArray("KHR_materials_unlit");
            doc["materials"][0]["extensions"] = new JObject { ["KHR_materials_unlit"] = new JObject() };
            byte[] bytes = Glb(doc, TriangleBytes());
            string source = Write("embedded.glb", bytes);
            JObject response = Import(source);
            string main = (string)response["data"]?["asset_path"];
            Assert.AreEqual(outputRoot + "/Renamed.glb", main, response.ToString());
            CollectionAssert.AreEqual(bytes, File.ReadAllBytes(main));
            CollectionAssert.AreEqual(bytes, File.ReadAllBytes(source));
            Assert.AreEqual("not_scanned", (string)response["data"]["dependency_bundle"]["inspection"]);
            Assert.AreEqual("glb_core_resources_only", (string)response["data"]["dependency_bundle"]["inspection_scope"]);
            Assert.AreNotEqual("unsupported_dependency_form", (string)response["data"]["dependency_bundle"]["error_code"]);
        }

        [TestCase("buffer")]
        [TestCase("image")]
        public void GlbCoreExternalResource_IsRejectedBeforeWriting(string kind)
        {
            JObject doc = TriangleDocument(
                kind == "buffer" ? "geometry.bin" : null,
                kind == "image" ? "texture.png" : "data:image/png;base64," + Convert.ToBase64String(Png(Color.blue))
            );
            Write("geometry.bin", TriangleBytes());
            Write("texture.png", Png(Color.blue));
            AssertPreflightFailure(Write("external.glb", Glb(doc, TriangleBytes())), "unsupported_dependency_form");
        }

        [TestCase("../outside.png")]
        [TestCase("/absolute.png")]
        [TestCase("https://example.invalid/texture.png")]
        [TestCase("file:///C:/texture.png")]
        [TestCase("Textures\\bad.png")]
        [TestCase("texture.png?query")]
        [TestCase("texture.png#fragment")]
        [TestCase("texture%2fescape.png")]
        public void GltfUnsafeReference_FailsBeforeOutput(string reference)
        {
            Write("geometry.bin", TriangleBytes());
            AssertPreflightFailure(Write("triangle.gltf", TriangleDocument("geometry.bin", reference).ToString(Formatting.None)), "unsafe_dependency_path");
        }

        [Test]
        public void MissingMtl_FailsBeforeOutput() => AssertPreflightFailure(Obj("missing.mtl"), "dependency_not_found");

        [TestCase("-s 1 1 1 texture.png")]
        [TestCase("\"texture.png\"")]
        [TestCase("nested/material.mtl")]
        public void UnsupportedObjDependencySyntax_IsExplicit(string syntax)
        {
            string source;
            if (syntax.EndsWith(".mtl", StringComparison.Ordinal))
                source = Obj(syntax);
            else
            {
                Mtl(syntax);
                source = Obj();
            }
            AssertPreflightFailure(source, "unsupported_dependency_form");
        }

        [Test]
        public void GltfOpaqueExtension_IsExplicitlyUnsupported()
        {
            JObject doc = TriangleDocument("geometry.bin", "texture.png");
            doc["extensionsUsed"] = new JArray("VENDOR_external_resource");
            AssertPreflightFailure(Write("triangle.gltf", doc.ToString(Formatting.None)), "unsupported_dependency_form");
        }

        [Test]
        public void ObjBundle_RepeatedImportKeepsExistingBundleAndSource()
        {
            Write("texture.png", Png(Color.red));
            Mtl("texture.png");
            string source = Obj();
            JObject first = Import(source);
            Assert.IsTrue((bool)first["success"], first.ToString());
            string firstPath = (string)first["data"]["asset_path"];
            byte[] firstBytes = File.ReadAllBytes(firstPath);
            JObject second = Import(source);
            Assert.IsTrue((bool)second["success"], second.ToString());
            Assert.AreEqual(outputRoot + "/Renamed_1/model.obj", (string)second["data"]["asset_path"]);
            CollectionAssert.AreEqual(firstBytes, File.ReadAllBytes(firstPath));
            CollectionAssert.AreEqual(firstBytes, File.ReadAllBytes(source));
        }

        private string PlannerBundle()
        {
            Write("Textures/texture.png", new byte[] { 1, 2, 3, 4 });
            Mtl("Textures/texture.png");
            return Obj();
        }

        [TestCase("files")]
        [TestCase("references")]
        [TestCase("bytes")]
        [TestCase("mtl")]
        [TestCase("metadata")]
        [TestCase("line")]
        [TestCase("path")]
        [TestCase("segments")]
        [TestCase("segment_bytes")]
        public void PlannerLimits_AcceptBoundaryAndRejectNextUnit(string kind)
        {
            string source = PlannerBundle();
            BundlePlan ordinary = ModelDependencyBundle.Plan(source);
            var limits = new BundleLimits();
            Action<int> set;
            int boundary;
            switch (kind)
            {
                case "files":
                    boundary = 3;
                    set = n => limits.MaxFiles = n;
                    break;
                case "references":
                    boundary = 2;
                    set = n => limits.MaxReferences = n;
                    break;
                case "bytes":
                    boundary = (int)ordinary.Bytes;
                    set = n => limits.MaxBytes = n;
                    break;
                case "mtl":
                    boundary = (int)new FileInfo(sourceRoot + "/model.mtl").Length;
                    set = n => limits.MaxMtlBytes = n;
                    break;
                case "metadata":
                    boundary = (int)new FileInfo(sourceRoot + "/model.mtl").Length;
                    set = n => limits.MaxMetadataBytes = n;
                    break;
                case "line":
                    boundary = File.ReadAllLines(source).Concat(File.ReadAllLines(sourceRoot + "/model.mtl")).Max(s => Encoding.UTF8.GetByteCount(s));
                    set = n => limits.MaxLineBytes = n;
                    break;
                case "path":
                    boundary = "Textures/texture.png".Length;
                    set = n => limits.MaxPathCharacters = n;
                    break;
                case "segments":
                    boundary = 2;
                    set = n => limits.MaxSegments = n;
                    break;
                default:
                    boundary = Encoding.UTF8.GetByteCount("texture.png");
                    set = n => limits.MaxSegmentBytes = n;
                    break;
            }
            set(boundary);
            Assert.DoesNotThrow(() => ModelDependencyBundle.Plan(source, limits));
            set(boundary - 1);
            BundleImportException error = Assert.Throws<BundleImportException>(() => ModelDependencyBundle.Plan(source, limits));
            Assert.AreEqual("dependency_limit_exceeded", (string)error.Evidence["error_code"], error.ToString());
            Assert.IsFalse(Directory.Exists(outputRoot));
        }

        [TestCase("json")]
        [TestCase("depth")]
        public void JsonLimits_RejectBoundedMetadata(string kind)
        {
            string source = Write("minimal.gltf", "{\"asset\":{\"version\":\"2.0\"}}");
            var limits = new BundleLimits();
            if (kind == "json")
                limits.MaxJsonBytes = (int)new FileInfo(source).Length;
            else
                limits.MaxJsonDepth = 2;
            Assert.DoesNotThrow(() => ModelDependencyBundle.Plan(source, limits));
            if (kind == "json")
                limits.MaxJsonBytes--;
            else
                limits.MaxJsonDepth--;
            BundleImportException error = Assert.Throws<BundleImportException>(() => ModelDependencyBundle.Plan(source, limits));
            Assert.AreEqual("dependency_limit_exceeded", (string)error.Evidence["error_code"]);
            Assert.IsFalse(Directory.Exists(outputRoot));
        }

        [Test]
        public void EmbeddedData_IsCountedWithoutCreatingSidecarFiles()
        {
            JObject document = TriangleDocument("data:application/octet-stream;base64,AQIDBA==", "data:image/png;base64,AQIDBA==");
            string source = Write("embedded.gltf", document.ToString(Formatting.None));
            var limits = new BundleLimits { MaxBytes = new FileInfo(source).Length + 8 };
            BundlePlan plan = ModelDependencyBundle.Plan(source, limits);
            Assert.AreEqual(1, plan.Entries.Count);
            Assert.AreEqual(8, plan.EmbeddedBytes);
            limits.MaxBytes--;
            Assert.AreEqual(
                "dependency_limit_exceeded",
                (string)Assert.Throws<BundleImportException>(() => ModelDependencyBundle.Plan(source, limits)).Evidence["error_code"]
            );
        }

        [TestCase("data:image/png,AQIDBA==")]
        [TestCase("data:image/svg+xml;base64,AQIDBA==")]
        [TestCase("data:image/png;base64,AA=A")]
        [TestCase("data:image/png;base64,====")]
        public void UnsupportedEmbeddedData_IsNotSilentlyAccepted(string image)
        {
            string source = Write("embedded.gltf", TriangleDocument("data:application/octet-stream;base64,AQIDBA==", image).ToString(Formatting.None));
            AssertPreflightFailure(source, "unsupported_dependency_form");
        }

        [TestCase("{\"asset\":{\"version\":\"2.0\"},\"buffers\":{},\"images\":[]}")]
        [TestCase("{\"asset\":{\"version\":\"2.0\"},\"asset\":{\"version\":\"2.0\"}}")]
        [TestCase("{\"asset\":{\"version\":\"2.0\"}} {}")]
        public void InvalidJsonShapeOrDuplicateProperties_FailBeforeStaging(string document)
        {
            AssertPreflightFailure(Write("invalid.gltf", document), "unsupported_dependency_form");
        }

        [Test]
        public void RepeatedReferences_AreDeduplicatedWithoutRewritingBytes()
        {
            string source = PlannerBundle();
            File.AppendAllText(source, "mtllib model.mtl\n");
            File.AppendAllText(sourceRoot + "/model.mtl", "map_Kd Textures/texture.png\n");
            BundlePlan plan = ModelDependencyBundle.Plan(source);
            Assert.AreEqual(3, plan.Entries.Count);
            Assert.AreEqual(4, plan.References);
            BundleStageResult staged = ModelDependencyBundle.Stage(plan, outputRoot, "copy");
            foreach (BundleEntry entry in plan.Entries)
                CollectionAssert.AreEqual(File.ReadAllBytes(entry.AbsolutePath), File.ReadAllBytes(staged.BundleRoot + "/" + entry.RelativePath));
        }

        [Test]
        public void PercentEncodedUnicodeReference_PreservesOriginalDocumentAndFilename()
        {
            Write("geometry.bin", TriangleBytes());
            string image = "\uD14D\uC2A4\uCC98.png";
            Write(image, Png(Color.red));
            string source = Write("unicode.gltf", TriangleDocument("geometry.bin", Uri.EscapeDataString(image)).ToString(Formatting.None));
            BundleStageResult staged = ModelDependencyBundle.Stage(ModelDependencyBundle.Plan(source), outputRoot, "copy");
            CollectionAssert.AreEqual(File.ReadAllBytes(source), File.ReadAllBytes(staged.MainAssetPath));
            CollectionAssert.AreEqual(File.ReadAllBytes(sourceRoot + "/" + image), File.ReadAllBytes(staged.BundleRoot + "/" + image));
        }

        [TestCase("Texture.png", "texture.png")]
        [TestCase("Textures/../texture.png", "texture.png")]
        [TestCase("CON.png", "texture.png")]
        [TestCase("texture.png.", "texture.png")]
        [TestCase("texture%252epng", "texture.png")]
        [TestCase("texture%FF.png", "texture.png")]
        [TestCase("texture%2Fother.png", "texture.png")]
        public void AliasedOrUnsafeDependencyNames_FailExplicitly(string first, string second)
        {
            Write("geometry.bin", TriangleBytes());
            Write(
                first.Contains("%")
                || first.Contains("..")
                || first.Contains("/")
                || first.StartsWith("CON", StringComparison.Ordinal)
                || first.EndsWith(".", StringComparison.Ordinal)
                    ? second
                    : first,
                Png(Color.red)
            );
            JObject document = TriangleDocument("geometry.bin", first);
            ((JArray)document["images"]).Add(new JObject { ["uri"] = second });
            AssertPreflightFailure(Write("unsafe.gltf", document.ToString(Formatting.None)), "unsafe_dependency_path");
        }

        [TestCase("file")]
        [TestCase("folder")]
        [TestCase("meta")]
        public void ExistingDestinationIsPreservedAndFreshBundleSelected(string kind)
        {
            string source = PlannerBundle();
            Directory.CreateDirectory(outputRoot);
            AssetDatabase.ImportAsset(outputRoot, ImportAssetOptions.ForceSynchronousImport);
            string existing = outputRoot + "/copy";
            if (kind == "folder")
            {
                Directory.CreateDirectory(existing);
                existing += "/keep.txt";
            }
            else if (kind == "meta")
                existing += ".meta";
            string content = kind == "meta" ? "fileFormatVersion: 2\nguid: " + Guid.NewGuid().ToString("N") + "\n" : "owned existing content";
            File.WriteAllText(existing, content);
            BundleStageResult staged = ModelDependencyBundle.Stage(ModelDependencyBundle.Plan(source), outputRoot, "copy");
            Assert.AreEqual(outputRoot + "/copy_1/model.obj", staged.MainAssetPath);
            Assert.AreEqual(content, File.ReadAllText(existing));
        }

        [Test]
        public void CollisionAttemptLimit_FailsWithoutTouchingExistingOutputs()
        {
            string source = PlannerBundle();
            Directory.CreateDirectory(outputRoot);
            File.WriteAllText(outputRoot + "/copy", "first");
            File.WriteAllText(outputRoot + "/copy_1", "second");
            var plan = ModelDependencyBundle.Plan(source, new BundleLimits { MaxNameAttempts = 2 });
            BundleImportException error = Assert.Throws<BundleImportException>(() => ModelDependencyBundle.Stage(plan, outputRoot, "copy"));
            Assert.AreEqual("dependency_limit_exceeded", (string)error.Evidence["error_code"]);
            Assert.AreEqual("not_staged", (string)error.Evidence["stage"]);
            Assert.AreEqual("first", File.ReadAllText(outputRoot + "/copy"));
            Assert.AreEqual("second", File.ReadAllText(outputRoot + "/copy_1"));
            Assert.IsFalse(Directory.Exists(outputRoot + "/copy_2"));
        }

        [Test]
        public void CopyFailure_RetainsOnlyActualPartialEvidenceAndOriginalFiles()
        {
            string source = PlannerBundle();
            BundlePlan plan = ModelDependencyBundle.Plan(source);
            byte[][] original = plan.Entries.Select(e => File.ReadAllBytes(e.AbsolutePath)).ToArray();
            BundleImportException error = Assert.Throws<BundleImportException>(() =>
                ModelDependencyBundle.Stage(
                    plan,
                    outputRoot,
                    "copy",
                    (ordinal, path) =>
                    {
                        if (ordinal == 1)
                            throw new IOException("owned injected copy failure");
                    }
                )
            );
            Assert.AreEqual("dependency_stage_failed", (string)error.Evidence["error_code"]);
            Assert.AreEqual("partial", (string)error.Evidence["stage"]);
            Assert.AreEqual(1, (int)error.Evidence["staged_file_count"]);
            Assert.AreEqual(new FileInfo(sourceRoot + "/model.mtl").Length, (long)error.Evidence["staged_bytes"]);
            Assert.IsTrue(File.Exists(outputRoot + "/copy/model.mtl"));
            Assert.IsFalse(File.Exists(outputRoot + "/copy/model.obj"));
            Assert.IsNull(error.Evidence["main_asset_path"]);
            for (int i = 0; i < plan.Entries.Count; i++)
                CollectionAssert.AreEqual(original[i], File.ReadAllBytes(plan.Entries[i].AbsolutePath));
        }

        [Test]
        public void SameLengthMetadataChange_IsDetectedBeforeExplicitModelImport()
        {
            string source = PlannerBundle();
            BundlePlan plan = ModelDependencyBundle.Plan(source);
            string library = sourceRoot + "/model.mtl";
            string changed = File.ReadAllText(library).Replace("Kd 1 1 1", "Kd 0 0 0");
            File.WriteAllText(library, changed, new UTF8Encoding(false));
            BundleImportException error = Assert.Throws<BundleImportException>(() => ModelDependencyBundle.Stage(plan, outputRoot, "copy"));
            Assert.AreEqual("dependency_source_changed", (string)error.Evidence["error_code"]);
            Assert.AreEqual("partial", (string)error.Evidence["stage"]);
            Assert.AreEqual(0, (int)error.Evidence["staged_file_count"]);
            Assert.AreEqual(changed, File.ReadAllText(library), "a rejected operation must not roll back the caller's source change");
            Assert.IsFalse(File.Exists(outputRoot + "/copy/model.obj"));
        }

        [Test]
        public void CreateNewCollision_DoesNotOverwriteInterveningFile()
        {
            BundlePlan plan = ModelDependencyBundle.Plan(PlannerBundle());
            string collision = null;
            BundleImportException error = Assert.Throws<BundleImportException>(() =>
                ModelDependencyBundle.Stage(
                    plan,
                    outputRoot,
                    "copy",
                    (ordinal, path) =>
                    {
                        if (ordinal == 0)
                        {
                            collision = path;
                            File.WriteAllText(path, "intervening owned file");
                        }
                    }
                )
            );
            Assert.AreEqual("dependency_stage_failed", (string)error.Evidence["error_code"]);
            Assert.AreEqual(0, (int)error.Evidence["staged_file_count"]);
            Assert.AreEqual(0, (long)error.Evidence["staged_bytes"]);
            Assert.AreEqual("intervening owned file", File.ReadAllText(collision));
        }

        [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
        [return: MarshalAs(UnmanagedType.I1)]
        private static extern bool CreateSymbolicLinkW(string link, string target, int flags);

        [DllImport("libc", SetLastError = true)]
        private static extern int symlink(string target, string link);

        [TestCase(false, false)]
        [TestCase(false, true)]
        [TestCase(true, false)]
        [TestCase(true, true)]
        public void LinkedDependency_IsRejectedWithoutFollowingOwnedTarget(bool directory, bool dangling)
        {
            string target = Path.GetFullPath(root + "/OwnedTarget");
            Directory.CreateDirectory(target);
            File.WriteAllBytes(target + "/texture.png", new byte[] { 1, 2, 3 });
            string link = Path.GetFullPath(sourceRoot + (directory ? "/Linked" : "/texture.png"));
            string linkedTarget = directory ? target : target + "/texture.png";
            bool created =
                Application.platform == RuntimePlatform.WindowsEditor
                    ? CreateSymbolicLinkW(link, linkedTarget, (directory ? 1 : 0) | 2)
                    : symlink(linkedTarget, link) == 0;
            if (!created)
                Assert.Ignore("Symlink creation unavailable: " + Marshal.GetLastWin32Error());
            try
            {
                if (dangling)
                {
                    File.Delete(target + "/texture.png");
                    if (directory)
                        Directory.Delete(target);
                }
                Mtl(directory ? "Linked/texture.png" : "texture.png");
                AssertPreflightFailure(Obj(), "unsafe_dependency_path");
            }
            finally
            {
                // Remove only the link before the ordinary owned-root teardown.
                if (directory)
                    Directory.Delete(link);
                else
                    File.Delete(link);
            }
        }

        [Test]
        public void DirectorySpellingAlias_IsRejectedBeforeAnyStaging()
        {
            Write("geometry.bin", TriangleBytes());
            Write("Textures/first.png", Png(Color.red));
            Write("textures/second.png", Png(Color.blue));
            JObject document = TriangleDocument("geometry.bin", "Textures/first.png");
            ((JArray)document["images"]).Add(new JObject { ["uri"] = "textures/second.png" });
            AssertPreflightFailure(Write("alias.gltf", document.ToString(Formatting.None)), "unsafe_dependency_path");
        }

        [Test]
        public void InterveningMetadata_IsNotAdoptedByNewDependencyFile()
        {
            BundlePlan plan = ModelDependencyBundle.Plan(PlannerBundle());
            string metadata = null;
            string existing = "fileFormatVersion: 2\nguid: " + Guid.NewGuid().ToString("N") + "\n";
            BundleImportException error = Assert.Throws<BundleImportException>(() =>
                ModelDependencyBundle.Stage(
                    plan,
                    outputRoot,
                    "copy",
                    (ordinal, path) =>
                    {
                        if (ordinal == 0)
                        {
                            metadata = path + ".meta";
                            File.WriteAllText(metadata, existing);
                        }
                    }
                )
            );
            Assert.AreEqual("dependency_stage_failed", (string)error.Evidence["error_code"]);
            Assert.AreEqual(0, (int)error.Evidence["staged_file_count"]);
            Assert.AreEqual(0, (long)error.Evidence["staged_bytes"]);
            Assert.AreEqual(existing, File.ReadAllText(metadata));
            Assert.IsFalse(File.Exists(metadata.Substring(0, metadata.Length - 5)), "do not attach copied bytes to an intervening GUID");
        }

        private void AssertPreflightFailure(string source, string code)
        {
            byte[] original = File.ReadAllBytes(source);
            JObject response = Import(source);
            Assert.IsFalse((bool)response["success"], response.ToString());
            Assert.AreEqual(code, (string)response["data"]?["dependency_bundle"]?["error_code"], response.ToString());
            Assert.AreEqual("not_staged", (string)response["data"]["dependency_bundle"]["stage"]);
            Assert.IsFalse(Directory.Exists(outputRoot));
            Assert.IsFalse(File.Exists(outputRoot + ".meta"));
            Assert.IsNull(response["data"]["asset_path"]);
            CollectionAssert.AreEqual(original, File.ReadAllBytes(source));
        }
    }
}
