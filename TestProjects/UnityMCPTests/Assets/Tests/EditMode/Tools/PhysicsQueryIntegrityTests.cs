using System;
using System.Collections.Generic;
using System.Linq;
using System.Text.RegularExpressions;
using MCPForUnity.Editor.Tools.Physics;
using MCPForUnity.Runtime.Helpers;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEditor;
using UnityEngine;
using UnityEngine.TestTools;

namespace MCPForUnityTests.Editor.Tools
{
    // NUnit3.5 equivalent of NonParallelizable; this fixture temporarily controls a global query flag.
    [Parallelizable(ParallelScope.None)]
    public class PhysicsQueryIntegrityTests
    {
        // Keep tiny circle/capsule casts in a numerically stable native physics range.
        private readonly Vector2 origin = new Vector2(128, 256);
        private const int Mask = 1 << 31;
        private List<GameObject> owned;
        private List<GameObject> owned3D;
        private List<BoxCollider2D> colliders;
        private bool previousQueriesHitTriggers;
        private bool previousQueriesHitTriggers3D;

        [SetUp]
        public void SetUp()
        {
            previousQueriesHitTriggers = Physics2D.queriesHitTriggers;
            previousQueriesHitTriggers3D = UnityEngine.Physics.queriesHitTriggers;
            owned = new List<GameObject>();
            owned3D = new List<GameObject>();
            colliders = new List<BoxCollider2D>();
            string prefix = "__McpPhysicsQueryIntegrity_" + Guid.NewGuid().ToString("N");
            for (int i = 0; i < 4; i++)
            {
                var gameObject = new GameObject(prefix + "_" + i);
                owned.Add(gameObject);
                gameObject.layer = 31;
                gameObject.transform.position = new Vector3(origin.x + 2 * (i + 1), origin.y, 0);
                var collider = gameObject.AddComponent<BoxCollider2D>();
                collider.size = Vector2.one * 0.5f;
                collider.isTrigger = i == 0;
                colliders.Add(collider);
            }
            Physics2D.SyncTransforms();
            try
            {
                Physics2D.queriesHitTriggers = true;
                var nearby = Physics2D.OverlapBoxAll(origin + Vector2.right * 5, new Vector2(11, 3), 0, Mask);
                if (nearby.Any(collider => !colliders.Any(ownedCollider => ownedCollider == collider)))
                    Assert.Ignore("The owned query volume must contain no existing scene colliders.");
                Assert.AreEqual(4, nearby.Length, "Owned colliders must be registered before querying.");
            }
            finally
            {
                Physics2D.queriesHitTriggers = previousQueriesHitTriggers;
            }
        }

        [TearDown]
        public void TearDown()
        {
            try
            {
                if (owned != null)
                    foreach (var gameObject in owned.Concat(owned3D))
                        if (gameObject != null)
                            UnityEngine.Object.DestroyImmediate(gameObject);
            }
            finally
            {
                Physics2D.queriesHitTriggers = previousQueriesHitTriggers;
                UnityEngine.Physics.queriesHitTriggers = previousQueriesHitTriggers3D;
            }
        }

        [TestCase("2d", "circle")]
        [TestCase("2d", "box")]
        [TestCase("2d", "capsule")]
        [TestCase("3d", "sphere")]
        [TestCase("3d", "box")]
        [TestCase("3d", "capsule")]
        public void OverlapTriggerModesAreLocalForEveryShape(string dimension, string shape)
        {
            if (dimension == "3d")
                Add3DColliders();
            foreach (bool global in new[] { false, true })
            {
                Physics2D.queriesHitTriggers = global;
                UnityEngine.Physics.queriesHitTriggers = global;
                foreach (string mode in new[] { "Ignore", "Collide", "UseGlobal" })
                {
                    JObject parameters = OverlapQuery(dimension, shape);
                    parameters["query_trigger_interaction"] = mode;
                    JObject response = Call(parameters);
                    if (dimension == "2d" && mode == "Collide" && !global)
                    {
                        AssertUnsupported2DTrigger(response);
                        Assert.AreEqual(global, Physics2D.queriesHitTriggers);
                        Assert.AreEqual(global, UnityEngine.Physics.queriesHitTriggers);
                        continue;
                    }
                    Assert.IsTrue(response.Value<bool>("success"), response.ToString());
                    bool includesTrigger = mode == "Collide" || (mode == "UseGlobal" && global);
                    var expected = QueryObjects(dimension).Skip(includesTrigger ? 0 : 1).Select(gameObject => gameObject.GetInstanceIDCompat()).ToArray();
                    var actual = ((JArray)response["data"]["colliders"]).Select(collider => collider.Value<int>("instanceID")).ToArray();
                    CollectionAssert.AreEquivalent(expected, actual, dimension + "/" + shape + "/" + mode);
                    Assert.AreEqual(global, Physics2D.queriesHitTriggers);
                    Assert.AreEqual(global, UnityEngine.Physics.queriesHitTriggers);

                    parameters["layer_mask"] = 0;
                    JObject empty = Call(parameters);
                    Assert.IsTrue(empty.Value<bool>("success"), empty.ToString());
                    Assert.AreEqual(0, ((JArray)empty["data"]["colliders"]).Count);
                }
            }
        }

        [TestCase("2d", "circle")]
        [TestCase("2d", "box")]
        [TestCase("2d", "capsule")]
        [TestCase("3d", "sphere")]
        [TestCase("3d", "box")]
        [TestCase("3d", "capsule")]
        public void OverlapRejectsUndefinedTriggerModes(string dimension, string shape)
        {
            JObject parameters = OverlapQuery(dimension, shape);
            parameters["query_trigger_interaction"] = "99";
            JObject response = Call(parameters);
            Assert.IsFalse(response.Value<bool>("success"), response.ToString());
            Assert.That(response.Value<string>("error"), Does.Contain("query_trigger_interaction"));
            Assert.AreEqual(previousQueriesHitTriggers, Physics2D.queriesHitTriggers);
            Assert.AreEqual(previousQueriesHitTriggers3D, UnityEngine.Physics.queriesHitTriggers);
        }

        private void Add3DColliders()
        {
            for (int i = 0; i < owned.Count; i++)
            {
                // Unity rejects mixing 3D and 2D physics components on one GameObject.
                var gameObject = new GameObject(owned[i].name + "_3D");
                owned3D.Add(gameObject);
                gameObject.layer = 31;
                gameObject.transform.position = owned[i].transform.position;
                var collider = gameObject.AddComponent<BoxCollider>();
                collider.size = Vector3.one * 0.5f;
                collider.isTrigger = i == 0;
            }
            UnityEngine.Physics.SyncTransforms();
        }

        private List<GameObject> QueryObjects(string dimension) => dimension == "3d" ? owned3D : owned;

        private JObject OverlapQuery(string dimension, string shape)
        {
            JObject parameters = Query("overlap", shape);
            parameters["dimension"] = dimension;
            parameters["position"] = new JArray(origin.x + 5, origin.y, 0);
            parameters["size"] =
                shape == "box" ? (JToken)(dimension == "2d" ? new JArray(12, 2) : new JArray(6, 1, 1))
                : shape != "capsule" ? (JToken)6
                : dimension == "2d"
                    ? new JObject
                    {
                        ["width"] = 14,
                        ["height"] = 2,
                        ["direction"] = "horizontal",
                    }
                : new JObject
                {
                    ["radius"] = 1,
                    ["height"] = 14,
                    ["direction"] = 0,
                };
            return parameters;
        }

        [TestCase("2d", "raycast_all", null)]
        [TestCase("3d", "raycast_all", null)]
        [TestCase("2d", "overlap", "circle")]
        [TestCase("2d", "overlap", "box")]
        [TestCase("2d", "overlap", "capsule")]
        [TestCase("3d", "overlap", "sphere")]
        [TestCase("3d", "overlap", "box")]
        [TestCase("3d", "overlap", "capsule")]
        public void MultiHitBuffersReportExactCapacityAndOverCapacityHonestly(string dimension, string action, string shape)
        {
            Physics2D.queriesHitTriggers = true;
            if (dimension == "3d")
                Add3DColliders();
            var queryObjects = QueryObjects(dimension);
            foreach (int capacity in new[] { 1, 4, 5, 1000 })
            {
                JObject parameters = MultiHitQuery(dimension, action, shape);
                parameters["max_results"] = capacity;
                JObject response = Call(parameters);
                Assert.IsTrue(response.Value<bool>("success"), response.ToString());
                JObject data = (JObject)response["data"];
                var results = (JArray)data[action == "overlap" ? "colliders" : "hits"];
                int expectedCount = Math.Min(capacity, 4);
                Assert.AreEqual(expectedCount, results.Count);
                Assert.AreEqual(expectedCount, data.Value<int>("returned_count"));
                Assert.AreEqual(capacity, data.Value<int>("max_results"));
                Assert.AreEqual(capacity <= 4, data.Value<bool>("buffer_saturated"));
                CollectionAssert.IsSubsetOf(
                    results.Select(result => result.Value<int>("instanceID")).ToArray(),
                    queryObjects.Select(go => go.GetInstanceIDCompat()).ToArray()
                );
                Assert.AreEqual(results.Count, results.Select(result => result.Value<int>("instanceID")).Distinct().Count());
                if (action == "raycast_all")
                {
                    Assert.AreEqual(expectedCount, data.Value<int>("hit_count"));
                    Assert.AreEqual("distance_ascending", data.Value<string>("ordering"));
                    Assert.AreEqual(capacity > 4, data.Value<bool>("nearest_hits_guaranteed"));
                    float[] distances = results.Select(hit => hit.Value<float>("distance")).ToArray();
                    CollectionAssert.AreEqual(distances.OrderBy(distance => distance).ToArray(), distances);
                }
                else
                {
                    Assert.AreEqual("unspecified", data.Value<string>("ordering"));
                    Assert.IsNull(data["nearest_hits_guaranteed"]);
                }
                if (capacity > 4)
                    CollectionAssert.AreEquivalent(
                        queryObjects.Select(go => go.GetInstanceIDCompat()).ToArray(),
                        results.Select(result => result.Value<int>("instanceID")).ToArray()
                    );

                JObject again = (JObject)Call(parameters)["data"];
                foreach (string field in new[] { "max_results", "returned_count", "buffer_saturated", "ordering", "nearest_hits_guaranteed" })
                    Assert.IsTrue(JToken.DeepEquals(data[field], again[field]), field);
            }

            JObject defaults = MultiHitQuery(dimension, action, shape);
            Assert.AreEqual(200, Call(defaults)["data"].Value<int>("max_results"));
            defaults["max_results"] = 1;
            defaults["layer_mask"] = 0;
            JObject empty = (JObject)Call(defaults)["data"];
            Assert.AreEqual(0, empty.Value<int>("returned_count"));
            Assert.IsFalse(empty.Value<bool>("buffer_saturated"));
            Assert.AreEqual(0, ((JArray)empty[action == "overlap" ? "colliders" : "hits"]).Count);
        }

        [TestCase("0")]
        [TestCase("-1")]
        [TestCase("1001")]
        [TestCase("2147483648")]
        [TestCase("1.0")]
        [TestCase("1.5")]
        [TestCase("true")]
        [TestCase("'2'")]
        [TestCase("[]")]
        [TestCase("{}")]
        public void InvalidMultiHitLimitsAreRejectedBeforeQueryInputs(string limit)
        {
            foreach (string dimension in new[] { "2d", "3d" })
            {
                foreach (string action in new[] { "overlap", "raycast_all" })
                {
                    // Missing geometry would fail differently if admission did not precede native work.
                    JObject response = Call(
                        new JObject
                        {
                            ["action"] = action,
                            ["dimension"] = dimension,
                            ["max_results"] = JToken.Parse(limit),
                        }
                    );
                    Assert.IsFalse(response.Value<bool>("success"), response.ToString());
                    Assert.That(response.Value<string>("error"), Does.Contain("max_results"));
                }
            }
        }

        [TestCase("raycast")]
        [TestCase("linecast")]
        [TestCase("shapecast")]
        public void MultiHitLimitDoesNotChangeSingleHitActions(string action)
        {
            JObject parameters = Query(action, "circle");
            parameters["max_results"] = 0;
            JObject response = Call(parameters);
            Assert.IsTrue(response.Value<bool>("success"), response.ToString());
            Assert.IsTrue(response["data"].Value<bool>("hit"));
            Assert.IsNull(response["data"]["buffer_saturated"]);
        }

        [TestCase("raycast", null)]
        [TestCase("raycast_all", null)]
        [TestCase("linecast", null)]
        [TestCase("shapecast", "circle")]
        [TestCase("shapecast", "box")]
        [TestCase("shapecast", "capsule")]
        [TestCase("overlap", "circle")]
        [TestCase("overlap", "box")]
        [TestCase("overlap", "capsule")]
        public void Unsupported2DTriggerOverrideFailsBeforeNativeGeometryIsRead(string action, string shape)
        {
            Physics2D.queriesHitTriggers = false;
            JObject parameters = Query(action, shape);
            parameters["query_trigger_interaction"] = "Collide";
            parameters["origin"] = new JArray("bad", 0);
            parameters["position"] = new JArray("bad", 0);
            parameters["start"] = new JArray("bad", 0);
            foreach (int mask in new[] { Mask, 0 })
            {
                parameters["layer_mask"] = mask;
                AssertUnsupported2DTrigger(Call(parameters));
                Assert.IsFalse(Physics2D.queriesHitTriggers);
            }
        }

        private static void AssertUnsupported2DTrigger(JObject response)
        {
            Assert.IsFalse(response.Value<bool>("success"), response.ToString());
            Assert.AreEqual("unsupported_2d_trigger_override", response.Value<string>("code"));
            Assert.That(response.Value<string>("error"), Does.Contain("Physics2D.queriesHitTriggers=true"));
            Assert.AreEqual("2d", response["data"].Value<string>("dimension"));
            Assert.AreEqual("Collide", response["data"].Value<string>("query_trigger_interaction"));
            Assert.IsFalse(response["data"].Value<bool>("queriesHitTriggers"));
        }

        private JObject MultiHitQuery(string dimension, string action, string shape)
        {
            JObject parameters = action == "overlap" ? OverlapQuery(dimension, shape) : Query(action);
            parameters["dimension"] = dimension;
            parameters["origin"] = new JArray(origin.x, origin.y, 0);
            parameters["direction"] = new JArray(1, 0, 0);
            parameters["query_trigger_interaction"] = "Collide";
            return parameters;
        }

        [TestCase("3d", "'bad'")]
        [TestCase("3d", "{flag:false}")]
        [TestCase("3d", "[false]")]
        [TestCase("2d", "'bad'")]
        [TestCase("2d", "{flag:false}")]
        [TestCase("2d", "[false]")]
        public void MalformedCollidePreservesMatrixAndSettingsDirtyState(string dimension, string collide)
        {
            bool before = dimension == "2d" ? Physics2D.GetIgnoreLayerCollision(0, 31) : UnityEngine.Physics.GetIgnoreLayerCollision(0, 31);
            string path = dimension == "2d" ? "ProjectSettings/Physics2DSettings.asset" : "ProjectSettings/DynamicsManager.asset";
            var settings = AssetDatabase.LoadAllAssetsAtPath(path);
            var dirtyCounts = settings.Select(EditorUtility.GetDirtyCount).ToArray();
            LogAssert.Expect(LogType.Error, new Regex(@"\[ManagePhysics\] Action 'set_collision_matrix' failed:"));
            JObject response = Call(
                new JObject
                {
                    ["action"] = "set_collision_matrix",
                    ["dimension"] = dimension,
                    ["layer_a"] = 0,
                    ["layer_b"] = 31,
                    ["collide"] = JToken.Parse(collide),
                }
            );
            Assert.IsFalse(response.Value<bool>("success"), response.ToString());
            Assert.That(response.Value<string>("error"), Does.Contain("collide"));
            bool after = dimension == "2d" ? Physics2D.GetIgnoreLayerCollision(0, 31) : UnityEngine.Physics.GetIgnoreLayerCollision(0, 31);
            Assert.AreEqual(before, after);
            CollectionAssert.AreEqual(dirtyCounts, settings.Select(EditorUtility.GetDirtyCount).ToArray());
        }

        [TestCase("raycast", null, "Ignore")]
        [TestCase("raycast", null, "Collide")]
        [TestCase("raycast_all", null, "Ignore")]
        [TestCase("raycast_all", null, "Collide")]
        [TestCase("linecast", null, "Ignore")]
        [TestCase("linecast", null, "Collide")]
        [TestCase("shapecast", "circle", "Ignore")]
        [TestCase("shapecast", "circle", "Collide")]
        [TestCase("shapecast", "box", "Ignore")]
        [TestCase("shapecast", "box", "Collide")]
        [TestCase("shapecast", "capsule", "Ignore")]
        [TestCase("shapecast", "capsule", "Collide")]
        public void ExplicitTriggerModesPreserveGlobalAndRejectUnsupportedOverrides(string action, string shape, string trigger)
        {
            foreach (bool global in new[] { false, true })
            {
                Physics2D.queriesHitTriggers = global;
                JObject parameters = Query(action, shape);
                parameters["query_trigger_interaction"] = trigger;
                JObject response = Call(parameters);
                Assert.AreEqual(global, Physics2D.queriesHitTriggers);
                if (trigger == "Collide" && !global)
                {
                    AssertUnsupported2DTrigger(response);
                    continue;
                }
                Assert.IsTrue(response.Value<bool>("success"), response.ToString());
                if (action == "raycast_all")
                {
                    var hits = (JArray)response["data"]["hits"];
                    Assert.AreEqual(trigger == "Ignore" ? 3 : 4, response["data"].Value<int>("hit_count"));
                    int[] expected = owned.Skip(trigger == "Ignore" ? 1 : 0).Select(gameObject => gameObject.GetInstanceIDCompat()).ToArray();
                    CollectionAssert.AreEqual(expected, hits.Select(hit => hit.Value<int>("instanceID")).ToArray());
                    float[] distances = hits.Select(hit => hit.Value<float>("distance")).ToArray();
                    CollectionAssert.AreEqual(distances.OrderBy(distance => distance).ToArray(), distances);
                }
                else
                {
                    Assert.IsTrue(response["data"].Value<bool>("hit"));
                    Assert.AreEqual(owned[trigger == "Ignore" ? 1 : 0].GetInstanceIDCompat(), response["data"].Value<int>("instanceID"));
                }
            }
        }

        [TestCase(null, false)]
        [TestCase(null, true)]
        [TestCase("", false)]
        [TestCase("", true)]
        [TestCase("UseGlobal", false)]
        [TestCase("UseGlobal", true)]
        [TestCase("bad", true)]
        [TestCase("99", true)]
        public void LegacyTriggerFormsKeepGlobalBehavior(string trigger, bool global)
        {
            Physics2D.queriesHitTriggers = global;
            try
            {
                JObject parameters = Query("raycast");
                if (trigger != null)
                    parameters["query_trigger_interaction"] = trigger;
                JObject response = Call(parameters);
                Assert.IsTrue(response.Value<bool>("success"), response.ToString());
                Assert.AreEqual(owned[global ? 0 : 1].GetInstanceIDCompat(), response["data"].Value<int>("instanceID"));
                Assert.AreEqual(global, Physics2D.queriesHitTriggers);
            }
            finally
            {
                Physics2D.queriesHitTriggers = previousQueriesHitTriggers;
            }
        }

        [TestCase("Ignore")]
        [TestCase("Collide")]
        public void ExplicitFilterPreservesZeroLayerMask(string trigger)
        {
            Physics2D.queriesHitTriggers = true;
            JObject parameters = Query("raycast");
            parameters["query_trigger_interaction"] = trigger;
            parameters["layer_mask"] = 0;
            JObject response = Call(parameters);
            Assert.IsTrue(response.Value<bool>("success"), response.ToString());
            Assert.IsFalse(response["data"].Value<bool>("hit"));
        }

        [TestCase("overlap", "3d", "unknown", "1")]
        [TestCase("overlap", "3d", "box", "[1]")]
        [TestCase("overlap", "3d", "sphere", "'bad'")]
        [TestCase("overlap", "3d", "capsule", "{radius:'bad'}")]
        [TestCase("shapecast", "3d", "unknown", "1")]
        [TestCase("shapecast", "3d", "box", "[1]")]
        [TestCase("shapecast", "3d", "sphere", "'bad'")]
        [TestCase("shapecast", "3d", "capsule", "{}")]
        [TestCase("overlap", "2d", "unknown", "1")]
        [TestCase("overlap", "2d", "box", "[1]")]
        [TestCase("overlap", "2d", "circle", "'bad'")]
        [TestCase("overlap", "2d", "capsule", "{width:'bad'}")]
        [TestCase("shapecast", "2d", "unknown", "1")]
        [TestCase("shapecast", "2d", "box", "[1]")]
        [TestCase("shapecast", "2d", "circle", "'bad'")]
        [TestCase("shapecast", "2d", "capsule", "{width:'bad'}")]
        public void InvalidShapeOrSizeRemainsStructuredFailure(string action, string dimension, string shape, string size)
        {
            JObject parameters = Query(action, shape);
            parameters["dimension"] = dimension;
            parameters["origin"] = new JArray(origin.x, origin.y, 0);
            parameters["direction"] = new JArray(1, 0, 0);
            parameters["size"] = JToken.Parse(size);
            if (size.Contains("'bad'") || (action == "shapecast" && dimension == "3d" && shape == "capsule"))
                LogAssert.Expect(LogType.Error, new Regex(@"\[ManagePhysics\] Action '" + Regex.Escape(action) + "' failed:"));
            Assert.IsFalse(Call(parameters).Value<bool>("success"));
        }

        [TestCase("99")]
        [TestCase("-1")]
        [TestCase("2147483647")]
        [TestCase("'99'")]
        [TestCase("'-1'")]
        public void Undefined3DTriggerValuesAreRejected(string trigger)
        {
            foreach (var parameters in Queries3D())
            {
                parameters["query_trigger_interaction"] = JToken.Parse(trigger);
                JObject response = Call(parameters);
                Assert.IsFalse(response.Value<bool>("success"), parameters.ToString());
                Assert.That(response.Value<string>("error"), Does.Contain("query_trigger_interaction"));
            }
        }

        [TestCase("0")]
        [TestCase("1")]
        [TestCase("2")]
        [TestCase("'0'")]
        [TestCase("'1'")]
        [TestCase("'2'")]
        [TestCase("'UseGlobal'")]
        [TestCase("'ignore'")]
        [TestCase("'COLLIDE'")]
        [TestCase("''")]
        [TestCase("null")]
        [TestCase(null)]
        public void DefinedAndDefault3DTriggerFormsRemainCompatible(string trigger)
        {
            foreach (var parameters in Queries3D())
            {
                if (trigger != null)
                    parameters["query_trigger_interaction"] = JToken.Parse(trigger);
                JObject response = Call(parameters);
                Assert.IsTrue(response.Value<bool>("success"), response.ToString());
                if (parameters.Value<string>("action") == "raycast_all")
                    Assert.AreEqual(0, response["data"].Value<int>("hit_count"));
                else
                    Assert.IsFalse(response["data"].Value<bool>("hit"));
            }
        }

        private IEnumerable<JObject> Queries3D()
        {
            foreach (string action in new[] { "raycast", "raycast_all", "linecast", "shapecast" })
            {
                foreach (string shape in action == "shapecast" ? new[] { "sphere", "box", "capsule" } : new[] { "sphere" })
                {
                    JObject parameters = Query(action, shape);
                    parameters["dimension"] = "3d";
                    parameters["layer_mask"] = 0;
                    parameters["origin"] = new JArray(origin.x, origin.y, 0);
                    parameters["direction"] = new JArray(1, 0, 0);
                    parameters["start"] = new JArray(origin.x, origin.y, 0);
                    parameters["end"] = new JArray(origin.x + 10, origin.y, 0);
                    parameters["size"] = shape == "box" ? (JToken)new JArray(0.2f, 0.2f, 0.2f) : (JToken)0.1f;
                    yield return parameters;
                }
            }
        }

        private JObject Query(string action, string shape = null)
        {
            JToken size =
                shape == "box" ? (JToken)new JArray(0.2f, 0.2f)
                : shape == "capsule" ? new JObject { ["width"] = 0.2f, ["height"] = 0.4f }
                : (JToken)0.1f;
            return new JObject
            {
                ["action"] = action,
                ["dimension"] = "2d",
                ["layer_mask"] = Mask,
                ["max_distance"] = 10,
                ["origin"] = new JArray(origin.x, origin.y),
                ["direction"] = new JArray(1, 0),
                ["start"] = new JArray(origin.x, origin.y),
                ["end"] = new JArray(origin.x + 10, origin.y),
                ["position"] = new JArray(origin.x, origin.y, 0),
                ["shape"] = shape,
                ["size"] = size,
            };
        }

        private static JObject Call(JObject parameters) => JObject.FromObject(ManagePhysics.HandleCommand(parameters));
    }
}
