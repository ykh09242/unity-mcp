using System;
using System.Collections;
using System.Collections.Generic;
using System.Linq;
using System.Reflection;
using MCPForUnity.Editor.Tools.ProBuilder;
using MCPForUnity.Runtime.Helpers;
using MCPForUnityTests.Editor.Helpers;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;
using UnityEngine.SceneManagement;

namespace MCPForUnityTests.Editor.Tools
{
    [Parallelizable(ParallelScope.None)]
    public class ProBuilderUtilityIntegrityTests
    {
#if UNITY_6000_7_OR_NEWER
        private const MaterialGlobalIlluminationFlags BakedEmission = MaterialGlobalIlluminationFlags.BakedEmission;
        private const MaterialGlobalIlluminationFlags RealtimeEmission = MaterialGlobalIlluminationFlags.RealtimeIndirectEmission;
#else
        private const MaterialGlobalIlluminationFlags BakedEmission = MaterialGlobalIlluminationFlags.BakedEmissive;
        private const MaterialGlobalIlluminationFlags RealtimeEmission = MaterialGlobalIlluminationFlags.RealtimeEmissive;
#endif
        private readonly List<GameObject> ownedObjects = new List<GameObject>();
        private readonly HashSet<Mesh> ownedMeshes = new HashSet<Mesh>();
        private readonly Dictionary<FieldInfo, object> savedResolutionFields = new Dictionary<FieldInfo, object>();
        private readonly PrefabTestSceneFixture sceneFixture = new PrefabTestSceneFixture();
        private Scene originalScene;
        private Scene ownedScene;
        private Component mesh;
        private Type meshType;
        private bool capturedScene;
        private string assetRoot;
        private string assetRootGuid;

        [OneTimeSetUp]
        public void OneTimeSetUp() => sceneFixture.PrepareRunnerBootstrap();

        [OneTimeTearDown]
        public void OneTimeTearDown() => sceneFixture.RestoreRunnerBootstrap();

        [SetUp]
        public void SetUp()
        {
            capturedScene = false;
            assetRoot = null;
            assetRootGuid = null;
            ownedScene = default(Scene);
            ownedObjects.Clear();
            ownedMeshes.Clear();
            savedResolutionFields.Clear();
            mesh = null;
            meshType = Type.GetType("UnityEngine.ProBuilder.ProBuilderMesh, Unity.ProBuilder");
            if (meshType == null)
                Assert.Ignore("Optional ProBuilder package is not installed.");
            if (PrefabStageUtility.GetCurrentPrefabStage() != null)
                Assert.Ignore("An unowned prefab stage is open.");

            // Use real package types without invoking the unrelated default-material patch.
            foreach (FieldInfo field in typeof(ManageProBuilder).GetFields(BindingFlags.Static | BindingFlags.NonPublic | BindingFlags.DeclaredOnly))
                if (field.FieldType == typeof(Type) || field.FieldType == typeof(bool))
                    savedResolutionFields.Add(field, field.GetValue(null));
            SetResolutionField("_typesResolved", true);
            SetResolutionField("_proBuilderAvailable", true);
            SetResolutionField("_proBuilderMeshType", meshType);
            SetResolutionField("_faceType", Type.GetType("UnityEngine.ProBuilder.Face, Unity.ProBuilder", true));
            SetResolutionField("_smoothingType", Type.GetType("UnityEngine.ProBuilder.Smoothing, Unity.ProBuilder", true));
            SetResolutionField("_editorMeshUtilityType", Type.GetType("UnityEditor.ProBuilder.EditorMeshUtility, Unity.ProBuilder.Editor"));
            SetResolutionField("_combineMeshesType", Type.GetType("UnityEngine.ProBuilder.MeshOperations.CombineMeshes, Unity.ProBuilder", true));
            SetResolutionField("_meshImporterType", Type.GetType("UnityEngine.ProBuilder.MeshOperations.MeshImporter, Unity.ProBuilder", true));
            SetResolutionField("_appendElementsType", Type.GetType("UnityEngine.ProBuilder.MeshOperations.AppendElements, Unity.ProBuilder", true));
            SetResolutionField("_connectElementsType", Type.GetType("UnityEngine.ProBuilder.MeshOperations.ConnectElements, Unity.ProBuilder", true));
            SetResolutionField("_edgeType", Type.GetType("UnityEngine.ProBuilder.Edge, Unity.ProBuilder", true));
            SetResolutionField("_vertexEditingType", Type.GetType("UnityEngine.ProBuilder.MeshOperations.VertexEditing, Unity.ProBuilder", true));
            SetResolutionField("_deleteElementsType", Type.GetType("UnityEngine.ProBuilder.MeshOperations.DeleteElements, Unity.ProBuilder", true));

            originalScene = SceneManager.GetActiveScene();
            capturedScene = true;
            ownedScene = sceneFixture.Create("McpProBuilderUtility_", Guid.NewGuid().ToString("N"));

            Type faceType = Type.GetType("UnityEngine.ProBuilder.Face, Unity.ProBuilder", true);
            Array faces = Array.CreateInstance(faceType, 2);
            faces.SetValue(Activator.CreateInstance(faceType, new object[] { new[] { 0, 1, 2 } }), 0);
            faces.SetValue(Activator.CreateInstance(faceType, new object[] { new[] { 3, 4, 5 } }), 1);
            var positions = new[]
            {
                new Vector3(1, 2, 3),
                new Vector3(-2, 1, 0),
                new Vector3(0, -1, 2),
                new Vector3(3, 0, 1),
                new Vector3(0, 4, -1),
                new Vector3(-1, 0, 3),
            };
            MethodInfo create = meshType.GetMethod(
                "Create",
                BindingFlags.Static | BindingFlags.Public,
                null,
                new[] { typeof(IEnumerable<Vector3>), typeof(IEnumerable<>).MakeGenericType(faceType) },
                null
            );
            Assert.IsNotNull(create, "Expected the documented ProBuilder Create overload.");
            mesh = (Component)create.Invoke(null, new object[] { positions, faces });
            Assert.IsNotNull(mesh);
            ownedObjects.Add(mesh.gameObject);
            mesh.gameObject.name = "__McpProBuilderUtility_" + Guid.NewGuid().ToString("N");
            CaptureMesh();
            JObject available = JObject.FromObject(ManageProBuilder.HandleCommand(new JObject { ["action"] = "ping" }));
            Assert.IsTrue(available.Value<bool>("success"), available.ToString());
            Assert.AreEqual(meshType, typeof(ManageProBuilder).GetField("_proBuilderMeshType", BindingFlags.Static | BindingFlags.NonPublic).GetValue(null));
        }

        [TearDown]
        public void TearDown()
        {
            try
            {
                if (mesh != null)
                    CaptureMesh();
                foreach (GameObject go in ownedObjects)
                {
                    if (go == null)
                        continue;
                    foreach (Component component in go.GetComponents<Component>())
                        if (component != null)
                            Undo.ClearUndo(component);
                    Undo.ClearUndo(go);
                }
                for (int i = ownedObjects.Count - 1; i >= 0; i--)
                    if (ownedObjects[i] != null)
                        UnityEngine.Object.DestroyImmediate(ownedObjects[i]);
                foreach (Mesh value in ownedMeshes)
                    if (value != null && !AssetDatabase.Contains(value))
                        UnityEngine.Object.DestroyImmediate(value);
            }
            finally
            {
                foreach (var saved in savedResolutionFields)
                    saved.Key.SetValue(null, saved.Value);
                savedResolutionFields.Clear();
                if (capturedScene && originalScene.IsValid() && originalScene.isLoaded)
                    SceneManager.SetActiveScene(originalScene);
                sceneFixture.Close();
                if (assetRootGuid != null)
                {
                    StringAssert.StartsWith("Assets/__McpProBuilderIntegrity_", assetRoot);
                    Assert.AreEqual(assetRootGuid, AssetDatabase.AssetPathToGUID(assetRoot));
                    Assert.IsTrue(AssetDatabase.DeleteAsset(assetRoot), "Only the folder created by this fixture is removed.");
                }
                ownedObjects.Clear();
                ownedMeshes.Clear();
                capturedScene = false;
            }
        }

        private static void SetResolutionField(string name, object value)
        {
            typeof(ManageProBuilder).GetField(name, BindingFlags.Static | BindingFlags.NonPublic).SetValue(null, value);
        }

        private void CaptureMesh()
        {
            MeshFilter filter = mesh.GetComponent<MeshFilter>();
            if (filter != null && filter.sharedMesh != null && !AssetDatabase.Contains(filter.sharedMesh))
                ownedMeshes.Add(filter.sharedMesh);
        }

        private void SaveOwnedScene()
        {
            assetRoot = "Assets/__McpProBuilderIntegrity_" + Guid.NewGuid().ToString("N");
            Assert.IsFalse(AssetDatabase.IsValidFolder(assetRoot));
            assetRootGuid = AssetDatabase.CreateFolder("Assets", assetRoot.Substring("Assets/".Length));
            Assert.IsNotEmpty(assetRootGuid);
            Assert.IsTrue(EditorSceneManager.SaveScene(ownedScene, assetRoot + "/" + ownedScene.name + ".unity"));
            Assert.IsFalse(ownedScene.isDirty);
        }

        [TestCase("ping")]
        [TestCase("create_shape")]
        public void TypeDiscoveryDoesNotPatchDefaultMaterial(string action)
        {
            var material = AssetDatabase.LoadAssetAtPath<Material>("Packages/com.unity.probuilder/Content/Resources/Materials/ProBuilderDefault.mat");
            if (material == null)
                Assert.Ignore("Optional ProBuilder default material is unavailable.");
            MaterialGlobalIlluminationFlags original = material.globalIlluminationFlags;
            var colors = new[] { "_EmissionColor", "_EmissionColorUI", "_EmissionColorWithMapUI" }
                .Where(material.HasProperty)
                .ToDictionary(name => name, material.GetColor);
            try
            {
                material.globalIlluminationFlags = BakedEmission | RealtimeEmission;
                var expected = material.globalIlluminationFlags;
                SetResolutionField("_typesResolved", false);
                var response = JObject.FromObject(ManageProBuilder.HandleCommand(new JObject { ["action"] = action }));
                Assert.AreEqual(action == "ping", response.Value<bool>("success"), response.ToString());
                Assert.AreEqual(expected, material.globalIlluminationFlags, "Type lookup/read/rejected creation must not alter shared material state.");
            }
            finally
            {
                material.globalIlluminationFlags = original;
                foreach (var color in colors)
                    material.SetColor(color.Key, color.Value);
            }
        }

        [Test]
        public void SuccessfulShapeCreationStillPatchesDefaultMaterial()
        {
            var material = AssetDatabase.LoadAssetAtPath<Material>("Packages/com.unity.probuilder/Content/Resources/Materials/ProBuilderDefault.mat");
            if (material == null)
                Assert.Ignore("Optional ProBuilder default material is unavailable.");
            MaterialGlobalIlluminationFlags original = material.globalIlluminationFlags;
            var colors = new[] { "_EmissionColor", "_EmissionColorUI", "_EmissionColorWithMapUI" }
                .Where(material.HasProperty)
                .ToDictionary(name => name, material.GetColor);
            try
            {
                material.globalIlluminationFlags = BakedEmission;
                SetResolutionField("_typesResolved", false);
                var response = JObject.FromObject(
                    ManageProBuilder.HandleCommand(
                        new JObject
                        {
                            ["action"] = "create_shape",
                            ["properties"] = new JObject { ["shapeType"] = "Cube" },
                        }
                    )
                );
                if (response.Value<bool>("success"))
                {
                    var created = ownedScene.GetRootGameObjects().Single(go => go.GetInstanceIDCompat() == response["data"].Value<int>("instanceId"));
                    ownedObjects.Add(created);
                    var filter = created.GetComponent<MeshFilter>();
                    if (filter != null && filter.sharedMesh != null)
                        ownedMeshes.Add(filter.sharedMesh);
                }
                Assert.IsTrue(response.Value<bool>("success"), response.ToString());
                Assert.AreEqual(MaterialGlobalIlluminationFlags.EmissiveIsBlack, material.globalIlluminationFlags);
            }
            finally
            {
                material.globalIlluminationFlags = original;
                foreach (var color in colors)
                    material.SetColor(color.Key, color.Value);
            }
        }

        private GameObject Parent(string name)
        {
            var go = new GameObject(name + Guid.NewGuid().ToString("N"));
            ownedObjects.Add(go);
            return go;
        }

        private Vector3[] WorldVertices()
        {
            return ((IEnumerable<Vector3>)meshType.GetProperty("positions").GetValue(mesh)).Select(mesh.transform.TransformPoint).ToArray();
        }

        private JObject Send(string action, JObject properties = null)
        {
            var request = new JObject
            {
                ["action"] = action,
                ["target"] = mesh.gameObject.GetInstanceIDCompat().ToString(),
                ["searchMethod"] = "by_id",
            };
            if (properties != null)
                request["properties"] = properties;
            JObject result = JObject.FromObject(ManageProBuilder.HandleCommand(request));
            CaptureMesh();
            return result;
        }

        [Test]
        public void MergeObjects_InvalidLaterMeshPreservesEarlierOrdinaryMesh()
        {
            var cube = GameObject.CreatePrimitive(PrimitiveType.Cube);
            cube.name = "__McpMergeCube_" + Guid.NewGuid().ToString("N");
            ownedObjects.Add(cube);
            var empty = Parent("__McpMergeEmpty_");
            var filter = cube.GetComponent<MeshFilter>();
            var source = filter.sharedMesh;
            var components = cube.GetComponents<Component>();
            SaveOwnedScene();
            int dirty = EditorUtility.GetDirtyCount(cube);

            var result = Send("merge_objects", new JObject { ["targets"] = new JArray(cube.name, empty.name) });

            Assert.IsFalse(result.Value<bool>("success"), result.ToString());
            // Track any output even on a regression so cleanup remains ownership scoped.
            if (filter.sharedMesh != null && filter.sharedMesh != source && !AssetDatabase.Contains(filter.sharedMesh))
                ownedMeshes.Add(filter.sharedMesh);
            CollectionAssert.AreEqual(components, cube.GetComponents<Component>());
            Assert.AreSame(source, filter.sharedMesh);
            Assert.AreEqual(dirty, EditorUtility.GetDirtyCount(cube));
            Assert.IsFalse(ownedScene.isDirty);
        }

        [TestCase("skipMaterialSwap")]
        [TestCase("skip_material_swap")]
        public void SetFaceColor_InvalidSkipSwapPreservesColorsAndMaterial(string field)
        {
            var colorsProperty = meshType.GetProperty("colors");
            var before = ((IEnumerable<Color>)colorsProperty.GetValue(mesh))?.ToArray();
            var renderer = mesh.GetComponent<Renderer>();
            var materials = renderer.sharedMaterials;
            SaveOwnedScene();
            int dirty = EditorUtility.GetDirtyCount(mesh);
            var props = new JObject { ["color"] = new JArray(.2, .4, .7, 1), [field] = "invalid_bool" };

            var result = Send("set_face_color", props);

            Assert.IsFalse(result.Value<bool>("success"), result.ToString());
            CollectionAssert.AreEqual(before, ((IEnumerable<Color>)colorsProperty.GetValue(mesh))?.ToArray());
            CollectionAssert.AreEqual(materials, renderer.sharedMaterials);
            Assert.AreEqual(dirty, EditorUtility.GetDirtyCount(mesh));
            Assert.IsFalse(ownedScene.isDirty);
        }

        [TestCase("rotation", "\"invalid_float\"")]
        [TestCase("flipU", "\"invalid_bool\"")]
        [TestCase("flipV", "\"invalid_bool\"")]
        [TestCase("offset", "[]")]
        public void SetFaceUVs_InvalidLaterSettingPreservesAllFaces(string field, string json)
        {
            var faces = ((IEnumerable)meshType.GetProperty("faces").GetValue(mesh)).Cast<object>().ToArray();
            var uvProperty = faces[0].GetType().GetProperty("uv");
            var before = faces.Select(face => JsonUtility.ToJson(uvProperty.GetValue(face))).ToArray();
            SaveOwnedScene();
            int dirty = EditorUtility.GetDirtyCount(mesh);
            var props = new JObject { ["scale"] = new JArray(2, 3), [field] = JToken.Parse(json) };

            var result = Send("set_face_uvs", props);

            Assert.IsFalse(result.Value<bool>("success"), result.ToString());
            CollectionAssert.AreEqual(before, faces.Select(face => JsonUtility.ToJson(uvProperty.GetValue(face))).ToArray());
            Assert.AreEqual(dirty, EditorUtility.GetDirtyCount(mesh));
            Assert.IsFalse(ownedScene.isDirty);
        }

        [TestCase("set_pivot", "{\"position\":null}")]
        [TestCase("set_pivot", "{\"position\":true}")]
        [TestCase("set_pivot", "{\"position\":[0,1]}")]
        [TestCase("set_pivot", "{\"position\":[1,\"bad\",3]}")]
        [TestCase("set_pivot", "{\"position\":{\"x\":1,\"y\":2}}")]
        [TestCase("set_pivot", "{\"position\":null,\"worldPosition\":[1,2,3]}")]
        [TestCase("move_vertices", "{\"vertexIndices\":[0,999],\"offset\":[1,2,3]}")]
        [TestCase("insert_vertex", "{\"point\":[0,0,0]}")]
        [TestCase("insert_vertex", "{\"point\":[0,0,0],\"faceIndex\":999}")]
        [TestCase("append_vertices_to_edge", "{\"edgeIndices\":[999]}")]
        [TestCase("subdivide", "{\"faceIndices\":[999]}")]
        [TestCase("connect_elements", "{}")]
        [TestCase("connect_elements", "{\"faceIndices\":[999]}")]
        [TestCase("connect_elements", "{\"edgeIndices\":[999]}")]
        [TestCase("weld_vertices", "{\"vertexIndices\":[-1]}")]
        [TestCase("weld_vertices", "{\"vertexIndices\":[0,999]}")]
        [TestCase("create_polygon", "{\"vertexIndices\":[-1]}")]
        [TestCase("create_polygon", "{\"vertexIndices\":[0,999]}")]
        [TestCase("bridge_edges", "{\"edgeA\":{\"a\":-1,\"b\":0},\"edgeB\":{\"a\":1,\"b\":2},\"allowNonManifold\":true}")]
        [TestCase("weld_vertices", "{\"vertexIndices\":null}")]
        [TestCase("create_polygon", "{\"vertexIndices\":null}")]
        [TestCase("split_vertices", "{\"vertexIndices\":null}")]
        [TestCase("delete_faces", "{\"faceIndices\":null}")]
        public void InvalidMeshEdit_PreservesPositionsAndCleanScene(string action, string json)
        {
            SaveOwnedScene();
            var before = WorldVertices();
            int dirty = EditorUtility.GetDirtyCount(mesh);

            var result = Send(action, JObject.Parse(json));

            Assert.IsFalse(result.Value<bool>("success"), result.ToString());
            CollectionAssert.AreEqual(before, WorldVertices());
            Assert.AreEqual(dirty, EditorUtility.GetDirtyCount(mesh));
            Assert.IsFalse(ownedScene.isDirty);
        }

        [TestCase(0)]
        [TestCase(1)]
        [TestCase(2)]
        [TestCase(3)]
        [TestCase(4)]
        public void FreezeKeepsEveryWorldVertexAndExistingResetSemantics(int hierarchy)
        {
            if (hierarchy > 0)
            {
                GameObject parent = Parent("Parent_");
                parent.transform.position = new Vector3(4, -2, 3);
                parent.transform.rotation = Quaternion.Euler(20, 37, -11);
                parent.transform.localScale = hierarchy == 1 ? Vector3.one : new Vector3(2, 3, .5f);
                mesh.transform.SetParent(parent.transform, false);
                if (hierarchy >= 3)
                {
                    GameObject ancestor = Parent("Ancestor_");
                    ancestor.transform.position = new Vector3(-3, 5, 1);
                    ancestor.transform.rotation = Quaternion.Euler(-13, 29, 41);
                    ancestor.transform.localScale = hierarchy == 4 ? new Vector3(-1.5f, 2, .75f) : new Vector3(1.5f, 2, .75f);
                    parent.transform.SetParent(ancestor.transform, false);
                }
            }
            mesh.transform.localPosition = new Vector3(3, 2, -4);
            mesh.transform.localRotation = Quaternion.Euler(31, -17, 23);
            mesh.transform.localScale = new Vector3(1.25f, .7f, 2);
            Transform parentBefore = mesh.transform.parent;
            Vector3[] before = WorldVertices();
            JObject result = Send("freeze_transform");
            Assert.IsTrue(result.Value<bool>("success"), result.ToString());
            Vector3[] after = WorldVertices();
            Assert.AreEqual(before.Length, after.Length);
            for (int i = 0; i < before.Length; i++)
                Assert.That(Vector3.Distance(before[i], after[i]), Is.LessThan(.001f), "Vertex " + i);
            Assert.AreSame(parentBefore, mesh.transform.parent);
            Assert.That(mesh.transform.position.sqrMagnitude, Is.LessThan(.000001f));
            Assert.That(Quaternion.Angle(Quaternion.identity, mesh.transform.rotation), Is.LessThan(.001f));
            Assert.AreEqual(Vector3.one, mesh.transform.localScale);
        }

        private sealed class MissingSmoothingApi { }

        [TestCase("[0]")]
        [TestCase("[]")]
        [TestCase(null)]
        public void MissingSmoothingMethodRejectsWithoutChangingMeshOrScene(string indices)
        {
            SetResolutionField("_smoothingType", typeof(MissingSmoothingApi));
            SaveOwnedScene();
            Vector3[] before = WorldVertices();
            int dirty = EditorUtility.GetDirtyCount(mesh);
            var properties = new JObject { ["angleThreshold"] = 0 };
            if (indices != null)
                properties["faceIndices"] = JToken.Parse(indices);

            JObject result = Send("auto_smooth", properties);

            Assert.IsFalse(result.Value<bool>("success"), result.ToString());
            StringAssert.Contains("ApplySmoothingGroups method not found", result.Value<string>("error"));
            CollectionAssert.AreEqual(before, WorldVertices());
            Assert.AreEqual(dirty, EditorUtility.GetDirtyCount(mesh));
            Assert.IsFalse(ownedScene.isDirty);
        }

        [Test]
        public void SetPivotRejectsEmptyPositionsWithoutMovingTransform()
        {
            meshType.GetProperty("positions").SetValue(mesh, new List<Vector3>());
            SaveOwnedScene();
            Vector3 before = mesh.transform.position;
            int dirty = EditorUtility.GetDirtyCount(mesh);

            JObject result = Send("set_pivot", new JObject { ["position"] = new JArray(4, 5, 6) });

            Assert.IsFalse(result.Value<bool>("success"), result.ToString());
            StringAssert.Contains("Could not read vertex positions", result.Value<string>("error"));
            Assert.AreEqual(before, mesh.transform.position);
            Assert.IsEmpty((IEnumerable)meshType.GetProperty("positions").GetValue(mesh));
            Assert.AreEqual(dirty, EditorUtility.GetDirtyCount(mesh));
            Assert.IsFalse(ownedScene.isDirty);
        }

        [TestCase("[1]", 1)]
        [TestCase("[]", 0)]
        [TestCase(null, 2)]
        public void AutoSmoothReportsFacesSuppliedToSmoothing(string indices, int expected)
        {
            var properties = new JObject { ["angleThreshold"] = 0 };
            if (indices != null)
                properties["faceIndices"] = JToken.Parse(indices);
            JObject result = Send("auto_smooth", properties);
            Assert.IsTrue(result.Value<bool>("success"), result.ToString());
            Assert.AreEqual(expected, result["data"].Value<int>("faceCount"));
        }
    }
}
