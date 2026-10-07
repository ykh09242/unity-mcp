using MCPForUnity.Editor.Tools;
using Newtonsoft.Json.Linq;
using NUnit.Framework;

namespace MCPForUnityTests.Editor.Tools
{
    public class UnityReflectGenericNameTests
    {
        [TestCase("List<T>", "System.Collections.Generic.List`1")]
        [TestCase("Dictionary<TKey,TValue>", "System.Collections.Generic.Dictionary`2")]
        [TestCase("Dictionary<string,List<int>>", "System.Collections.Generic.Dictionary`2")]
        [TestCase("Dictionary<List<int>,Dictionary<string,List<bool>>>", "System.Collections.Generic.Dictionary`2")]
        [TestCase("System.Collections.Generic.List<Dictionary<string,int>>", "System.Collections.Generic.List`1")]
        [TestCase("Tuple<List<int>,Dictionary<string,bool>,int>", "System.Tuple`3")]
        [TestCase("System.Collections.Generic.Dictionary`2", "System.Collections.Generic.Dictionary`2")]
        public void GetType_GenericArgumentsResolveToSafeDefinition(string className, string expectedName)
        {
            var response = Invoke("get_type", className);
            Assert.IsTrue((bool)response["success"]);
            var data = response["data"];
            Assert.IsTrue((bool)data["found"]);
            Assert.AreEqual(expectedName, (string)data["full_name"]);
            Assert.IsTrue((bool)data["is_generic_type_definition"]);
            Assert.IsNull(data["members"], "Keep the open generic member-reflection safeguard.");

            var memberResponse = Invoke("get_member", className);
            Assert.IsTrue((bool)memberResponse["success"]);
            Assert.IsFalse((bool)memberResponse["data"]["found"]);
            Assert.IsTrue((bool)memberResponse["data"]["is_generic_type_definition"]);
            Assert.AreEqual(expectedName, (string)memberResponse["data"]["type_name"]);
        }

        [TestCase("ZzzUnknownAuditType<T>")]
        [TestCase("List<T")]
        [TestCase("List<T>>")]
        [TestCase("List<<T>")]
        [TestCase("List<>")]
        public void GetType_UnknownOrUnbalancedNameRemainsNotFound(string className)
        {
            var response = Invoke("get_type", className);
            Assert.IsTrue((bool)response["success"]);
            Assert.IsFalse((bool)response["data"]["found"]);
        }

        private static JObject Invoke(string action, string className)
        {
            return JObject.FromObject(
                UnityReflect.HandleCommand(
                    new JObject
                    {
                        ["action"] = action,
                        ["class_name"] = className,
                        ["member_name"] = "Add",
                    }
                )
            );
        }
    }
}
