using System;
using System.Reflection;
using System.Text.RegularExpressions;
using MCPForUnity.Editor.Tools;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEngine.TestTools;

namespace MCPForUnityTests.Editor.Tools
{
    public class ToolScalarInputContractTests
    {
        [TestCase("MCPForUnity.Editor.Tools.Graphics.RenderPipelineOps", "ConvertPropertyValue")]
        [TestCase("MCPForUnity.Editor.Tools.Graphics.VolumeOps", "ConvertToParameterType")]
        [TestCase("MCPForUnity.Editor.Tools.Physics.JointOps", "ConvertValue")]
        public void DynamicPropertyConversionRejectsBooleanForNumericProperty(string typeName, string methodName)
        {
            var token = JObject.Parse("{\"properties\":{\"amount\":true}}")["properties"]["amount"];
            var method = FindMethod(typeName, methodName);
            LogAssert.Expect(UnityEngine.LogType.Error, new Regex("Error converting token to"));

            var exception = Assert.Throws<TargetInvocationException>(() => method.Invoke(null, new object[] { token, typeof(double) }));

            Assert.That(exception.InnerException, Is.TypeOf<ArgumentException>());
            Assert.That(exception.InnerException.Message, Does.Contain("properties.amount"));
        }

        [TestCase("MCPForUnity.Editor.Tools.Graphics.RenderPipelineOps", "ConvertPropertyValue")]
        [TestCase("MCPForUnity.Editor.Tools.Graphics.VolumeOps", "ConvertToParameterType")]
        [TestCase("MCPForUnity.Editor.Tools.Physics.JointOps", "ConvertValue")]
        public void DynamicPropertyConversionRejectsNumericBoolean(string typeName, string methodName)
        {
            var token = JObject.Parse("{\"properties\":{\"enabled\":1}}")["properties"]["enabled"];
            var method = FindMethod(typeName, methodName);

            var exception = Assert.Throws<TargetInvocationException>(() => method.Invoke(null, new object[] { token, typeof(bool) }));

            Assert.That(exception.InnerException, Is.TypeOf<ArgumentException>());
        }

        [TestCase("MCPForUnity.Editor.Tools.Graphics.RenderPipelineOps", "ConvertPropertyValue")]
        [TestCase("MCPForUnity.Editor.Tools.Graphics.VolumeOps", "ConvertToParameterType")]
        [TestCase("MCPForUnity.Editor.Tools.Physics.JointOps", "ConvertValue")]
        public void DynamicPropertyConversionRejectsBooleanForEnum(string typeName, string methodName)
        {
            var method = FindMethod(typeName, methodName);
            LogAssert.Expect(UnityEngine.LogType.Error, new Regex("Error converting token to"));

            var exception = Assert.Throws<TargetInvocationException>(() => method.Invoke(null, new object[] { new JValue(true), typeof(DayOfWeek) }));

            Assert.That(exception.InnerException, Is.TypeOf<ArgumentException>());
        }

        [TestCase("MCPForUnity.Editor.Tools.Graphics.RenderPipelineOps", "ConvertPropertyValue", "\"Monday\"")]
        [TestCase("MCPForUnity.Editor.Tools.Graphics.VolumeOps", "ConvertToParameterType", "\"Monday\"")]
        [TestCase("MCPForUnity.Editor.Tools.Physics.JointOps", "ConvertValue", "\"Monday\"")]
        [TestCase("MCPForUnity.Editor.Tools.Graphics.RenderPipelineOps", "ConvertPropertyValue", "1")]
        [TestCase("MCPForUnity.Editor.Tools.Graphics.VolumeOps", "ConvertToParameterType", "1")]
        [TestCase("MCPForUnity.Editor.Tools.Physics.JointOps", "ConvertValue", "1")]
        public void DynamicPropertyConversionPreservesEnumNameAndInteger(string typeName, string methodName, string json)
        {
            var method = FindMethod(typeName, methodName);

            var converted = method.Invoke(null, new object[] { JToken.Parse(json), typeof(DayOfWeek) });

            Assert.That(converted, Is.EqualTo(DayOfWeek.Monday));
        }

        [TestCase("true")]
        [TestCase("1.0")]
        [TestCase("\"1.0\"")]
        public void ProBuilderEdgeRejectsNonIntegerToken(string json)
        {
            var method = FindMethod("MCPForUnity.Editor.Tools.ProBuilder.ManageProBuilder", "ParseEdgeVertex");

            var exception = Assert.Throws<TargetInvocationException>(() => method.Invoke(null, new object[] { JToken.Parse(json) }));

            Assert.That(exception.InnerException, Is.TypeOf<ArgumentException>());
        }

        [TestCase("2")]
        [TestCase("\"2\"")]
        public void ProBuilderEdgeAcceptsIntegerTokenAndIntegerString(string json)
        {
            var method = FindMethod("MCPForUnity.Editor.Tools.ProBuilder.ManageProBuilder", "ParseEdgeVertex");

            var vertex = method.Invoke(null, new object[] { JToken.Parse(json) });

            Assert.That(vertex, Is.EqualTo(2));
        }

        [TestCase("true")]
        [TestCase("1.0")]
        [TestCase("\"1.0\"")]
        public void ScriptableObjectArraySizeRejectsNonIntegerToken(string json)
        {
            var method = FindMethod("MCPForUnity.Editor.Tools.ManageScriptableObject", "TryReadArraySize");
            var arguments = new object[] { JToken.Parse(json), 0 };

            var accepted = method.Invoke(null, arguments);

            Assert.That(accepted, Is.False);
        }

        private static MethodInfo FindMethod(string typeName, string methodName)
        {
            return typeof(ManageShader).Assembly.GetType(typeName, true)
                .GetMethod(methodName, BindingFlags.Static | BindingFlags.NonPublic);
        }
    }
}
