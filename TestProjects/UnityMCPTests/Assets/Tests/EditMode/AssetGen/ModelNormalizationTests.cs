using System;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Runtime.InteropServices;
using MCPForUnity.Editor.Services.AssetGen;
using MCPForUnity.Editor.Services.AssetGen.Import;
using MCPForUnity.Editor.Tools.Blender;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;
using UnityEngine.SceneManagement;

namespace MCPForUnityTests.Editor.AssetGen
{
    // Owns only uniquely named Assets fixtures and preview scenes; never changes EditorPrefs or connects to Blender.
    public class ModelNormalizationTests
    {
        private string root;
        private const BindingFlags InternalStatic = BindingFlags.NonPublic | BindingFlags.Public | BindingFlags.Static;

        [SetUp]
        public void SetUp()
        {
            root = "Assets/__ModelNormalization_" + Guid.NewGuid().ToString("N");
            Assert.IsNotEmpty(AssetDatabase.CreateFolder("Assets", Path.GetFileName(root)));
        }

        [TearDown]
        public void TearDown()
        {
            Assert.IsTrue(root.StartsWith("Assets/__ModelNormalization_", StringComparison.Ordinal));
            AssetDatabase.DeleteAsset(root);
        }

        private string Source(string contents = "hierarchy")
        {
            string path = root + "/model.mcpnormalizationfixture";
            File.WriteAllText(path, contents);
            AssetDatabase.ImportAsset(path, ImportAssetOptions.ForceSynchronousImport | ImportAssetOptions.ForceUpdate);
            Assert.IsNotNull(AssetDatabase.LoadAssetAtPath<GameObject>(path), "The real fixture importer must run before normalization assertions.");
            return path;
        }

        private static AssetGenJob Import(string path, float target, bool enabled)
        {
            var job = new AssetGenJob { TargetSize = target, State = AssetGenJobState.Importing };
            MethodInfo entry = typeof(ModelImportPipeline).GetMethod(
                "ImportInto",
                InternalStatic,
                null,
                new[] { typeof(AssetGenJob), typeof(string), typeof(bool) },
                null
            );
            // Baseline still exercises the real production pipeline, which ignores scripted-importer target size.
            return entry == null ? ModelImportPipeline.ImportInto(job, path) : (AssetGenJob)entry.Invoke(null, new object[] { job, path, enabled });
        }

        private static Bounds RendererBounds(GameObject model)
        {
            Renderer[] renderers = model.GetComponentsInChildren<Renderer>(true);
            Assert.IsNotEmpty(renderers);
            Bounds bounds = renderers[0].bounds;
            foreach (Renderer renderer in renderers.Skip(1))
                bounds.Encapsulate(renderer.bounds);
            return bounds;
        }

        private static void AssertSize(Vector3 expected, Vector3 actual)
        {
            Assert.That(actual.x, Is.EqualTo(expected.x).Within(.002f));
            Assert.That(actual.y, Is.EqualTo(expected.y).Within(.002f));
            Assert.That(actual.z, Is.EqualTo(expected.z).Within(.002f));
        }

        [Test]
        public void HierarchyMeasurement_AccountsForOffsetRotationAndNonuniformScale()
        {
            string source = Source();
            GameObject model = AssetDatabase.LoadAssetAtPath<GameObject>(source);
            Type boundsType = typeof(ModelImportPipeline).Assembly.GetType("MCPForUnity.Editor.Services.AssetGen.Import.ModelHierarchyBounds");
            if (boundsType == null)
            {
                // Genuine behavioral RED against the old private implementation, not a missing API compilation error.
                MethodInfo legacy = typeof(ModelImportPipeline).GetMethod("ComputeMaxDimension", InternalStatic);
                Assert.IsNotNull(legacy);
                Assert.That((float)legacy.Invoke(null, new object[] { source }), Is.EqualTo(8f).Within(.002f));
                return;
            }
            AssertSize(new Vector3(8f, 1f, 1f), Measure(model).size);
        }

        [TestCase(true)]
        [TestCase(false)]
        public void ScriptedImporter_ProductionPipeline_ReturnsVerifiedTargetOrOriginal(bool enabled)
        {
            string source = Source();
            byte[] bytes = File.ReadAllBytes(source);
            string sourceGuid = AssetDatabase.AssetPathToGUID(source);
            GameObject original = AssetDatabase.LoadAssetAtPath<GameObject>(source);
            Mesh originalMesh = original.GetComponentInChildren<MeshFilter>().sharedMesh;
            Material originalMaterial = original.GetComponentInChildren<MeshRenderer>().sharedMaterial;
            AssetGenJob result = Import(source, 2f, enabled);
            Assert.AreEqual(AssetGenJobState.Done, result.State, result.Error);
            Assert.AreEqual("usable", result.ModelImportStage);
            Assert.IsNotEmpty(result.AssetGuid);
            Assert.AreEqual(AssetDatabase.AssetPathToGUID(result.AssetPath), result.AssetGuid);
            Assert.AreEqual(enabled, result.AssetPath.EndsWith(".prefab", StringComparison.Ordinal));
            if (!enabled)
                Assert.AreEqual(source, result.AssetPath);
            GameObject output = AssetDatabase.LoadAssetAtPath<GameObject>(result.AssetPath);
            if (enabled)
            {
                Assert.AreEqual(PrefabAssetType.Variant, PrefabUtility.GetPrefabAssetType(output));
                Assert.AreEqual(source, AssetDatabase.GetAssetPath(PrefabUtility.GetCorrespondingObjectFromOriginalSource(output)));
            }
            Assert.AreEqual(originalMesh, output.GetComponentInChildren<MeshFilter>().sharedMesh);
            Assert.AreEqual(originalMaterial, output.GetComponentInChildren<MeshRenderer>().sharedMaterial);
            Scene preview = EditorSceneManager.NewPreviewScene();
            try
            {
                var placed = (GameObject)PrefabUtility.InstantiatePrefab(output, preview);
                AssertSize(enabled ? new Vector3(2f, .25f, .25f) : new Vector3(8f, 1f, 1f), RendererBounds(placed).size);
            }
            finally
            {
                EditorSceneManager.ClosePreviewScene(preview);
            }
            CollectionAssert.AreEqual(bytes, File.ReadAllBytes(source));
            Assert.AreEqual(sourceGuid, AssetDatabase.AssetPathToGUID(source));
            AssertSize(new Vector3(8f, 1f, 1f), MeasureOriginalOrLegacy(source));
        }

        private static Vector3 MeasureOriginalOrLegacy(string source)
        {
            Scene preview = EditorSceneManager.NewPreviewScene();
            try
            {
                var go = (GameObject)PrefabUtility.InstantiatePrefab(AssetDatabase.LoadAssetAtPath<GameObject>(source), preview);
                return RendererBounds(go).size;
            }
            finally
            {
                EditorSceneManager.ClosePreviewScene(preview);
            }
        }

        [TestCase(0f, true)]
        [TestCase(-2f, true)]
        [TestCase(0f, false)]
        [TestCase(-2f, false)]
        public void NonpositiveTarget_PreservesImportedAsset(float target, bool enabled)
        {
            string source = Source();
            AssetGenJob result = Import(source, target, enabled);
            Assert.AreEqual(AssetGenJobState.Done, result.State, result.Error);
            Assert.AreEqual(source, result.AssetPath);
            AssertSize(new Vector3(8f, 1f, 1f), MeasureOriginalOrLegacy(source));
            Assert.IsEmpty(Directory.GetFiles(root, "*.prefab", SearchOption.AllDirectories));
        }

        [TestCase(float.NaN)]
        [TestCase(float.PositiveInfinity)]
        [TestCase(float.NegativeInfinity)]
        public void NonfiniteTarget_FailsWithRetainedSourceEvidence(float target)
        {
            string source = Source();
            AssetGenJob result = Import(source, target, true);
            Assert.AreEqual(AssetGenJobState.Failed, result.State);
            Assert.AreEqual("invalid_target_size", result.ModelImportErrorCode);
            Assert.AreEqual(source, result.AssetPath);
            Assert.AreEqual("registered", result.ModelImportStage);
        }

        [TestCase(true)]
        [TestCase(false)]
        public void RealObj_NormalizesFinalAssetWithoutChangingFileScaleOrDrift(bool fileScale)
        {
            string source = root + "/offset.obj";
            const string obj = "o Left\nv -4 0 0\nv -2 0 0\nv -4 1 1\nf 1 2 3\no Right\nv 2 0 0\nv 4 0 0\nv 4 1 1\nf 4 5 6\n";
            File.WriteAllText(source, obj);
            AssetDatabase.ImportAsset(source, ImportAssetOptions.ForceSynchronousImport);
            var importer = (ModelImporter)AssetImporter.GetAtPath(source);
            importer.useFileScale = fileScale;
            importer.globalScale = 3f;
            importer.SaveAndReimport();
            AssetGenJob result = Import(source, 2f, true);
            Assert.AreEqual(AssetGenJobState.Done, result.State, result.Error);
            Assert.AreEqual(source, result.AssetPath);
            Assert.That(MeasureOriginalOrLegacy(source).x, Is.EqualTo(2f).Within(.002f));
            importer = (ModelImporter)AssetImporter.GetAtPath(source);
            Assert.AreEqual(fileScale, importer.useFileScale);
            float scale = importer.globalScale;
            result = Import(source, 2f, true);
            Assert.AreEqual(AssetGenJobState.Done, result.State, result.Error);
            Assert.That(((ModelImporter)AssetImporter.GetAtPath(source)).globalScale, Is.EqualTo(scale).Within(1e-5f));
            Assert.That(MeasureOriginalOrLegacy(source).x, Is.EqualTo(2f).Within(.002f));
            Assert.AreEqual(obj, File.ReadAllText(source));
        }

        [Test]
        public void Blender_DefaultClipLookup_UsesEmbeddedOriginalSubassetThroughVariant()
        {
            string source = Source();
            AnimationClip clip = AssetDatabase.LoadAllAssetRepresentationsAtPath(source).OfType<AnimationClip>().Single();
            Assert.IsTrue(AssetDatabase.TryGetGUIDAndLocalFileIdentifier(clip, out string originalGuid, out long originalLocalId));
            string originalName = clip.name;
            byte[] originalBytes = File.ReadAllBytes(source);
            var originalBindings = AnimationUtility.GetCurveBindings(clip).Select(b => b.path + ":" + b.propertyName).ToArray();
            AssetGenJob result = Import(source, 2f, true);
            Assert.AreEqual(AssetGenJobState.Done, result.State, result.Error);
            StringAssert.EndsWith(".prefab", result.AssetPath);
            Scene preview = EditorSceneManager.NewPreviewScene();
            try
            {
                var placed = (GameObject)PrefabUtility.InstantiatePrefab(AssetDatabase.LoadAssetAtPath<GameObject>(result.AssetPath), preview);
                // Default production lookup, with no supplied clip array or standalone .anim substitution.
                JObject animation = BlenderBridgeTool.SetupAnimation(placed, result.AssetPath, "OwnedClipLookup", true);
                Assert.IsNotNull(animation);
                CollectionAssert.Contains(animation["clips"].Values<string>().ToArray(), originalName);
                AnimationClip boundClip = placed.GetComponentInChildren<Animator>().runtimeAnimatorController.animationClips.Single();
                // ImportInto reimports the source; persistent subasset identity must survive even if the managed wrapper changes.
                Assert.IsTrue(AssetDatabase.TryGetGUIDAndLocalFileIdentifier(boundClip, out string boundGuid, out long boundLocalId));
                Assert.AreEqual(source, AssetDatabase.GetAssetPath(boundClip));
                Assert.AreEqual(originalGuid, boundGuid);
                Assert.AreEqual(originalLocalId, boundLocalId);
                CollectionAssert.AreEqual(originalBindings, AnimationUtility.GetCurveBindings(boundClip).Select(b => b.path + ":" + b.propertyName).ToArray());
                CollectionAssert.AreEqual(originalBytes, File.ReadAllBytes(source));
            }
            finally
            {
                EditorSceneManager.ClosePreviewScene(preview);
            }
        }

        [Test]
        public void Blender_ProductionPlacement_DoesNotScaleNormalizedAssetAgain()
        {
            string source = Source();
            AssetGenJob result = Import(source, 2f, true);
            MethodInfo place = typeof(BlenderBridgeTool).GetMethod("PlaceImportedModel", InternalStatic);
            Assert.IsNotNull(place);
            Scene preview = EditorSceneManager.NewPreviewScene();
            try
            {
                var go = (GameObject)place.Invoke(null, new object[] { result.AssetPath, "OwnedPlaced", new Vector3(10f, 20f, 30f), preview });
                Assert.AreEqual(new Vector3(10f, 20f, 30f), go.transform.position);
                AssertSize(new Vector3(2f, .25f, .25f), RendererBounds(go).size);
                Assert.That(go.transform.localScale.x, Is.EqualTo(.25f).Within(.001f));
            }
            finally
            {
                EditorSceneManager.ClosePreviewScene(preview);
            }
        }

        [Test]
        public void Blender_ImportFailureBoundary_PreservesCompleteRetainedEvidence()
        {
            var response = new JObject
            {
                ["success"] = false,
                ["error"] = "normalization failed",
                ["data"] = new JObject
                {
                    ["asset_path"] = root + "/retained.prefab",
                    ["asset_guid"] = "owned-guid",
                    ["model_import_stage"] = "registered",
                    ["model_import_error_code"] = "target_size_not_achieved",
                    ["dependency_bundle"] = new JObject { ["stage"] = "staged", ["staged_file_count"] = 3 },
                },
            };
            MethodInfo map = typeof(BlenderBridgeTool).GetMethod("MapImportFailure", InternalStatic);
            Assert.IsNotNull(map);
            JObject actual = JObject.FromObject(map.Invoke(null, new object[] { response }));
            Assert.IsFalse(actual.Value<bool>("success"));
            StringAssert.Contains("normalization failed", actual.Value<string>("error"));
            Assert.IsTrue(JToken.DeepEquals(response["data"], actual["data"]));
        }

        [TestCase(2, 256, 10000, "normalization_bounds_limit")]
        [TestCase(10000, 1, 10000, "normalization_bounds_limit")]
        [TestCase(10000, 256, 1, "normalization_bounds_limit")]
        public void BoundsLimits_RejectPartialHierarchy(int nodes, int depth, int meshes, string expected)
        {
            string source = Source();
            object[] args = MeasureArguments(AssetDatabase.LoadAssetAtPath<GameObject>(source), nodes, depth, meshes);
            MethodInfo measure = BoundsMethod();
            Assert.IsFalse((bool)measure.Invoke(null, args));
            Assert.AreEqual(expected, args[3]);
        }

        [Test]
        public void Bounds_HandlesNestedMirroredInactiveAndUnreadableMeshes()
        {
            var go = new GameObject("OwnedBoundsRoot");
            Mesh mesh = NormalizationFixtureImporter.CreateCube();
            try
            {
                go.transform.localScale = new Vector3(2f, 3f, .5f);
                var parent = new GameObject("OwnedParent");
                parent.transform.SetParent(go.transform, false);
                parent.transform.localRotation = Quaternion.Euler(0f, 0f, 30f);
                parent.transform.localScale = new Vector3(-2f, .5f, 1f);
                var child = new GameObject("OwnedInactiveChild");
                child.transform.SetParent(parent.transform, false);
                child.transform.localPosition = new Vector3(3f, 1f, -2f);
                child.AddComponent<MeshFilter>().sharedMesh = mesh;
                child.SetActive(false);
                Vector3[] vertices = mesh.vertices;
                Bounds expected = new Bounds(child.transform.TransformPoint(vertices[0]), Vector3.zero);
                foreach (Vector3 vertex in vertices.Skip(1))
                    expected.Encapsulate(child.transform.TransformPoint(vertex));
                mesh.UploadMeshData(true);
                Bounds actual = Measure(go);
                AssertSize(expected.size, actual.size);
                AssertSize(expected.center, actual.center);
            }
            finally
            {
                UnityEngine.Object.DestroyImmediate(go);
                UnityEngine.Object.DestroyImmediate(mesh);
            }
        }

        [Test]
        public void MissingGltfImporter_TargetTwoRetainsOriginalAndCreatesNoVariant()
        {
            string source = root + "/owned.glb";
            File.WriteAllBytes(source, ModelImportPipelineTests.ContainerOnlyGlb());
            AssetGenJob result = Import(source, 2f, true);
            Assert.AreEqual(AssetGenJobState.Failed, result.State);
            Assert.AreEqual("missing_gltf_importer", result.ModelImportErrorCode);
            Assert.AreEqual(source, result.AssetPath);
            Assert.AreEqual("registered", result.ModelImportStage);
            Assert.IsEmpty(Directory.GetFiles(root, "*.prefab", SearchOption.AllDirectories));
        }

        [Test]
        public void Bounds_ExactBudgetsIncludeEveryContribution()
        {
            string source = Source();
            object[] args = MeasureArguments(AssetDatabase.LoadAssetAtPath<GameObject>(source), 3, 2, 2);
            Assert.IsTrue((bool)BoundsMethod().Invoke(null, args), args[3]?.ToString());
            AssertSize(new Vector3(8f, 1f, 1f), ((Bounds)args[2]).size);
        }

        [Test]
        public void Bounds_NonfiniteMatrixFailsWithoutAClaimedPartialBound()
        {
            string source = Source();
            object[] args = MeasureArguments(AssetDatabase.LoadAssetAtPath<GameObject>(source));
            Matrix4x4 matrix = Matrix4x4.identity;
            matrix[0, 0] = float.NaN;
            args[1] = matrix;
            Assert.IsFalse((bool)BoundsMethod().Invoke(null, args));
            Assert.AreEqual("normalization_invalid_bounds", args[3]);
            Assert.AreEqual(default(Bounds), (Bounds)args[2]);
        }

        [Test]
        public void Bounds_EmptyHierarchyFails()
        {
            var go = new GameObject("OwnedEmptyBounds");
            try
            {
                object[] args = MeasureArguments(go);
                Assert.IsFalse((bool)BoundsMethod().Invoke(null, args));
                Assert.AreEqual("normalization_invalid_bounds", args[3]);
            }
            finally
            {
                UnityEngine.Object.DestroyImmediate(go);
            }
        }

        [Test]
        public void Bounds_SkinnedResourceAndCollapsedAxisRemainMeasurable()
        {
            var go = new GameObject("OwnedSkinnedBounds");
            Mesh mesh = NormalizationFixtureImporter.CreateCube();
            try
            {
                go.transform.localScale = new Vector3(0f, -2f, 3f);
                go.AddComponent<SkinnedMeshRenderer>().sharedMesh = mesh;
                go.SetActive(false);
                mesh.UploadMeshData(true);
                AssertSize(new Vector3(0f, 2f, 3f), Measure(go).size);
            }
            finally
            {
                UnityEngine.Object.DestroyImmediate(go);
                UnityEngine.Object.DestroyImmediate(mesh);
            }
        }

        [TestCase(true)]
        [TestCase(false)]
        public void DegenerateIndexedGeometry_IsNotSilentlyNormalized(bool enabled)
        {
            string source = Source("degenerate");
            AssetGenJob result = Import(source, 2f, enabled);
            Assert.AreEqual(enabled ? AssetGenJobState.Failed : AssetGenJobState.Done, result.State);
            if (enabled)
            {
                Assert.AreEqual("normalization_invalid_bounds", result.ModelImportErrorCode);
                Assert.AreEqual("registered", result.ModelImportStage);
            }
            Assert.AreEqual(source, result.AssetPath);
            Assert.IsEmpty(Directory.GetFiles(root, "*.prefab", SearchOption.AllDirectories));
        }

        [Test]
        public void Variant_SavedFailureRetainsOwnedOutputAndClosesPreviewScene()
        {
            string source = Source();
            byte[] sourceBytes = File.ReadAllBytes(source);
            int previews = EditorSceneManager.previewSceneCount;
            bool sceneDirty = SceneManager.GetActiveScene().isDirty;
            Action<string> failAfterSave = _ => throw new IOException("owned failure after actual variant save");
            TargetInvocationException failure = Assert.Throws<TargetInvocationException>(() =>
                VariantMethod().Invoke(null, new object[] { source, .25f, 1000, failAfterSave })
            );
            Exception error = failure.InnerException;
            string saved = (string)error.GetType().GetProperty("SavedAssetPath", BindingFlags.Instance | BindingFlags.NonPublic).GetValue(error);
            Assert.IsNotNull(saved);
            Assert.IsTrue(File.Exists(saved));
            Assert.IsNotEmpty(AssetDatabase.AssetPathToGUID(saved));
            StringAssert.StartsWith(root + "/", saved);
            Assert.AreEqual(previews, EditorSceneManager.previewSceneCount);
            Assert.AreEqual(sceneDirty, SceneManager.GetActiveScene().isDirty);
            CollectionAssert.AreEqual(sourceBytes, File.ReadAllBytes(source));
        }

        [Test]
        public void Variant_CollisionsAndAttemptLimitNeverOverwriteExistingOutput()
        {
            string source = Source();
            string occupied = root + "/model_Normalized";
            Assert.IsNotEmpty(AssetDatabase.CreateFolder(root, "model_Normalized"));
            string sentinel = occupied + "/preserve.txt";
            File.WriteAllText(sentinel, "owned existing output");
            string guid = AssetDatabase.AssetPathToGUID(occupied);
            TargetInvocationException failure = Assert.Throws<TargetInvocationException>(() =>
                VariantMethod().Invoke(null, new object[] { source, .25f, 1, null })
            );
            Assert.AreEqual(
                "normalization_output_conflict",
                failure.InnerException.GetType().GetProperty("ErrorCode", BindingFlags.Instance | BindingFlags.NonPublic).GetValue(failure.InnerException)
            );
            Assert.AreEqual(guid, AssetDatabase.AssetPathToGUID(occupied));
            Assert.AreEqual("owned existing output", File.ReadAllText(sentinel));
            string output = (string)VariantMethod().Invoke(null, new object[] { source, .25f, 2, null });
            Assert.AreEqual(root + "/model_Normalized_1/model.prefab", output);
            Assert.AreEqual("owned existing output", File.ReadAllText(sentinel));
        }

        [Test]
        public void Variant_RepeatedImportsKeepFreshOutputsAndOriginalIdentity()
        {
            string source = Source();
            string guid = AssetDatabase.AssetPathToGUID(source);
            AssetGenJob first = Import(source, 2f, true);
            AssetGenJob second = Import(source, 2f, true);
            Assert.AreEqual(AssetGenJobState.Done, first.State, first.Error);
            Assert.AreEqual(AssetGenJobState.Done, second.State, second.Error);
            Assert.AreNotEqual(first.AssetPath, second.AssetPath);
            Assert.AreNotEqual(first.AssetGuid, second.AssetGuid);
            Assert.AreEqual(guid, AssetDatabase.AssetPathToGUID(source));
            AssertSize(new Vector3(2f, .25f, .25f), MeasureOriginalOrLegacy(first.AssetPath));
            AssertSize(new Vector3(2f, .25f, .25f), MeasureOriginalOrLegacy(second.AssetPath));
        }

        [Test]
        public void Variant_InvalidFactorFailsBeforeCreatingOutput()
        {
            string source = Source();
            TargetInvocationException failure = Assert.Throws<TargetInvocationException>(() =>
                VariantMethod().Invoke(null, new object[] { source, float.PositiveInfinity, 1000, null })
            );
            Assert.AreEqual(
                "target_size_out_of_range",
                failure.InnerException.GetType().GetProperty("ErrorCode", BindingFlags.Instance | BindingFlags.NonPublic).GetValue(failure.InnerException)
            );
            Assert.IsEmpty(Directory.GetDirectories(root));
        }

        [Test]
        public void TargetAlreadyReached_DoesNotCreateUnnecessaryVariant()
        {
            string source = Source();
            AssetGenJob result = Import(source, 8f, true);
            Assert.AreEqual(AssetGenJobState.Done, result.State, result.Error);
            Assert.AreEqual(source, result.AssetPath);
            Assert.IsEmpty(Directory.GetFiles(root, "*.prefab", SearchOption.AllDirectories));
        }

        [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
        [return: MarshalAs(UnmanagedType.I1)]
        private static extern bool CreateSymbolicLinkW(string link, string target, int flags);

        [DllImport("libc", SetLastError = true)]
        private static extern int symlink(string target, string link);

        [Test]
        public void Variant_DanglingMetadataLinkRejectsBeforeCreatingFolderOrTarget()
        {
            string source = Source();
            string link = Path.GetFullPath(root + "/model_Normalized.meta");
            string target = Path.GetFullPath(root + "/owned-missing-meta-target");
            bool created = false;
            AssetDatabase.DisallowAutoRefresh();
            try
            {
                created = Application.platform == RuntimePlatform.WindowsEditor ? CreateSymbolicLinkW(link, target, 2) : symlink(target, link) == 0;
                if (!created)
                    Assert.Ignore("Symlink creation unavailable: " + Marshal.GetLastWin32Error());
                Assert.Throws<TargetInvocationException>(() => VariantMethod().Invoke(null, new object[] { source, .25f, 1000, null }));
                Assert.IsFalse(Directory.Exists(root + "/model_Normalized"));
                Assert.IsFalse(File.Exists(target), "A dangling metadata target must not be created by Unity's folder or prefab save.");
                Assert.AreNotEqual((FileAttributes)0, File.GetAttributes(link) & FileAttributes.ReparsePoint);
            }
            finally
            {
                // Unlink before the ordinary owned-root teardown; never recurse through a link.
                if (created)
                    File.Delete(link);
                AssetDatabase.AllowAutoRefresh();
            }
        }

        private static MethodInfo VariantMethod()
        {
            Type type = typeof(ModelImportPipeline).Assembly.GetType("MCPForUnity.Editor.Services.AssetGen.Import.NormalizedModelVariant");
            Assert.IsNotNull(type);
            return type.GetMethod("Create", InternalStatic);
        }

        private static MethodInfo BoundsMethod()
        {
            Type type = typeof(ModelImportPipeline).Assembly.GetType("MCPForUnity.Editor.Services.AssetGen.Import.ModelHierarchyBounds");
            Assert.IsNotNull(type);
            return type.GetMethod("TryMeasure", InternalStatic);
        }

        private static object[] MeasureArguments(GameObject go, int nodes = 10000, int depth = 256, int meshes = 10000) =>
            new object[]
            {
                go,
                Matrix4x4.TRS(go.transform.localPosition, go.transform.localRotation, go.transform.localScale),
                null,
                null,
                nodes,
                depth,
                meshes,
            };

        private static Bounds Measure(GameObject go)
        {
            object[] args = MeasureArguments(go);
            Assert.IsTrue((bool)BoundsMethod().Invoke(null, args), args[3]?.ToString());
            return (Bounds)args[2];
        }
    }
}
