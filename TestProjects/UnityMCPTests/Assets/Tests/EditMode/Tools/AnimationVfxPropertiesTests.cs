using System;
using System.Reflection;
using MCPForUnity.Editor.Tools.Animation;
using MCPForUnity.Editor.Tools.Vfx;
using Newtonsoft.Json.Linq;
using NUnit.Framework;

namespace MCPForUnityTests.Editor.Tools
{
    public class AnimationVfxPropertiesTests
    {
        [TestCase(false, "[]")]
        [TestCase(false, "null")]
        [TestCase(false, "1")]
        [TestCase(false, "{broken")]
        [TestCase(true, "[]")]
        [TestCase(true, "null")]
        [TestCase(true, "1")]
        [TestCase(true, "{broken")]
        public void InvalidPropertiesReturnFailureBeforeActionDispatch(bool vfx, string json)
        {
            var parameters = new JObject { ["action"] = "invalid", ["properties"] = json };
            var result = JObject.FromObject(vfx ? ManageVFX.HandleCommand(parameters) : ManageAnimation.HandleCommand(parameters));
            Assert.IsFalse(result.Value<bool>("success"));
            StringAssert.Contains("properties", (result["error"] ?? result["message"]).ToString());
        }

        [TestCase(false)]
        [TestCase(true)]
        public void ObjectPropertiesPreserveValuesAndTopLevelPrecedence(bool vfx)
        {
            var type = vfx ? typeof(ManageVFX) : typeof(ManageAnimation);
            var normalize = type.GetMethod("NormalizeParams", BindingFlags.NonPublic | BindingFlags.Static);
            foreach (var properties in new JToken[] {
                JObject.Parse("{\"enabled\":false,\"speed\":0,\"value\":null,\"target\":\"nested\"}"),
                new JValue("{\"enabled\":false,\"speed\":0,\"value\":null,\"target\":\"nested\"}") })
            {
                var parameters = new JObject { ["properties"] = properties, ["target"] = "explicit" };
                var original = parameters.DeepClone();
                var result = (JObject)normalize.Invoke(null, new object[] { parameters });
                Assert.IsFalse(result.Value<bool>("enabled"));
                Assert.AreEqual(0, result.Value<int>("speed"));
                Assert.AreEqual(JTokenType.Null, result["value"].Type);
                Assert.AreEqual("explicit", result.Value<string>("target"));
                Assert.IsTrue(JToken.DeepEquals(original, parameters), "Normalization must not modify caller parameters.");
            }
            Assert.IsNotNull(normalize.Invoke(null, new object[] { new JObject() }));
            Assert.IsNotNull(normalize.Invoke(null, new object[] { new JObject { ["properties"] = JValue.CreateNull() } }));
        }
    }
}
