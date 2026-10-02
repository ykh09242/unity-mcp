using System.Collections.Generic;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEngine;
using MCPForUnity.Editor.Tools.Physics;

namespace MCPForUnityTests.Editor.Tools
{
    public class PhysicsForceContractTests
    {
        private readonly List<GameObject> objects = new List<GameObject>();

        private GameObject Body(string name, bool twoD = false)
        {
            var go = new GameObject(name);
            objects.Add(go);
            if (twoD) go.AddComponent<Rigidbody2D>().gravityScale = 0f;
            else go.AddComponent<Rigidbody>().useGravity = false;
            return go;
        }

        [TearDown]
        public void TearDown()
        {
            foreach (var go in objects)
                if (go != null) Object.DestroyImmediate(go);
            objects.Clear();
        }

        [TestCase("3d", "[]")]
        [TestCase("3d", "[1,2]")]
        [TestCase("3d", "[1,2,\"bad\"]")]
        [TestCase("2d", "[]")]
        [TestCase("2d", "[\"bad\"]")]
        [TestCase("2d", "[1,2]")]
        public void InvalidTorque_DoesNotQueueForce(string dimension, string torque)
        {
            var go = Body("PhysicsContract_Rejected", dimension == "2d");
            var response = JObject.FromObject(ManagePhysics.HandleCommand(new JObject
            {
                ["action"] = "apply_force", ["target"] = go.GetInstanceID().ToString(),
                ["dimension"] = dimension, ["force"] = new JArray(1, 2, 3),
                ["torque"] = JToken.Parse(torque)
            }));
            Assert.IsFalse(response.Value<bool>("success"), response.ToString());
#if UNITY_2022_2_OR_NEWER
            if (dimension == "3d")
            {
                Assert.AreEqual(Vector3.zero, go.GetComponent<Rigidbody>().GetAccumulatedForce());
                Assert.AreEqual(Vector3.zero, go.GetComponent<Rigidbody>().GetAccumulatedTorque());
            }
#endif
        }

        [TestCase("[2]")]
        [TestCase("2")]
        public void TwoDTorque_AcceptsDocumentedArrayAndLegacyScalar(string torque)
        {
            var go = Body("PhysicsContract_2D", true);
            var response = JObject.FromObject(ManagePhysics.HandleCommand(new JObject
            {
                ["action"] = "apply_force", ["target"] = go.GetInstanceID().ToString(),
                ["dimension"] = "2d", ["force"] = new JArray(1, 2),
                ["torque"] = JToken.Parse(torque), ["force_mode"] = "Impulse"
            }));
            Assert.IsTrue(response.Value<bool>("success"), response.ToString());
            Assert.AreEqual(2f, response["data"].Value<float>("torque"));
            CollectionAssert.AreEqual(new[] { 1f, 2f }, response["data"]["force"].ToObject<float[]>());
        }

        [Test]
        public void ThreeDForceAndTorque_PreservePayload()
        {
            var go = Body("PhysicsContract_3D");
            var response = JObject.FromObject(ManagePhysics.HandleCommand(new JObject
            {
                ["action"] = "apply_force", ["target"] = go.GetInstanceID().ToString(),
                ["force"] = new JArray(1, 2, 3), ["torque"] = new JArray(4, 5, 6),
                ["position"] = new JArray(0, 1, 0), ["force_mode"] = "Impulse"
            }));
            Assert.IsTrue(response.Value<bool>("success"), response.ToString());
            CollectionAssert.AreEqual(new[] { 4f, 5f, 6f }, response["data"]["torque"].ToObject<float[]>());
        }

        [Test]
        public void InvalidDimension_DoesNotQueueForce()
        {
            var go = Body("PhysicsContract_Dimension");
            var response = JObject.FromObject(ManagePhysics.HandleCommand(new JObject
            {
                ["action"] = "apply_force", ["target"] = go.GetInstanceID().ToString(),
                ["dimension"] = "4d", ["force"] = new JArray(1, 2, 3)
            }));
            Assert.IsFalse(response.Value<bool>("success"), response.ToString());
#if UNITY_2022_2_OR_NEWER
            Assert.AreEqual(Vector3.zero, go.GetComponent<Rigidbody>().GetAccumulatedForce());
#endif
        }

        [TestCase("{force:[1,2,3],force_mode:'10'}")]
        [TestCase("{force:[1,2,3],position:[0,'NaN',0]}")]
        [TestCase("{force:[1,2,3],torque:[0,'Infinity',0]}")]
        public void InvalidNormalForceInputs_DoNotQueueForce(string input)
        {
            var go = Body("PhysicsContract_InvalidInput");
            var parameters = JObject.Parse(input);
            parameters["action"] = "apply_force";
            parameters["target"] = go.GetInstanceID().ToString();
            var response = JObject.FromObject(ManagePhysics.HandleCommand(parameters));
            Assert.IsFalse(response.Value<bool>("success"), response.ToString());
#if UNITY_2022_2_OR_NEWER
            Assert.AreEqual(Vector3.zero, go.GetComponent<Rigidbody>().GetAccumulatedForce());
            Assert.AreEqual(Vector3.zero, go.GetComponent<Rigidbody>().GetAccumulatedTorque());
#endif
        }

        [TestCase("2d")]
        [TestCase("3d")]
        public void OptionalNullForceAndPosition_PreserveAbsentBehavior(string dimension)
        {
            var go = Body("PhysicsContract_Null", dimension == "2d");
            var parameters = new JObject
            {
                ["action"] = "apply_force", ["target"] = go.GetInstanceID().ToString(),
                ["dimension"] = dimension, ["force"] = JValue.CreateNull(),
                ["torque"] = dimension == "2d" ? (JToken)new JValue(2f) : new JArray(1, 2, 3)
            };
            var torqueResult = JObject.FromObject(ManagePhysics.HandleCommand(parameters));
            Assert.IsTrue(torqueResult.Value<bool>("success"), torqueResult.ToString());
            Assert.IsNull(torqueResult["data"]["force"]);
            parameters.Remove("torque");
            parameters["force"] = new JArray(1, 2, 3);
            parameters["position"] = JValue.CreateNull();
            var forceResult = JObject.FromObject(ManagePhysics.HandleCommand(parameters));
            Assert.IsTrue(forceResult.Value<bool>("success"), forceResult.ToString());
            Assert.IsNotNull(forceResult["data"]["force"]);
        }

        [TestCase("by_name")]
        [TestCase("by_path")]
        [TestCase("by_id")]
        [TestCase(null)]
        public void ForceAndJoint_RespectExplicitSelectorAndPreserveDefaultId(string method)
        {
            var idBody = Body("PhysicsContract_Id");
            string target = idBody.GetInstanceID().ToString();
            var namedBody = Body(target);
            var idJoint = idBody.AddComponent<FixedJoint>();
            var namedJoint = namedBody.AddComponent<FixedJoint>();
            idJoint.breakForce = namedJoint.breakForce = 100f;
            var selected = method == "by_name" || method == "by_path" ? namedBody : idBody;
            var forceParams = new JObject
            {
                ["action"] = "apply_force", ["target"] = target, ["force"] = new JArray(1, 2, 3)
            };
            var jointParams = new JObject
            {
                ["action"] = "configure_joint", ["target"] = target, ["joint_type"] = "fixed",
                ["properties"] = new JObject { ["breakForce"] = 42f }
            };
            if (method != null)
            {
                forceParams["search_method"] = method;
                jointParams["search_method"] = method;
            }
            var forceResult = JObject.FromObject(ManagePhysics.HandleCommand(forceParams));
            Assert.IsTrue(forceResult.Value<bool>("success"), forceResult.ToString());
            Assert.AreEqual(selected.name, forceResult["data"].Value<string>("target"));
#if UNITY_2022_2_OR_NEWER
            var untouched = selected == idBody ? namedBody : idBody;
            Assert.AreEqual(Vector3.zero, untouched.GetComponent<Rigidbody>().GetAccumulatedForce());
            Assert.AreNotEqual(Vector3.zero, selected.GetComponent<Rigidbody>().GetAccumulatedForce());
#endif
            var jointResult = JObject.FromObject(ManagePhysics.HandleCommand(jointParams));
            Assert.IsTrue(jointResult.Value<bool>("success"), jointResult.ToString());
            Assert.AreEqual(selected == idBody ? 42f : 100f, idJoint.breakForce);
            Assert.AreEqual(selected == namedBody ? 42f : 100f, namedJoint.breakForce);
            jointParams["action"] = "remove_joint";
            jointParams.Remove("properties");
            var removeResult = JObject.FromObject(ManagePhysics.HandleCommand(jointParams));
            Assert.IsTrue(removeResult.Value<bool>("success"), removeResult.ToString());
            Assert.IsTrue(selected == idBody ? idJoint == null : namedJoint == null);
            Assert.IsTrue(selected == idBody ? namedJoint != null : idJoint != null);
        }
    }
}
