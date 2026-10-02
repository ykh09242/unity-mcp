using System;
using System.Collections.Generic;
using System.Linq;
using System.Reflection;
using MCPForUnity.Editor.Tools.ProBuilder;
using MCPForUnity.Runtime.Helpers;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;
using UnityEngine.SceneManagement;

namespace MCPForUnityTests.Editor.Tools
{
    // Optional real-package controls plus explicitly package-shaped failure boundaries.
    // Compilation does not establish ProBuilder geometry or native importer rollback.
    [Parallelizable(ParallelScope.None)]
    public class ProBuilderCreationIntegrityTests
    {
        private readonly Dictionary<FieldInfo, object> savedFields = new();
        private readonly HashSet<GameObject> ownedObjects = new();
        private readonly HashSet<Mesh> ownedMeshes = new();
        private readonly HashSet<Material> ownedMaterials = new();
        private Scene originalScene;
        private Scene ownedScene;
        private UnityEngine.Object[] originalSelection;
        private UnityEngine.Object originalActiveSelection;
        private bool captured;
        private Type realMeshType;
        private const BindingFlags Fields = BindingFlags.Static | BindingFlags.NonPublic | BindingFlags.Public;

        [SetUp]
        public void SetUp()
        {
            captured = false;
            savedFields.Clear();
            ownedObjects.Clear();
            ownedMeshes.Clear();
            ownedMaterials.Clear();
            ownedScene = default;
            if (PrefabStageUtility.GetCurrentPrefabStage() != null)
                Assert.Ignore("An unowned Prefab Stage is open.");
            realMeshType = Type.GetType("UnityEngine.ProBuilder.ProBuilderMesh, Unity.ProBuilder");
            if (realMeshType == null)
                Assert.Ignore("Optional ProBuilder package is not installed; no native geometry substitute.");
            originalScene = SceneManager.GetActiveScene();
            originalSelection = Selection.objects;
            originalActiveSelection = Selection.activeObject;
            foreach (var field in typeof(ManageProBuilder).GetFields(Fields))
                if (!field.IsLiteral && !field.IsInitOnly) savedFields.Add(field, field.GetValue(null));
            captured = true;
            // Resolve only the required real types locally. Never run the global default-material patch.
            Set("_typesResolved", true);
            Set("_proBuilderAvailable", true);
            Set("_proBuilderMeshType", realMeshType);
            Set("_shapeGeneratorType", PackageType("UnityEngine.ProBuilder.ShapeGenerator"));
            Set("_shapeTypeEnum", PackageType("UnityEngine.ProBuilder.ShapeType"));
            Set("_pivotLocationType", PackageType("UnityEngine.ProBuilder.PivotLocation"));
            Set("_appendElementsType", PackageType("UnityEngine.ProBuilder.MeshOperations.AppendElements"));
            Set("_meshImporterType", PackageType("UnityEngine.ProBuilder.MeshOperations.MeshImporter"));
            ownedScene = EditorSceneManager.NewScene(NewSceneSetup.EmptyScene, NewSceneMode.Additive);
            Assert.IsTrue(SceneManager.SetActiveScene(ownedScene));
            ProBuilderCreationFaultMesh.ThrowRefresh = false;
            ProBuilderCreationFaultPolygon.Throw = false;
            ProBuilderCreationFaultPolygon.Status = "Success";
        }

        [TearDown]
        public void TearDown()
        {
            if (!captured) return;
            try
            {
                foreach (var go in ownedObjects)
                {
                    if (go == null) continue;
                    foreach (var component in go.GetComponents<Component>())
                        if (component != null) Undo.ClearUndo(component);
                    Undo.ClearUndo(go);
                    UnityEngine.Object.DestroyImmediate(go);
                }
                foreach (var mesh in ownedMeshes)
                    if (mesh != null && !AssetDatabase.Contains(mesh)) UnityEngine.Object.DestroyImmediate(mesh);
                foreach (var material in ownedMaterials)
                    if (material != null && !AssetDatabase.Contains(material)) UnityEngine.Object.DestroyImmediate(material);
                if (ownedScene.IsValid() && ownedScene.isLoaded)
                {
                    Assert.AreEqual(0, ownedScene.rootCount, "Unexpected roots retained for diagnosis.");
                    Assert.IsTrue(EditorSceneManager.CloseScene(ownedScene, true));
                }
            }
            finally
            {
                foreach (var entry in savedFields) entry.Key.SetValue(null, entry.Value);
                ProBuilderCreationFaultMesh.ThrowRefresh = false;
                ProBuilderCreationFaultPolygon.Throw = false;
                ProBuilderCreationFaultPolygon.Status = "Success";
                if (originalScene.IsValid() && originalScene.isLoaded) SceneManager.SetActiveScene(originalScene);
                Selection.objects = originalSelection;
                Selection.activeObject = originalActiveSelection;
                captured = false;
            }
        }

        [Test]
        public void RealCubeReturnsOwnedSceneObjectAndDimensions()
        {
            var name = UniqueName();
            var result = Send("create_shape", new JObject
            {
                ["shapeType"] = "Cube", ["name"] = name, ["size"] = 2,
                ["position"] = new JArray(0, -1, 2),
            });
            Assert.IsTrue(result.Value<bool>("success"), result.ToString());
            var go = ResultObject(result);
            Assert.AreEqual(name, go.name);
            Assert.AreEqual(new Vector3(0, -1, 2), go.transform.position);
            Assert.AreEqual(ownedScene, go.scene);
            Assert.IsNotNull(go.GetComponent(realMeshType));
            Assert.Greater((int)realMeshType.GetProperty("vertexCount").GetValue(go.GetComponent(realMeshType)), 0);
            Assert.AreEqual(new Vector3(2, 2, 2), go.GetComponent<MeshFilter>().sharedMesh.bounds.size);
        }

        [Test]
        public void RealPolygonCreationUsesFourArgumentApi()
        {
            var result = Send("create_poly_shape", PolygonProperties());
            Assert.IsTrue(result.Value<bool>("success"), result.ToString());
            Assert.IsNotNull(ResultObject(result).GetComponent(realMeshType));
        }

        [Test]
        public void RealModernImporterConvertsOwnedMesh()
        {
            var go = Triangle();
            var source = go.GetComponent<MeshFilter>().sharedMesh;
            var result = Send("convert_to_probuilder", null, go);
            Assert.IsTrue(result.Value<bool>("success"), result.ToString());
            Assert.IsNotNull(go.GetComponent(realMeshType));
            Assert.AreEqual(ownedScene, go.scene);
            Assert.IsTrue(source != null, "Adding/refreshing ProBuilder must not destroy the borrowed input mesh.");
            Assert.AreEqual(3, source.vertexCount);
        }

        [Test]
        public void RealUnsupportedTopologyFailureRestoresBorrowedInput()
        {
            var go = Triangle();
            var filter = go.GetComponent<MeshFilter>();
            var source = filter.sharedMesh;
            source.SetIndices(new[] { 0, 1 }, MeshTopology.Lines, 0);
            var vertices = source.vertices;
            var materials = go.GetComponent<MeshRenderer>().sharedMaterials;
            var result = Send("convert_to_probuilder", null, go);
            Assert.IsFalse(result.Value<bool>("success"));
            Assert.IsNull(go.GetComponent(realMeshType));
            Assert.IsTrue(source != null, "ProBuilder OnDestroy must not delete borrowed input.");
            Assert.AreSame(source, filter.sharedMesh);
            CollectionAssert.AreEqual(vertices, source.vertices);
            CollectionAssert.AreEqual(materials, go.GetComponent<MeshRenderer>().sharedMaterials);
        }

        [Test]
        public void ExistingRealComponentIsRetainedOnRejection()
        {
            var go = Triangle();
            var original = go.AddComponent(realMeshType);
            var result = Send("convert_to_probuilder", null, go);
            Assert.IsFalse(result.Value<bool>("success"));
            Assert.AreSame(original, go.GetComponent(realMeshType));
        }

        [TestCase("Failure")]
        [TestCase("Canceled")]
        [TestCase("NoChange")]
        public void PackageShapedPolygonResultRejectsWithoutRetainedObject(string status)
        {
            FaultTypes();
            var prior = Triangle();
            ProBuilderCreationFaultPolygon.Status = status;
            var result = Send("create_poly_shape", PolygonProperties());
            Assert.IsFalse(result.Value<bool>("success"));
            Assert.AreEqual(1, ownedScene.rootCount);
            Assert.IsNotNull(prior);
        }

        [TestCase(false)]
        [TestCase(true)]
        public void PackageShapedCreationExceptionRemovesOnlyNewObject(bool polygon)
        {
            FaultTypes();
            var prior = Triangle();
            ProBuilderCreationFaultMesh.ThrowRefresh = true;
            var result = Send(polygon ? "create_poly_shape" : "create_shape",
                polygon ? PolygonProperties() : new JObject { ["shapeType"] = "Cube" });
            Assert.IsFalse(result.Value<bool>("success"));
            Assert.AreEqual(1, ownedScene.rootCount);
            Assert.IsNotNull(prior);
        }

        [Test]
        public void PackageShapedPolygonInvokeExceptionRemovesNewObject()
        {
            FaultTypes();
            ProBuilderCreationFaultPolygon.Throw = true;
            Assert.IsFalse(Send("create_poly_shape", PolygonProperties()).Value<bool>("success"));
            Assert.AreEqual(0, ownedScene.rootCount);
        }

        [Test]
        public void MissingImporterConstructorAddsNoComponent()
        {
            var go = Triangle();
            Set("_meshImporterType", typeof(ProBuilderCreationMissingImporter));
            Assert.IsFalse(Send("convert_to_probuilder", null, go).Value<bool>("success"));
            Assert.IsNull(go.GetComponent(realMeshType));
        }

        [Test]
        public void PackageShapedMissingImportAddsNoComponent()
        {
            FaultTypes();
            var go = Triangle();
            Set("_meshImporterType", typeof(ProBuilderCreationNoImport));
            Assert.IsFalse(Send("convert_to_probuilder", null, go).Value<bool>("success"));
            Assert.IsNull(go.GetComponent<ProBuilderCreationFaultMesh>());
        }

        [Test]
        public void PackageShapedImportExceptionRemovesOnlyAddedComponent()
        {
            FaultTypes();
            var go = Triangle();
            var mesh = go.GetComponent<MeshFilter>().sharedMesh;
            Set("_meshImporterType", typeof(ProBuilderCreationThrowingImporter));
            Assert.IsFalse(Send("convert_to_probuilder", null, go).Value<bool>("success"));
            Assert.IsNull(go.GetComponent<ProBuilderCreationFaultMesh>());
            Assert.AreSame(mesh, go.GetComponent<MeshFilter>().sharedMesh);
            Assert.IsNotNull(go);
        }

        private JObject Send(string action, JObject properties, GameObject target = null)
        {
            var request = new JObject { ["action"] = action };
            if (properties != null) request["properties"] = properties;
            if (target != null) { request["target"] = target.GetInstanceIDCompat().ToString(); request["searchMethod"] = "by_id"; }
            try { return JObject.FromObject(ManageProBuilder.HandleCommand(request)); }
            finally
            {
                foreach (var go in ownedScene.GetRootGameObjects())
                {
                    ownedObjects.Add(go);
                    foreach (var filter in go.GetComponentsInChildren<MeshFilter>(true))
                        if (filter.sharedMesh != null && !AssetDatabase.Contains(filter.sharedMesh)) ownedMeshes.Add(filter.sharedMesh);
                }
            }
        }
        private GameObject Triangle()
        {
            var go = new GameObject(UniqueName());
            ownedObjects.Add(go);
            var mesh = new Mesh { vertices = new[] { Vector3.zero, Vector3.right, Vector3.up }, triangles = new[] { 0, 1, 2 } };
            ownedMeshes.Add(mesh);
            go.AddComponent<MeshFilter>().sharedMesh = mesh;
            var shader = Shader.Find("Standard") ?? Shader.Find("Hidden/InternalErrorShader");
            Assert.IsNotNull(shader, "Owned importer fixture requires an available shader.");
            var material = new Material(shader);
            ownedMaterials.Add(material);
            go.AddComponent<MeshRenderer>().sharedMaterials = new[] { material };
            return go;
        }
        private GameObject ResultObject(JObject result) => ownedScene.GetRootGameObjects().Single(go => go.GetInstanceIDCompat() == result["data"]["instanceId"].Value<int>());
        private static JObject PolygonProperties() => new JObject { ["name"] = UniqueName(), ["points"] = new JArray(new JArray(0, 0, 0), new JArray(1, 0, 0), new JArray(0, 0, 1)), ["extrudeHeight"] = 1, ["flipNormals"] = false };
        private static string UniqueName() => "McpCreationIntegrity_" + Guid.NewGuid().ToString("N");
        private static Type PackageType(string name) => Type.GetType(name + ", Unity.ProBuilder");
        private static void Set(string name, object value) => typeof(ManageProBuilder).GetField(name, Fields).SetValue(null, value);
        private static void FaultTypes()
        {
            Set("_proBuilderMeshType", typeof(ProBuilderCreationFaultMesh));
            Set("_shapeGeneratorType", typeof(ProBuilderCreationFaultShapes));
            Set("_shapeTypeEnum", typeof(ProBuilderCreationShape));
            Set("_pivotLocationType", typeof(ProBuilderCreationPivot));
            Set("_appendElementsType", typeof(ProBuilderCreationFaultPolygon));
        }
    }

    public class ProBuilderCreationFaultMesh : MonoBehaviour
    {
        public static bool ThrowRefresh;
        public int faceCount => 1;
        public int vertexCount => 3;
        public void ToMesh() { }
        public void Refresh() { if (ThrowRefresh) throw new InvalidOperationException("Owned test refresh failure"); }
    }
    public enum ProBuilderCreationShape { Cube }
    public enum ProBuilderCreationPivot { Center }
    public static class ProBuilderCreationFaultShapes
    {
        public static ProBuilderCreationFaultMesh GenerateCube(ProBuilderCreationPivot pivot, Vector3 size) => new GameObject("OwnedFaultCube").AddComponent<ProBuilderCreationFaultMesh>();
    }
    public sealed class ProBuilderCreationResult
    {
        public string status { get; }
        public string notification => "Owned test polygon result";
        public ProBuilderCreationResult(string value) { status = value; }
    }
    public static class ProBuilderCreationFaultPolygon
    {
        public static string Status = "Success";
        public static bool Throw;
        public static ProBuilderCreationResult CreateShapeFromPolygon(ProBuilderCreationFaultMesh mesh, IList<Vector3> points, float height, bool flip)
        {
            if (Throw) throw new InvalidOperationException("Owned test polygon failure");
            return new ProBuilderCreationResult(Status);
        }
    }
    public sealed class ProBuilderCreationMissingImporter { }
    public sealed class ProBuilderCreationNoImport
    {
        public ProBuilderCreationNoImport(ProBuilderCreationFaultMesh mesh) { }
    }
    public sealed class ProBuilderCreationThrowingImporter
    {
        public ProBuilderCreationThrowingImporter(ProBuilderCreationFaultMesh mesh) { }
        public void Import() => throw new InvalidOperationException("Owned test import failure");
    }
}
