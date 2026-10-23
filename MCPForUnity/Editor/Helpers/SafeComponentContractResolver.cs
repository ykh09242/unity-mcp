using System;
using System.Collections.Generic;
using System.Reflection;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using Newtonsoft.Json.Serialization;
using UnityEngine;

namespace MCPForUnity.Editor.Helpers
{
    /// <summary>Field inspection must not enter user getters, converters or serialization callbacks.</summary>
    internal sealed class SafeComponentContractResolver : DefaultContractResolver
    {
        private readonly bool includePrivate;

        private readonly IList<JsonConverter> frameworkConverters;

        internal SafeComponentContractResolver(bool includePrivate, IList<JsonConverter> frameworkConverters)
        {
            this.includePrivate = includePrivate;
            this.frameworkConverters = frameworkConverters;
        }

        private sealed class SafeTokenConverter : JsonConverter
        {
            internal static readonly SafeTokenConverter Instance = new SafeTokenConverter();

            internal static bool IsFrameworkToken(Type type) =>
                type == typeof(JObject)
                || type == typeof(JArray)
                || type == typeof(JValue)
                || type == typeof(JRaw)
                || type == typeof(JProperty)
                || type == typeof(JConstructor);

            public override bool CanConvert(Type type) => IsFrameworkToken(type);

            public override bool CanRead => false;

            public override object ReadJson(JsonReader reader, Type type, object existing, JsonSerializer serializer) => throw new NotSupportedException();

            public override void WriteJson(JsonWriter writer, object value, JsonSerializer serializer)
            {
                // Resolve every child independently. A framework container may contain
                // a user token whose virtual Type/WriteTo must never be dispatched.
                if (value is JObject obj)
                {
                    writer.WriteStartObject();
                    foreach (JProperty property in obj.Properties())
                    {
                        writer.WritePropertyName(property.Name);
                        serializer.Serialize(writer, property.GetType() == typeof(JProperty) ? property.Value : property);
                    }
                    writer.WriteEndObject();
                }
                else if (value is JArray array)
                {
                    writer.WriteStartArray();
                    foreach (JToken item in array)
                        serializer.Serialize(writer, item);
                    writer.WriteEndArray();
                }
                else if (value is JConstructor constructor)
                {
                    writer.WriteStartConstructor(constructor.Name);
                    foreach (JToken item in constructor)
                        serializer.Serialize(writer, item);
                    writer.WriteEndConstructor();
                }
                else if (value is JProperty property)
                {
                    writer.WritePropertyName(property.Name);
                    serializer.Serialize(writer, property.Value);
                }
                else if (value is JRaw raw && raw.Value is string text)
                    writer.WriteRawValue(text);
                else
                    // This also avoids custom ToString on an object stored inside JRaw.
                    serializer.Serialize(writer, ((JValue)value).Value);
            }
        }

        protected override JsonContract CreateContract(Type type)
        {
            // These exact runtime shapes have framework-owned serialization/enumeration.
            // User collection subclasses use the field contract below instead of their enumerator.
            // The field contract deliberately uses object as its underlying type.
            // Select our trusted Unity converters here so their reference/value output
            // remains intact without constructing a contract from a user-derived type.
            foreach (JsonConverter converter in frameworkConverters)
                if (converter.CanConvert(type))
                    return new JsonObjectContract(typeof(object)) { Converter = converter };
            Type scalar = Nullable.GetUnderlyingType(type) ?? type;
            // Enum attributes may name user converters. Even contract initialization
            // can invoke their constructors, so do not use the default resolver here.
            if (scalar.IsEnum)
                return new JsonPrimitiveContract(type);
            if (
                scalar.IsPrimitive
                || type == typeof(byte[])
                || scalar == typeof(string)
                || scalar == typeof(decimal)
                || scalar == typeof(DateTime)
                || scalar == typeof(DateTimeOffset)
                || scalar == typeof(Guid)
                || scalar == typeof(TimeSpan)
                || scalar == typeof(Uri)
                || scalar == typeof(System.Numerics.BigInteger)
            )
                return base.CreateContract(type);
            if (SafeTokenConverter.IsFrameworkToken(type))
                return new JsonLinqContract(type) { Converter = SafeTokenConverter.Instance };
            if (type.IsArray || (type.IsGenericType && type.GetGenericTypeDefinition() == typeof(List<>)))
                return base.CreateArrayContract(type);
            if (type.IsGenericType && type.GetGenericTypeDefinition() == typeof(Dictionary<,>))
            {
                Type key = type.GetGenericArguments()[0];
                if (key == typeof(string) || key.IsEnum || key.IsPrimitive || key == typeof(Guid))
                    return base.CreateDictionaryContract(type);
            }

            // Construct directly: DefaultContractResolver would discover user callbacks,
            // ShouldSerialize methods, custom converters, ISerializable and extension data.
            // JsonContainerContract inspects attributes in its constructor. Use an
            // attribute-free framework shape, then attach only our field value providers.
            var contract = new JsonObjectContract(typeof(object)) { MemberSerialization = MemberSerialization.Fields };
            var names = new HashSet<string>(StringComparer.Ordinal);
            for (Type current = type; current != null && current != typeof(object); current = current.BaseType)
            {
                foreach (FieldInfo field in current.GetFields(BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.DeclaredOnly))
                {
                    bool serialized = field.IsDefined(typeof(SerializeField), true) || field.IsDefined(typeof(SerializeReference), true);
                    if (
                        field.IsStatic
                        || field.IsNotSerialized
                        || (!field.IsPublic && !(includePrivate && serialized))
                        || (field.Name.EndsWith("k__BackingField", StringComparison.Ordinal) && !serialized)
                        || GameObjectSerializer.IsUnsafeInspectionType(field.FieldType)
                        || !names.Add(field.Name)
                    )
                        continue;
                    contract.Properties.Add(
                        new JsonProperty
                        {
                            PropertyName = field.Name,
                            UnderlyingName = field.Name,
                            PropertyType = field.FieldType,
                            DeclaringType = field.DeclaringType,
                            Readable = true,
                            Writable = false,
                            ValueProvider = new ReflectionValueProvider(field),
                        }
                    );
                }
            }
            return contract;
        }
    }
}
