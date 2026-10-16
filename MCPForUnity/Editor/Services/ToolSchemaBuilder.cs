using System;
using System.Collections.Generic;
using System.Globalization;
using System.Linq;
using System.Reflection;
using System.Runtime.Serialization;
using System.Text;
using MCPForUnity.Editor.Tools;
using Newtonsoft.Json;
using Newtonsoft.Json.Converters;
using Newtonsoft.Json.Linq;

namespace MCPForUnity.Editor.Services
{
    /// <summary>Bounded CLR metadata projection. Exact nested-default validation belongs to the updated server.</summary>
    internal static class ToolSchemaBuilder
    {
        private const string Dialect = "https://json-schema.org/draft/2020-12/schema";
        private const int MaxBytes = 65536;

        public static void Build(Type toolType, McpForUnityToolAttribute attribute, ToolMetadata metadata)
        {
            if (
                attribute.SchemaVersion != 1
                || attribute.OutputDataType != null && !attribute.StructuredOutput
                || attribute.OutputAllowNull && attribute.OutputDataType == null
            )
                throw new InvalidOperationException("Unsupported rich schema configuration.");
            var input = new Context();
            var properties = new JObject();
            var required = new JArray();
            var parameters = toolType.GetNestedType("Parameters");
            var members = parameters == null ? new MemberInfo[0] : Members(parameters, true).ToArray();
            metadata.Parameters = new List<ParameterMetadata>();
            if (parameters != null)
            {
                foreach (var member in members.OrderBy(member => member.Name, StringComparer.Ordinal))
                {
                    var parameter = member.GetCustomAttribute<ToolParameterAttribute>();
                    properties[member.Name] = input.Member(member, parameter, 0);
                    if (parameter.Required)
                        required.Add(member.Name);
                }
            }
            // Project compatibility fields from the same bounded member set, preserving
            // property-before-field declaration order without a preliminary reflection pass.
            foreach (var member in members)
            {
                var parameter = member.GetCustomAttribute<ToolParameterAttribute>();
                metadata.Parameters.Add(
                    new ParameterMetadata
                    {
                        Name = member.Name,
                        Description = parameter.Description,
                        Type = "object",
                        Required = parameter.Required,
                        DefaultValue = parameter.DefaultValue,
                    }
                );
            }
            var root = new JObject
            {
                ["$schema"] = Dialect,
                ["type"] = "object",
                ["properties"] = properties,
                ["additionalProperties"] = false,
            };
            if (required.Count > 0)
                root["required"] = required;
            input.Finish(root);
            metadata.InputSchema = root;
            foreach (var parameter in metadata.Parameters)
            {
                var schema = (JObject)properties[parameter.Name];
                if (schema == null)
                    continue;
                var kind = schema["type"] is JArray kinds ? kinds.First(value => (string)value != "null") : schema["type"];
                if (kind != null)
                    parameter.Type = (string)kind;
                if (schema.TryGetValue("default", out var value))
                    parameter.DefaultValue =
                        value.Type == JTokenType.Null || parameter.Type == "object" && value.Type != JTokenType.Object ? null
                        : value.Type == JTokenType.String ? (string)value
                        : value.ToString(Formatting.None);
            }
            if (attribute.OutputDataType != null)
            {
                var output = new Context();
                var schema = output.Type(attribute.OutputDataType, attribute.OutputAllowNull, 0);
                schema["$schema"] = Dialect;
                output.Finish(schema);
                metadata.OutputDataSchema = schema;
            }
            CheckBudget(metadata.InputSchema, metadata.OutputDataSchema);
        }

        public static void AppendSchema(JObject descriptor, ToolMetadata metadata)
        {
            if (!string.IsNullOrEmpty(metadata.SchemaError))
                throw new InvalidOperationException("Custom rich schema discovery failed; catalog was not published.");
            if (metadata.SchemaVersion == 0)
                return;
            if (metadata.SchemaVersion != 1 || metadata.InputSchema == null)
                throw new InvalidOperationException("Invalid custom rich schema metadata.");
            CheckBudget(metadata.InputSchema, metadata.OutputDataSchema);
            descriptor["schema_version"] = metadata.SchemaVersion;
            descriptor["input_schema"] = metadata.InputSchema.DeepClone();
            if (metadata.OutputDataSchema != null)
                descriptor["output_data_schema"] = metadata.OutputDataSchema.DeepClone();
        }

        private static IEnumerable<MemberInfo> Members(Type type, bool parameters)
        {
            var flags = BindingFlags.Public | BindingFlags.Instance;
            if (!parameters)
                flags |= BindingFlags.NonPublic;
            var properties = type.GetProperties(flags);
            var fields = type.GetFields(flags);
            if (properties.Length + fields.Length > 4096)
                throw new InvalidOperationException("Custom reflection member budget exceeded.");
            var selected = new Dictionary<string, MemberInfo>(StringComparer.Ordinal);
            var declarationOrder = new List<string>();
            foreach (var property in properties)
            {
                if (!parameters && property.IsDefined(typeof(JsonExtensionDataAttribute), true))
                    throw new InvalidOperationException("Custom extension-data contracts are unsupported.");
                if (
                    !parameters
                    && (
                        property.GetIndexParameters().Length != 0
                        || property.GetGetMethod() == null
                        || property.GetSetMethod() == null
                        || property.IsDefined(typeof(JsonIgnoreAttribute), true)
                    )
                )
                    continue;
                if (!parameters || property.IsDefined(typeof(ToolParameterAttribute), true))
                {
                    if (!selected.ContainsKey(property.Name))
                        declarationOrder.Add(property.Name);
                    selected[property.Name] = property;
                }
            }
            foreach (var field in fields)
            {
                if (!parameters && field.IsDefined(typeof(JsonExtensionDataAttribute), true))
                    throw new InvalidOperationException("Custom extension-data contracts are unsupported.");
                if (
                    (!parameters && (!field.IsPublic || field.IsNotSerialized || field.IsDefined(typeof(JsonIgnoreAttribute), true)))
                    || selected.ContainsKey(field.Name)
                )
                    continue;
                if (!parameters || field.IsDefined(typeof(ToolParameterAttribute), true))
                {
                    declarationOrder.Add(field.Name);
                    selected.Add(field.Name, field);
                }
            }
            if (selected.Count > (parameters ? 128 : 64))
                throw new InvalidOperationException("Custom property budget exceeded.");
            return parameters ? declarationOrder.Select(name => selected[name]) : selected.Values.OrderBy(member => member.Name, StringComparer.Ordinal);
        }

        private static void CheckBudget(params JObject[] schemas)
        {
            if (schemas.Where(schema => schema != null).Sum(schema => (schema["$defs"] as JObject)?.Count ?? 0) > 128)
                throw new InvalidOperationException("Custom combined definition budget exceeded.");
            var work = new Stack<KeyValuePair<JToken, int>>();
            foreach (var schema in schemas)
                if (schema != null)
                    work.Push(new KeyValuePair<JToken, int>(schema, 0));
            int nodes = 0,
                characters = 0;
            while (work.Count > 0)
            {
                var current = work.Pop();
                if (++nodes > 4096 || current.Value > 32)
                    throw new InvalidOperationException("Custom schema graph budget exceeded.");
                if (current.Key is JValue value && value.Type == JTokenType.String)
                {
                    ScalarLength((string)value);
                    characters += ((string)value).Length;
                }
                if (characters > MaxBytes)
                    throw new InvalidOperationException("Custom schema byte budget exceeded.");
                if (current.Key is JObject dictionary)
                {
                    foreach (var property in dictionary.Properties())
                    {
                        if (property.Name.Length > 256)
                            throw new InvalidOperationException("Custom schema identifier budget exceeded.");
                        ScalarLength(property.Name);
                        characters += property.Name.Length;
                        work.Push(new KeyValuePair<JToken, int>(property.Value, current.Value + 1));
                    }
                }
                else if (current.Key is JArray array)
                {
                    foreach (var child in array)
                        work.Push(new KeyValuePair<JToken, int>(child, current.Value + 1));
                }
            }
            int bytes = 0;
            foreach (var schema in schemas)
                if (schema != null)
                    bytes += Encoding.UTF8.GetByteCount(schema.ToString(Formatting.None));
            if (bytes > MaxBytes)
                throw new InvalidOperationException("Custom schema byte budget exceeded.");
        }

        private static bool Integer(Type type, out JToken minimum, out JToken maximum)
        {
            minimum = maximum = null;
            if (type == typeof(byte))
            {
                minimum = byte.MinValue;
                maximum = byte.MaxValue;
            }
            else if (type == typeof(sbyte))
            {
                minimum = sbyte.MinValue;
                maximum = sbyte.MaxValue;
            }
            else if (type == typeof(short))
            {
                minimum = short.MinValue;
                maximum = short.MaxValue;
            }
            else if (type == typeof(ushort))
            {
                minimum = ushort.MinValue;
                maximum = ushort.MaxValue;
            }
            else if (type == typeof(int))
            {
                minimum = int.MinValue;
                maximum = int.MaxValue;
            }
            else if (type == typeof(uint))
            {
                minimum = uint.MinValue;
                maximum = uint.MaxValue;
            }
            else if (type == typeof(long))
            {
                minimum = long.MinValue;
                maximum = long.MaxValue;
            }
            else if (type == typeof(ulong))
            {
                minimum = ulong.MinValue;
                maximum = ulong.MaxValue;
            }
            return minimum != null;
        }

        private static bool Dictionary(Type type, out Type valueType)
        {
            valueType = null;
            var candidate = type.GetInterfaces()
                .Concat(new[] { type })
                .FirstOrDefault(item =>
                    item.IsGenericType
                    && (item.GetGenericTypeDefinition() == typeof(IDictionary<,>) || item.GetGenericTypeDefinition() == typeof(IReadOnlyDictionary<,>))
                );
            if (candidate == null)
                return false;
            var arguments = candidate.GetGenericArguments();
            if (arguments[0] != typeof(string))
                throw new InvalidOperationException("Custom dictionaries require string keys.");
            valueType = arguments[1];
            return true;
        }

        private static JObject Enum(Type type, bool strings)
        {
            if (type.IsDefined(typeof(FlagsAttribute), false))
                throw new InvalidOperationException("Flags enums are not supported by rich schemas.");
            var values = new JArray();
            foreach (var value in System.Enum.GetValues(type))
            {
                JToken encoded;
                if (strings)
                {
                    string name = System.Enum.GetName(type, value);
                    string spelling = type.GetField(name).GetCustomAttribute<EnumMemberAttribute>()?.Value ?? name;
                    if (spelling.Length > 256)
                        throw new InvalidOperationException("Custom enum text budget exceeded.");
                    encoded = spelling;
                }
                else
                    encoded =
                        System.Enum.GetUnderlyingType(type) == typeof(ulong)
                            ? new JValue(Convert.ToUInt64(value, CultureInfo.InvariantCulture))
                            : new JValue(Convert.ToInt64(value, CultureInfo.InvariantCulture));
                if (!values.Any(existing => JToken.DeepEquals(existing, encoded)))
                    values.Add(encoded);
                if (values.Count > 256)
                    throw new InvalidOperationException("Custom enum budget exceeded.");
            }
            return new JObject { ["type"] = strings ? "string" : "integer", ["enum"] = values };
        }

        private static void Constraints(JObject schema, Type type, ToolParameterAttribute parameter)
        {
            type = Nullable.GetUnderlyingType(type) ?? type;
            bool numeric = Integer(type, out _, out _) || type == typeof(float) || type == typeof(double) || type == typeof(decimal);
            if (!double.IsNaN(parameter.Minimum) || !double.IsNaN(parameter.Maximum))
            {
                if (!numeric || double.IsInfinity(parameter.Minimum) || double.IsInfinity(parameter.Maximum))
                    throw new InvalidOperationException("Custom numeric bounds must be finite and applicable.");
                // Retain exact CLR integer endpoints when an attribute does not tighten them.
                // Rewriting an unchanged long/ulong endpoint through double can widen its value.
                if (!double.IsNaN(parameter.Minimum) && (schema["minimum"] == null || parameter.Minimum > schema["minimum"].Value<double>()))
                    schema["minimum"] = parameter.Minimum;
                if (!double.IsNaN(parameter.Maximum) && (schema["maximum"] == null || parameter.Maximum < schema["maximum"].Value<double>()))
                    schema["maximum"] = parameter.Maximum;
                if (schema["minimum"] != null && schema["maximum"] != null && schema["minimum"].Value<double>() > schema["maximum"].Value<double>())
                    throw new InvalidOperationException("Custom numeric bounds contradict.");
            }
            Length(schema, "minLength", "maxLength", parameter.MinLength, parameter.MaxLength, type == typeof(string));
            Length(
                schema,
                "minItems",
                "maxItems",
                parameter.MinItems,
                parameter.MaxItems,
                type.IsArray || type.IsGenericType && type.GetGenericTypeDefinition() == typeof(List<>)
            );
        }

        private static void Length(JObject schema, string lower, string upper, int minimum, int maximum, bool applicable)
        {
            if (minimum < -1 || maximum < -1 || (minimum != -1 || maximum != -1) && !applicable || minimum >= 0 && maximum >= 0 && minimum > maximum)
                throw new InvalidOperationException("Custom length bounds contradict or are inapplicable.");
            if (minimum >= 0)
                schema[lower] = minimum;
            if (maximum >= 0)
                schema[upper] = maximum;
        }

        private static JToken Default(Type type, string raw, JObject schema)
        {
            if (raw.Length > MaxBytes)
                throw new InvalidOperationException("Custom default byte budget exceeded.");
            type = Nullable.GetUnderlyingType(type) ?? type;
            var kind = schema["type"] is JArray kinds ? kinds.First(value => (string)value != "null") : schema["type"];
            JToken value;
            if ((string)kind == "string")
                value = raw;
            else if (type == typeof(bool) && bool.TryParse(raw, out var boolean))
                value = boolean;
            else
                value = JToken.Parse(raw);
            bool nullable =
                schema["type"] is JArray union && union.Any(item => (string)item == "null")
                || schema["anyOf"] is JArray alternatives && alternatives.Any(item => (string)item["type"] == "null");
            if (value.Type == JTokenType.Null)
            {
                if (!nullable && type != typeof(object))
                    throw new InvalidOperationException("Custom scalar default is not nullable.");
                return value;
            }
            if (
                (string)kind == "integer" && value.Type != JTokenType.Integer
                || (string)kind == "number" && value.Type != JTokenType.Integer && value.Type != JTokenType.Float
                || (string)kind == "boolean" && value.Type != JTokenType.Boolean
                || (string)kind == "array" && value.Type != JTokenType.Array
                || (string)kind == "object" && value.Type != JTokenType.Object
            )
                throw new InvalidOperationException("Custom default has the wrong primitive shape.");
            if ((schema["$ref"] != null || schema["anyOf"] != null) && value.Type != JTokenType.Object)
                throw new InvalidOperationException("Custom DTO default must have object shape.");
            if (
                value is JArray array
                && (
                    schema["minItems"] != null && array.Count < schema["minItems"].Value<int>()
                    || schema["maxItems"] != null && array.Count > schema["maxItems"].Value<int>()
                )
            )
                throw new InvalidOperationException("Custom array default violates its declared bounds.");
            if (schema["enum"] is JArray choices && !choices.Any(choice => JToken.DeepEquals(choice, value)))
                throw new InvalidOperationException("Custom default is not a declared enum value.");
            if (value.Type == JTokenType.String)
            {
                int length = ScalarLength((string)value);
                if (
                    schema["minLength"] != null && length < schema["minLength"].Value<int>()
                    || schema["maxLength"] != null && length > schema["maxLength"].Value<int>()
                )
                    throw new InvalidOperationException("Custom string default violates its declared bounds.");
            }
            if (value.Type == JTokenType.Integer || value.Type == JTokenType.Float)
            {
                double number = value.Value<double>();
                if (
                    double.IsNaN(number)
                    || double.IsInfinity(number)
                    || schema["minimum"] != null && number < schema["minimum"].Value<double>()
                    || schema["maximum"] != null && number > schema["maximum"].Value<double>()
                    || (string)kind == "integer" && value.Type != JTokenType.Integer
                )
                    throw new InvalidOperationException("Custom scalar default violates its declared bounds.");
                if (
                    (string)kind == "integer"
                    && (
                        schema["minimum"] != null && value.Value<decimal>() < schema["minimum"].Value<decimal>()
                        || schema["maximum"] != null && value.Value<decimal>() > schema["maximum"].Value<decimal>()
                    )
                )
                    throw new InvalidOperationException("Custom integer default violates its exact bounds.");
            }
            // Nested semantics intentionally remain the server validator's responsibility.
            return value;
        }

        private static int ScalarLength(string text)
        {
            int count = 0;
            for (int index = 0; index < text.Length; index++, count++)
            {
                if (char.IsHighSurrogate(text[index]))
                {
                    if (index + 1 >= text.Length || !char.IsLowSurrogate(text[index + 1]))
                        throw new InvalidOperationException("Custom metadata requires valid Unicode scalars.");
                    index++;
                }
                else if (char.IsLowSurrogate(text[index]))
                    throw new InvalidOperationException("Custom metadata requires valid Unicode scalars.");
            }
            return count;
        }

        private sealed class Context
        {
            private readonly Dictionary<Type, string> _ids = new Dictionary<Type, string>();
            private readonly JObject _definitions = new JObject();
            private readonly HashSet<Type> _serializationChecked = new HashSet<Type>();
            private int _work;

            private void CheckSerializationHierarchy(Type type)
            {
                int depth = 0;
                for (var current = type; current != null; current = current.BaseType)
                {
                    if (++depth > 32 || ++_work > 4096)
                        throw new InvalidOperationException("Custom serialization metadata budget exceeded.");
                    if (_serializationChecked.Contains(current))
                        continue;
                    if (current.IsDefined(typeof(JsonContainerAttribute), false) || !type.IsEnum && current.IsDefined(typeof(DataContractAttribute), false))
                        throw new InvalidOperationException("Unsupported custom serialization contract.");
                    var flags = BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Instance | BindingFlags.DeclaredOnly;
                    var fields = current.GetFields(flags);
                    var properties = current.GetProperties(flags);
                    _work += fields.Length + properties.Length;
                    if (_work > 4096)
                        throw new InvalidOperationException("Custom serialization metadata budget exceeded.");
                    if (
                        fields.Any(field => field.IsDefined(typeof(JsonExtensionDataAttribute), false))
                        || properties.Any(property => property.IsDefined(typeof(JsonExtensionDataAttribute), false))
                    )
                        throw new InvalidOperationException("Custom extension-data contracts are unsupported.");
                    _serializationChecked.Add(current);
                }
            }

            public void Finish(JObject schema)
            {
                if (_definitions.Count > 0)
                    schema["$defs"] = _definitions;
            }

            public JObject Member(MemberInfo member, ToolParameterAttribute parameter, int depth)
            {
                var memberType = member is PropertyInfo property ? property.PropertyType : ((FieldInfo)member).FieldType;
                var converter = member.GetCustomAttribute<JsonConverterAttribute>();
                var schema = Type(memberType, parameter?.AllowNull ?? false, depth, converter);
                if (parameter == null)
                    return schema;
                if ((parameter.Description?.Length ?? 0) > 1024)
                    throw new InvalidOperationException("Custom description budget exceeded.");
                if (!string.IsNullOrEmpty(parameter.Description))
                    schema["description"] = parameter.Description;
                Constraints(schema, memberType, parameter);
                if (parameter.DefaultValue != null)
                    schema["default"] = Default(memberType, parameter.DefaultValue, schema);
                return schema;
            }

            public JObject Type(Type type, bool nullable, int depth, JsonConverterAttribute memberConverter = null)
            {
                if (++_work > 4096 || depth > 32)
                    throw new InvalidOperationException("Custom type graph budget exceeded.");
                var underlying = Nullable.GetUnderlyingType(type);
                nullable |= underlying != null;
                type = underlying ?? type;
                CheckSerializationHierarchy(type);
                var converter = memberConverter ?? type.GetCustomAttribute<JsonConverterAttribute>();
                if (converter != null && (!type.IsEnum || converter.ConverterType != typeof(StringEnumConverter) || converter.ConverterParameters?.Length > 0))
                    throw new InvalidOperationException("Unsupported custom JSON converter.");
                JObject schema;
                if (type == typeof(object))
                    return new JObject();
                if (type == typeof(string))
                    schema = new JObject { ["type"] = "string" };
                else if (type == typeof(bool))
                    schema = new JObject { ["type"] = "boolean" };
                else if (type.IsEnum)
                    schema = Enum(type, converter != null);
                else if (Integer(type, out var minimum, out var maximum))
                    schema = new JObject
                    {
                        ["type"] = "integer",
                        ["minimum"] = minimum,
                        ["maximum"] = maximum,
                    };
                else if (type == typeof(float) || type == typeof(double) || type == typeof(decimal))
                    schema = new JObject { ["type"] = "number" };
                else if (Dictionary(type, out var valueType))
                    schema = new JObject { ["type"] = "object", ["additionalProperties"] = Type(valueType, false, depth + 1) };
                else if (
                    type.IsArray && type.GetArrayRank() == 1 && type != typeof(byte[])
                    || type.IsGenericType && type.GetGenericTypeDefinition() == typeof(List<>)
                )
                    schema = new JObject
                    {
                        ["type"] = "array",
                        ["items"] = Type(type.IsArray ? type.GetElementType() : type.GetGenericArguments()[0], false, depth + 1),
                    };
                else
                    schema = Dto(type, depth);
                if (!nullable)
                    return schema;
                if (schema["$ref"] != null)
                    return new JObject { ["anyOf"] = new JArray(schema, new JObject { ["type"] = "null" }) };
                schema["type"] = new JArray(schema["type"].DeepClone(), "null");
                if (schema["enum"] is JArray values)
                    values.Add(JValue.CreateNull());
                return schema;
            }

            private JObject Dto(Type type, int depth)
            {
                if (
                    type.IsPointer
                    || type.IsByRef
                    || type.ContainsGenericParameters
                    || type.IsArray
                    || type.IsInterface
                    || type.IsAbstract
                    || typeof(Delegate).IsAssignableFrom(type)
                    || typeof(UnityEngine.Object).IsAssignableFrom(type)
                    || typeof(System.Collections.IEnumerable).IsAssignableFrom(type)
                    || type.Namespace?.StartsWith("System", StringComparison.Ordinal) == true
                )
                    throw new InvalidOperationException("Unsupported custom DTO contract.");
                if (_ids.TryGetValue(type, out var existing))
                    return new JObject { ["$ref"] = "#/$defs/" + existing };
                if (_ids.Count >= 128)
                    throw new InvalidOperationException("Custom definition budget exceeded.");
                string id = "t" + _ids.Count.ToString(CultureInfo.InvariantCulture);
                _ids.Add(type, id);
                var properties = new JObject();
                var required = new JArray();
                _definitions[id] = new JObject
                {
                    ["type"] = "object",
                    ["properties"] = properties,
                    ["additionalProperties"] = false,
                };
                foreach (var member in Members(type, false))
                {
                    var conditional = type.GetMethod(
                        "ShouldSerialize" + member.Name,
                        BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Instance,
                        null,
                        System.Type.EmptyTypes,
                        null
                    );
                    var specifiedField = type.GetField(member.Name + "Specified", BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Instance);
                    var specifiedProperty = type.GetProperty(member.Name + "Specified", BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Instance);
                    if (conditional?.ReturnType == typeof(bool) || specifiedField?.FieldType == typeof(bool) || specifiedProperty?.PropertyType == typeof(bool))
                        throw new InvalidOperationException("Conditional custom member serialization is unsupported.");
                    var alias = member.GetCustomAttribute<JsonPropertyAttribute>();
                    if (
                        alias != null
                        && (
                            alias.NamingStrategyType != null
                            || alias.ItemConverterType != null
                            || alias.Required != Required.Default
                            || alias.NullValueHandling != NullValueHandling.Include
                            || alias.DefaultValueHandling != DefaultValueHandling.Include
                            || alias.IsReference
                            || alias.ItemIsReference
                            || alias.TypeNameHandling != TypeNameHandling.None
                            || alias.ItemTypeNameHandling != TypeNameHandling.None
                            || alias.ReferenceLoopHandling != ReferenceLoopHandling.Error
                            || alias.ItemReferenceLoopHandling != ReferenceLoopHandling.Error
                        )
                    )
                        throw new InvalidOperationException("Unsupported custom member serialization contract.");
                    string name = alias?.PropertyName ?? member.Name;
                    if (name.Length > 256 || properties[name] != null)
                        throw new InvalidOperationException("Invalid or duplicate custom property name.");
                    var parameter = member.GetCustomAttribute<ToolParameterAttribute>();
                    properties[name] = Member(member, parameter, depth + 1);
                    if (parameter?.Required ?? true)
                        required.Add(name);
                }
                if (required.Count > 0)
                    _definitions[id]["required"] = required;
                return new JObject { ["$ref"] = "#/$defs/" + id };
            }
        }
    }
}
