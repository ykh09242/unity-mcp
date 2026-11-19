using System;
using System.Collections.Generic;
using System.Reflection;
using MCPForUnity.Editor.Tools.ProBuilder;
using MCPForUnityTests.Editor.Helpers;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEditor;
using UnityEngine;
using static MCPForUnityTests.Editor.TestUtilities;

namespace MCPForUnityTests.Editor.Tools
{
    public class ManageProBuilderTests
    {
        private readonly List<GameObject> _createdObjects = new List<GameObject>();
        private readonly PrefabTestSceneFixture _scenes = new PrefabTestSceneFixture();
        private bool _proBuilderInstalled;
        private string _legacyMaterialFailure;

        [OneTimeSetUp]
        public void OneTimeSetUp()
        {
            _scenes.PrepareRunnerBootstrap();
            _proBuilderInstalled = ManageProBuilder.InstalledMeshType != null;
            try
            {
                Type.GetType("ProBuilder.Core.pb_Material, ProBuilderCore")?.GetProperty("DefaultMaterial")?.GetValue(null);
            }
            catch (TargetInvocationException ex)
            {
                _legacyMaterialFailure = ex.GetBaseException().Message;
            }
        }

        [OneTimeTearDown]
        public void RestoreRunnerScene() => _scenes.RestoreRunnerBootstrap();

        [SetUp]
        public void RequireUsableNativeGeometry()
        {
            string test = TestContext.CurrentContext.Test.MethodName;
            if (
                _legacyMaterialFailure != null
                && !test.StartsWith("HandleCommand_")
                && test != nameof(ReadinessReflectsInstalledMeshGeneration)
                && test != nameof(UnusableLegacyMaterialRefusesCreationBeforeUndo)
            )
                Assert.Ignore("The installed ProBuilder DLL cannot build geometry on this Editor: " + _legacyMaterialFailure);
            _scenes.Create("McpProBuilderGeneration_", Guid.NewGuid().ToString("N"));
        }

        [TearDown]
        public void TearDown()
        {
            foreach (var go in _createdObjects)
            {
                if (go != null)
                    UnityEngine.Object.DestroyImmediate(go);
            }
            _createdObjects.Clear();
            _scenes.Close();
        }

        // =====================================================================
        // Basic action validation (works regardless of ProBuilder installation)
        // =====================================================================

        [TestCase(null)]
        [TestCase("nonexistent_action")]
        public void HandleCommand_InvalidActionValidatesBeforePackageInitialization(string action)
        {
            var resolved = typeof(ManageProBuilder).GetField("_typesResolved", BindingFlags.Static | BindingFlags.NonPublic);
            var available = typeof(ManageProBuilder).GetField("_proBuilderAvailable", BindingFlags.Static | BindingFlags.NonPublic);
            var oldResolved = resolved.GetValue(null);
            var oldAvailable = available.GetValue(null);
            try
            {
                // A cached unavailable package isolates the action boundary from shared material patching.
                resolved.SetValue(null, true);
                available.SetValue(null, false);

                var result = ToJObject(ManageProBuilder.HandleCommand(new JObject { ["action"] = action }));

                Assert.IsFalse(result.Value<bool>("success"), result.ToString());
                Assert.That(result["error"]?.ToString(), Does.Contain(action == null ? "Action is required" : "Unknown action"));
            }
            finally
            {
                resolved.SetValue(null, oldResolved);
                available.SetValue(null, oldAvailable);
            }
        }

        [Test]
        public void HandleCommand_MissingAction_ReturnsError()
        {
            var paramsObj = new JObject();
            var result = ToJObject(ManageProBuilder.HandleCommand(paramsObj));

            Assert.IsFalse(result.Value<bool>("success"), result.ToString());
        }

        [Test]
        public void HandleCommand_UnknownAction_ReturnsError()
        {
            var paramsObj = new JObject { ["action"] = "nonexistent_action" };
            var result = ToJObject(ManageProBuilder.HandleCommand(paramsObj));

            Assert.IsFalse(result.Value<bool>("success"), result.ToString());
            var errorMsg = result["error"]?.ToString() ?? result["message"]?.ToString();
            Assert.That(errorMsg, Does.Contain("Unknown action").Or.Contain("not installed"));
        }

        [Test]
        public void HandleCommand_Ping_ReturnsSuccess()
        {
            var paramsObj = new JObject { ["action"] = "ping" };
            var result = ToJObject(ManageProBuilder.HandleCommand(paramsObj));

            if (!_proBuilderInstalled)
            {
                // Without ProBuilder, should return error about missing package
                Assert.IsFalse(result.Value<bool>("success"), result.ToString());
                Assert.That(result["error"]?.ToString(), Does.Contain("ProBuilder").IgnoreCase);
                return;
            }

            Assert.IsTrue(result.Value<bool>("success"), result.ToString());
        }

        [Test]
        public void ReadinessReflectsInstalledMeshGeneration()
        {
            var result = ToJObject(MCPForUnity.Editor.Resources.Project.ProjectInfo.HandleCommand(new JObject { ["include_readiness"] = true }));
            Assert.AreEqual(_proBuilderInstalled, result.SelectToken("data.featureReadiness.probuilder.supported")?.Value<bool?>(), result.ToString());
        }

        [Test]
        public void UnusableLegacyMaterialRefusesCreationBeforeUndo()
        {
            if (_legacyMaterialFailure == null)
                Assert.Ignore("Requires a legacy package with an incompatible native material getter.");
            int undo = Undo.GetCurrentGroup();
            int before = Resources.FindObjectsOfTypeAll(ManageProBuilder.InstalledMeshType).Length;
            var result = ToJObject(
                ManageProBuilder.HandleCommand(
                    new JObject
                    {
                        ["action"] = "create_shape",
                        ["properties"] = new JObject { ["shapeType"] = "Cube" },
                    }
                )
            );
            Assert.IsFalse(result.Value<bool>("success"), result.ToString());
            StringAssert.Contains(_legacyMaterialFailure, result.Value<string>("error"));
            Assert.AreEqual(before, Resources.FindObjectsOfTypeAll(ManageProBuilder.InstalledMeshType).Length);
            Assert.AreEqual(undo, Undo.GetCurrentGroup());
        }

        [Test]
        public void ConversionUndoRestoresBorrowedMesh()
        {
            if (!_proBuilderInstalled)
                Assert.Ignore("Requires a native ProBuilder package.");
            var go = GameObject.CreatePrimitive(PrimitiveType.Cube);
            go.name = "PBUndo_" + Guid.NewGuid().ToString("N");
            _createdObjects.Add(go);
            var filter = go.GetComponent<MeshFilter>();
            var original = filter.sharedMesh;
            var materials = go.GetComponent<Renderer>().sharedMaterials;
            Undo.IncrementCurrentGroup();
            var result = ToJObject(ManageProBuilder.HandleCommand(new JObject { ["action"] = "convert_to_probuilder", ["target"] = go.name }));
            Assert.IsTrue(result.Value<bool>("success"), result.ToString());
            Assert.IsNotNull(go.GetComponent(ManageProBuilder.InstalledMeshType));
            Assert.AreNotSame(original, filter.sharedMesh);
            Undo.FlushUndoRecordObjects();
            Undo.PerformUndo();
            Assert.IsNull(go.GetComponent(ManageProBuilder.InstalledMeshType));
            Assert.IsTrue(original != null);
            Assert.AreSame(original, filter.sharedMesh);
            CollectionAssert.AreEqual(materials, go.GetComponent<Renderer>().sharedMaterials);
        }

        [Test]
        public void RepairCountsTrianglesAcrossNativeGenerations()
        {
            if (!_proBuilderInstalled)
                Assert.Ignore("Requires a native ProBuilder package.");
            var meshType = ManageProBuilder.InstalledMeshType;
            bool legacy = meshType.FullName == "ProBuilder.Core.pb_Object";
            var faceType = meshType.Assembly.GetType(legacy ? "ProBuilder.Core.pb_Face" : "UnityEngine.ProBuilder.Face", true);
            var faces = Array.CreateInstance(faceType, 2);
            faces.SetValue(Activator.CreateInstance(faceType, new object[] { new[] { 0, 1, 2 } }), 0);
            faces.SetValue(Activator.CreateInstance(faceType, new object[] { new[] { 3, 4, 5 } }), 1);
            var points = new[]
            {
                Vector3.zero,
                Vector3.right,
                Vector3.up,
                Vector3.forward,
                Vector3.forward + Vector3.right,
                Vector3.forward + Vector3.right * 2,
            };
            var factory = meshType.GetMethod(legacy ? "CreateInstanceWithVerticesFaces" : "Create", new[] { typeof(Vector3[]), faces.GetType() });
            var mesh = (Component)factory.Invoke(null, new object[] { points, faces });
            mesh.gameObject.name = "PBRepair_" + Guid.NewGuid().ToString("N");
            _createdObjects.Add(mesh.gameObject);
            var result = ToJObject(ManageProBuilder.HandleCommand(new JObject { ["action"] = "repair_mesh", ["target"] = mesh.gameObject.name }));
            Assert.AreEqual(1, result.SelectToken("data.degenerateTrianglesRemoved")?.Value<int>(), result.ToString());
            Assert.AreEqual(1, mesh.GetComponent<MeshFilter>().sharedMesh.triangles.Length / 3);
        }

        [Test]
        public void MergeUndoRestoresBothConvertedTargets()
        {
            if (Type.GetType("UnityEngine.ProBuilder.MeshOperations.CombineMeshes, Unity.ProBuilder") == null)
                Assert.Ignore("Requires the native in-place mesh combination API.");
            var first = GameObject.CreatePrimitive(PrimitiveType.Cube);
            var second = GameObject.CreatePrimitive(PrimitiveType.Cube);
            _createdObjects.Add(first);
            _createdObjects.Add(second);
            string firstName = first.name = "PBFirst_" + Guid.NewGuid().ToString("N");
            second.name = "PBSecond_" + Guid.NewGuid().ToString("N");
            second.transform.position = Vector3.right * 3f;
            Mesh firstSource = first.GetComponent<MeshFilter>().sharedMesh,
                secondSource = second.GetComponent<MeshFilter>().sharedMesh;
            var request = new JObject
            {
                ["action"] = "merge_objects",
                ["properties"] = new JObject { ["targets"] = new JArray(first.name, second.name), ["name"] = "PBCombined" },
            };
            var result = ToJObject(ManageProBuilder.HandleCommand(request));
            Assert.IsTrue(result.Value<bool>("success"), result.ToString());
            Assert.AreEqual(24, first.GetComponent<MeshFilter>().sharedMesh.triangles.Length / 3);
            Undo.FlushUndoRecordObjects();
            Undo.PerformUndo();
            Assert.AreEqual(firstName, first.name);
            Assert.IsNull(first.GetComponent(ManageProBuilder.InstalledMeshType));
            Assert.IsNull(second.GetComponent(ManageProBuilder.InstalledMeshType));
            Assert.AreSame(firstSource, first.GetComponent<MeshFilter>().sharedMesh);
            Assert.AreSame(secondSource, second.GetComponent<MeshFilter>().sharedMesh);
        }

        [Test]
        public void NativeCubeTopologyGuardsEditsAcrossMeshGenerations()
        {
            if (!_proBuilderInstalled)
                Assert.Ignore("Requires a native ProBuilder package.");
            string name = "PBTopology_" + Guid.NewGuid().ToString("N");
            JObject Send(string action, JObject properties) =>
                ToJObject(
                    ManageProBuilder.HandleCommand(
                        new JObject
                        {
                            ["action"] = action,
                            ["target"] = name,
                            ["properties"] = properties,
                        }
                    )
                );
            var created = Send(
                "create_shape",
                new JObject
                {
                    ["name"] = name,
                    ["shapeType"] = "Cube",
                    ["width"] = 3,
                }
            );
            Assert.IsTrue(created.Value<bool>("success"), created.ToString());
            var go = GameObject.Find(name);
            _createdObjects.Add(go);
            Assert.AreEqual(3f, go.GetComponent<MeshFilter>().sharedMesh.bounds.size.x, 0.001f);
            foreach (var page in new[] { ("faces", 6), ("edges", 12), ("vertices", 24) })
            {
                var topology = Send("get_topology", new JObject { ["kind"] = page.Item1 });
                Assert.AreEqual(page.Item2, topology.SelectToken("data.total")?.Value<int>(), topology.ToString());
            }
            var revision = Send("get_topology", new JObject()).SelectToken("data.mesh_revision")?.Value<string>();
            var indices = new JArray();
            for (int i = 0; i < 24; i++)
                indices.Add(i);
            var move = new JObject
            {
                ["vertexIndices"] = indices,
                ["offset"] = new JArray(0, 1, 0),
                ["mesh_revision"] = revision,
            };
            var moved = Send("move_vertices", move);
            Assert.IsTrue(moved.Value<bool>("success"), moved.ToString());
            Assert.AreEqual("stale_mesh_revision", Send("move_vertices", move).Value<string>("error"));
            var subdivide = Send("subdivide", new JObject { ["faceIndices"] = new JArray(0) });
            Assert.IsTrue(subdivide.Value<bool>("success"), subdivide.ToString());
            Assert.Greater(subdivide.SelectToken("data.faceCount").Value<int>(), 6);
        }

        [Test]
        public void FailedNativeFaceConnectPreservesTopology()
        {
            if (!_proBuilderInstalled)
                Assert.Ignore("Requires a native ProBuilder package.");
            string name = "PBBadFace_" + Guid.NewGuid().ToString("N");
            JObject Send(string action, JObject properties) =>
                ToJObject(
                    ManageProBuilder.HandleCommand(
                        new JObject
                        {
                            ["action"] = action,
                            ["target"] = name,
                            ["properties"] = properties,
                        }
                    )
                );
            var created = Send(
                "create_shape",
                new JObject
                {
                    ["name"] = name,
                    ["shapeType"] = "Cube",
                    ["width"] = 3,
                }
            );
            Assert.IsTrue(created.Value<bool>("success"), created.ToString());
            _createdObjects.Add(GameObject.Find(name));
            var moved = Send("move_vertices", new JObject { ["vertexIndices"] = new JArray(0), ["offset"] = new JArray(0, 1, 0) });
            Assert.IsTrue(moved.Value<bool>("success"), moved.ToString());
            var before = Send("get_topology", new JObject());
            var rejected = Send("subdivide", new JObject { ["faceIndices"] = new JArray(0) });
            Assert.IsFalse(rejected.Value<bool>("success"), rejected.ToString());
            var after = Send("get_topology", new JObject());
            Assert.IsTrue(before.Value<bool>("success") && after.Value<bool>("success"));
            Assert.AreEqual(before.SelectToken("data.mesh_revision").Value<string>(), after.SelectToken("data.mesh_revision").Value<string>());
        }

        [Test]
        public void NativeBridgeHonorsNonManifoldPolicy()
        {
            if (!_proBuilderInstalled)
                Assert.Ignore("Requires a native ProBuilder package.");
            string name = "PBBridge_" + Guid.NewGuid().ToString("N");
            JObject Send(string action, JObject properties) =>
                ToJObject(
                    ManageProBuilder.HandleCommand(
                        new JObject
                        {
                            ["action"] = action,
                            ["target"] = name,
                            ["properties"] = properties,
                        }
                    )
                );
            var created = Send("create_shape", new JObject { ["name"] = name, ["shapeType"] = "Cube" });
            Assert.IsTrue(created.Value<bool>("success"), created.ToString());
            _createdObjects.Add(GameObject.Find(name));
            var page = Send("get_topology", new JObject { ["kind"] = "edges" });
            var edges = (JArray)page.SelectToken("data.items");
            Vector3 Center(JToken edge)
            {
                var a = edge["positionA"].ToObject<float[]>();
                var b = edge["positionB"].ToObject<float[]>();
                return new Vector3(a[0] + b[0], a[1] + b[1], a[2] + b[2]) * .5f;
            }
            JToken first = edges[0],
                opposite = null;
            foreach (var edge in edges)
                if ((Center(edge) + Center(first)).sqrMagnitude < .000001f)
                    opposite = edge;
            Assert.IsNotNull(opposite);
            JObject Pair(JToken edge) => new JObject { ["a"] = edge["vertexA"], ["b"] = edge["vertexB"] };
            var args = new JObject { ["edgeA"] = Pair(first), ["edgeB"] = Pair(opposite) };
            var perimeterOnly = Send("bridge_edges", args);
            Assert.AreEqual(false, perimeterOnly.SelectToken("data.bridgeCreated")?.Value<bool>(), perimeterOnly.ToString());
            args["allowNonManifold"] = true;
            var allowed = Send("bridge_edges", args);
            Assert.AreEqual(true, allowed.SelectToken("data.bridgeCreated")?.Value<bool>(), allowed.ToString());
        }

        [Test]
        public void LegacyFaceColorKeepsNativeMaterialSlotsThroughRebuildAndUndo()
        {
            var native = Type.GetType("ProBuilder.Core.pb_Object, ProBuilderCore");
            if (native == null)
                Assert.Ignore("Requires a native legacy mesh with per-face materials.");
            string name = "PBColored_" + Guid.NewGuid().ToString("N");
            JObject Send(string action, JObject properties) =>
                ToJObject(
                    ManageProBuilder.HandleCommand(
                        new JObject
                        {
                            ["action"] = action,
                            ["target"] = name,
                            ["properties"] = properties,
                        }
                    )
                );
            var created = Send("create_shape", new JObject { ["name"] = name, ["shapeType"] = "Cube" });
            Assert.IsTrue(created.Value<bool>("success"), created.ToString());
            var go = GameObject.Find(name);
            _createdObjects.Add(go);
            var mesh = go.GetComponent(native);
            var renderer = go.GetComponent<Renderer>();
            var faces = (Array)native.GetProperty("faces").GetValue(mesh);
            var material = faces.GetType().GetElementType().GetProperty("material");
            var original = AssetDatabase.GetBuiltinExtraResource<Material>("Default-Material.mat");
            var other = new Material(original);
            try
            {
                for (int i = 0; i < faces.Length; i++)
                    material.SetValue(faces.GetValue(i), i == faces.Length - 1 ? other : original);
                ManageProBuilder.RefreshMesh(mesh);
                Assert.AreSame(original, renderer.sharedMaterial);
                var originalShader = original.shader;
                Undo.IncrementCurrentGroup();
                var colored = Send("set_face_color", new JObject { ["faceIndices"] = new JArray(0), ["color"] = new JArray(1, 0, 0, 1) });
                Assert.IsTrue(colored.Value<bool>("success"), colored.ToString());
                var replacement = renderer.sharedMaterial;
                Assert.AreNotSame(original, replacement);
                Assert.AreSame(originalShader, original.shader);
                ManageProBuilder.RefreshMesh(mesh);
                Assert.AreSame(replacement, renderer.sharedMaterial);
                CheckBindings(replacement);
                var topology = Send("get_topology", new JObject { ["kind"] = "faces" });
                Assert.AreEqual(6, topology.SelectToken("data.total")?.Value<int>(), topology.ToString());
                Undo.FlushUndoRecordObjects();
                Undo.PerformUndo();
                CheckBindings(original);
                Assert.AreSame(original, renderer.sharedMaterial);
                Assert.AreEqual(other, renderer.sharedMaterials[1]);

                void CheckBindings(Material expected)
                {
                    var actual = (Array)native.GetProperty("faces").GetValue(mesh);
                    for (int i = 0; i < actual.Length; i++)
                        Assert.AreSame(i == actual.Length - 1 ? other : expected, material.GetValue(actual.GetValue(i)));
                }
            }
            finally
            {
                UnityEngine.Object.DestroyImmediate(other);
            }
        }

        // =====================================================================
        // Shape creation (requires ProBuilder)
        // =====================================================================

        [Test]
        public void CreateShape_MissingShapeType_ReturnsError()
        {
            if (!_proBuilderInstalled)
            {
                Assert.Pass("ProBuilder not installed - skipping.");
                return;
            }

            var paramsObj = new JObject { ["action"] = "create_shape" };
            var result = ToJObject(ManageProBuilder.HandleCommand(paramsObj));
            Assert.IsFalse(result.Value<bool>("success"), result.ToString());
        }

        [Test]
        public void CreateShape_InvalidShapeType_ReturnsError()
        {
            if (!_proBuilderInstalled)
            {
                Assert.Pass("ProBuilder not installed - skipping.");
                return;
            }

            var paramsObj = new JObject
            {
                ["action"] = "create_shape",
                ["properties"] = new JObject { ["shapeType"] = "InvalidShape" },
            };
            var result = ToJObject(ManageProBuilder.HandleCommand(paramsObj));
            Assert.IsFalse(result.Value<bool>("success"), result.ToString());
        }

        [Test]
        public void CreateShape_Cube_CreatesGameObject()
        {
            if (!_proBuilderInstalled)
            {
                Assert.Pass("ProBuilder not installed - skipping.");
                return;
            }

            var paramsObj = new JObject
            {
                ["action"] = "create_shape",
                ["properties"] = new JObject { ["shapeType"] = "Cube", ["name"] = "PBTestCube" },
            };
            var result = ToJObject(ManageProBuilder.HandleCommand(paramsObj));
            Assert.IsTrue(result.Value<bool>("success"), result.ToString());

            var data = result["data"] as JObject;
            Assert.IsNotNull(data, "Result should contain data");
            Assert.AreEqual("PBTestCube", data.Value<string>("gameObjectName"));
            Assert.Greater(data.Value<int>("faceCount"), 0);
            Assert.Greater(data.Value<int>("vertexCount"), 0);

            // Track for cleanup
            var go = GameObject.Find("PBTestCube");
            if (go != null)
                _createdObjects.Add(go);
        }

        [Test]
        public void CreateShape_WithPosition_SetsTransform()
        {
            if (!_proBuilderInstalled)
            {
                Assert.Pass("ProBuilder not installed - skipping.");
                return;
            }

            var paramsObj = new JObject
            {
                ["action"] = "create_shape",
                ["properties"] = new JObject
                {
                    ["shapeType"] = "Cube",
                    ["name"] = "PBTestCubePos",
                    ["position"] = new JArray(5f, 10f, 15f),
                },
            };
            var result = ToJObject(ManageProBuilder.HandleCommand(paramsObj));
            Assert.IsTrue(result.Value<bool>("success"), result.ToString());

            var go = GameObject.Find("PBTestCubePos");
            Assert.IsNotNull(go, "Created GameObject should exist");
            Assert.AreEqual(new Vector3(5f, 10f, 15f), go.transform.position);

            _createdObjects.Add(go);
        }

        [Test]
        public void CreatePolyShape_CreatesFromPoints()
        {
            if (!_proBuilderInstalled)
            {
                Assert.Pass("ProBuilder not installed - skipping.");
                return;
            }

            var paramsObj = new JObject
            {
                ["action"] = "create_poly_shape",
                ["properties"] = new JObject
                {
                    ["points"] = new JArray(new JArray(0f, 0f, 0f), new JArray(5f, 0f, 0f), new JArray(5f, 0f, 5f), new JArray(0f, 0f, 5f)),
                    ["extrudeHeight"] = 3f,
                    ["name"] = "PBTestPoly",
                },
            };
            var result = ToJObject(ManageProBuilder.HandleCommand(paramsObj));
            Assert.IsTrue(result.Value<bool>("success"), result.ToString());

            var go = GameObject.Find("PBTestPoly");
            if (go != null)
                _createdObjects.Add(go);
        }

        // =====================================================================
        // Mesh editing (requires ProBuilder + created shape)
        // =====================================================================

        [Test]
        public void GetMeshInfo_ReturnsDetails()
        {
            if (!_proBuilderInstalled)
            {
                Assert.Pass("ProBuilder not installed - skipping.");
                return;
            }

            // First create a shape
            var createParams = new JObject
            {
                ["action"] = "create_shape",
                ["properties"] = new JObject { ["shapeType"] = "Cube", ["name"] = "PBTestInfoCube" },
            };
            var createResult = ToJObject(ManageProBuilder.HandleCommand(createParams));
            Assert.IsTrue(createResult.Value<bool>("success"), createResult.ToString());

            var go = GameObject.Find("PBTestInfoCube");
            Assert.IsNotNull(go);
            _createdObjects.Add(go);

            // Now get mesh info
            var infoParams = new JObject
            {
                ["action"] = "get_mesh_info",
                ["target"] = "PBTestInfoCube",
                ["properties"] = new JObject { ["include"] = "faces" },
            };
            var result = ToJObject(ManageProBuilder.HandleCommand(infoParams));
            Assert.IsTrue(result.Value<bool>("success"), result.ToString());

            var data = result["data"] as JObject;
            Assert.IsNotNull(data);
            Assert.Greater(data.Value<int>("faceCount"), 0);
            Assert.Greater(data.Value<int>("vertexCount"), 0);
            Assert.IsNotNull(data["bounds"]);
            Assert.IsNotNull(data["faces"]);
        }

        [Test]
        public void ExtrudeFaces_IncreaseFaceCount()
        {
            if (!_proBuilderInstalled)
            {
                Assert.Pass("ProBuilder not installed - skipping.");
                return;
            }

            // Create a cube
            var createParams = new JObject
            {
                ["action"] = "create_shape",
                ["properties"] = new JObject { ["shapeType"] = "Cube", ["name"] = "PBTestExtrudeCube" },
            };
            var createResult = ToJObject(ManageProBuilder.HandleCommand(createParams));
            Assert.IsTrue(createResult.Value<bool>("success"), createResult.ToString());

            var go = GameObject.Find("PBTestExtrudeCube");
            _createdObjects.Add(go);

            int initialFaceCount = createResult["data"].Value<int>("faceCount");

            // Extrude face 0
            var extrudeParams = new JObject
            {
                ["action"] = "extrude_faces",
                ["target"] = "PBTestExtrudeCube",
                ["properties"] = new JObject { ["faceIndices"] = new JArray(0), ["distance"] = 1.0f },
            };
            var result = ToJObject(ManageProBuilder.HandleCommand(extrudeParams));
            Assert.IsTrue(result.Value<bool>("success"), result.ToString());

            int newFaceCount = result["data"].Value<int>("faceCount");
            Assert.Greater(newFaceCount, initialFaceCount, "Face count should increase after extrusion");
        }

        [Test]
        public void DeleteFaces_DecreasesFaceCount()
        {
            if (!_proBuilderInstalled)
            {
                Assert.Pass("ProBuilder not installed - skipping.");
                return;
            }

            var createParams = new JObject
            {
                ["action"] = "create_shape",
                ["properties"] = new JObject { ["shapeType"] = "Cube", ["name"] = "PBTestDeleteCube" },
            };
            var createResult = ToJObject(ManageProBuilder.HandleCommand(createParams));
            Assert.IsTrue(createResult.Value<bool>("success"), createResult.ToString());

            var go = GameObject.Find("PBTestDeleteCube");
            _createdObjects.Add(go);

            int initialFaceCount = createResult["data"].Value<int>("faceCount");

            var deleteParams = new JObject
            {
                ["action"] = "delete_faces",
                ["target"] = "PBTestDeleteCube",
                ["properties"] = new JObject { ["faceIndices"] = new JArray(0) },
            };
            var result = ToJObject(ManageProBuilder.HandleCommand(deleteParams));
            Assert.IsTrue(result.Value<bool>("success"), result.ToString());

            int newFaceCount = result["data"].Value<int>("faceCount");
            Assert.Less(newFaceCount, initialFaceCount, "Face count should decrease after deletion");
        }

        [Test]
        public void SetFaceMaterial_WithMissingTarget_ReturnsError()
        {
            if (!_proBuilderInstalled)
            {
                Assert.Pass("ProBuilder not installed - skipping.");
                return;
            }

            var paramsObj = new JObject
            {
                ["action"] = "set_face_material",
                ["target"] = "NonExistentObject999",
                ["properties"] = new JObject { ["faceIndices"] = new JArray(0), ["materialPath"] = "Assets/Materials/Test.mat" },
            };
            var result = ToJObject(ManageProBuilder.HandleCommand(paramsObj));
            Assert.IsFalse(result.Value<bool>("success"), result.ToString());
        }

        [TestCase(false)]
        [TestCase(true)]
        public void SetFaceUVs_WritesNativeSettingsAndPreservesOtherFaces(bool jsonProperties)
        {
            if (!_proBuilderInstalled)
                Assert.Ignore("ProBuilder is required for native UV verification.");

            var created = ToJObject(
                ManageProBuilder.HandleCommand(
                    new JObject
                    {
                        ["action"] = "create_shape",
                        ["properties"] = new JObject { ["shapeType"] = "Cube", ["name"] = "PBTestUVSettings" },
                    }
                )
            );
            Assert.IsTrue(created.Value<bool>("success"), created.ToString());
            var go = GameObject.Find("PBTestUVSettings");
            Assert.IsNotNull(go);
            _createdObjects.Add(go);
            var meshType = ManageProBuilder.InstalledMeshType;
            var mesh = go.GetComponent(meshType);
            var faces = (System.Collections.IList)meshType.GetProperty("faces").GetValue(mesh);
            var uvProperty = faces[0].GetType().GetProperty("uv");
            var untouched = uvProperty.GetValue(faces[1]);
            var properties = JObject.Parse("{\"faceIndices\":[0],\"scale\":[2,3],\"offset\":[4,5],\"rotation\":45,\"flipU\":true,\"flipV\":true}");
            var result = ToJObject(
                ManageProBuilder.HandleCommand(
                    new JObject
                    {
                        ["action"] = "set_face_uvs",
                        ["target"] = go.name,
                        ["properties"] = jsonProperties ? (JToken)new JValue(properties.ToString()) : properties,
                    }
                )
            );
            Assert.IsTrue(result.Value<bool>("success"), result.ToString());
            var uv = uvProperty.GetValue(faces[0]);
            var settingsType = uv.GetType();
            object Read(string name) => settingsType.GetProperty(name)?.GetValue(uv) ?? settingsType.GetField(name).GetValue(uv);
            Assert.AreEqual(new Vector2(2, 3), Read("scale"));
            Assert.AreEqual(new Vector2(4, 5), Read("offset"));
            Assert.AreEqual(45f, Read("rotation"));
            Assert.AreEqual(true, Read("flipU"));
            Assert.AreEqual(true, Read("flipV"));
            Assert.AreEqual(untouched, uvProperty.GetValue(faces[1]));
        }

        [Test]
        public void FlipNormals_SucceedsOnValidMesh()
        {
            if (!_proBuilderInstalled)
            {
                Assert.Pass("ProBuilder not installed - skipping.");
                return;
            }

            var createParams = new JObject
            {
                ["action"] = "create_shape",
                ["properties"] = new JObject { ["shapeType"] = "Cube", ["name"] = "PBTestFlipCube" },
            };
            var createResult = ToJObject(ManageProBuilder.HandleCommand(createParams));
            Assert.IsTrue(createResult.Value<bool>("success"), createResult.ToString());

            var go = GameObject.Find("PBTestFlipCube");
            _createdObjects.Add(go);

            var flipParams = new JObject
            {
                ["action"] = "flip_normals",
                ["target"] = "PBTestFlipCube",
                ["properties"] = new JObject { ["faceIndices"] = new JArray(0, 1) },
            };
            var result = ToJObject(ManageProBuilder.HandleCommand(flipParams));
            Assert.IsTrue(result.Value<bool>("success"), result.ToString());
        }

        // =====================================================================
        // Enhanced get_mesh_info with include parameter
        // =====================================================================

        [Test]
        public void GetMeshInfo_DefaultInclude_ReturnsSummaryOnly()
        {
            if (!_proBuilderInstalled)
            {
                Assert.Pass("ProBuilder not installed - skipping.");
                return;
            }

            var createParams = new JObject
            {
                ["action"] = "create_shape",
                ["properties"] = new JObject { ["shapeType"] = "Cube", ["name"] = "PBTestSummaryCube" },
            };
            var createResult = ToJObject(ManageProBuilder.HandleCommand(createParams));
            Assert.IsTrue(createResult.Value<bool>("success"), createResult.ToString());

            var go = GameObject.Find("PBTestSummaryCube");
            Assert.IsNotNull(go);
            _createdObjects.Add(go);

            var infoParams = new JObject { ["action"] = "get_mesh_info", ["target"] = "PBTestSummaryCube" };
            var result = ToJObject(ManageProBuilder.HandleCommand(infoParams));
            Assert.IsTrue(result.Value<bool>("success"), result.ToString());

            var data = result["data"] as JObject;
            Assert.IsNotNull(data);
            Assert.Greater(data.Value<int>("faceCount"), 0);
            // Default "summary" should NOT include faces array
            Assert.IsNull(data["faces"], "Summary mode should not include faces array");
        }

        [Test]
        public void GetMeshInfo_IncludeFaces_ReturnsFaceNormalsAndDirections()
        {
            if (!_proBuilderInstalled)
            {
                Assert.Pass("ProBuilder not installed - skipping.");
                return;
            }

            var createParams = new JObject
            {
                ["action"] = "create_shape",
                ["properties"] = new JObject { ["shapeType"] = "Cube", ["name"] = "PBTestFacesCube" },
            };
            var createResult = ToJObject(ManageProBuilder.HandleCommand(createParams));
            Assert.IsTrue(createResult.Value<bool>("success"), createResult.ToString());

            var go = GameObject.Find("PBTestFacesCube");
            Assert.IsNotNull(go);
            _createdObjects.Add(go);

            var infoParams = new JObject
            {
                ["action"] = "get_mesh_info",
                ["target"] = "PBTestFacesCube",
                ["properties"] = new JObject { ["include"] = "faces" },
            };
            var result = ToJObject(ManageProBuilder.HandleCommand(infoParams));
            Assert.IsTrue(result.Value<bool>("success"), result.ToString());

            var data = result["data"] as JObject;
            Assert.IsNotNull(data);
            var faces = data["faces"] as JArray;
            Assert.IsNotNull(faces, "Faces mode should include faces array");
            Assert.Greater(faces.Count, 0);

            // Each face should have normal, center, direction
            var firstFace = faces[0] as JObject;
            Assert.IsNotNull(firstFace);
            Assert.IsNotNull(firstFace["normal"], "Face should have normal");
            Assert.IsNotNull(firstFace["center"], "Face should have center");
            // direction may be null for angled faces, but should exist as key
            Assert.IsTrue(firstFace.ContainsKey("direction"), "Face should have direction key");
        }

        [Test]
        public void GetMeshInfo_IncludeEdges_ReturnsEdgeData()
        {
            if (!_proBuilderInstalled)
            {
                Assert.Pass("ProBuilder not installed - skipping.");
                return;
            }

            var createParams = new JObject
            {
                ["action"] = "create_shape",
                ["properties"] = new JObject { ["shapeType"] = "Cube", ["name"] = "PBTestEdgesCube" },
            };
            var createResult = ToJObject(ManageProBuilder.HandleCommand(createParams));
            Assert.IsTrue(createResult.Value<bool>("success"), createResult.ToString());

            var go = GameObject.Find("PBTestEdgesCube");
            Assert.IsNotNull(go);
            _createdObjects.Add(go);

            var infoParams = new JObject
            {
                ["action"] = "get_mesh_info",
                ["target"] = "PBTestEdgesCube",
                ["properties"] = new JObject { ["include"] = "edges" },
            };
            var result = ToJObject(ManageProBuilder.HandleCommand(infoParams));
            Assert.IsTrue(result.Value<bool>("success"), result.ToString());

            var data = result["data"] as JObject;
            Assert.IsNotNull(data);
            var edges = data["edges"] as JArray;
            Assert.IsNotNull(edges, "Edges mode should include edges array");
            Assert.Greater(edges.Count, 0);

            var firstEdge = edges[0] as JObject;
            Assert.IsNotNull(firstEdge);
            Assert.IsTrue(firstEdge.ContainsKey("vertexA"), "Edge should have vertexA");
            Assert.IsTrue(firstEdge.ContainsKey("vertexB"), "Edge should have vertexB");
        }

        [Test]
        public void GetMeshInfo_CubeTopFace_HasUpNormal()
        {
            if (!_proBuilderInstalled)
            {
                Assert.Pass("ProBuilder not installed - skipping.");
                return;
            }

            var createParams = new JObject
            {
                ["action"] = "create_shape",
                ["properties"] = new JObject { ["shapeType"] = "Cube", ["name"] = "PBTestTopNormalCube" },
            };
            var createResult = ToJObject(ManageProBuilder.HandleCommand(createParams));
            Assert.IsTrue(createResult.Value<bool>("success"), createResult.ToString());

            var go = GameObject.Find("PBTestTopNormalCube");
            Assert.IsNotNull(go);
            _createdObjects.Add(go);

            var infoParams = new JObject
            {
                ["action"] = "get_mesh_info",
                ["target"] = "PBTestTopNormalCube",
                ["properties"] = new JObject { ["include"] = "faces" },
            };
            var result = ToJObject(ManageProBuilder.HandleCommand(infoParams));
            Assert.IsTrue(result.Value<bool>("success"), result.ToString());

            var faces = result["data"]["faces"] as JArray;
            Assert.IsNotNull(faces);

            // At least one face should have direction "top"
            bool hasTop = false;
            foreach (JObject face in faces)
            {
                if (face["direction"]?.ToString() == "top")
                {
                    hasTop = true;
                    break;
                }
            }
            Assert.IsTrue(hasTop, "A cube should have at least one face with direction 'top'");
        }

        // =====================================================================
        // Smoothing
        // =====================================================================

        [Test]
        public void AutoSmooth_DefaultAngle_AssignsGroups()
        {
            if (!_proBuilderInstalled)
            {
                Assert.Pass("ProBuilder not installed - skipping.");
                return;
            }

            var createParams = new JObject
            {
                ["action"] = "create_shape",
                ["properties"] = new JObject { ["shapeType"] = "Cube", ["name"] = "PBTestAutoSmoothCube" },
            };
            var createResult = ToJObject(ManageProBuilder.HandleCommand(createParams));
            Assert.IsTrue(createResult.Value<bool>("success"), createResult.ToString());

            var go = GameObject.Find("PBTestAutoSmoothCube");
            Assert.IsNotNull(go);
            _createdObjects.Add(go);

            var smoothParams = new JObject
            {
                ["action"] = "auto_smooth",
                ["target"] = "PBTestAutoSmoothCube",
                ["properties"] = new JObject { ["angleThreshold"] = 30 },
            };
            var result = ToJObject(ManageProBuilder.HandleCommand(smoothParams));
            Assert.IsTrue(result.Value<bool>("success"), result.ToString());
        }

        [Test]
        public void SetSmoothing_OnSpecificFaces_SetsGroup()
        {
            if (!_proBuilderInstalled)
            {
                Assert.Pass("ProBuilder not installed - skipping.");
                return;
            }

            var createParams = new JObject
            {
                ["action"] = "create_shape",
                ["properties"] = new JObject { ["shapeType"] = "Cube", ["name"] = "PBTestSetSmoothCube" },
            };
            var createResult = ToJObject(ManageProBuilder.HandleCommand(createParams));
            Assert.IsTrue(createResult.Value<bool>("success"), createResult.ToString());

            var go = GameObject.Find("PBTestSetSmoothCube");
            Assert.IsNotNull(go);
            _createdObjects.Add(go);

            var smoothParams = new JObject
            {
                ["action"] = "set_smoothing",
                ["target"] = "PBTestSetSmoothCube",
                ["properties"] = new JObject { ["faceIndices"] = new JArray(0, 1), ["smoothingGroup"] = 1 },
            };
            var result = ToJObject(ManageProBuilder.HandleCommand(smoothParams));
            Assert.IsTrue(result.Value<bool>("success"), result.ToString());

            var data = result["data"] as JObject;
            Assert.IsNotNull(data);
            Assert.AreEqual(2, data.Value<int>("facesModified"));
        }

        // =====================================================================
        // Mesh Utilities
        // =====================================================================

        [Test]
        public void CenterPivot_MovesPivotToCenter()
        {
            if (!_proBuilderInstalled)
            {
                Assert.Pass("ProBuilder not installed - skipping.");
                return;
            }

            var createParams = new JObject
            {
                ["action"] = "create_shape",
                ["properties"] = new JObject { ["shapeType"] = "Cube", ["name"] = "PBTestCenterPivot" },
            };
            var createResult = ToJObject(ManageProBuilder.HandleCommand(createParams));
            Assert.IsTrue(createResult.Value<bool>("success"), createResult.ToString());

            var go = GameObject.Find("PBTestCenterPivot");
            Assert.IsNotNull(go);
            _createdObjects.Add(go);

            var pivotParams = new JObject { ["action"] = "center_pivot", ["target"] = "PBTestCenterPivot" };
            var result = ToJObject(ManageProBuilder.HandleCommand(pivotParams));
            Assert.IsTrue(result.Value<bool>("success"), result.ToString());
        }

        [Test]
        public void FreezeTransform_ResetsTransformKeepsShape()
        {
            if (!_proBuilderInstalled)
            {
                Assert.Pass("ProBuilder not installed - skipping.");
                return;
            }

            var createParams = new JObject
            {
                ["action"] = "create_shape",
                ["properties"] = new JObject
                {
                    ["shapeType"] = "Cube",
                    ["name"] = "PBTestFreeze",
                    ["position"] = new JArray(5f, 3f, 2f),
                },
            };
            var createResult = ToJObject(ManageProBuilder.HandleCommand(createParams));
            Assert.IsTrue(createResult.Value<bool>("success"), createResult.ToString());

            var go = GameObject.Find("PBTestFreeze");
            Assert.IsNotNull(go);
            _createdObjects.Add(go);

            var freezeParams = new JObject { ["action"] = "freeze_transform", ["target"] = "PBTestFreeze" };
            var result = ToJObject(ManageProBuilder.HandleCommand(freezeParams));
            Assert.IsTrue(result.Value<bool>("success"), result.ToString());

            // Transform should be reset to identity
            Assert.AreEqual(Vector3.zero, go.transform.position);
            Assert.AreEqual(Quaternion.identity, go.transform.rotation);
            Assert.AreEqual(Vector3.one, go.transform.localScale);
        }

        [Test]
        public void ValidateMesh_CleanMesh_ReturnsNoIssues()
        {
            if (!_proBuilderInstalled)
            {
                Assert.Pass("ProBuilder not installed - skipping.");
                return;
            }

            var createParams = new JObject
            {
                ["action"] = "create_shape",
                ["properties"] = new JObject { ["shapeType"] = "Cube", ["name"] = "PBTestValidate" },
            };
            var createResult = ToJObject(ManageProBuilder.HandleCommand(createParams));
            Assert.IsTrue(createResult.Value<bool>("success"), createResult.ToString());

            var go = GameObject.Find("PBTestValidate");
            Assert.IsNotNull(go);
            _createdObjects.Add(go);

            var validateParams = new JObject { ["action"] = "validate_mesh", ["target"] = "PBTestValidate" };
            var result = ToJObject(ManageProBuilder.HandleCommand(validateParams));
            Assert.IsTrue(result.Value<bool>("success"), result.ToString());

            var data = result["data"] as JObject;
            Assert.IsNotNull(data);
            Assert.IsTrue(data.Value<bool>("healthy"), "Fresh cube should be healthy");
            Assert.AreEqual(0, data.Value<int>("degenerateTriangles"));
        }

        [Test]
        public void RepairMesh_OnCleanMesh_ReportsNoChanges()
        {
            if (!_proBuilderInstalled)
            {
                Assert.Pass("ProBuilder not installed - skipping.");
                return;
            }

            var createParams = new JObject
            {
                ["action"] = "create_shape",
                ["properties"] = new JObject { ["shapeType"] = "Cube", ["name"] = "PBTestRepair" },
            };
            var createResult = ToJObject(ManageProBuilder.HandleCommand(createParams));
            Assert.IsTrue(createResult.Value<bool>("success"), createResult.ToString());

            var go = GameObject.Find("PBTestRepair");
            Assert.IsNotNull(go);
            _createdObjects.Add(go);

            var repairParams = new JObject { ["action"] = "repair_mesh", ["target"] = "PBTestRepair" };
            var result = ToJObject(ManageProBuilder.HandleCommand(repairParams));
            Assert.IsTrue(result.Value<bool>("success"), result.ToString());

            var data = result["data"] as JObject;
            Assert.IsNotNull(data);
            Assert.AreEqual(0, data.Value<int>("degenerateTrianglesRemoved"));
        }

        // =====================================================================
        // ProBuilder not installed fallback
        // =====================================================================

        [Test]
        public void AllActions_WithoutProBuilder_ReturnPackageError()
        {
            if (_proBuilderInstalled)
            {
                Assert.Pass("ProBuilder IS installed - this test verifies the not-installed path.");
                return;
            }

            string[] testActions =
            {
                "ping",
                "create_shape",
                "get_mesh_info",
                "extrude_faces",
                "auto_smooth",
                "set_smoothing",
                "center_pivot",
                "validate_mesh",
            };
            foreach (var action in testActions)
            {
                var paramsObj = new JObject { ["action"] = action };
                var result = ToJObject(ManageProBuilder.HandleCommand(paramsObj));
                Assert.IsFalse(result.Value<bool>("success"), $"Action '{action}' should fail without ProBuilder: {result}");
                Assert.That(result["error"]?.ToString(), Does.Contain("ProBuilder").IgnoreCase, $"Error for '{action}' should mention ProBuilder");
            }
        }
    }
}
