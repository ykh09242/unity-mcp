using System;
using System.Collections.Generic;
using System.Reflection;
using MCPForUnity.Editor.Tools.ProBuilder;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEngine;
using MCPForUnity.Runtime.Helpers;

namespace MCPForUnityTests.Editor.Tools
{
    // Package-shaped components exercise production dispatch/reflection without requiring ProBuilder.
    // These fixtures do not assert ProBuilder's native geometry implementation.
    public class ManageProBuilderContractTests
    {
        private readonly Dictionary<FieldInfo, object> savedFields = new Dictionary<FieldInfo, object>();
        private GameObject target;
        private ProBuilderContractMesh mesh;

        [SetUp]
        public void SetUp()
        {
            foreach (string name in new[] { "_typesResolved", "_proBuilderAvailable", "_proBuilderMeshType",
                "_faceType", "_edgeType", "_editorMeshUtilityType", "_extrudeElementsType", "_connectElementsType" })
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
            ProBuilderContractExtrude.Calls = 0;
            ProBuilderContractConnect.FaceCalls = ProBuilderContractConnect.EdgeCalls = 0;
            target = new GameObject("ProBuilderContractTarget");
            mesh = target.AddComponent<ProBuilderContractMesh>();
            target.transform.position = new Vector3(10, 0, 0);
        }

        [TearDown]
        public void TearDown()
        {
            foreach (var saved in savedFields)
                saved.Key.SetValue(null, saved.Value);
            savedFields.Clear();
            if (target != null)
                UnityEngine.Object.DestroyImmediate(target);
        }

        private static void SetField(string name, object value)
        {
            typeof(ManageProBuilder).GetField(name, BindingFlags.NonPublic | BindingFlags.Static).SetValue(null, value);
        }

        private JObject Dispatch(string action, string properties)
        {
            return JObject.FromObject(ManageProBuilder.HandleCommand(new JObject
            {
                ["action"] = action,
                ["target"] = target.GetInstanceIDCompat().ToString(),
                ["searchMethod"] = "by_id",
                ["properties"] = JObject.Parse(properties),
            }));
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
            var response = Dispatch("extrude_edges", "{\"edges\":[{\"a\":\"0\",\"b\":1.0},{\"a\":1,\"b\":0}],\"distance\":0,\"asGroup\":false}");
            Assert.That(response["success"].Value<bool>(), Is.True);
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

        [TestCase("by_id")]
        [TestCase("by_name")]
        [TestCase("by_path")]
        public void ExplicitTargetSelectorsRemainSupported(string searchMethod)
        {
            target.name = "123";
            var response = JObject.FromObject(ManageProBuilder.HandleCommand(new JObject
            {
                ["action"] = "move_vertices",
                ["target"] = searchMethod == "by_id" ? target.GetInstanceIDCompat().ToString() : target.name,
                ["searchMethod"] = searchMethod,
                ["properties"] = JObject.Parse("{\"vertexIndices\":[0],\"offset\":[1,0,0]}"),
            }));
            Assert.That(response["success"].Value<bool>(), Is.True);
            Assert.That(mesh.positions[0].x, Is.EqualTo(2));
        }
    }

    public enum ProBuilderContractRefreshMask { None = 0, All = 255 }
    public class ProBuilderContractMesh : MonoBehaviour
    {
        private IList<Vector3> vertexPositions = new List<Vector3> { new Vector3(1, 2, 3), new Vector3(4, 5, 6) };
        public int Sets, Refreshes, ToMeshes, VertexReads;
        public ProBuilderContractRefreshMask LastMask;
        public MeshTopology LastTopology;
        public IList<Vector3> positions { get => vertexPositions; set { Sets++; vertexPositions = value; } }
        public int vertexCount { get { VertexReads++; return positions.Count; } }
        public int faceCount => faces.Length;
        public ProBuilderContractFace[] faces { get; } = new[] { new ProBuilderContractFace(), new ProBuilderContractFace() };
        public int[][] sharedVertices { get; set; }
        public void ToMesh(MeshTopology topology = MeshTopology.Triangles) { ToMeshes++; LastTopology = topology; }
        public void Refresh(ProBuilderContractRefreshMask mask = ProBuilderContractRefreshMask.All) { Refreshes++; LastMask = mask; }
    }
    public class ProBuilderContractParameterlessMesh : MonoBehaviour
    {
        public int Refreshes, ToMeshes;
        public void ToMesh() { ToMeshes++; }
        public void Refresh() { Refreshes++; }
    }
    public class ProBuilderContractEdge
    {
        public int a, b;
        public ProBuilderContractEdge(int a, int b) { this.a = a; this.b = b; }
    }
    public class ProBuilderContractFace
    {
        public ProBuilderContractEdge[] edges { get; set; } = new[] { new ProBuilderContractEdge(0, 1) };
        public int smoothingGroup { get; set; }
    }
    public static class ProBuilderContractExtrude
    {
        public static int Calls;
        public static ProBuilderContractEdge[] Last;
        public static bool AsGroup;
        public static float Distance;
        public static void Extrude(ProBuilderContractMesh mesh, ProBuilderContractEdge[] edges, float distance, bool asGroup, bool ignored)
        { Calls++; Last = edges; Distance = distance; AsGroup = asGroup; }
    }
    public static class ProBuilderContractConnect
    {
        public static int FaceCalls, EdgeCalls;
        public static void Connect(ProBuilderContractMesh mesh, IEnumerable<ProBuilderContractFace> faces) { FaceCalls++; }
        public static void Connect(ProBuilderContractMesh mesh, IEnumerable<ProBuilderContractEdge> edges) { EdgeCalls++; }
    }
}
