using System;
using System.Collections.Generic;
using System.Reflection;
using System.Text.RegularExpressions;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEngine;
using UnityEngine.TestTools;
using MCPForUnity.Editor.Tools.Physics;
using MCPForUnity.Runtime.Helpers;

namespace MCPForUnityTests.Editor.Tools
{
    public class PhysicsSettingsSimulationIntegrityTests
    {
        private static JObject Send(string action, string dimension, JObject data)
        {
            data["action"] = action;
            data["dimension"] = dimension;
            return JObject.FromObject(ManagePhysics.HandleCommand(data));
        }

        [TestCase("queriesHitTriggers", "'bad'", true)]
        [TestCase("gravity", "[1,2]", false)]
        [TestCase("defaultSolverIterations", "'bad'", true)]
        [TestCase("simulationMode", "'bad'", false)]
        [TestCase("simulationMode", "'99'", false)]
        public void RejectedThreeDSettings_PreserveGlobalState(string key, string value, bool logsError)
        {
            float beforeBounce = UnityEngine.Physics.bounceThreshold;
            var beforeMode = UnityPhysicsCompat.GetPhysicsSimulationMode();
            if (key == "simulationMode" && beforeMode == UnityPhysicsCompat.SimulationMode.Unknown)
                Assert.Ignore("The original mode is unavailable; conditional restoration cannot be guaranteed.");
            var before = Send("get_settings", "3d", new JObject())["data"];
            try
            {
                if (logsError)
                    LogAssert.Expect(LogType.Error, new Regex("\\[ManagePhysics\\] Action 'set_settings' failed:"));
                var response = Send("set_settings", "3d", new JObject
                {
                    ["settings"] = new JObject { ["bounceThreshold"] = beforeBounce + 1f, [key] = JToken.Parse(value) }
                });
                Assert.IsFalse(response.Value<bool>("success"), response.ToString());
                Assert.IsTrue(JToken.DeepEquals(before, Send("get_settings", "3d", new JObject())["data"]));
            }
            finally
            {
                if (UnityEngine.Physics.bounceThreshold != beforeBounce)
                    UnityEngine.Physics.bounceThreshold = beforeBounce;
                if (key == "simulationMode" && UnityPhysicsCompat.GetPhysicsSimulationMode() != beforeMode)
                    Assert.IsTrue(UnityPhysicsCompat.TrySetPhysicsSimulationMode(beforeMode), "Restore the original simulation mode.");
            }
        }

        [TestCase("queriesHitTriggers", "'bad'", true)]
        [TestCase("gravity", "[1]", false)]
        [TestCase("positionIterations", "'bad'", true)]
        [TestCase("autoSyncTransforms", "'bad'", true)]
        public void RejectedTwoDSettings_PreserveGlobalState(string key, string value, bool logsError)
        {
            int beforeIterations = Physics2D.velocityIterations;
            var before = Send("get_settings", "2d", new JObject())["data"];
            try
            {
                if (logsError)
                    LogAssert.Expect(LogType.Error, new Regex("\\[ManagePhysics\\] Action 'set_settings' failed:"));
                var response = Send("set_settings", "2d", new JObject
                {
                    ["settings"] = new JObject { ["velocityIterations"] = beforeIterations + 1, [key] = JToken.Parse(value) }
                });
                Assert.IsFalse(response.Value<bool>("success"), response.ToString());
                Assert.IsTrue(JToken.DeepEquals(before, Send("get_settings", "2d", new JObject())["data"]));
            }
            finally
            {
                if (Physics2D.velocityIterations != beforeIterations)
                    Physics2D.velocityIterations = beforeIterations;
            }
        }

        [Test]
        public void ModeCapabilityQuery_DoesNotChangeTheCurrentMode()
        {
            var before = UnityPhysicsCompat.GetPhysicsSimulationMode();
            Assert.IsFalse(UnityPhysicsCompat.CanSetPhysicsSimulationMode((UnityPhysicsCompat.SimulationMode)99));
            Assert.IsFalse(UnityPhysicsCompat.CanSetPhysicsSimulationMode(UnityPhysicsCompat.SimulationMode.Unknown));
            UnityPhysicsCompat.CanSetPhysicsSimulationMode(UnityPhysicsCompat.SimulationMode.FixedUpdate);
            UnityPhysicsCompat.CanSetPhysicsSimulationMode(UnityPhysicsCompat.SimulationMode.Script);
            Assert.AreEqual(before, UnityPhysicsCompat.GetPhysicsSimulationMode());
        }

        [Test]
        public void InvalidSimulationDimension_DoesNotChangeEitherSettingsState()
        {
            var before3 = Send("get_settings", "3d", new JObject())["data"];
            var before2 = Send("get_settings", "2d", new JObject())["data"];
            var response = Send("simulate_step", "4d", new JObject { ["steps"] = 2 });
            Assert.IsFalse(response.Value<bool>("success"), response.ToString());
            Assert.IsTrue(JToken.DeepEquals(before3, Send("get_settings", "3d", new JObject())["data"]));
            Assert.IsTrue(JToken.DeepEquals(before2, Send("get_settings", "2d", new JObject())["data"]));
        }

        [Test]
        public void ResultCollection_DefaultIdTargetsExactOwnedBodyWithoutSimulating()
        {
            var go = new GameObject("PhysicsSimulationResult_" + Guid.NewGuid().ToString("N"));
            try
            {
                go.AddComponent<Rigidbody>();
                var type = typeof(ManagePhysics).Assembly.GetType("MCPForUnity.Editor.Tools.Physics.PhysicsSimulationOps");
                var collect = type.GetMethod("CollectTargetRigidbody", BindingFlags.NonPublic | BindingFlags.Static);
                var results = (List<object>)collect.Invoke(null, new object[] { go.GetInstanceID().ToString(), null, "3d" });
                Assert.AreEqual(1, results.Count);
                Assert.AreEqual(go.GetInstanceID(), JObject.FromObject(results[0]).Value<int>("instanceID"));
                go.SetActive(false);
                results = (List<object>)collect.Invoke(null, new object[] { go.GetInstanceID().ToString(), null, "3d" });
                Assert.IsEmpty(results);
            }
            finally
            {
                UnityEngine.Object.DestroyImmediate(go);
            }
        }
    }
}
