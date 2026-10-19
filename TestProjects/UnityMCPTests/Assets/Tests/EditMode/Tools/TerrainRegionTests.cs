using System;
using System.Collections.Generic;
using System.Linq;
using System.Reflection;
using System.Threading;
using System.Threading.Tasks;
using MCPForUnity.Runtime.Helpers;
using MCPForUnityTests.Editor.Helpers;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEditor;
using UnityEngine;

namespace MCPForUnityTests.Editor.Tools
{
    [Parallelizable(ParallelScope.None)]
    public class TerrainRegionTests
    {
        private readonly PrefabTestSceneFixture scenes = new PrefabTestSceneFixture();
        private readonly List<GameObject> objects = new List<GameObject>();
        private TerrainData data,
            neighborData;
        private Terrain terrain,
            neighbor;
        private TerrainLayer first,
            second,
            replacement;
        private string root,
            rootGuid,
            dataGuid;

        [OneTimeSetUp]
        public void Prepare() => scenes.PrepareRunnerBootstrap();

        [OneTimeTearDown]
        public void Restore() => scenes.RestoreRunnerBootstrap();

        [SetUp]
        public void SetUp()
        {
            root = null;
            objects.Clear();
            scenes.Create("McpTerrain_", Guid.NewGuid().ToString("N"));
            root = "Assets/__McpTerrain_" + Guid.NewGuid().ToString("N");
            rootGuid = AssetDatabase.CreateFolder("Assets", root.Substring("Assets/".Length));
            first = Layer("First");
            second = Layer("Second");
            replacement = Layer("Replacement");
            data = CreateData("Main");
            neighborData = CreateData("Neighbor");
            terrain = CreateTerrain(data, Vector3.zero);
            neighbor = CreateTerrain(neighborData, new Vector3(32, 0, 0));
            terrain.SetNeighbors(null, null, neighbor, null);
            neighbor.SetNeighbors(terrain, null, null, null);
            dataGuid = AssetDatabase.AssetPathToGUID(AssetDatabase.GetAssetPath(data));
            var texture = new Texture2D(2, 2);
            AssetDatabase.CreateAsset(texture, root + "/Detail.asset");
            data.detailPrototypes = new[]
            {
                new DetailPrototype
                {
                    prototypeTexture = texture,
                    minWidth = 1,
                    maxWidth = 1,
                    minHeight = 1,
                    maxHeight = 1,
                    renderMode = DetailRenderMode.GrassBillboard,
                },
            };
            data.SetDetailResolution(32, 8);
            var tree = GameObject.CreatePrimitive(PrimitiveType.Cube);
            var prefab = PrefabUtility.SaveAsPrefabAsset(tree, root + "/Tree.prefab");
            UnityEngine.Object.DestroyImmediate(tree);
            data.treePrototypes = new[] { new TreePrototype { prefab = prefab } };
            data.SetTreeInstances(
                new[]
                {
                    new TreeInstance
                    {
                        position = new Vector3(.2f, .1f, .2f),
                        widthScale = 1,
                        heightScale = 1,
                        color = Color.white,
                        lightmapColor = Color.white,
                        prototypeIndex = 0,
                    },
                    new TreeInstance
                    {
                        position = new Vector3(.8f, .1f, .8f),
                        widthScale = 1,
                        heightScale = 1,
                        color = Color.white,
                        lightmapColor = Color.white,
                        prototypeIndex = 0,
                    },
                },
                false
            );
        }

        private TerrainLayer Layer(string name)
        {
            var value = new TerrainLayer();
            AssetDatabase.CreateAsset(value, root + "/" + name + ".terrainlayer");
            return value;
        }

        private TerrainData CreateData(string name)
        {
            var value = new TerrainData
            {
                heightmapResolution = 33,
                alphamapResolution = 16,
                size = new Vector3(32, 10, 32),
            };
            AssetDatabase.CreateAsset(value, root + "/" + name + ".asset");
            var heights = new float[33, 33];
            for (int y = 0; y < 33; y++)
            for (int x = 0; x < 33; x++)
                heights[y, x] = .1f;
            value.SetHeights(0, 0, heights);
            value.terrainLayers = new[] { first, second };
            var alpha = new float[16, 16, 2];
            for (int y = 0; y < 16; y++)
            for (int x = 0; x < 16; x++)
                alpha[y, x, 0] = 1;
            value.SetAlphamaps(0, 0, alpha);
            return value;
        }

        private Terrain CreateTerrain(TerrainData value, Vector3 position)
        {
            var go = new GameObject("Terrain_" + Guid.NewGuid().ToString("N"));
            objects.Add(go);
            go.transform.position = position;
            var component = go.AddComponent<Terrain>();
            component.terrainData = value;
            return component;
        }

        [TearDown]
        public void TearDown()
        {
            try
            {
                Type edit = Type.GetType("MCPForUnity.Editor.Tools.Terrain.TerrainEdit, MCPForUnity.Editor");
                edit?.GetField("AfterWriteForTests", BindingFlags.Static | BindingFlags.NonPublic)?.SetValue(null, null);
                foreach (var go in objects)
                    if (go != null)
                    {
                        Undo.ClearUndo(go);
                        UnityEngine.Object.DestroyImmediate(go);
                    }
                if (data != null)
                    Undo.ClearUndo(data);
                if (neighborData != null)
                    Undo.ClearUndo(neighborData);
                if (root != null)
                {
                    StringAssert.StartsWith("Assets/__McpTerrain_", root);
                    Assert.AreEqual(rootGuid, AssetDatabase.AssetPathToGUID(root), "Delete only this fixture's owned asset folder.");
                    Assert.IsTrue(AssetDatabase.DeleteAsset(root));
                }
            }
            finally
            {
                scenes.Close();
            }
        }

        private JObject Call(string action, JObject args = null, CancellationToken? cancellation = null)
        {
            Type handler = Type.GetType("MCPForUnity.Editor.Tools.Terrain.ManageTerrain, MCPForUnity.Editor");
            Assert.IsNotNull(handler, "SPEC18 dedicated Terrain handler is missing.");
            var request = args == null ? new JObject() : (JObject)args.DeepClone();
            request["action"] = action;
            request["target"] = terrain.gameObject.GetInstanceIDCompat().ToString();
            request["searchMethod"] = "by_id";
            object response;
            if (cancellation.HasValue)
            {
                var method = handler.GetMethod("HandleCommand", new[] { typeof(JObject), typeof(CancellationToken) });
                Assert.IsNotNull(method, "Terrain edits must expose the registry's cooperative cancellation overload.");
                response = ((Task<object>)method.Invoke(null, new object[] { request, cancellation.Value })).GetAwaiter().GetResult();
            }
            else
                response = handler.GetMethod("HandleCommand", new[] { typeof(JObject) }).Invoke(null, new object[] { request });
            return JObject.FromObject(response);
        }

        private static JObject Region(string channel, int x = 2, int y = 2, int width = 2, int height = 2) =>
            new JObject
            {
                ["channel"] = channel,
                ["region"] = new JObject
                {
                    ["x"] = x,
                    ["y"] = y,
                    ["width"] = width,
                    ["height"] = height,
                },
            };

        private JObject Read(string action, JObject args)
        {
            JObject response = Call(action, args);
            Assert.IsTrue(response.Value<bool>("success"), response.ToString());
            return (JObject)response["data"];
        }

        private JObject Guard(string action, JObject args)
        {
            JObject observation = Read(action, args);
            var result = (JObject)args.DeepClone();
            result["expected_revision"] = observation["revision"];
            result["expected_data_guid"] = dataGuid;
            return result;
        }

        private static JArray Grid(float value) => new JArray(new JArray(value, value), new JArray(value, value));

        private static void AssertSameNativeAsset(UnityEngine.Object expected, UnityEngine.Object actual, string context)
        {
            Assert.IsTrue(expected != null && actual != null, context + ": both native assets must exist.");
            Assert.IsTrue(expected == actual, context + ": Unity native object equality must hold.");
            Assert.AreEqual(expected.GetInstanceIDCompat(), actual.GetInstanceIDCompat(), context + ": native instance identity must be preserved.");
            Assert.IsTrue(
                AssetDatabase.TryGetGUIDAndLocalFileIdentifier(expected, out string expectedGuid, out long expectedLocalId),
                context + ": expected asset must be persistent."
            );
            Assert.IsTrue(
                AssetDatabase.TryGetGUIDAndLocalFileIdentifier(actual, out string actualGuid, out long actualLocalId),
                context + ": actual asset must be persistent."
            );
            Assert.AreEqual(expectedGuid, actualGuid, context + ": asset GUID must be preserved.");
            Assert.AreEqual(expectedLocalId, actualLocalId, context + ": local file ID must be preserved.");
        }

        [Test]
        public void HeightPatchPreservesOutsideNeighborsLayersGuidAndNativeUndo()
        {
            float[,] before = data.GetHeights(0, 0, 33, 33);
            float[,] adjacent = neighborData.GetHeights(0, 0, 33, 33);
            TerrainLayer[] layers = data.terrainLayers;
            JObject patch = Guard("get_region", Region("heights"));
            patch["values"] = Grid(.4f);
            JObject result = Call("set_region", patch);
            Assert.IsTrue(result.Value<bool>("success"), result.ToString());
            float[,] after = data.GetHeights(0, 0, 33, 33);
            for (int y = 0; y < 33; y++)
            for (int x = 0; x < 33; x++)
                Assert.AreEqual(x >= 2 && x < 4 && y >= 2 && y < 4 ? .4f : before[y, x], after[y, x], 0.00002f, "Height at " + x + "," + y);
            CollectionAssert.AreEqual(adjacent, neighborData.GetHeights(0, 0, 33, 33));
            CollectionAssert.AreEqual(layers, data.terrainLayers);
            AssertSameNativeAsset(data, terrain.terrainData, "Height patch TerrainData");
            Assert.AreEqual(dataGuid, AssetDatabase.AssetPathToGUID(AssetDatabase.GetAssetPath(data)));
            Assert.IsTrue(result["data"].Value<bool>("undo_registered"), result.ToString());
            Undo.FlushUndoRecordObjects();
            Undo.PerformUndo();
            CollectionAssert.AreEqual(before, data.GetHeights(0, 0, 33, 33), "Native Undo must restore the real heightmap.");
        }

        [Test]
        public void SplatHolesAndDetailPatchesPreserveOutsideData()
        {
            var splat = Guard("get_region", Region("alphamaps"));
            splat["values"] = new JArray(new JArray(new JArray(0, 1), new JArray(0, 1)), new JArray(new JArray(0, 1), new JArray(0, 1)));
            JObject alphaResult = Call("set_region", splat);
            Assert.IsTrue(alphaResult.Value<bool>("success"), alphaResult.ToString());
            var weights = data.GetAlphamaps(0, 0, 16, 16);
            for (int y = 0; y < 16; y++)
            for (int x = 0; x < 16; x++)
                Assert.AreEqual(x >= 2 && x < 4 && y >= 2 && y < 4 ? 1 : 0, weights[y, x, 1], .005f);
            var hole = Guard("get_region", Region("holes"));
            hole["values"] = new JArray(new JArray(false, false), new JArray(false, false));
            JObject holesResult = Call("set_region", hole);
            Assert.IsTrue(holesResult.Value<bool>("success"), holesResult.ToString());
            bool[,] holes = data.GetHoles(0, 0, 32, 32);
            for (int y = 0; y < 32; y++)
            for (int x = 0; x < 32; x++)
                Assert.AreEqual(!(x >= 2 && x < 4 && y >= 2 && y < 4), holes[y, x]);
            var detailArgs = Region("details");
            detailArgs["detail_layer"] = 0;
            var detail = Guard("get_region", detailArgs);
            detail["values"] = new JArray(new JArray(2, 2), new JArray(2, 2));
            JObject detailResult = Call("set_region", detail);
            Assert.IsTrue(detailResult.Value<bool>("success"), detailResult.ToString());
            int[,] map = data.GetDetailLayer(0, 0, 32, 32, 0);
            for (int y = 0; y < 32; y++)
            for (int x = 0; x < 32; x++)
                Assert.AreEqual(x >= 2 && x < 4 && y >= 2 && y < 4 ? 2 : 0, map[y, x]);
            Assert.AreSame(first, data.terrainLayers[0]);
            Assert.AreSame(second, data.terrainLayers[1]);
        }

        [Test]
        public void IndexedTreeAndLayerEditsPreserveOtherInstancesAndSplatWeights()
        {
            TreeInstance before = data.GetTreeInstance(0);
            var trees = Guard("get_trees", new JObject { ["indices"] = new JArray(1) });
            trees["values"] = new JArray(new JObject { ["width_scale"] = 2f, ["rotation"] = .5f });
            JObject treeResult = Call("set_trees", trees);
            Assert.IsTrue(treeResult.Value<bool>("success"), treeResult.ToString());
            Assert.AreEqual(2, data.GetTreeInstance(1).widthScale);
            Assert.AreEqual(.5f, data.GetTreeInstance(1).rotation);
            Assert.AreEqual(before.position, data.GetTreeInstance(0).position);
            Assert.AreEqual(before.widthScale, data.GetTreeInstance(0).widthScale);
            Assert.AreEqual(0, data.GetTreeInstance(1).prototypeIndex);
            float[,,] weights = data.GetAlphamaps(0, 0, 16, 16);
            var layer = Guard("get_layer", new JObject { ["index"] = 1 });
            layer["layer_path"] = AssetDatabase.GetAssetPath(replacement);
            layer["layer_guid"] = AssetDatabase.AssetPathToGUID(AssetDatabase.GetAssetPath(replacement));
            JObject layerResult = Call("set_layer", layer);
            Assert.IsTrue(layerResult.Value<bool>("success"), layerResult.ToString());
            Assert.AreSame(first, data.terrainLayers[0]);
            Assert.AreSame(replacement, data.terrainLayers[1]);
            CollectionAssert.AreEqual(weights, data.GetAlphamaps(0, 0, 16, 16), "Layer replacement must preserve all splat weights.");
            Assert.AreEqual(dataGuid, AssetDatabase.AssetPathToGUID(AssetDatabase.GetAssetPath(data)));
        }

        [Test]
        public void BoundaryAcknowledgementNeverChangesAdjacentTerrain()
        {
            var args = Region("heights", 32, 2, 1, 1);
            var patch = Guard("get_region", args);
            patch["values"] = new JArray { new JArray(.7f) };
            var denied = Call("set_region", patch);
            Assert.IsFalse(denied.Value<bool>("success"), denied.ToString());
            Assert.AreEqual("terrain_boundary_ack_required", denied.Value<string>("code"));
            var before = neighborData.GetHeights(0, 0, 33, 33);
            patch["allow_boundary"] = true;
            var allowed = Call("set_region", patch);
            Assert.IsTrue(allowed.Value<bool>("success"), allowed.ToString());
            Assert.IsTrue(allowed["data"].Value<bool>("boundary_touched"), allowed.ToString());
            CollectionAssert.AreEqual(before, neighborData.GetHeights(0, 0, 33, 33));
            Assert.IsTrue(neighbor == terrain.rightNeighbor, "The connected neighbor native object must be preserved.");
            Assert.AreEqual(
                neighbor.GetInstanceIDCompat(),
                terrain.rightNeighbor.GetInstanceIDCompat(),
                "The connected neighbor instance ID must be preserved."
            );
            AssertSameNativeAsset(neighborData, terrain.rightNeighbor.terrainData, "Adjacent TerrainData");
        }

        [Test]
        public void SharedTerrainDataRequiresExplicitAcknowledgement()
        {
            Terrain shared = CreateTerrain(data, new Vector3(0, 0, 64));
            var patch = Guard("get_region", Region("heights"));
            patch["values"] = Grid(.3f);
            JObject denied = Call("set_region", patch);
            Assert.IsFalse(denied.Value<bool>("success"), denied.ToString());
            Assert.AreEqual("terrain_shared_data_ack_required", denied.Value<string>("code"));
            patch["allow_shared_data"] = true;
            JObject allowed = Call("set_region", patch);
            Assert.IsTrue(allowed.Value<bool>("success"), allowed.ToString());
            AssertSameNativeAsset(data, shared.terrainData, "Shared TerrainData");
            Assert.AreEqual(.3f, shared.terrainData.GetHeights(2, 2, 1, 1)[0, 0], .00002f);
        }

        [Test]
        public void InvalidShapeIdentityRevisionAndIndicesRefuseBeforeWrites()
        {
            var patch = Guard("get_region", Region("heights"));
            patch["values"] = Grid(.5f);
            var before = data.GetHeights(0, 0, 33, 33);
            JObject invalid = (JObject)patch.DeepClone();
            invalid["values"] = new JArray(new JArray(.2f));
            Assert.IsFalse(Call("set_region", invalid).Value<bool>("success"));
            invalid = (JObject)patch.DeepClone();
            invalid["expected_data_guid"] = new string('0', 32);
            Assert.IsFalse(Call("set_region", invalid).Value<bool>("success"));
            Assert.IsFalse(Call("get_trees", new JObject { ["indices"] = new JArray(2) }).Value<bool>("success"));
            CollectionAssert.AreEqual(before, data.GetHeights(0, 0, 33, 33));
            data.SetHeights(
                2,
                2,
                new[,]
                {
                    { .8f },
                }
            );
            float[,] external = data.GetHeights(0, 0, 33, 33);
            JObject stale = Call("set_region", patch);
            Assert.IsFalse(stale.Value<bool>("success"), stale.ToString());
            Assert.AreEqual("stale_terrain_revision", stale.Value<string>("code"));
            CollectionAssert.AreEqual(external, data.GetHeights(0, 0, 33, 33));
        }

        private static void Stage(Action<TerrainData> callback)
        {
            Type type = Type.GetType("MCPForUnity.Editor.Tools.Terrain.TerrainEdit, MCPForUnity.Editor");
            Assert.IsNotNull(type, "The conditional recovery implementation is required.");
            FieldInfo field = type.GetField("AfterWriteForTests", BindingFlags.Static | BindingFlags.NonPublic);
            Assert.IsNotNull(field, "A narrow after-verified-write fault seam is required for causal recovery coverage.");
            field.SetValue(null, callback);
        }

        [Test]
        public void CancellationBeforeWriteAndAfterVerifiedWritePreservesOriginal()
        {
            var patch = Guard("get_region", Region("heights"));
            patch["values"] = Grid(.4f);
            float[,] before = data.GetHeights(0, 0, 33, 33);
            using (var cancelled = new CancellationTokenSource())
            {
                cancelled.Cancel();
                JObject early = Call("set_region", patch, cancelled.Token);
                Assert.IsFalse(early.Value<bool>("success"), early.ToString());
                Assert.AreEqual("terrain_cancelled", early.Value<string>("code"));
            }
            using (var cancelled = new CancellationTokenSource())
            {
                Stage(_ => cancelled.Cancel());
                JObject late = Call("set_region", patch, cancelled.Token);
                Assert.IsFalse(late.Value<bool>("success"), late.ToString());
                Assert.AreEqual("terrain_cancelled", late.Value<string>("code"));
                Assert.AreEqual(4, late["data"]["recovery"].Value<int>("restored_cells"), late.ToString());
            }
            CollectionAssert.AreEqual(before, data.GetHeights(0, 0, 33, 33));
        }

        [Test]
        public void ConditionalRecoveryRestoresOwnedCellsAndPreservesExternalWriter()
        {
            var patch = Guard("get_region", Region("heights"));
            patch["values"] = Grid(.4f);
            float original = data.GetHeights(2, 2, 1, 1)[0, 0];
            Stage(value =>
            {
                value.SetHeights(
                    2,
                    2,
                    new[,]
                    {
                        { .8f },
                    }
                );
                throw new InvalidOperationException("Owned fixture injects a failure after native readback.");
            });
            JObject response = Call("set_region", patch);
            Assert.IsFalse(response.Value<bool>("success"), response.ToString());
            Assert.AreEqual(3, response["data"]["recovery"].Value<int>("restored_cells"), response.ToString());
            Assert.AreEqual(1, response["data"]["recovery"].Value<int>("conflicted_cells"), response.ToString());
            Assert.AreEqual(.8f, data.GetHeights(2, 2, 1, 1)[0, 0], .00002f);
            Assert.AreEqual(original, data.GetHeights(3, 2, 1, 1)[0, 0]);
            Assert.AreEqual(original, data.GetHeights(2, 3, 1, 1)[0, 0]);
            Assert.AreEqual(original, data.GetHeights(3, 3, 1, 1)[0, 0]);
        }
    }
}
