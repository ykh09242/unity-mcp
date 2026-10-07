using System.Reflection;
using MCPForUnity.Editor.Resources.MenuItems;
using MCPForUnity.Editor.Tools;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEditor;

namespace MCPForUnityTests.Editor.Tools
{
    [Parallelizable(ParallelScope.None)]
    public class ExecuteMenuItemTests
    {
        private const string OwnedMenuPath = "Tools/MCPForUnityTests/Owned Menu 80f1a802b581420493c68b31d1b43d8c";
        private static bool _ownedMenuInvoked;

        [MenuItem(OwnedMenuPath)]
        private static void InvokeOwnedMenu() => _ownedMenuInvoked = true;

        [SetUp]
        public void SetUp() => _ownedMenuInvoked = false;

        [TearDown]
        public void TearDown() => _ownedMenuInvoked = false;

        private static JObject ToJO(object o) => JObject.FromObject(o);

        [Test]
        public void Execute_MissingParam_ReturnsError()
        {
            var res = ExecuteMenuItem.HandleCommand(new JObject());
            var jo = ToJO(res);
            Assert.IsFalse((bool)jo["success"], "Expected success false");
            StringAssert.Contains("Required parameter", (string)jo["error"]);
        }

        [Test]
        public void Execute_Blacklisted_ReturnsError()
        {
            var res = ExecuteMenuItem.HandleCommand(new JObject { ["menuPath"] = "File/Quit" });
            var jo = ToJO(res);
            Assert.IsFalse((bool)jo["success"], "Expected success false for blacklisted menu");
            StringAssert.Contains("blocked for safety", (string)jo["error"], "Expected blacklist message");
        }

        [Test]
        public void Execute_OwnedHarmlessMenu_InvokesOnlyManagedCallback()
        {
            try
            {
                var jo = ToJO(ExecuteMenuItem.HandleCommand(new JObject { ["menuPath"] = OwnedMenuPath }));
                Assert.IsTrue((bool)jo["success"], jo.ToString());
                Assert.IsTrue(_ownedMenuInvoked, "The exact owned callback must execute synchronously.");
            }
            finally
            {
                _ownedMenuInvoked = false;
            }
        }

        [Test]
        public void Execute_NullParameters_ReturnsError()
        {
            Assert.IsFalse(ToJO(ExecuteMenuItem.HandleCommand(null)).Value<bool>("success"));
            Assert.IsFalse(_ownedMenuInvoked);
        }

        [TestCase("{}")]
        [TestCase("{\"menu_path\":null,\"menuPath\":\"Fixture/A\"}")]
        [TestCase("{\"menuPath\":\"\"}")]
        [TestCase("{\"menu_path\":\"   \"}")]
        [TestCase("{\"menu_path\":42}")]
        [TestCase("{\"menuPath\":false}")]
        [TestCase("{\"menu_path\":{\"path\":\"Fixture/A\"}}")]
        [TestCase("{\"menuPath\":[\"Fixture/A\"]}")]
        public void PathParserRejectsInvalidInputsWithoutMenuExecution(string json)
        {
            var parameters = JObject.Parse(json);
            string before = parameters.ToString();
            var method = typeof(ExecuteMenuItem).GetMethod("TryGetMenuPath", BindingFlags.Static | BindingFlags.NonPublic);
            Assert.IsNotNull(method);
            object[] arguments = { parameters, null, null };
            Assert.IsFalse((bool)method.Invoke(null, arguments));
            Assert.IsFalse(ToJO(arguments[2]).Value<bool>("success"));
            StringAssert.Contains("menu_path", ToJO(arguments[2]).Value<string>("error"));
            Assert.AreEqual(before, parameters.ToString());
            Assert.IsFalse(_ownedMenuInvoked);
        }

        [TestCase("{\"menu_path\":\"Fixture/A\"}", "Fixture/A")]
        [TestCase("{\"menuPath\":\"Fixture/Z\"}", "Fixture/Z")]
        [TestCase("{\"menu_path\":\"Fixture/A\",\"menuPath\":\"Fixture/Z\"}", "Fixture/A")]
        [TestCase("{\"menu_path\":\" Fixture/A \"}", " Fixture/A ")]
        public void PathParserPreservesStringsAndPrecedence(string json, string expected)
        {
            var parameters = JObject.Parse(json);
            string before = parameters.ToString();
            var method = typeof(ExecuteMenuItem).GetMethod("TryGetMenuPath", BindingFlags.Static | BindingFlags.NonPublic);
            Assert.IsNotNull(method);
            object[] arguments = { parameters, null, null };
            Assert.IsTrue((bool)method.Invoke(null, arguments));
            Assert.AreEqual(expected, arguments[1]);
            Assert.IsNull(arguments[2]);
            Assert.AreEqual(before, parameters.ToString());
            Assert.IsFalse(_ownedMenuInvoked);
        }

        [TestCase("{}", false)]
        [TestCase("{\"refresh\":null}", false)]
        [TestCase("{\"refresh\":false}", false)]
        [TestCase("{\"refresh\":0}", false)]
        [TestCase("{\"refresh\":\"false\"}", false)]
        [TestCase("{\"refresh\":true}", true)]
        [TestCase("{\"refresh\":1}", true)]
        [TestCase("{\"refresh\":\"true\"}", true)]
        public void RefreshParserPreservesDefaultsAndCoercions(string json, bool expected)
        {
            var parameters = JObject.Parse(json);
            string before = parameters.ToString();
            var method = typeof(GetMenuItems).GetMethod("TryReadRefresh", BindingFlags.Static | BindingFlags.NonPublic);
            Assert.IsNotNull(method);
            object[] arguments = { parameters, false, null };
            Assert.IsTrue((bool)method.Invoke(null, arguments));
            Assert.AreEqual(expected, arguments[1]);
            Assert.IsNull(arguments[2]);
            Assert.AreEqual(before, parameters.ToString());
        }

        [TestCase("{\"refresh\":\"invalid\"}")]
        [TestCase("{\"refresh\":{}}")]
        [TestCase("{\"refresh\":[]}")]
        public void RefreshParserRejectsMalformedValuesWithoutDiscovery(string json)
        {
            var parameters = JObject.Parse(json);
            string before = parameters.ToString();
            var method = typeof(GetMenuItems).GetMethod("TryReadRefresh", BindingFlags.Static | BindingFlags.NonPublic);
            Assert.IsNotNull(method);
            object[] arguments = { parameters, false, null };
            Assert.IsFalse((bool)method.Invoke(null, arguments));
            Assert.IsFalse(ToJO(arguments[2]).Value<bool>("success"));
            StringAssert.Contains("refresh", ToJO(arguments[2]).Value<string>("error"));
            Assert.AreEqual(before, parameters.ToString());
        }
    }
}
