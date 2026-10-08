using System;
using System.Collections.Generic;
using System.Reflection;
using MCPForUnity.Editor.Tools.Profiler;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEngine;

namespace MCPForUnityTests.EditMode.Tools
{
    public class ProfilerQueryIntegrityTests
    {
        [TestCase("object_path", "false")]
        [TestCase("object_path", "0")]
        [TestCase("object_path", "{}")]
        [TestCase("object_path", "[]")]
        [TestCase("objectPath", "false")]
        [TestCase("objectPath", "0")]
        [TestCase("objectPath", "{}")]
        [TestCase("objectPath", "[]")]
        public void NonStringObjectPath_IsRejectedEvenWhenItsTextNamesAnObject(string key, string json)
        {
            var owned = new GameObject(
                json == "false" ? "False"
                : json == "0" ? "0"
                : "__ProfilerPath_" + Guid.NewGuid().ToString("N")
            );
            try
            {
                var response = JObject.FromObject(
                    ManageProfiler.HandleCommand(new JObject { ["action"] = "get_object_memory", [key] = JToken.Parse(json) }).GetAwaiter().GetResult()
                );

                Assert.IsFalse(response.Value<bool>("success"), response.ToString());
                StringAssert.Contains("'object_path' must be a string or null", response.Value<string>("error"));
            }
            finally
            {
                UnityEngine.Object.DestroyImmediate(owned);
            }
        }

        [TestCase(null)]
        [TestCase("null")]
        [TestCase("\"\"")]
        public void MissingObjectPath_KeepsTheRequiredParameterError(string json)
        {
            var request = new JObject { ["action"] = "get_object_memory" };
            if (json != null)
                request["object_path"] = JToken.Parse(json);
            var response = JObject.FromObject(ManageProfiler.HandleCommand(request).GetAwaiter().GetResult());

            Assert.IsFalse(response.Value<bool>("success"), response.ToString());
            StringAssert.Contains("'object_path' parameter is required", response.Value<string>("error"));
        }

        [TestCase("object_path", "False")]
        [TestCase("object_path", "0")]
        [TestCase("objectPath", "__ProfilerStringPath")]
        public void StringObjectPath_PreservesLiteralNamesAndCamelCaseAlias(string key, string name)
        {
            var owned = new GameObject(name);
            try
            {
                var response = JObject.FromObject(
                    ManageProfiler.HandleCommand(new JObject { ["action"] = "GET_OBJECT_MEMORY", [key] = name }).GetAwaiter().GetResult()
                );

                Assert.IsTrue(response.Value<bool>("success"), response.ToString());
                Assert.AreEqual(name, response["data"].Value<string>("object_name"));
                Assert.AreEqual("scene_hierarchy", response["data"].Value<string>("source"));
            }
            finally
            {
                UnityEngine.Object.DestroyImmediate(owned);
            }
        }

        private static Type EventDataType()
        {
            var type =
                Type.GetType("UnityEditorInternal.FrameDebuggerInternal.FrameDebuggerEventData, UnityEditor")
                ?? Type.GetType("UnityEditorInternal.FrameDebuggerEventData, UnityEditor");
            if (type == null)
                Assert.Ignore("This Editor does not expose the supported FrameDebuggerEventData layout.");
            return type;
        }

        private static Dictionary<string, object> Read(object data, string member, string alias, string outputKey = null)
        {
            var method = typeof(FrameDebuggerOps).GetMethod("TryAddField", BindingFlags.Static | BindingFlags.NonPublic);
            Assert.IsNotNull(method);
            var result = new Dictionary<string, object>();
            method.Invoke(null, new object[] { data.GetType(), data, member, result, outputKey, alias });
            return result;
        }

        [TestCase("shaderName", "m_OriginalShaderName", "FixtureShader")]
        [TestCase("passName", "m_PassName", "FixturePass")]
        [TestCase("rtName", "m_RenderTargetName", "FixtureTarget")]
        [TestCase("rtWidth", "m_RenderTargetWidth", 640)]
        [TestCase("rtHeight", "m_RenderTargetHeight", 480)]
        [TestCase("vertexCount", "m_VertexCount", 17)]
        [TestCase("indexCount", "m_IndexCount", 18)]
        [TestCase("instanceCount", "m_InstanceCount", 0)]
        public void RealManagedEventDataUsesStableOutputKey(string legacy, string modern, object expected)
        {
            var type = EventDataType();
            var field =
                type.GetField(legacy, BindingFlags.Public | BindingFlags.Instance) ?? type.GetField(modern, BindingFlags.Public | BindingFlags.Instance);
            if (field == null)
                Assert.Ignore("The installed Editor uses a different detail member layout.");
            var data = Activator.CreateInstance(type);
            field.SetValue(data, expected);

            var result = Read(data, legacy, modern);

            Assert.AreEqual(1, result.Count);
            Assert.AreEqual(expected, result[legacy]);
        }

        [Test]
        public void RealManagedDescriptorEnumUsesEventTypeKey()
        {
            var type =
                Type.GetType("UnityEditorInternal.FrameDebuggerInternal.FrameDebuggerEvent, UnityEditor")
                ?? Type.GetType("UnityEditorInternal.FrameDebuggerEvent, UnityEditor");
            if (type == null)
                Assert.Ignore("This Editor does not expose the supported descriptor layout.");
            var field =
                type.GetField("type", BindingFlags.Public | BindingFlags.Instance) ?? type.GetField("m_Type", BindingFlags.Public | BindingFlags.Instance);
            Assert.IsNotNull(field);
            Assert.IsTrue(field.FieldType.IsEnum);
            var expected = Enum.GetValues(field.FieldType).GetValue(0);
            var descriptor = Activator.CreateInstance(type);
            field.SetValue(descriptor, expected);

            var result = Read(descriptor, "type", "m_Type", "event_type");

            Assert.AreEqual(expected.ToString(), result["event_type"]);
            Assert.IsFalse(result.ContainsKey("type"));
            Assert.IsFalse(result.ContainsKey("m_Type"));
        }

        private class PrimaryAndAlias
        {
            public int vertexCount = 0;
            public int m_VertexCount = 9;
        }

        [Test]
        public void NonnullPrimaryZeroWinsOverAlias()
        {
            var result = Read(new PrimaryAndAlias(), "vertexCount", "m_VertexCount");
            Assert.AreEqual(0, result["vertexCount"]);
        }

        private class ThrowingPrimary
        {
            public string shaderName => throw new InvalidOperationException("Owned managed getter failure");
            public string m_OriginalShaderName = "Fallback";
        }

        [Test]
        public void FailedPrimaryGetterCanUseAvailableAlias()
        {
            var result = Read(new ThrowingPrimary(), "shaderName", "m_OriginalShaderName");
            Assert.AreEqual("Fallback", result["shaderName"]);
        }
    }
}
