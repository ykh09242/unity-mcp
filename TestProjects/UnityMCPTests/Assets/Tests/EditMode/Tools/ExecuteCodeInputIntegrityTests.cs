using System;
using System.Reflection;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Editor.Tools;
using Newtonsoft.Json.Linq;
using NUnit.Framework;

namespace MCPForUnityTests.Editor.Tools
{
    // Exercise the actual shared input conversion without executing code or touching history.
    public class ExecuteCodeInputIntegrityTests
    {
        private static object[] Convert<T>(string json, string field, out bool accepted) where T : struct
        {
            var parameters = new JObject();
            if (json != null)
                parameters[field] = JToken.Parse(json);
            string before = parameters.ToString();
            var method = typeof(ExecuteCode).GetMethod("TryReadOptionalValue", BindingFlags.NonPublic | BindingFlags.Static);
            Assert.IsNotNull(method, "The production conversion helper must exist.");
            object[] arguments = { parameters, field, null, null };
            accepted = (bool)method.MakeGenericMethod(typeof(T)).Invoke(null, arguments);
            Assert.AreEqual(before, parameters.ToString(), "Conversion must preserve the input object.");
            return arguments;
        }

        [TestCase(null, null)]
        [TestCase("null", null)]
        [TestCase("false", false)]
        [TestCase("true", true)]
        [TestCase("0", false)]
        [TestCase("1", true)]
        [TestCase("\"false\"", false)]
        [TestCase("\"true\"", true)]
        public void BooleanOptionalValuePreservesAcceptedConversions(string json, object expected)
        {
            var arguments = Convert<bool>(json, "safety_checks", out bool accepted);
            Assert.IsTrue(accepted);
            Assert.AreEqual(expected, arguments[2]);
            Assert.IsNull(arguments[3]);
        }

        [TestCase(null, null)]
        [TestCase("null", null)]
        [TestCase("0", 0)]
        [TestCase("-1", -1)]
        [TestCase("\"1\"", 1)]
        [TestCase("false", 0)]
        [TestCase("true", 1)]
        [TestCase("2147483647", int.MaxValue)]
        public void IntegerOptionalValuePreservesAcceptedConversions(string json, object expected)
        {
            var arguments = Convert<int>(json, "limit", out bool accepted);
            Assert.IsTrue(accepted);
            Assert.AreEqual(expected, arguments[2]);
            Assert.IsNull(arguments[3]);
        }

        [TestCase("\"invalid\"")]
        [TestCase("{}")]
        [TestCase("[]")]
        public void InvalidBooleanReturnsNamedError(string json)
        {
            var arguments = Convert<bool>(json, "safety_checks", out bool accepted);
            Assert.IsFalse(accepted);
            Assert.IsNull(arguments[2]);
            Assert.IsInstanceOf<ErrorResponse>(arguments[3]);
            StringAssert.Contains("'safety_checks'", ((ErrorResponse)arguments[3]).Error);
        }

        [TestCase("\"invalid\"")]
        [TestCase("{}")]
        [TestCase("[]")]
        [TestCase("9223372036854775807")]
        [TestCase("1e100")]
        public void InvalidIntegerReturnsNamedError(string json)
        {
            var arguments = Convert<int>(json, "index", out bool accepted);
            Assert.IsFalse(accepted);
            Assert.IsNull(arguments[2]);
            Assert.IsInstanceOf<ErrorResponse>(arguments[3]);
            StringAssert.Contains("'index'", ((ErrorResponse)arguments[3]).Error);
        }
    }
}
