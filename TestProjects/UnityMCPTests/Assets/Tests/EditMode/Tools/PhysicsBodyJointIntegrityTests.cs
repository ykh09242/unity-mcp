using System;
using System.Collections.Generic;
using System.Linq;
using System.Text.RegularExpressions;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Editor.Tools.Physics;
using MCPForUnity.Runtime.Helpers;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEditor;
using UnityEngine;
using UnityEngine.TestTools;

namespace MCPForUnityTests.Editor.Tools
{
    public class PhysicsBodyJointIntegrityTests
    {
        private readonly List<GameObject> objects = new List<GameObject>();

        private GameObject Body(bool twoD = false)
        {
            var go = new GameObject("PhysicsBodyJoint_" + Guid.NewGuid().ToString("N"));
            objects.Add(go);
            if (twoD)
                go.AddComponent<Rigidbody2D>();
            else
                go.AddComponent<Rigidbody>();
            return go;
        }

        private static JObject Send(GameObject go, string action, JObject options)
        {
            options["action"] = action;
            options["target"] = go.GetInstanceIDCompat().ToString();
            options["search_method"] = "by_id";
            return JObject.FromObject(ManagePhysics.HandleCommand(options));
        }

        [TearDown]
        public void TearDown()
        {
            foreach (var go in objects)
                if (go != null)
                    UnityEngine.Object.DestroyImmediate(go);
            objects.Clear();
        }

        [TestCase("useGravity")]
        [TestCase("interpolation")]
        [TestCase("collisionDetectionMode")]
        [TestCase("constraints")]
        public void ThreeDLateInvalidValue_PreservesBody(string invalidProperty)
        {
            var go = Body();
            var rb = go.GetComponent<Rigidbody>();
            var before = Send(go, "get_rigidbody", new JObject())["data"];
            int dirty = EditorUtility.GetDirtyCount(rb);
            if (invalidProperty == "useGravity")
                LogAssert.Expect(LogType.Error, new Regex("\\[ManagePhysics\\] Action 'configure_rigidbody' failed:"));
            var response = Send(
                go,
                "configure_rigidbody",
                new JObject
                {
                    ["properties"] = new JObject { ["mass"] = 2, [invalidProperty] = "bad" },
                }
            );
            Assert.IsFalse(response.Value<bool>("success"), response.ToString());
            Assert.IsTrue(JToken.DeepEquals(before, Send(go, "get_rigidbody", new JObject())["data"]));
            Assert.AreEqual(dirty, EditorUtility.GetDirtyCount(rb));
        }

        [TestCase("simulated")]
        [TestCase("bodyType")]
        [TestCase("collisionDetectionMode")]
        [TestCase("constraints")]
        public void TwoDLateInvalidValue_PreservesBody(string invalidProperty)
        {
            var go = Body(true);
            var rb = go.GetComponent<Rigidbody2D>();
            var before = Send(go, "get_rigidbody", new JObject())["data"];
            int dirty = EditorUtility.GetDirtyCount(rb);
            if (invalidProperty == "simulated")
                LogAssert.Expect(LogType.Error, new Regex("\\[ManagePhysics\\] Action 'configure_rigidbody' failed:"));
            var response = Send(
                go,
                "configure_rigidbody",
                new JObject
                {
                    ["properties"] = new JObject { ["mass"] = 2, [invalidProperty] = "bad" },
                }
            );
            Assert.IsFalse(response.Value<bool>("success"), response.ToString());
            Assert.IsTrue(JToken.DeepEquals(before, Send(go, "get_rigidbody", new JObject())["data"]));
            Assert.AreEqual(dirty, EditorUtility.GetDirtyCount(rb));
        }

        [Test]
        public void AddJoint_InvalidKnownProperty_DoesNotCreateComponent()
        {
            var go = Body();
            int dirty = EditorUtility.GetDirtyCount(go);
            LogAssert.Expect(LogType.Error, new Regex("\\[ManagePhysics\\] Action 'add_joint' failed:"));
            var response = Send(go, "add_joint", new JObject { ["joint_type"] = "fixed", ["properties"] = JObject.Parse("{breakForce:2,breakTorque:'bad'}") });
            Assert.IsFalse(response.Value<bool>("success"), response.ToString());
            Assert.IsEmpty(go.GetComponents<Joint>());
            Assert.AreEqual(dirty, EditorUtility.GetDirtyCount(go));
        }

        [TestCase("limits")]
        [TestCase("drive")]
        [TestCase("properties")]
        public void JointLateInvalidSection_PreservesMotorAndProperties(string section)
        {
            var go = Body();
            var joint = go.AddComponent<HingeJoint>();
            var motor = joint.motor;
            bool useMotor = joint.useMotor;
            float breakForce = joint.breakForce;
            int dirty = EditorUtility.GetDirtyCount(joint);
            if (section != "drive")
                LogAssert.Expect(LogType.Error, new Regex("\\[ManagePhysics\\] Action 'configure_joint' failed:"));
            var response = Send(
                go,
                "configure_joint",
                new JObject
                {
                    ["joint_type"] = "hinge",
                    ["motor"] = JObject.Parse("{targetVelocity:2,force:3}"),
                    [section] = section == "properties" ? JObject.Parse("{breakForce:2,breakTorque:'bad'}") : JObject.Parse("{min:'bad'}"),
                }
            );
            Assert.IsFalse(response.Value<bool>("success"), response.ToString());
            Assert.AreEqual(motor.targetVelocity, joint.motor.targetVelocity);
            Assert.AreEqual(motor.force, joint.motor.force);
            Assert.AreEqual(useMotor, joint.useMotor);
            Assert.AreEqual(breakForce, joint.breakForce);
            Assert.AreEqual(dirty, EditorUtility.GetDirtyCount(joint));
        }

        [Test]
        public void ValidJointProperties_PreserveNumericStringZeroAndVectorExtras()
        {
            var go = Body();
            var response = Send(
                go,
                "add_joint",
                new JObject { ["joint_type"] = "fixed", ["properties"] = JObject.Parse("{breakForce:'2',anchor:[1,2,3,4],unknown:1,enableCollision:false}") }
            );
            Assert.IsTrue(response.Value<bool>("success"), response.ToString());
            var joint = go.GetComponent<FixedJoint>();
            Assert.AreEqual(2f, joint.breakForce);
            Assert.AreEqual(new Vector3(1, 2, 3), joint.anchor);
            Assert.IsFalse(joint.enableCollision);
        }

        [Test]
        public void ValidBodyAndJointSections_PreserveValues()
        {
            var go = Body(true);
            var bodyResponse = Send(go, "configure_rigidbody", new JObject { ["properties"] = JObject.Parse("{mass:'2',gravityScale:-2,simulated:false}") });
            Assert.IsTrue(bodyResponse.Value<bool>("success"), bodyResponse.ToString());
            Assert.AreEqual(2f, go.GetComponent<Rigidbody2D>().mass);
            Assert.AreEqual(-2f, go.GetComponent<Rigidbody2D>().gravityScale);
            Assert.IsFalse(go.GetComponent<Rigidbody2D>().simulated);

            var threeD = Body();
            var joint = threeD.AddComponent<HingeJoint>();
            var response = Send(
                threeD,
                "configure_joint",
                new JObject
                {
                    ["joint_type"] = "hinge",
                    ["motor"] = JObject.Parse("{targetVelocity:'2',force:0,freeSpin:false}"),
                    ["limits"] = JObject.Parse("{min:-2,max:2}"),
                }
            );
            Assert.IsTrue(response.Value<bool>("success"), response.ToString());
            Assert.AreEqual(2f, joint.motor.targetVelocity);
            Assert.AreEqual(0f, joint.motor.force);
            Assert.AreEqual(-2f, joint.limits.min);
            Assert.IsTrue(joint.useMotor);
            Assert.IsTrue(joint.useLimits);
        }

        [TestCase("get_rigidbody")]
        [TestCase("configure_rigidbody")]
        public void DefaultIdTarget_SelectsOwnedBodyDespiteNumericName(string action)
        {
            var go = Body();
            string id = go.GetInstanceIDCompat().ToString();
            if (GameObjectLookup.GetAllSceneObjects(true).Any(other => other.name == id))
                Assert.Ignore("A numeric-name collision already exists; preserve unowned objects.");
            var collision = Body();
            collision.name = id;
            var response = JObject.FromObject(
                ManagePhysics.HandleCommand(
                    new JObject
                    {
                        ["action"] = action,
                        ["target"] = id,
                        ["properties"] = JObject.Parse("{mass:3}"),
                    }
                )
            );
            Assert.IsTrue(response.Value<bool>("success"), response.ToString());
            if (action == "get_rigidbody")
                Assert.AreEqual(go.GetInstanceIDCompat(), response["data"].Value<int>("instanceID"));
            else
            {
                Assert.AreEqual(3f, go.GetComponent<Rigidbody>().mass);
                Assert.AreEqual(1f, collision.GetComponent<Rigidbody>().mass);
            }
        }

        [Test]
        public void ExplicitPathAndInactivePolicies_ArePreserved()
        {
            var go = Body();
            var response = JObject.FromObject(
                ManagePhysics.HandleCommand(
                    new JObject
                    {
                        ["action"] = "configure_rigidbody",
                        ["target"] = go.name,
                        ["search_method"] = "by_path",
                        ["properties"] = JObject.Parse("{mass:2}"),
                    }
                )
            );
            Assert.IsTrue(response.Value<bool>("success"), response.ToString());
            go.SetActive(false);
            response = JObject.FromObject(
                ManagePhysics.HandleCommand(
                    new JObject
                    {
                        ["action"] = "configure_rigidbody",
                        ["target"] = go.GetInstanceIDCompat().ToString(),
                        ["properties"] = JObject.Parse("{mass:3}"),
                    }
                )
            );
            Assert.IsFalse(response.Value<bool>("success"), response.ToString());
            Assert.AreEqual(2f, go.GetComponent<Rigidbody>().mass);
        }

        [TestCase("true", 0)]
        [TestCase("false", 0)]
        [TestCase("1", 1)]
        [TestCase("0", 0)]
        public void RawJointSelectorCompatibility_SelectsExpectedOwnedJoint(string selector, int expectedIndex)
        {
            var go = Body();
            var first = go.AddComponent<FixedJoint>();
            var second = go.AddComponent<FixedJoint>();
            float firstBefore = first.breakForce;
            float secondBefore = second.breakForce;
            var response = Send(
                go,
                "configure_joint",
                new JObject
                {
                    ["joint_type"] = "fixed",
                    ["componentIndex"] = JToken.Parse(selector),
                    ["properties"] = JObject.Parse("{breakForce:2}"),
                }
            );
            Assert.IsTrue(response.Value<bool>("success"), response.ToString());
            Assert.AreEqual(expectedIndex == 0 ? 2f : firstBefore, first.breakForce);
            Assert.AreEqual(expectedIndex == 1 ? 2f : secondBefore, second.breakForce);
        }

        [TestCase("true", 2)]
        [TestCase("false", 2)]
        [TestCase("1", 1)]
        [TestCase("0", 1)]
        public void RawJointRemovalSelectorCompatibility_PreservesEstablishedCounts(string selector, int expectedRemoved)
        {
            var go = Body();
            var first = go.AddComponent<FixedJoint>();
            var second = go.AddComponent<FixedJoint>();
            var response = Send(go, "remove_joint", new JObject { ["joint_type"] = "fixed", ["componentIndex"] = JToken.Parse(selector) });
            Assert.IsTrue(response.Value<bool>("success"), response.ToString());
            Assert.AreEqual(expectedRemoved, response["data"].Value<int>("removedCount"));
            if (selector == "1")
            {
                Assert.IsTrue(first != null);
                Assert.IsTrue(second == null);
            }
            else if (selector == "0")
            {
                Assert.IsTrue(first == null);
                Assert.IsTrue(second != null);
            }
            else
                Assert.IsEmpty(go.GetComponents<FixedJoint>());
        }

        [Test]
        public void InvalidDimension_DoesNotModifyBody()
        {
            var go = Body();
            int dirty = EditorUtility.GetDirtyCount(go.GetComponent<Rigidbody>());
            var response = Send(go, "configure_rigidbody", new JObject { ["dimension"] = "4d", ["properties"] = JObject.Parse("{mass:2}") });
            Assert.IsFalse(response.Value<bool>("success"), response.ToString());
            Assert.AreEqual(1f, go.GetComponent<Rigidbody>().mass);
            Assert.AreEqual(dirty, EditorUtility.GetDirtyCount(go.GetComponent<Rigidbody>()));
        }

        [Test]
        public void IndexedDeletion_RemovesOnlyRequestedOwnedJoint()
        {
            var go = Body();
            var first = go.AddComponent<FixedJoint>();
            var second = go.AddComponent<FixedJoint>();
            int dirty = EditorUtility.GetDirtyCount(go);
            var rejected = Send(go, "remove_joint", new JObject { ["joint_type"] = "fixed", ["component_index"] = -1 });
            Assert.IsFalse(rejected.Value<bool>("success"), rejected.ToString());
            CollectionAssert.AreEqual(new[] { first, second }, go.GetComponents<FixedJoint>());
            Assert.AreEqual(dirty, EditorUtility.GetDirtyCount(go));
            var response = Send(go, "remove_joint", new JObject { ["joint_type"] = "fixed", ["component_index"] = 1 });
            Assert.IsTrue(response.Value<bool>("success"), response.ToString());
            Assert.IsTrue(first != null);
            Assert.IsTrue(second == null);
            CollectionAssert.AreEqual(new[] { first }, go.GetComponents<FixedJoint>());
        }
    }
}
