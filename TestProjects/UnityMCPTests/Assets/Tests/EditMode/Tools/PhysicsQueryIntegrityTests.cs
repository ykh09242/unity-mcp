using System;
using System.Collections.Generic;
using System.Linq;
using System.Text.RegularExpressions;
using MCPForUnity.Editor.Tools.Physics;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEditor;
using UnityEngine;
using MCPForUnity.Runtime.Helpers;
using UnityEngine.TestTools;

namespace MCPForUnityTests.Editor.Tools
{
    // NUnit3.5 equivalent of NonParallelizable; this fixture temporarily controls a global query flag.
    [Parallelizable(ParallelScope.None)]
    public class PhysicsQueryIntegrityTests
    {
        private readonly Vector2 origin = new Vector2(65432, 54321);
        private const int Mask = 1 << 31;
        private List<GameObject> owned;
        private List<BoxCollider2D> colliders;
        private bool previousQueriesHitTriggers;

        [SetUp]
        public void SetUp()
        {
            previousQueriesHitTriggers = Physics2D.queriesHitTriggers;
            owned = new List<GameObject>();
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
                    foreach (var gameObject in owned)
                        if (gameObject != null) UnityEngine.Object.DestroyImmediate(gameObject);
            }
            finally
            {
                Physics2D.queriesHitTriggers = previousQueriesHitTriggers;
            }
        }

        [TestCase("3d", "'bad'")]
        [TestCase("3d", "{flag:false}")]
        [TestCase("3d", "[false]")]
        [TestCase("2d", "'bad'")]
        [TestCase("2d", "{flag:false}")]
        [TestCase("2d", "[false]")]
        public void MalformedCollidePreservesMatrixAndSettingsDirtyState(string dimension, string collide)
        {
            bool before = dimension == "2d"
                ? Physics2D.GetIgnoreLayerCollision(0, 31)
                : UnityEngine.Physics.GetIgnoreLayerCollision(0, 31);
            string path = dimension == "2d" ? "ProjectSettings/Physics2DSettings.asset" : "ProjectSettings/DynamicsManager.asset";
            var settings = AssetDatabase.LoadAllAssetsAtPath(path);
            var dirtyCounts = settings.Select(EditorUtility.GetDirtyCount).ToArray();
            JObject response = Call(new JObject
            {
                ["action"] = "set_collision_matrix", ["dimension"] = dimension,
                ["layer_a"] = 0, ["layer_b"] = 31, ["collide"] = JToken.Parse(collide)
            });
            Assert.IsFalse(response.Value<bool>("success"), response.ToString());
            Assert.That(response.Value<string>("error"), Does.Contain("collide"));
            bool after = dimension == "2d"
                ? Physics2D.GetIgnoreLayerCollision(0, 31)
                : UnityEngine.Physics.GetIgnoreLayerCollision(0, 31);
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
        public void ExplicitTriggerModeOverridesGlobalWithoutChangingIt(string action, string shape, string trigger)
        {
            bool oppositeGlobal = trigger == "Ignore";
            Physics2D.queriesHitTriggers = oppositeGlobal;
            try
            {
                JObject parameters = Query(action, shape);
                parameters["query_trigger_interaction"] = trigger;
                JObject response = Call(parameters);
                Assert.IsTrue(response.Value<bool>("success"), response.ToString());
                Assert.AreEqual(oppositeGlobal, Physics2D.queriesHitTriggers);
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
            finally
            {
                Physics2D.queriesHitTriggers = previousQueriesHitTriggers;
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
                if (trigger != null) parameters["query_trigger_interaction"] = trigger;
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

        private JObject Query(string action, string shape = null)
        {
            JToken size = shape == "box" ? (JToken)new JArray(0.2f, 0.2f)
                : shape == "capsule" ? new JObject { ["width"] = 0.2f, ["height"] = 0.4f }
                : (JToken)0.1f;
            return new JObject
            {
                ["action"] = action, ["dimension"] = "2d", ["layer_mask"] = Mask, ["max_distance"] = 10,
                ["origin"] = new JArray(origin.x, origin.y), ["direction"] = new JArray(1, 0),
                ["start"] = new JArray(origin.x, origin.y), ["end"] = new JArray(origin.x + 10, origin.y),
                ["position"] = new JArray(origin.x, origin.y, 0), ["shape"] = shape, ["size"] = size
            };
        }

        private static JObject Call(JObject parameters) => JObject.FromObject(ManagePhysics.HandleCommand(parameters));
    }
}
