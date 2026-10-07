using System;
using System.Collections.Generic;
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
    // Package-shaped components exercise production dispatch/reflection without requiring ProBuilder.
    // These fixtures do not assert ProBuilder's native geometry implementation.
    [Parallelizable(ParallelScope.None)]
    public class ManageProBuilderContractTests
    {
        private readonly Dictionary<FieldInfo, object> savedFields = new Dictionary<FieldInfo, object>();
        private GameObject target;
        private ProBuilderContractMesh mesh;
        private readonly PrefabTestSceneFixture sceneFixture = new PrefabTestSceneFixture();
        private readonly List<GameObject> ownedObjects = new List<GameObject>();
        private readonly List<Mesh> ownedMeshes = new List<Mesh>();
        private Scene ownedScene;
        private string assetRoot;
        private string assetRootGuid;

        [OneTimeSetUp]
        public void OneTimeSetUp() => sceneFixture.PrepareRunnerBootstrap();

        [OneTimeTearDown]
        public void OneTimeTearDown() => sceneFixture.RestoreRunnerBootstrap();

        [SetUp]
        public void SetUp()
        {
            assetRoot = assetRootGuid = null;
            ownedScene = sceneFixture.Create("McpProBuilderContract_", Guid.NewGuid().ToString("N"));
            foreach (
                string name in new[]
                {
                    "_typesResolved",
                    "_proBuilderAvailable",
                    "_proBuilderMeshType",
                    "_faceType",
                    "_edgeType",
                    "_editorMeshUtilityType",
                    "_extrudeElementsType",
                    "_connectElementsType",
                    "_appendElementsType",
                    "_vertexEditingType",
                    "_meshImporterType",
                    "_combineMeshesType",
                    "_shapeGeneratorType",
                    "_shapeTypeEnum",
                    "_pivotLocationType",
                    "_deleteElementsType",
                }
            )
            {
                var field = typeof(ManageProBuilder).GetField(name, BindingFlags.NonPublic | BindingFlags.Static);
                savedFields.Add(field, field.GetValue(null));
            }
            SetField("_typesResolved", true);
            SetField("_proBuilderAvailable", true);
            SetField("_proBuilderMeshType", typeof(ProBuilderContractMesh));
            SetField("_faceType", typeof(ProBuilderContractFace));
            SetField("_edgeType", typeof(ProBuilderContractEdge));
            SetField("_editorMeshUtilityType", null);
            SetField("_extrudeElementsType", typeof(ProBuilderContractExtrude));
            SetField("_connectElementsType", typeof(ProBuilderContractConnect));
            SetField("_appendElementsType", typeof(ProBuilderContractAppend));
            SetField("_vertexEditingType", typeof(ProBuilderContractVertexEditing));
            SetField("_meshImporterType", typeof(ProBuilderContractImporter));
            SetField("_combineMeshesType", typeof(ProBuilderContractCombine));
            SetField("_shapeGeneratorType", typeof(ProBuilderContractGenerator));
            SetField("_shapeTypeEnum", typeof(ProBuilderContractShape));
            SetField("_pivotLocationType", typeof(ProBuilderContractPivot));
            SetField("_deleteElementsType", typeof(ProBuilderContractDelete));
            ProBuilderContractAppend.Calls = ProBuilderContractVertexEditing.Calls = 0;
            ProBuilderContractAppend.PolygonCalls = ProBuilderContractVertexEditing.WeldCalls = 0;
            ProBuilderContractAppend.BridgeCalls = 0;
            ProBuilderContractDelete.Calls = ProBuilderContractVertexEditing.SplitCalls = 0;
            ProBuilderContractImporter.Calls = ProBuilderContractCombine.Calls = 0;
            ProBuilderContractGenerator.Created.Clear();
            ProBuilderContractExtrude.Calls = 0;
            ProBuilderContractConnect.FaceCalls = ProBuilderContractConnect.EdgeCalls = 0;
            target = new GameObject("ProBuilderContractTarget");
            ownedObjects.Add(target);
            mesh = target.AddComponent<ProBuilderContractMesh>();
            target.transform.position = new Vector3(10, 0, 0);
        }

        [TearDown]
        public void TearDown()
        {
            foreach (var saved in savedFields)
                saved.Key.SetValue(null, saved.Value);
            savedFields.Clear();
            ownedObjects.AddRange(ProBuilderContractGenerator.Created);
            foreach (var go in ownedObjects)
            {
                if (go == null)
                    continue;
                foreach (Component component in go.GetComponents<Component>())
                    if (component != null)
                        Undo.ClearUndo(component);
                Undo.ClearUndo(go);
                UnityEngine.Object.DestroyImmediate(go);
            }
            foreach (Mesh value in ownedMeshes)
                if (value != null)
                    UnityEngine.Object.DestroyImmediate(value);
            ownedObjects.Clear();
            ownedMeshes.Clear();
            sceneFixture.Close();
            if (assetRootGuid != null)
            {
                StringAssert.StartsWith("Assets/__McpProBuilderContract_", assetRoot);
                Assert.AreEqual(assetRootGuid, AssetDatabase.AssetPathToGUID(assetRoot));
                Assert.IsTrue(AssetDatabase.DeleteAsset(assetRoot));
            }
        }

        private void SaveOwnedScene()
        {
            assetRoot = "Assets/__McpProBuilderContract_" + Guid.NewGuid().ToString("N");
            Assert.IsFalse(AssetDatabase.IsValidFolder(assetRoot));
            assetRootGuid = AssetDatabase.CreateFolder("Assets", assetRoot.Substring("Assets/".Length));
            Assert.IsNotEmpty(assetRootGuid);
            Assert.IsTrue(EditorSceneManager.SaveScene(ownedScene, assetRoot + "/" + ownedScene.name + ".unity"));
            Assert.IsFalse(ownedScene.isDirty);
        }

        private GameObject ImportSource(MeshTopology topology)
        {
            var go = new GameObject("ProBuilderImportSource_" + Guid.NewGuid().ToString("N"));
            ownedObjects.Add(go);
            var source = new Mesh { vertices = new[] { Vector3.zero, Vector3.right, Vector3.up, Vector3.one } };
            ownedMeshes.Add(source);
            source.SetIndices(
                topology == MeshTopology.Quads ? new[] { 0, 1, 2, 3 }
                    : topology == MeshTopology.Triangles ? new[] { 0, 1, 2 }
                    : new[] { 0, 1 },
                topology,
                0
            );
            go.AddComponent<MeshFilter>().sharedMesh = source;
            go.AddComponent<MeshRenderer>();
            return go;
        }

        private JObject MergeSources(GameObject first, GameObject second)
        {
            return JObject.FromObject(
                ManageProBuilder.HandleCommand(
                    new JObject
                    {
                        ["action"] = "merge_objects",
                        ["properties"] = new JObject
                        {
                            ["targets"] = new JArray(first.GetInstanceIDCompat().ToString(), second.GetInstanceIDCompat().ToString()),
                        },
                    }
                )
            );
        }

        [TestCase("{\"a\":-1,\"b\":1}")]
        [TestCase("{\"a\":0,\"b\":2}")]
        [TestCase("{\"a\":2,\"b\":0}")]
        [TestCase("{}")]
        [TestCase("{\"a\":0}")]
        public void InvalidInsertEdgeStopsBeforeNativeEditAndUndo(string edge)
        {
            SaveOwnedScene();
            var response = Dispatch("insert_vertex", "{\"edge\":" + edge + ",\"point\":[0,0,0]}");
            Assert.That(response.Value<bool>("success"), Is.False, response.ToString());
            Assert.That(ProBuilderContractAppend.Calls, Is.Zero);
            Assert.That(mesh.Refreshes, Is.Zero);
            Assert.That(ownedScene.isDirty, Is.False);
        }

        [TestCase("[]")]
        [TestCase("[-1,0]")]
        [TestCase("[0,2]")]
        public void InvalidMergeVerticesStopsBeforeNativeEditAndUndo(string indices)
        {
            SaveOwnedScene();
            var response = Dispatch("merge_vertices", "{\"vertexIndices\":" + indices + "}");
            Assert.That(response.Value<bool>("success"), Is.False, response.ToString());
            Assert.That(ProBuilderContractVertexEditing.Calls, Is.Zero);
            Assert.That(mesh.Refreshes, Is.Zero);
            Assert.That(ownedScene.isDirty, Is.False);
        }

        [Test]
        public void ValidZeroVertexIndicesRemainSupported()
        {
            Assert.That(Dispatch("insert_vertex", "{\"edge\":{\"a\":\"0\",\"b\":1},\"point\":[0,0,0]}").Value<bool>("success"), Is.True);
            Assert.That(Dispatch("merge_vertices", "{\"vertexIndices\":[\"0\",1],\"collapseToFirst\":false}").Value<bool>("success"), Is.True);
            Assert.That(ProBuilderContractAppend.Calls, Is.EqualTo(1));
            Assert.That(ProBuilderContractVertexEditing.Calls, Is.EqualTo(1));
        }

        [TestCase("weld_vertices", "[-1]")]
        [TestCase("weld_vertices", "[0,2]")]
        [TestCase("create_polygon", "[-1]")]
        [TestCase("create_polygon", "[0,2]")]
        public void InvalidVertexSelectorStopsBeforeWeldPolygonAndUndo(string action, string indices)
        {
            SaveOwnedScene();
            var response = Dispatch(action, "{\"vertexIndices\":" + indices + "}");
            Assert.That(response.Value<bool>("success"), Is.False, response.ToString());
            Assert.That(ProBuilderContractVertexEditing.WeldCalls, Is.Zero);
            Assert.That(ProBuilderContractAppend.PolygonCalls, Is.Zero);
            Assert.That(mesh.Refreshes, Is.Zero);
            Assert.That(ownedScene.isDirty, Is.False);
        }

        [Test]
        public void WeldAndPolygonAcceptZeroAndLastVertexIndices()
        {
            mesh.positions = new List<Vector3> { Vector3.zero, Vector3.right, Vector3.up };
            var weld = Dispatch("weld_vertices", "{\"vertexIndices\":[\"0\",2],\"radius\":0}");
            var polygon = Dispatch("create_polygon", "{\"vertex_indices\":[\"0\",1,2],\"unordered\":false}");
            Assert.That(weld.Value<bool>("success"), Is.True, weld.ToString());
            Assert.That(polygon.Value<bool>("success"), Is.True, polygon.ToString());
            Assert.That(ProBuilderContractVertexEditing.WeldCalls, Is.EqualTo(1));
            Assert.That(ProBuilderContractVertexEditing.LastRadius, Is.Zero);
            Assert.That(ProBuilderContractAppend.PolygonCalls, Is.EqualTo(1));
            Assert.That(ProBuilderContractAppend.LastUnordered, Is.False);
        }

        [TestCase("{\"a\":-1,\"b\":0}", "{\"a\":0,\"b\":1}")]
        [TestCase("{\"a\":0,\"b\":1}", "{\"a\":0,\"b\":2}")]
        [TestCase("{}", "{\"a\":0,\"b\":1}")]
        public void InvalidBridgeEdgesStopBeforeNativeEditAndUndo(string edgeA, string edgeB)
        {
            SaveOwnedScene();
            var response = Dispatch("bridge_edges", "{\"edgeA\":" + edgeA + ",\"edgeB\":" + edgeB + ",\"allowNonManifold\":true}");
            Assert.That(response.Value<bool>("success"), Is.False, response.ToString());
            Assert.That(ProBuilderContractAppend.BridgeCalls, Is.Zero);
            Assert.That(mesh.Refreshes, Is.Zero);
            Assert.That(ownedScene.isDirty, Is.False);
        }

        [Test]
        public void ValidBridgeNullResultRetainsSuccessfulNoOpContract()
        {
            var response = Dispatch("bridge_edges", "{\"edge_a\":{\"a\":\"0\",\"b\":1},\"edge_b\":{\"a\":1,\"b\":0},\"allow_non_manifold\":false}");
            Assert.That(response.Value<bool>("success"), Is.True, response.ToString());
            Assert.That(response["data"].Value<bool>("bridgeCreated"), Is.False);
            Assert.That(ProBuilderContractAppend.BridgeCalls, Is.EqualTo(1));
        }

        [TestCase("weld_vertices", "vertexIndices")]
        [TestCase("create_polygon", "vertexIndices")]
        [TestCase("split_vertices", "vertexIndices")]
        [TestCase("delete_faces", "faceIndices")]
        public void NullSelectorsStopBeforeNativeEditAndUndo(string action, string field)
        {
            SaveOwnedScene();
            var response = Dispatch(action, "{\"" + field + "\":null}");
            Assert.That(response.Value<bool>("success"), Is.False, response.ToString());
            Assert.That(mesh.Refreshes, Is.Zero);
            Assert.That(ownedScene.isDirty, Is.False);
        }

        [TestCase("weld_vertices", "vertexIndices")]
        [TestCase("create_polygon", "vertexIndices")]
        [TestCase("split_vertices", "vertexIndices")]
        [TestCase("delete_faces", "faceIndices")]
        public void EmptySelectorsRetainAcceptedNoOpContract(string action, string field)
        {
            var response = Dispatch(action, "{\"" + field + "\":[]}");
            Assert.That(response.Value<bool>("success"), Is.True, response.ToString());
            Assert.That(mesh.Refreshes, Is.EqualTo(1));
        }

        [TestCase(MeshTopology.Lines, false)]
        [TestCase(MeshTopology.Points, false)]
        [TestCase(MeshTopology.Triangles, true)]
        public void InvalidLaterImportSourceDoesNotConvertEarlierObject(MeshTopology topology, bool unreadable)
        {
            var first = ImportSource(MeshTopology.Triangles);
            var second = ImportSource(topology);
            var firstMesh = first.GetComponent<MeshFilter>().sharedMesh;
            var secondMesh = second.GetComponent<MeshFilter>().sharedMesh;
            if (unreadable)
                secondMesh.UploadMeshData(true);
            SaveOwnedScene();
            var response = MergeSources(first, second);
            Assert.That(response.Value<bool>("success"), Is.False, response.ToString());
            Assert.That(ProBuilderContractImporter.Calls, Is.Zero);
            Assert.That(ProBuilderContractCombine.Calls, Is.Zero);
            Assert.That(first.GetComponent<ProBuilderContractMesh>(), Is.Null);
            Assert.That(second.GetComponent<ProBuilderContractMesh>(), Is.Null);
            Assert.That(first.GetComponent<MeshFilter>().sharedMesh, Is.EqualTo(firstMesh));
            Assert.That(second.GetComponent<MeshFilter>().sharedMesh, Is.EqualTo(secondMesh));
            Assert.That(ownedScene.isDirty, Is.False);
        }

        [TestCase(MeshTopology.Triangles)]
        [TestCase(MeshTopology.Quads)]
        public void SupportedImportTopologiesReachImporter(MeshTopology topology)
        {
            var response = MergeSources(ImportSource(MeshTopology.Triangles), ImportSource(topology));
            Assert.That(response.Value<bool>("success"), Is.True, response.ToString());
            Assert.That(ProBuilderContractImporter.Calls, Is.EqualTo(2));
            Assert.That(ProBuilderContractCombine.Calls, Is.EqualTo(1));
        }

        [Test]
        public void InvalidShapeWidthDoesNotAdvanceUndoGroup()
        {
            int group = Undo.GetCurrentGroup();
            var response = Dispatch("create_shape", "{\"shapeType\":\"Cube\",\"width\":\"bad\"}");
            Assert.That(response.Value<bool>("success"), Is.False, response.ToString());
            Assert.That(ProBuilderContractGenerator.Created, Is.Empty);
            Assert.That(Undo.GetCurrentGroup(), Is.EqualTo(group));
        }

        [Test]
        public void ValidShapeDimensionsReachGenerator()
        {
            var response = Dispatch("create_shape", "{\"shapeType\":\"Cube\",\"width\":2,\"height\":3,\"depth\":4}");
            Assert.That(response.Value<bool>("success"), Is.True, response.ToString());
            Assert.That(ProBuilderContractGenerator.Size, Is.EqualTo(new Vector3(2, 3, 4)));
            Assert.That(ProBuilderContractGenerator.Created.Count, Is.EqualTo(1));
        }

        private static void SetField(string name, object value)
        {
            typeof(ManageProBuilder).GetField(name, BindingFlags.NonPublic | BindingFlags.Static).SetValue(null, value);
        }

        private JObject Dispatch(string action, string properties)
        {
            return JObject.FromObject(
                ManageProBuilder.HandleCommand(
                    new JObject
                    {
                        ["action"] = action,
                        ["target"] = target.GetInstanceIDCompat().ToString(),
                        ["searchMethod"] = "by_id",
                        ["properties"] = JObject.Parse(properties),
                    }
                )
            );
        }

        [Test]
        public void RefreshUsesDeclaredOptionalDefaults()
        {
            ManageProBuilder.RefreshMesh(mesh);
            Assert.That(mesh.LastMask, Is.EqualTo(ProBuilderContractRefreshMask.All));
            Assert.That(mesh.LastTopology, Is.EqualTo(MeshTopology.Triangles));
            Assert.That(mesh.Refreshes, Is.EqualTo(1));
            Assert.That(mesh.ToMeshes, Is.EqualTo(1));
        }

        [Test]
        public void ParameterlessRefreshRemainsSupported()
        {
            SetField("_proBuilderMeshType", typeof(ProBuilderContractParameterlessMesh));
            var parameterless = target.AddComponent<ProBuilderContractParameterlessMesh>();
            ManageProBuilder.RefreshMesh(parameterless);
            Assert.That(parameterless.Refreshes, Is.EqualTo(1));
            Assert.That(parameterless.ToMeshes, Is.EqualTo(1));
        }

        [TestCase("\"garbage\"")]
        [TestCase("[1,2]")]
        [TestCase("null")]
        [TestCase("{\"x\":1}")]
        public void InvalidVectorsDoNotWriteVerticesOrMovePivot(string vector)
        {
            var move = Dispatch("move_vertices", "{\"vertexIndices\":[0],\"offset\":" + vector + "}");
            var pivot = Dispatch("set_pivot", "{\"position\":" + vector + "}");
            Assert.That(move["success"].Value<bool>(), Is.False);
            Assert.That(pivot["success"].Value<bool>(), Is.False);
            Assert.That(mesh.Sets, Is.Zero);
            Assert.That(mesh.Refreshes, Is.Zero);
            Assert.That(mesh.positions[0], Is.EqualTo(new Vector3(1, 2, 3)));
            Assert.That(target.transform.position, Is.EqualTo(new Vector3(10, 0, 0)));
        }

        [TestCase("[{}]")]
        [TestCase("[{\"a\":1}]")]
        [TestCase("[{\"a\":0,\"b\":0.5}]")]
        [TestCase("[{\"a\":0,\"b\":1.0}]")]
        [TestCase("[{\"a\":0,\"b\":-1}]")]
        [TestCase("[{\"a\":0,\"b\":2}]")]
        public void InvalidExplicitEdgesStopBeforeExtrusion(string edges)
        {
            var response = Dispatch("extrude_edges", "{\"edges\":" + edges + "}");
            Assert.That(response["success"].Value<bool>(), Is.False);
            Assert.That(response["error"].ToString(), Does.Contain("edge").IgnoreCase);
            Assert.That(ProBuilderContractExtrude.Calls, Is.Zero);
            Assert.That(mesh.Refreshes, Is.Zero);
        }

        [Test]
        public void NumericStringsRepeatedIndicesAndZeroOffsetRemainSupported()
        {
            Assert.That(Dispatch("move_vertices", "{\"vertexIndices\":[\"0\",0],\"offset\":[\"1\",\"0\",\"0\"]}")["success"].Value<bool>(), Is.True);
            Assert.That(mesh.positions[0], Is.EqualTo(new Vector3(3, 2, 3)));
            Assert.That(Dispatch("move_vertices", "{\"vertexIndices\":[0],\"offset\":[0,0,0]}")["success"].Value<bool>(), Is.True);
            Assert.That(mesh.positions[0], Is.EqualTo(new Vector3(3, 2, 3)));
        }

        [Test]
        public void ZeroPivotAndObjectAliasRemainSupported()
        {
            Assert.That(Dispatch("set_pivot", "{\"position\":[0,0,0]}")["success"].Value<bool>(), Is.True);
            Assert.That(target.transform.position, Is.EqualTo(Vector3.zero));
            Assert.That(mesh.positions[0], Is.EqualTo(new Vector3(11, 2, 3)));
            Assert.That(Dispatch("set_pivot", "{\"world_position\":{\"x\":12,\"y\":0,\"z\":0}}")["success"].Value<bool>(), Is.True);
            Assert.That(target.transform.position.x, Is.EqualTo(12));
            Assert.That(mesh.positions[0].x, Is.EqualTo(-1));
        }

        [Test]
        public void ExplicitEdgeNumbersFalseAndZeroRemainSupported()
        {
            var response = Dispatch("extrude_edges", "{\"edges\":[{\"a\":\"0\",\"b\":1},{\"a\":1,\"b\":0}],\"distance\":0,\"asGroup\":false}");
            Assert.That(response["success"].Value<bool>(), Is.True, response.ToString());
            Assert.That(ProBuilderContractExtrude.Last.Length, Is.EqualTo(2));
            Assert.That(ProBuilderContractExtrude.Last[0].a, Is.Zero);
            Assert.That(ProBuilderContractExtrude.Last[1].a, Is.EqualTo(1));
            Assert.That(ProBuilderContractExtrude.AsGroup, Is.False);
            Assert.That(ProBuilderContractExtrude.Distance, Is.Zero);
            Assert.That(mesh.VertexReads, Is.EqualTo(1));
        }

        [Test]
        public void SharedVertexEdgesRemainDeduplicated()
        {
            mesh.positions = new List<Vector3> { Vector3.zero, Vector3.one, Vector3.zero, Vector3.one };
            mesh.sharedVertices = new[] { new[] { 0, 2 }, new[] { 1, 3 } };
            mesh.faces[1].edges = new[] { new ProBuilderContractEdge(2, 3) };
            Assert.That(ManageProBuilder.CollectUniqueEdges(mesh).Count, Is.EqualTo(1));
            Assert.That(Dispatch("extrude_edges", "{\"edge_indices\":[0]}")["success"].Value<bool>(), Is.True);
            Assert.That(ProBuilderContractExtrude.Last.Length, Is.EqualTo(1));
        }

        [Test]
        public void FaceSelectionDistinguishesOmittedNullAndEmpty()
        {
            Assert.That(ManageProBuilder.GetFacesByIndices(mesh, null).Length, Is.EqualTo(2));
            Assert.That(ManageProBuilder.GetFacesByIndices(mesh, new JArray()).Length, Is.Zero);
            Assert.Throws<ArgumentException>(() => ManageProBuilder.GetFacesByIndices(mesh, JValue.CreateNull()));
            Assert.That(Dispatch("set_smoothing", "{\"faceIndices\":null,\"smoothingGroup\":5}")["success"].Value<bool>(), Is.False);
            Assert.That(Dispatch("connect_elements", "{\"faceIndices\":null,\"edges\":[{\"a\":0,\"b\":1}]}")["success"].Value<bool>(), Is.False);
            Assert.That(mesh.faces[0].smoothingGroup, Is.Zero);
            Assert.That(mesh.faces[1].smoothingGroup, Is.Zero);
            Assert.That(ProBuilderContractConnect.FaceCalls + ProBuilderContractConnect.EdgeCalls, Is.Zero);
            Assert.That(mesh.Refreshes, Is.Zero);
        }

        [TestCase(false)]
        [TestCase(true)]
        public void FaceUVSettingsWritePropertiesOnSelectedFaces(bool jsonProperties)
        {
            var properties = JObject.Parse("{\"faceIndices\":[0],\"scale\":[2,3],\"offset\":[4,5],\"rotation\":45,\"flip_u\":true,\"flip_v\":true}");
            var response = JObject.FromObject(
                ManageProBuilder.HandleCommand(
                    new JObject
                    {
                        ["action"] = "set_face_uvs",
                        ["target"] = target.GetInstanceIDCompat().ToString(),
                        ["searchMethod"] = "by_id",
                        ["properties"] = jsonProperties ? (JToken)new JValue(properties.ToString()) : properties,
                    }
                )
            );
            Assert.That(response["success"].Value<bool>(), Is.True, response.ToString());
            Assert.That(mesh.faces[0].uv.scale, Is.EqualTo(new Vector2(2, 3)));
            Assert.That(mesh.faces[0].uv.offset, Is.EqualTo(new Vector2(4, 5)));
            Assert.That(mesh.faces[0].uv.rotation, Is.EqualTo(45));
            Assert.That(mesh.faces[0].uv.flipU, Is.True);
            Assert.That(mesh.faces[0].uv.flipV, Is.True);
            Assert.That(mesh.faces[1].uv.scale, Is.EqualTo(Vector2.zero));
            Assert.That(mesh.faces[1].uv.offset, Is.EqualTo(Vector2.zero));
            Assert.That(mesh.faces[1].uv.rotation, Is.Zero);
            Assert.That(mesh.faces[1].uv.flipU, Is.False);
            Assert.That(mesh.faces[1].uv.flipV, Is.False);
            Assert.That(mesh.Refreshes, Is.EqualTo(1));
        }

        [TestCase("by_id")]
        [TestCase("by_name")]
        [TestCase("by_path")]
        public void ExplicitTargetSelectorsRemainSupported(string searchMethod)
        {
            target.name = "123";
            var response = JObject.FromObject(
                ManageProBuilder.HandleCommand(
                    new JObject
                    {
                        ["action"] = "move_vertices",
                        ["target"] = searchMethod == "by_id" ? target.GetInstanceIDCompat().ToString() : target.name,
                        ["searchMethod"] = searchMethod,
                        ["properties"] = JObject.Parse("{\"vertexIndices\":[0],\"offset\":[1,0,0]}"),
                    }
                )
            );
            Assert.That(response["success"].Value<bool>(), Is.True);
            Assert.That(mesh.positions[0].x, Is.EqualTo(2));
        }
    }

    public enum ProBuilderContractRefreshMask
    {
        None = 0,
        All = 255,
    }

    public class ProBuilderContractMesh : MonoBehaviour
    {
        private IList<Vector3> vertexPositions = new List<Vector3> { new Vector3(1, 2, 3), new Vector3(4, 5, 6) };
        public int Sets,
            Refreshes,
            ToMeshes,
            VertexReads;
        public ProBuilderContractRefreshMask LastMask;
        public MeshTopology LastTopology;
        public IList<Vector3> positions
        {
            get => vertexPositions;
            set
            {
                Sets++;
                vertexPositions = value;
            }
        }
        public int vertexCount
        {
            get
            {
                VertexReads++;
                return positions.Count;
            }
        }
        public int faceCount => faces.Length;
        public ProBuilderContractFace[] faces { get; } = new[] { new ProBuilderContractFace(), new ProBuilderContractFace() };
        public int[][] sharedVertices { get; set; }

        public void ToMesh(MeshTopology topology = MeshTopology.Triangles)
        {
            ToMeshes++;
            LastTopology = topology;
        }

        public void Refresh(ProBuilderContractRefreshMask mask = ProBuilderContractRefreshMask.All)
        {
            Refreshes++;
            LastMask = mask;
        }
    }

    public class ProBuilderContractParameterlessMesh : MonoBehaviour
    {
        public int Refreshes,
            ToMeshes;

        public void ToMesh()
        {
            ToMeshes++;
        }

        public void Refresh()
        {
            Refreshes++;
        }
    }

    public class ProBuilderContractEdge
    {
        public int a,
            b;

        public ProBuilderContractEdge(int a, int b)
        {
            this.a = a;
            this.b = b;
        }
    }

    public class ProBuilderContractFace
    {
        public ProBuilderContractEdge[] edges { get; set; } = new[] { new ProBuilderContractEdge(0, 1) };
        public int smoothingGroup { get; set; }
        public ProBuilderContractUVSettings uv { get; set; }
    }

    public struct ProBuilderContractUVSettings
    {
        public Vector2 scale { get; set; }
        public Vector2 offset { get; set; }
        public float rotation { get; set; }
        public bool flipU { get; set; }
        public bool flipV { get; set; }
    }

    public static class ProBuilderContractExtrude
    {
        public static int Calls;
        public static ProBuilderContractEdge[] Last;
        public static bool AsGroup;
        public static float Distance;

        public static void Extrude(ProBuilderContractMesh mesh, ProBuilderContractEdge[] edges, float distance, bool asGroup, bool ignored)
        {
            Calls++;
            Last = edges;
            Distance = distance;
            AsGroup = asGroup;
        }
    }

    public static class ProBuilderContractConnect
    {
        public static int FaceCalls,
            EdgeCalls;

        public static void Connect(ProBuilderContractMesh mesh, IEnumerable<ProBuilderContractFace> faces)
        {
            FaceCalls++;
        }

        public static void Connect(ProBuilderContractMesh mesh, IEnumerable<ProBuilderContractEdge> edges)
        {
            EdgeCalls++;
        }
    }

    public static class ProBuilderContractAppend
    {
        public static int Calls;
        public static int PolygonCalls;
        public static bool LastUnordered;
        public static int BridgeCalls;

        public static void InsertVertexOnEdge(ProBuilderContractMesh mesh, ProBuilderContractEdge edge, Vector3 point)
        {
            Calls++;
            if (edge.a < 0 || edge.b < 0 || edge.a >= mesh.vertexCount || edge.b >= mesh.vertexCount)
                throw new ArgumentOutOfRangeException(nameof(edge));
        }

        public static ProBuilderContractFace CreatePolygon(ProBuilderContractMesh mesh, IList<int> indices, bool unordered)
        {
            PolygonCalls++;
            LastUnordered = unordered;
            foreach (int index in indices)
                if (index < 0 || index >= mesh.vertexCount)
                    throw new KeyNotFoundException("Vertex has no shared handle.");
            return new ProBuilderContractFace();
        }

        public static ProBuilderContractFace Bridge(
            ProBuilderContractMesh mesh,
            ProBuilderContractEdge first,
            ProBuilderContractEdge second,
            bool allowNonManifold
        )
        {
            BridgeCalls++;
            foreach (int index in new[] { first.a, first.b, second.a, second.b })
                if (index < 0 || index >= mesh.vertexCount)
                    throw new KeyNotFoundException("Vertex has no shared handle.");
            return null;
        }
    }

    public static class ProBuilderContractVertexEditing
    {
        public static int Calls;
        public static int WeldCalls;
        public static float LastRadius;
        public static int SplitCalls;

        public static int MergeVertices(ProBuilderContractMesh mesh, int[] indices, bool collapseToFirst)
        {
            Calls++;
            if (indices.Length == 0)
                throw new ArgumentException("At least one index is required.");
            foreach (int index in indices)
                if (index < 0 || index >= mesh.vertexCount)
                    throw new ArgumentOutOfRangeException(nameof(indices));
            return indices[0];
        }

        public static int[] WeldVertices(ProBuilderContractMesh mesh, IEnumerable<int> indices, float neighborRadius)
        {
            WeldCalls++;
            LastRadius = neighborRadius;
            var result = new List<int>();
            foreach (int index in indices)
            {
                if (index < 0 || index >= mesh.vertexCount)
                    throw new KeyNotFoundException("Vertex has no shared handle.");
                result.Add(index);
            }
            return result.ToArray();
        }

        public static void SplitVertices(ProBuilderContractMesh mesh, IEnumerable<int> indices)
        {
            SplitCalls++;
        }
    }

    public static class ProBuilderContractDelete
    {
        public static int Calls;

        public static void DeleteFaces(ProBuilderContractMesh mesh, IList<int> indices)
        {
            Calls++;
        }
    }

    public sealed class ProBuilderContractImporter
    {
        public static int Calls;
        private readonly Mesh source;

        public ProBuilderContractImporter(Mesh source, Material[] materials, ProBuilderContractMesh destination)
        {
            this.source = source;
        }

        public void Import()
        {
            Calls++;
            if (!source.isReadable)
                throw new ArgumentException("Source mesh is not readable.");
            for (int i = 0; i < source.subMeshCount; i++)
                if (source.GetTopology(i) != MeshTopology.Triangles && source.GetTopology(i) != MeshTopology.Quads)
                    throw new ArgumentException("Unsupported source topology.");
        }
    }

    public static class ProBuilderContractCombine
    {
        public static int Calls;

        public static void Combine(IEnumerable<ProBuilderContractMesh> meshes, ProBuilderContractMesh destination)
        {
            Calls++;
        }
    }

    public enum ProBuilderContractShape
    {
        Cube,
    }

    public enum ProBuilderContractPivot
    {
        Center,
    }

    public static class ProBuilderContractGenerator
    {
        public static readonly List<GameObject> Created = new List<GameObject>();
        public static Vector3 Size;

        public static ProBuilderContractMesh GenerateCube(ProBuilderContractPivot pivot, Vector3 size)
        {
            Size = size;
            var go = new GameObject("GeneratedContractCube");
            Created.Add(go);
            return go.AddComponent<ProBuilderContractMesh>();
        }
    }
}
