using System;
using System.Collections;
using System.Collections.Generic;
using System.Linq;
using System.Reflection;
using System.Text;
using MCPForUnity.Editor.Tools.ProBuilder;
using MCPForUnity.Runtime.Helpers;
using MCPForUnityTests.Editor.Helpers;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEditor;
using UnityEngine;

namespace MCPForUnityTests.Editor.Tools
{
    [Parallelizable(ParallelScope.None)]
    public class ProBuilderTopologyTests
    {
        private readonly PrefabTestSceneFixture scenes = new PrefabTestSceneFixture();
        private readonly Dictionary<FieldInfo, object> savedFields = new Dictionary<FieldInfo, object>();
        private readonly HashSet<Mesh> ownedMeshes = new HashSet<Mesh>();
        private Component mesh;
        private Type meshType;
        private Type faceType;

        [OneTimeSetUp]
        public void Prepare() => scenes.PrepareRunnerBootstrap();

        [OneTimeTearDown]
        public void Restore() => scenes.RestoreRunnerBootstrap();

        [SetUp]
        public void SetUp()
        {
            mesh = null;
            savedFields.Clear();
            ownedMeshes.Clear();
            meshType = Type.GetType("UnityEngine.ProBuilder.ProBuilderMesh, Unity.ProBuilder");
            if (meshType == null)
                Assert.Ignore("Optional real ProBuilder package is required for topology acceptance.");
            faceType = Type.GetType("UnityEngine.ProBuilder.Face, Unity.ProBuilder", true);
            scenes.Create("McpProBuilderTopology_", Guid.NewGuid().ToString("N"));
            foreach (FieldInfo field in typeof(ManageProBuilder).GetFields(BindingFlags.Static | BindingFlags.NonPublic | BindingFlags.DeclaredOnly))
                if (field.FieldType == typeof(Type) || field.FieldType == typeof(bool))
                    savedFields.Add(field, field.GetValue(null));
            SetField("_typesResolved", true);
            SetField("_proBuilderAvailable", true);
            SetField("_proBuilderMeshType", meshType);
            SetField("_faceType", faceType);
            foreach (string name in new[] { "edge", "smoothing" })
                SetField(
                    "_" + name + "Type",
                    Type.GetType("UnityEngine.ProBuilder." + char.ToUpperInvariant(name[0]) + name.Substring(1) + ", Unity.ProBuilder", true)
                );
            foreach (string name in new[] { "appendElements", "deleteElements", "vertexEditing" })
                SetField(
                    "_" + name + "Type",
                    Type.GetType("UnityEngine.ProBuilder.MeshOperations." + char.ToUpperInvariant(name[0]) + name.Substring(1) + ", Unity.ProBuilder", true)
                );
            // Use the installed editor optimization path without patching package-default materials.
            SetField("_editorMeshUtilityType", Type.GetType("UnityEditor.ProBuilder.EditorMeshUtility, Unity.ProBuilder.Editor"));
            var positions = new List<Vector3>();
            for (int i = 0; i < 100; i++)
            {
                float angle = i * Mathf.PI * 2 / 100;
                positions.Add(new Vector3(Mathf.Cos(angle) * 10, 0, Mathf.Sin(angle) * 10));
            }
            var triangles = new List<int[]>();
            for (int i = 1; i < 99; i++)
                triangles.Add(new[] { 0, i, i + 1 });
            // A triangulated disk: F=98,E=197,V=100; each interior split adds 2,3,1.
            for (int i = 0; i < 101; i++)
            {
                int[] t = triangles[i];
                int center = positions.Count;
                positions.Add((positions[t[0]] + positions[t[1]] + positions[t[2]]) / 3);
                triangles[i] = new[] { t[0], t[1], center };
                triangles.Add(new[] { t[1], t[2], center });
                triangles.Add(new[] { t[2], t[0], center });
            }
            Array faces = Array.CreateInstance(faceType, triangles.Count);
            for (int i = 0; i < triangles.Count; i++)
                faces.SetValue(Activator.CreateInstance(faceType, new object[] { triangles[i] }), i);
            MethodInfo create = meshType.GetMethod(
                "Create",
                BindingFlags.Static | BindingFlags.Public,
                null,
                new[] { typeof(IEnumerable<Vector3>), typeof(IEnumerable<>).MakeGenericType(faceType) },
                null
            );
            Assert.IsNotNull(create, "The installed package must provide its public mesh-creation API.");
            mesh = (Component)create.Invoke(null, new object[] { positions, faces });
            Assert.IsNotNull(mesh);
            mesh.gameObject.name = "__McpTopology_" + Guid.NewGuid().ToString("N");
            CaptureMesh();
            Assert.AreEqual(300, Faces().Count, "Fixture must contain exactly 300 real ProBuilder faces.");
            Assert.AreEqual(201, Positions().Count, "Fixture must contain exactly 201 real ProBuilder vertices.");
            Assert.AreEqual(500, ManageProBuilder.CollectUniqueEdges(mesh).Count, "Fixture must contain exactly 500 native mutation-indexed edges.");
        }

        [TearDown]
        public void TearDown()
        {
            try
            {
                if (mesh != null)
                {
                    CaptureMesh();
                    Undo.ClearUndo(mesh);
                    Undo.ClearUndo(mesh.gameObject);
                    UnityEngine.Object.DestroyImmediate(mesh.gameObject);
                }
                foreach (Mesh value in ownedMeshes)
                    if (value != null && !AssetDatabase.Contains(value))
                        UnityEngine.Object.DestroyImmediate(value);
            }
            finally
            {
                foreach (var saved in savedFields)
                    saved.Key.SetValue(null, saved.Value);
                savedFields.Clear();
                scenes.Close();
            }
        }

        private static void SetField(string name, object value) =>
            typeof(ManageProBuilder).GetField(name, BindingFlags.Static | BindingFlags.NonPublic).SetValue(null, value);

        private void CaptureMesh()
        {
            var filter = mesh.GetComponent<MeshFilter>();
            if (filter != null && filter.sharedMesh != null)
                ownedMeshes.Add(filter.sharedMesh);
        }

        private IList Faces() => (IList)meshType.GetProperty("faces").GetValue(mesh);

        private IList<Vector3> Positions() => (IList<Vector3>)meshType.GetProperty("positions").GetValue(mesh);

        private JObject Command(string action, JObject properties)
        {
            var result = JObject.FromObject(
                ManageProBuilder.HandleCommand(
                    new JObject
                    {
                        ["action"] = action,
                        ["target"] = mesh.gameObject.GetInstanceIDCompat().ToString(),
                        ["searchMethod"] = "by_id",
                        ["properties"] = properties,
                    }
                )
            );
            return result;
        }

        private JObject Page(string kind, int size = 37, string cursor = null, string revision = null)
        {
            var p = new JObject { ["kind"] = kind, ["page_size"] = size };
            if (cursor != null)
                p["cursor"] = cursor;
            if (revision != null)
                p["mesh_revision"] = revision;
            JObject response = Command("get_topology", p);
            Assert.IsTrue(response.Value<bool>("success"), response.ToString());
            Assert.LessOrEqual(
                Encoding.UTF8.GetByteCount(response.ToString(Formatting.None)),
                256 * 1024,
                "The entire wire response must respect the byte budget."
            );
            return (JObject)response["data"];
        }

        private List<JObject> All(string kind, int total)
        {
            var rows = new List<JObject>();
            string cursor = null;
            string revision = null;
            do
            {
                JObject page = Page(kind, 37, cursor, revision);
                Assert.AreEqual(total, page.Value<int>("total"), kind + " exact total");
                revision = revision ?? page.Value<string>("mesh_revision");
                Assert.AreEqual(revision, page.Value<string>("mesh_revision"), "Unchanged mesh must retain its revision.");
                Assert.AreEqual(rows.Count, page.Value<int>("offset"), "Pages must be contiguous.");
                rows.AddRange(((JArray)page["items"]).Cast<JObject>());
                cursor = page.Value<string>("next_cursor");
                Assert.LessOrEqual(rows.Count, total, "Continuation must terminate without repeating rows.");
            } while (cursor != null);
            CollectionAssert.AreEqual(
                Enumerable.Range(0, total),
                rows.Select(r => r.Value<int>("index")),
                kind + " must expose every native index exactly once."
            );
            return rows;
        }

        [Test]
        public void AllPagesExposeExactly300Faces500Edges201VerticesAndLegacyCaps()
        {
            List<JObject> faces = All("faces", 300);
            for (int i = 0; i < faces.Count; i++)
                CollectionAssert.AreEqual(
                    (IEnumerable)faceType.GetProperty("indexes").GetValue(Faces()[i]),
                    faces[i]["indices"].ToObject<int[]>(),
                    "Face triangle indices must match the package at " + i
                );
            List<JObject> edges = All("edges", 500);
            List<JObject> vertices = All("vertices", 201);
            for (int i = 0; i < vertices.Count; i++)
                CollectionAssert.AreEqual(
                    new[] { Positions()[i].x, Positions()[i].y, Positions()[i].z },
                    vertices[i]["position"].ToObject<float[]>(),
                    "Vertex position must match the package at " + i
                );
            var nativeEdges = ManageProBuilder.CollectUniqueEdges(mesh);
            for (int i = 0; i < nativeEdges.Count; i++)
            {
                object e = nativeEdges[i];
                Assert.AreEqual((int)e.GetType().GetField("a").GetValue(e), edges[i].Value<int>("vertexA"), "Edge A ordering at " + i);
                Assert.AreEqual((int)e.GetType().GetField("b").GetValue(e), edges[i].Value<int>("vertexB"), "Edge B ordering at " + i);
            }
            JObject legacy = Command("get_mesh_info", new JObject { ["include"] = "all" });
            Assert.IsTrue(legacy.Value<bool>("success"), legacy.ToString());
            Assert.AreEqual(100, ((JArray)legacy["data"]["faces"]).Count);
            Assert.AreEqual(200, ((JArray)legacy["data"]["edges"]).Count);
            Assert.IsTrue(legacy["data"].Value<bool>("truncated"));
            Assert.IsTrue(legacy["data"].Value<bool>("edgesTruncated"));
        }

        [Test]
        public void FinalFaceMutationUsesRevisionAndNativeIndex()
        {
            string revision = Page("faces").Value<string>("mesh_revision");
            All("faces", 300);
            JObject result = Command(
                "set_smoothing",
                new JObject
                {
                    ["faceIndices"] = new JArray(299),
                    ["smoothingGroup"] = 42,
                    ["mesh_revision"] = revision,
                }
            );
            Assert.IsTrue(result.Value<bool>("success"), result.ToString());
            Assert.AreEqual(42, faceType.GetProperty("smoothingGroup").GetValue(Faces()[299]), "Final native face must be modified.");
            Assert.AreNotEqual(42, faceType.GetProperty("smoothingGroup").GetValue(Faces()[298]), "Adjacent face must remain unchanged.");
            Assert.AreNotEqual(revision, Page("faces").Value<string>("mesh_revision"), "Returned face attributes participate in revision.");
        }

        [Test]
        public void FinalEdgeMutationUsesRevisionAndNativeIndex()
        {
            JObject first = Page("edges");
            JObject last = All("edges", 500).Last();
            Vector3 a = Positions()[last.Value<int>("vertexA")];
            Vector3 b = Positions()[last.Value<int>("vertexB")];
            Vector3 midpoint = (a + b) / 2;
            JObject result = Command(
                "append_vertices_to_edge",
                new JObject
                {
                    ["edgeIndices"] = new JArray(499),
                    ["count"] = 1,
                    ["mesh_revision"] = first["mesh_revision"],
                }
            );
            Assert.IsTrue(result.Value<bool>("success"), result.ToString());
            Assert.Greater(Positions().Count, 201, "Inserting on the final edge must add real mesh vertices.");
            Assert.IsTrue(Positions().Any(v => Vector3.Distance(v, midpoint) < 0.00001f), "A new vertex must occur at the selected final edge midpoint.");
            Assert.AreNotEqual(first.Value<string>("mesh_revision"), Page("edges").Value<string>("mesh_revision"));
        }

        [Test]
        public void FinalVertexMutationUsesRevisionAndNativeIndex()
        {
            string revision = Page("vertices").Value<string>("mesh_revision");
            All("vertices", 201);
            Vector3[] before = Positions().ToArray();
            JObject result = Command(
                "move_vertices",
                new JObject
                {
                    ["vertexIndices"] = new JArray(200),
                    ["offset"] = new JArray(0, 0.125f, 0),
                    ["mesh_revision"] = revision,
                }
            );
            Assert.IsTrue(result.Value<bool>("success"), result.ToString());
            Assert.AreEqual(before[200] + new Vector3(0, 0.125f, 0), Positions()[200], "The final native vertex must move.");
            CollectionAssert.AreEqual(before.Take(200), Positions().Take(200), "Other positions must be preserved.");
            Assert.AreNotEqual(revision, Page("vertices").Value<string>("mesh_revision"));
        }

        [Test]
        public void ExternalDriftRejectsContinuationAndMutationWithoutFurtherWrites()
        {
            JObject first = Page("faces");
            var values = Positions().ToList();
            values[200] += Vector3.up;
            meshType.GetProperty("positions").SetValue(mesh, values);
            Vector3[] afterExternalEdit = Positions().ToArray();
            JObject stalePage = Command(
                "get_topology",
                new JObject
                {
                    ["kind"] = "faces",
                    ["page_size"] = 37,
                    ["cursor"] = first["next_cursor"],
                    ["mesh_revision"] = first["mesh_revision"],
                }
            );
            Assert.IsFalse(stalePage.Value<bool>("success"), stalePage.ToString());
            Assert.AreEqual("stale_mesh_revision", stalePage.Value<string>("code"), stalePage.ToString());
            JObject staleMutation = Command(
                "move_vertices",
                new JObject
                {
                    ["vertexIndices"] = new JArray(200),
                    ["offset"] = new JArray(0, 2, 0),
                    ["mesh_revision"] = first["mesh_revision"],
                }
            );
            Assert.IsFalse(staleMutation.Value<bool>("success"), staleMutation.ToString());
            Assert.AreEqual("stale_mesh_revision", staleMutation.Value<string>("code"), staleMutation.ToString());
            CollectionAssert.AreEqual(afterExternalEdit, Positions(), "Refused requests must not mutate positions.");
            Assert.AreEqual(300, Faces().Count, "Refused requests must preserve topology.");
        }

        [Test]
        public void TargetRebindingAndVertexBudgetRefuseWithoutMutations()
        {
            JObject first = Page("faces");
            GameObject clone = UnityEngine.Object.Instantiate(mesh.gameObject);
            try
            {
                JObject rebound = JObject.FromObject(
                    ManageProBuilder.HandleCommand(
                        new JObject
                        {
                            ["action"] = "get_topology",
                            ["target"] = clone.GetInstanceIDCompat().ToString(),
                            ["searchMethod"] = "by_id",
                            ["properties"] = new JObject
                            {
                                ["kind"] = "faces",
                                ["page_size"] = 37,
                                ["cursor"] = first["next_cursor"],
                                ["mesh_revision"] = first["mesh_revision"],
                            },
                        }
                    )
                );
                Assert.IsFalse(rebound.Value<bool>("success"), "A cursor cannot be rebound to an identical copied mesh: " + rebound);
                Assert.AreEqual("stale_mesh_revision", rebound.Value<string>("code"), rebound.ToString());
            }
            finally
            {
                var cloneFilter = clone.GetComponent<MeshFilter>();
                if (cloneFilter != null && cloneFilter.sharedMesh != null)
                    ownedMeshes.Add(cloneFilter.sharedMesh);
                UnityEngine.Object.DestroyImmediate(clone);
            }
            var oversized = Positions().ToList();
            while (oversized.Count < 32769)
                oversized.Add(Vector3.zero);
            meshType.GetProperty("positions").SetValue(mesh, oversized);
            JObject limited = Command("get_topology", new JObject { ["kind"] = "vertices" });
            Assert.IsFalse(limited.Value<bool>("success"), limited.ToString());
            Assert.AreEqual("topology_budget_exceeded", limited.Value<string>("code"), limited.ToString());
            Assert.AreEqual(32769, Positions().Count, "Budget refusal must preserve source state.");
        }

        [Test]
        public void CursorBindingAndStrictAdmissionRejectMalformedRequests()
        {
            JObject first = Page("faces");
            foreach (
                JObject p in new[]
                {
                    new JObject
                    {
                        ["kind"] = "edges",
                        ["page_size"] = 37,
                        ["cursor"] = first["next_cursor"],
                        ["mesh_revision"] = first["mesh_revision"],
                    },
                    new JObject
                    {
                        ["kind"] = "faces",
                        ["page_size"] = 38,
                        ["cursor"] = first["next_cursor"],
                        ["mesh_revision"] = first["mesh_revision"],
                    },
                    new JObject { ["cursor"] = "malformed", ["mesh_revision"] = first["mesh_revision"] },
                    new JObject { ["page_size"] = true },
                    new JObject { ["page_size"] = 101 },
                    new JObject { ["page_size"] = "10" },
                    new JObject { ["kind"] = "all" },
                    new JObject { ["offset"] = 37 },
                }
            )
            {
                JObject result = Command("get_topology", p);
                Assert.IsFalse(result.Value<bool>("success"), result.ToString());
            }
            Vector3[] before = Positions().ToArray();
            JObject unsupported = Command("center_pivot", new JObject { ["mesh_revision"] = first["mesh_revision"] });
            Assert.IsFalse(unsupported.Value<bool>("success"), "An unsupported guard must never be silently ignored: " + unsupported);
            CollectionAssert.AreEqual(before, Positions());
        }
    }
}
