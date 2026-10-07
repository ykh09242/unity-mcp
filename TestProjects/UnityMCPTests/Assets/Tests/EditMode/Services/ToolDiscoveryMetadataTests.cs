using System;
using System.Collections;
using System.Collections.Generic;
using System.Linq;
using System.Reflection;
using MCPForUnity.Editor.Services;
using MCPForUnity.Editor.Tools;
using NUnit.Framework;

namespace MCPForUnity.Editor.Tests.EditMode.Services
{
    public class ToolDiscoveryMetadataTests
    {
        // Keep fixtures undecorated so they do not become tools in the Editor catalog.
        private class CaptureScreenshotTool { }

        private class ToolSettingsTool { }

        private class BaseParameters
        {
            [ToolParameter("Property metadata", Required = false, DefaultValue = "property")]
            public string shared { get; set; }
        }

        private class FieldParameterTool
        {
            public class Parameters : BaseParameters
            {
                [ToolParameter("Required count")]
                public int count = 1;

                [ToolParameter("Optional amount", Required = false, DefaultValue = "1.5")]
                public double? amount = 1.5;

                [ToolParameter("Hidden field metadata")]
                public new string shared = "field";

                public string ignored = "ignored";

                [ToolParameter("Static field")]
                public static bool staticField = true;
            }
        }

        private class CollectionParameterTool
        {
            public class Parameters
            {
                [ToolParameter("Concrete map")]
                public Dictionary<string, object> map { get; set; }

                [ToolParameter("Map interface")]
                public IDictionary<string, object> mapInterface { get; set; }

                [ToolParameter("Read only map")]
                public IReadOnlyDictionary<string, object> readOnlyMap { get; set; }

                [ToolParameter("Non generic map")]
                public Hashtable nonGenericMap { get; set; }

                [ToolParameter("List")]
                public List<int> list { get; set; }

                [ToolParameter("Array")]
                public int[] array { get; set; }
            }
        }

        [TestCase(typeof(CaptureScreenshotTool), "capture_screenshot_tool")]
        [TestCase(typeof(ToolSettingsTool), "tool_settings_tool")]
        public void UnnamedTool_PreservesWholeClassNameToMatchCommandRegistry(Type type, string expected)
        {
            Assert.AreEqual(expected, ExtractMetadata(type, new McpForUnityToolAttribute()).Name);
        }

        [Test]
        public void ExplicitToolName_IsPreserved()
        {
            Assert.AreEqual("custom_name", ExtractMetadata(typeof(CaptureScreenshotTool), new McpForUnityToolAttribute("custom_name")).Name);
        }

        [Test]
        public void AnnotatedFields_AreDiscoveredWithNullableTypesAndDefaults()
        {
            var parameters = ExtractMetadata(typeof(FieldParameterTool), new McpForUnityToolAttribute()).Parameters;
            var count = parameters.Single(parameter => parameter.Name == "count");
            Assert.AreEqual("integer", count.Type);
            Assert.AreEqual("Required count", count.Description);
            Assert.IsTrue(count.Required);

            var amount = parameters.Single(parameter => parameter.Name == "amount");
            Assert.AreEqual("number", amount.Type);
            Assert.AreEqual("Optional amount", amount.Description);
            Assert.IsFalse(amount.Required);
            Assert.AreEqual("1.5", amount.DefaultValue);
            Assert.IsFalse(parameters.Any(parameter => parameter.Name == "ignored" || parameter.Name == "staticField"));
        }

        [Test]
        public void AnnotatedProperty_TakesPrecedenceOverHidingField()
        {
            var parameters = ExtractMetadata(typeof(FieldParameterTool), new McpForUnityToolAttribute()).Parameters;
            var shared = parameters.Single(parameter => parameter.Name == "shared");
            Assert.AreEqual("Property metadata", shared.Description);
            Assert.IsFalse(shared.Required);
            Assert.AreEqual("property", shared.DefaultValue);
        }

        [TestCase("map", "object")]
        [TestCase("mapInterface", "object")]
        [TestCase("readOnlyMap", "object")]
        [TestCase("nonGenericMap", "object")]
        [TestCase("list", "array")]
        [TestCase("array", "array")]
        public void CollectionParameters_DescribeDictionariesAsObjectsAndSequencesAsArrays(string name, string expected)
        {
            var parameters = ExtractMetadata(typeof(CollectionParameterTool), new McpForUnityToolAttribute()).Parameters;
            Assert.AreEqual(expected, parameters.Single(parameter => parameter.Name == name).Type);
        }

        private static ToolMetadata ExtractMetadata(Type type, McpForUnityToolAttribute attribute)
        {
            var method = typeof(ToolDiscoveryService).GetMethod("ExtractToolMetadata", BindingFlags.Instance | BindingFlags.NonPublic);
            Assert.IsNotNull(method);
            return (ToolMetadata)method.Invoke(new ToolDiscoveryService(), new object[] { type, attribute });
        }
    }
}
