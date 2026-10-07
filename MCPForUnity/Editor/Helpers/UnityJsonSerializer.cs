using System;
using System.Collections.Generic;
using MCPForUnity.Runtime.Serialization;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;

namespace MCPForUnity.Editor.Helpers
{
    /// <summary>
    /// Shared JsonSerializer with Unity type converters.
    /// Extracted from ManageGameObject to eliminate cross-tool dependencies.
    /// </summary>
    public static class UnityJsonSerializer
    {
        /// <summary>
        /// Shared JsonSerializer instance with converters for Unity types.
        /// Use this for all JToken-to-Unity-type conversions.
        /// </summary>
        public static readonly JsonSerializer Instance = JsonSerializer.Create(
            new JsonSerializerSettings
            {
                Converters = new List<JsonConverter>
                {
                    new StrictScalarConverter(),
                    new Vector2Converter(),
                    new Vector3Converter(),
                    new Vector4Converter(),
                    new QuaternionConverter(),
                    new ColorConverter(),
                    new RectConverter(),
                    new BoundsConverter(),
                    new Matrix4x4Converter(),
                    new UnityEngineObjectConverter(),
                },
            }
        );

        // Json.NET otherwise converts numeric flags to Boolean, rounds floating
        // tokens for integer members, and accepts Boolean values as numbers.
        // A shared converter also covers members inside DTOs, lists and dictionaries.
        private sealed class StrictScalarConverter : JsonConverter
        {
            public override bool CanWrite => false;

            public override bool CanConvert(Type objectType) => objectType == typeof(byte[]) || JsonScalarConversion.Supports(objectType);

            public override object ReadJson(JsonReader reader, Type objectType, object existingValue, JsonSerializer serializer)
            {
                JToken token = JToken.Load(reader);
                // Optional top-level defaults belong to PropertyConversion/ParamCoercion.
                // A null inside a DTO or collection cannot erase a required value.
                if (token.Type == JTokenType.Null && objectType.IsValueType && Nullable.GetUnderlyingType(objectType) == null)
                    throw new JsonSerializationException($"Cannot assign null to {objectType.Name} at '{token.Path}'.");
                // Json.NET reads byte[] using ReadAsBytes, bypassing per-element
                // converters and rounding floats. Preserve base64 output/input.
                if (objectType == typeof(byte[]))
                {
                    if (token.Type == JTokenType.Null)
                        return null;
                    if (token.Type == JTokenType.Bytes)
                        return token.Value<byte[]>();
                    if (token.Type == JTokenType.String)
                        return Convert.FromBase64String(token.Value<string>());
                    if (token is JArray array)
                    {
                        var bytes = new byte[array.Count];
                        for (int i = 0; i < array.Count; i++)
                        {
                            if (array[i].Type == JTokenType.Null)
                                throw new JsonSerializationException($"Cannot assign null to Byte at '{array[i].Path}'.");
                            bytes[i] = (byte)JsonScalarConversion.Read(array[i], typeof(byte));
                        }
                        return bytes;
                    }
                    throw new JsonSerializationException("Expected byte array or base64 string.");
                }
                return JsonScalarConversion.Read(token, objectType);
            }

            public override void WriteJson(JsonWriter writer, object value, JsonSerializer serializer) => throw new NotSupportedException();
        }
    }
}
