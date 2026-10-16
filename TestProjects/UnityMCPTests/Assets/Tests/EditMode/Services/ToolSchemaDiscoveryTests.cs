using System;
using System.Collections.Generic;
using System.Linq;
using System.Reflection;
using System.Runtime.Serialization;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Editor.Resources.Editor;
using MCPForUnity.Editor.Services;
using MCPForUnity.Editor.Services.Transport.Transports;
using MCPForUnity.Editor.Tools;
using Newtonsoft.Json;
using Newtonsoft.Json.Converters;
using Newtonsoft.Json.Linq;
using NUnit.Framework;

namespace MCPForUnity.Editor.Tests.EditMode.Services
{
    [Parallelizable(ParallelScope.None)]
    public class ToolSchemaDiscoveryTests
    {
        // Undecorated fixtures: never register tools or initialize Editor preferences.
        private enum NumericMode
        {
            First = 0,
            Second = 2,
        }

        [JsonConverter(typeof(StringEnumConverter))]
        [DataContract]
        private enum StringMode
        {
            [EnumMember(Value = "first-mode")]
            First,
            Second,
        }

        [Flags]
        private enum UnsupportedFlags
        {
            First = 1,
            Second = 2,
        }

        private class Node
        {
            public int count = 0;

            [ToolParameter("Optional recursive child", Required = false)]
            public Node child = null;
        }

        private class NestedTool
        {
            public static object HandleCommand(JObject arguments) =>
                new SuccessResponse("Owned nested result.", new JObject { ["total"] = ((JArray)arguments["nodes"]).Sum(node => (int)node["count"]) });

            public class Parameters
            {
                [ToolParameter("Nested nodes")]
                public List<Node> nodes = new List<Node>();

                [ToolParameter("Typed map")]
                public Dictionary<string, int> weights = new Dictionary<string, int>();

                [ToolParameter("Numeric enum")]
                public NumericMode mode = NumericMode.First;

                [ToolParameter("String enum")]
                public StringMode spelling = StringMode.First;

                [ToolParameter("Required nullable")]
                public int? nullable = null;
            }
        }

        private class ResultDto
        {
            public int total = 0;
        }

        private class GuardedDto
        {
            public static int Constructors = 0;
            public static int Getters = 0;

            public GuardedDto()
            {
                Constructors++;
                throw new InvalidOperationException("DTO construction forbidden");
            }

            public string danger
            {
                get
                {
                    Getters++;
                    throw new InvalidOperationException("DTO getter forbidden");
                }
                set { }
            }
        }

        private class GuardedTool
        {
            public class Parameters
            {
                [ToolParameter("Owned DTO")]
                public GuardedDto value = null;

                [ToolParameter("Read-only metadata property")]
                public string readOnly => throw new InvalidOperationException("Top-level getter forbidden");
            }
        }

        private class BoundedTool
        {
            public class Parameters
            {
                [ToolParameter("Label", MinLength = 1, MaxLength = 8)]
                public string label = "";

                [ToolParameter("Rows", MinItems = 1, MaxItems = 2)]
                public List<int> rows = new List<int>();

                [ToolParameter("Nullable reference", Required = false, AllowNull = true)]
                public Node optional = null;
            }
        }

        private class InvalidDefaultTool
        {
            public class Parameters
            {
                [ToolParameter("Invalid scalar default", Required = false, DefaultValue = "3", Minimum = 0, Maximum = 2)]
                public int value = 0;
            }
        }

        private class WideIntegerTool
        {
            public class Parameters
            {
                [ToolParameter("Signed endpoint", Minimum = -double.MaxValue, Maximum = double.MaxValue)]
                public long signed = 0;

                [ToolParameter("Unsigned endpoint", Maximum = double.MaxValue)]
                public ulong unsigned = 0;
            }
        }

        private class NullableDefaultTool
        {
            public class Parameters
            {
                [ToolParameter("Nullable scalar", Required = false, DefaultValue = "null")]
                public int? value = null;

                [ToolParameter("Nullable DTO", Required = false, AllowNull = true, DefaultValue = "null")]
                public Node node = null;

                [ToolParameter("Unconstrained scalar default", Required = false, DefaultValue = "7")]
                public object flexible = null;

                [ToolParameter("Unconstrained array default", Required = false, DefaultValue = "[1,2]")]
                public object values = null;
            }
        }

        private class ExtensionDataDto
        {
            [JsonExtensionData]
            public Dictionary<string, object> Extra { get; set; }
        }

        [DataContract]
        private class OptInDto
        {
            public int Value { get; set; }
        }

        private class ReferenceDto
        {
            [JsonProperty(IsReference = true)]
            public Node Value { get; set; }
        }

        private class TypeNameDto
        {
            [JsonProperty(TypeNameHandling = TypeNameHandling.All)]
            public Node Value { get; set; }
        }

        private class ConditionalDto
        {
            public int Value { get; set; }

            public bool ShouldSerializeValue() => throw new InvalidOperationException("Conditional serializer execution forbidden");
        }

        private class SpecifiedDto
        {
            public int Value { get; set; }
            public bool ValueSpecified { get; set; }
        }

        [JsonObject(MemberSerialization.OptIn)]
        private class CustomDictionary : Dictionary<string, int> { }

        [JsonArray]
        private class CustomArrayDto
        {
            public int Value { get; set; }
        }

        [DataContract]
        private class ContractBase
        {
            public int FromBase { get; set; }
        }

        private class ContractDerived : ContractBase
        {
            public int Own { get; set; }
        }

        private class ExtensionBase
        {
#pragma warning disable CS0414 // This private extension-data field is inspected through reflection.
            [JsonExtensionData]
            private Dictionary<string, object> _extra = null;
#pragma warning restore CS0414
        }

        private class ExtensionDerived : ExtensionBase
        {
            public int Own { get; set; }
        }

        private class PlainBase
        {
            public int FromBase = 0;
        }

        private class PlainDerived : PlainBase
        {
            public string Name { get; set; }
        }

        private sealed class OwnedDiscovery : IToolDiscoveryService
        {
            private readonly List<ToolMetadata> _tools;

            public OwnedDiscovery(params ToolMetadata[] tools)
            {
                _tools = tools.ToList();
            }

            public List<ToolMetadata> DiscoverAllTools() => _tools;

            public ToolMetadata GetToolMetadata(string name) => _tools.FirstOrDefault(tool => tool.Name == name);

            public List<ToolMetadata> GetEnabledTools() => _tools;

            public bool IsToolEnabled(string name) => true;

            public void SetToolEnabled(string name, bool enabled) => throw new InvalidOperationException("Preferences forbidden");

            public void InvalidateCache() { }
        }

        private class FlagsTool
        {
            public class Parameters
            {
                [ToolParameter("Unsupported flags")]
                public UnsupportedFlags flags = UnsupportedFlags.First;
            }
        }

        [Test]
        public void RichDiscovery_PreservesNestedCollectionsEnumsAndRequiredNullable()
        {
            var metadata = Extract(typeof(NestedTool), RichAttribute());
            var schema = Schema(metadata, "InputSchema");
            Assert.AreEqual("object", (string)schema["type"]);
            Assert.AreEqual("array", (string)schema["properties"]["nodes"]["type"]);
            Assert.IsNotEmpty((string)schema["properties"]["nodes"]["items"]["$ref"]);
            Assert.AreEqual("integer", (string)schema["properties"]["weights"]["additionalProperties"]["type"]);
            CollectionAssert.AreEquivalent(new[] { 0, 2 }, schema["properties"]["mode"]["enum"].ToObject<int[]>());
            CollectionAssert.AreEquivalent(new[] { "first-mode", "Second" }, schema["properties"]["spelling"]["enum"].ToObject<string[]>());
            CollectionAssert.Contains(schema["required"].ToObject<string[]>(), "nullable");
            CollectionAssert.AreEquivalent(new[] { "integer", "null" }, schema["properties"]["nullable"]["type"].ToObject<string[]>());
            var legacyParameters = Extract(typeof(NestedTool), new McpForUnityToolAttribute()).Parameters;
            CollectionAssert.AreEqual(
                legacyParameters.Select(parameter => parameter.Name).ToArray(),
                metadata.Parameters.Select(parameter => parameter.Name).ToArray()
            );
            foreach (var parameter in metadata.Parameters)
            {
                var legacy = legacyParameters.Single(value => value.Name == parameter.Name);
                Assert.AreEqual(legacy.Required, parameter.Required);
                Assert.AreEqual(legacy.Description, parameter.Description);
                Assert.AreEqual(legacy.DefaultValue, parameter.DefaultValue);
            }
        }

        [Test]
        public void RichDiscovery_RecursiveTypeUsesOneStableConcreteDefinition()
        {
            var first = Schema(Extract(typeof(NestedTool), RichAttribute()), "InputSchema");
            var second = Schema(Extract(typeof(NestedTool), RichAttribute()), "InputSchema");
            Assert.IsTrue(JToken.DeepEquals(first, second));
            var reference = (string)first["properties"]["nodes"]["items"]["$ref"];
            var definition = first["$defs"][reference.Substring("#/$defs/".Length)];
            Assert.AreEqual("object", (string)definition["type"]);
            Assert.AreEqual(reference, (string)definition["properties"]["child"]["$ref"]);
        }

        [Test]
        public void RichDiscovery_OutputDescribesDataRatherThanHandlerObject()
        {
            var attribute = RichAttribute();
            Property(attribute, "OutputDataType").SetValue(attribute, typeof(List<Node>));
            var output = Schema(Extract(typeof(NestedTool), attribute), "OutputDataSchema");
            Assert.AreEqual("array", (string)output["type"]);
            Assert.IsNotEmpty((string)output["items"]["$ref"]);
        }

        [Test]
        public void RichDiscovery_UnsupportedContractRetainsFailureRatherThanOmittingTool()
        {
            var metadata = Extract(typeof(FlagsTool), RichAttribute());
            Assert.IsNotNull(metadata);
            Assert.IsNotEmpty((string)Property(metadata, "SchemaError").GetValue(metadata));
        }

        [Test]
        public void RichDiscovery_DoesNotConstructDtosOrInvokeGetters()
        {
            GuardedDto.Constructors = GuardedDto.Getters = 0;
            var metadata = Extract(typeof(GuardedTool), RichAttribute());
            Assert.IsNull(metadata.SchemaError);
            Assert.IsNotNull(metadata.InputSchema["properties"]["readOnly"]);
            Assert.AreEqual(0, GuardedDto.Constructors);
            Assert.AreEqual(0, GuardedDto.Getters);
        }

        [Test]
        public void RichDiscovery_PreservesCardinalityAndExplicitNullableReference()
        {
            var metadata = Extract(typeof(BoundedTool), RichAttribute());
            Assert.IsNull(metadata.SchemaError);
            Assert.AreEqual(1, (int)metadata.InputSchema["properties"]["label"]["minLength"]);
            Assert.AreEqual(8, (int)metadata.InputSchema["properties"]["label"]["maxLength"]);
            Assert.AreEqual(1, (int)metadata.InputSchema["properties"]["rows"]["minItems"]);
            Assert.AreEqual(2, (int)metadata.InputSchema["properties"]["rows"]["maxItems"]);
            Assert.IsTrue(((JArray)metadata.InputSchema["properties"]["optional"]["anyOf"]).Any(branch => (string)branch["type"] == "null"));
        }

        [Test]
        public void RichDiscovery_InvalidPrimitiveDefaultRetainsFailure()
        {
            Assert.IsNotEmpty(Extract(typeof(InvalidDefaultTool), RichAttribute()).SchemaError);
        }

        [Test]
        public void RichDiscovery_RedundantDoubleBoundsDoNotWidenExactIntegerEndpoints()
        {
            var metadata = Extract(typeof(WideIntegerTool), RichAttribute());
            Assert.IsNull(metadata.SchemaError);
            var properties = metadata.InputSchema["properties"];
            Assert.AreEqual(JTokenType.Integer, properties["signed"]["minimum"].Type);
            Assert.AreEqual(long.MinValue, properties["signed"]["minimum"].Value<long>());
            Assert.AreEqual(JTokenType.Integer, properties["signed"]["maximum"].Type);
            Assert.AreEqual(long.MaxValue, properties["signed"]["maximum"].Value<long>());
            Assert.AreEqual(JTokenType.Integer, properties["unsigned"]["maximum"].Type);
            Assert.AreEqual(ulong.MaxValue, properties["unsigned"]["maximum"].Value<ulong>());
        }

        [Test]
        public void RichWire_NullDefaultsKeepRichMeaningAndValidLegacyProjection()
        {
            var metadata = Extract(typeof(NullableDefaultTool), RichAttribute());
            Assert.IsNull(metadata.SchemaError);
            foreach (var parameter in metadata.Parameters)
            {
                Assert.IsNull(parameter.DefaultValue);
            }
            foreach (string name in new[] { "value", "node" })
                Assert.AreEqual(JTokenType.Null, metadata.InputSchema["properties"][name]["default"].Type);
            Assert.AreEqual(7, (int)metadata.InputSchema["properties"]["flexible"]["default"]);
            CollectionAssert.AreEqual(new[] { 1, 2 }, metadata.InputSchema["properties"]["values"]["default"].ToObject<int[]>());
            Assert.AreEqual("object", metadata.Parameters.Single(parameter => parameter.Name == "flexible").Type);
            Assert.AreEqual("object", metadata.Parameters.Single(parameter => parameter.Name == "values").Type);
            var descriptor = Outbound(metadata)["tools"][0];
            var state = States(metadata)["data"]["tools"][0];
            Assert.IsTrue(JToken.DeepEquals(descriptor["parameters"], state["parameters"]));
            Assert.IsTrue(JToken.DeepEquals(descriptor["input_schema"], state["input_schema"]));
            var arguments = JObject.Parse("{\"value\":null,\"node\":null,\"flexible\":7,\"values\":[1,2]}");
            TestContext.WriteLine(
                "DEV08_NULL_DEFAULT_REPLAY "
                    + new JObject
                    {
                        ["descriptor"] = descriptor,
                        ["arguments"] = arguments,
                        ["response"] = JObject.FromObject(new SuccessResponse("Owned nullable defaults.", arguments)),
                    }.ToString(Formatting.None)
            );
        }

        [TestCase(typeof(ExtensionDataDto))]
        [TestCase(typeof(OptInDto))]
        [TestCase(typeof(ReferenceDto))]
        [TestCase(typeof(TypeNameDto))]
        [TestCase(typeof(ConditionalDto))]
        [TestCase(typeof(SpecifiedDto))]
        [TestCase(typeof(CustomDictionary))]
        [TestCase(typeof(CustomArrayDto))]
        [TestCase(typeof(ContractDerived))]
        [TestCase(typeof(ExtensionDerived))]
        public void RichDiscovery_UnsupportedSerializationRetainsFailure(Type outputType)
        {
            var attribute = RichAttribute();
            attribute.OutputDataType = outputType;
            var metadata = Extract(typeof(NestedTool), attribute);
            Assert.IsNotEmpty(metadata.SchemaError);
            Assert.IsNull(metadata.InputSchema);
            Assert.IsNull(metadata.OutputDataSchema);
        }

        [Test]
        public void RichDiscovery_PlainInheritedDtoKeepsEachPublicMemberOnce()
        {
            var attribute = RichAttribute();
            attribute.OutputDataType = typeof(PlainDerived);
            var metadata = Extract(typeof(NestedTool), attribute);
            Assert.IsNull(metadata.SchemaError);
            var output = metadata.OutputDataSchema;
            var definition = output["$defs"][(string)output["$ref"].ToString().Substring("#/$defs/".Length)];
            var properties = (JObject)definition["properties"];
            CollectionAssert.AreEquivalent(new[] { "FromBase", "Name" }, properties.Properties().Select(property => property.Name).ToArray());
            Assert.AreEqual(2, properties.Count);
        }

        [Test]
        public void RichDiscovery_TypeDepthIsBoundedWithoutConstructingValues()
        {
            var attribute = RichAttribute();
            Type type = typeof(int);
            for (int index = 0; index < 40; index++)
                type = typeof(List<>).MakeGenericType(type);
            attribute.OutputDataType = type;
            Assert.IsNotEmpty(Extract(typeof(NestedTool), attribute).SchemaError);
        }

        [Test]
        public void RichWire_ToolStatesAndActualOutboundBuilderPreserveIdenticalSchema()
        {
            var attribute = RichAttribute();
            attribute.OutputDataType = typeof(ResultDto);
            var metadata = Extract(typeof(NestedTool), attribute);
            Assert.IsNull(metadata.SchemaError);
            var outbound = Outbound(metadata);
            var states = States(metadata);
            var state = states["data"]["tools"][0];
            foreach (string field in new[] { "schema_version", "input_schema", "output_data_schema", "parameters" })
                Assert.IsTrue(JToken.DeepEquals(outbound["tools"][0][field], state[field]), field);
        }

        [Test]
        public void RichWire_FailedMemberRefusesWholeCatalogInBothSerializers()
        {
            var valid = Extract(typeof(NestedTool), RichAttribute());
            var invalid = Extract(typeof(FlagsTool), RichAttribute());
            var failure = Assert.Throws<TargetInvocationException>(() => Outbound(valid, invalid));
            Assert.IsInstanceOf<InvalidOperationException>(failure.InnerException);
            var states = States(valid, invalid);
            Assert.IsFalse((bool)states["success"]);
            Assert.IsTrue(states["data"] == null || states["data"].Type == JTokenType.Null);
        }

        [Test]
        public void RichWire_ExportsExactOwnedHandlerEnvelopeForSdkReplay()
        {
            var attribute = RichAttribute();
            attribute.OutputDataType = typeof(ResultDto);
            var metadata = Extract(typeof(NestedTool), attribute);
            Assert.IsNull(metadata.SchemaError);
            var arguments = JObject.Parse(
                "{\"nodes\":[{\"count\":1,\"child\":{\"count\":2}}],\"weights\":{\"a\":3},\"mode\":2,\"spelling\":\"first-mode\",\"nullable\":null}"
            );
            var response = JObject.FromObject(NestedTool.HandleCommand(arguments));
            Assert.IsTrue((bool)response["success"]);
            Assert.AreEqual(1, (int)response["data"]["total"]);
            TestContext.WriteLine(
                "DEV08_SCHEMA_REPLAY "
                    + new JObject
                    {
                        ["descriptor"] = Outbound(metadata)["tools"][0],
                        ["arguments"] = arguments,
                        ["response"] = response,
                    }.ToString(Formatting.None)
            );
        }

        private static JObject Outbound(params ToolMetadata[] tools)
        {
            var method = typeof(WebSocketTransportClient).GetMethod("BuildToolsRegistration", BindingFlags.NonPublic | BindingFlags.Static);
            Assert.IsNotNull(method);
            return (JObject)method.Invoke(null, new object[] { tools.ToList() });
        }

        private static JObject States(params ToolMetadata[] tools)
        {
            var field = typeof(MCPServiceLocator).GetField("_toolDiscoveryService", BindingFlags.NonPublic | BindingFlags.Static);
            var previous = field.GetValue(null);
            var settings = typeof(ToolStates).GetField("_settingsStateOverride", BindingFlags.NonPublic | BindingFlags.Static);
            var previousSettings = settings?.GetValue(null);
            try
            {
                // Concurrent DEV21 adds memory-only desired state; keep this fixture's state owned.
                if (settings != null)
                    settings.SetValue(null, Activator.CreateInstance(settings.FieldType));
                MCPServiceLocator.Register<IToolDiscoveryService>(new OwnedDiscovery(tools));
                return JObject.FromObject(ToolStates.HandleCommand(new JObject()));
            }
            finally
            {
                field.SetValue(null, previous);
                settings?.SetValue(null, previousSettings);
            }
        }

        private static McpForUnityToolAttribute RichAttribute()
        {
            var attribute = new McpForUnityToolAttribute("owned_schema_fixture");
            Property(attribute, "SchemaVersion").SetValue(attribute, 1);
            return attribute;
        }

        private static PropertyInfo Property(object value, string name)
        {
            var property = value.GetType().GetProperty(name);
            Assert.IsNotNull(property, "Rich schema metadata API is absent: " + name);
            return property;
        }

        private static JObject Schema(ToolMetadata metadata, string name) => (JObject)Property(metadata, name).GetValue(metadata);

        private static ToolMetadata Extract(Type type, McpForUnityToolAttribute attribute)
        {
            var method = typeof(ToolDiscoveryService).GetMethod("ExtractToolMetadata", BindingFlags.NonPublic | BindingFlags.Instance);
            return (ToolMetadata)method.Invoke(new ToolDiscoveryService(), new object[] { type, attribute });
        }
    }
}
