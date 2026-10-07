using System;
using System.Collections.Generic;
using System.Reflection;
using MCPForUnity.Runtime.Helpers;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using UnityEngine;
#if UNITY_EDITOR
using UnityEditor; // Required for AssetDatabase and EditorUtility
#endif

namespace MCPForUnity.Runtime.Serialization
{
    public class Vector3Converter : JsonConverter<Vector3>
    {
        public override void WriteJson(JsonWriter writer, Vector3 value, JsonSerializer serializer)
        {
            writer.WriteStartObject();
            writer.WritePropertyName("x");
            writer.WriteValue(value.x);
            writer.WritePropertyName("y");
            writer.WriteValue(value.y);
            writer.WritePropertyName("z");
            writer.WriteValue(value.z);
            writer.WriteEndObject();
        }

        public override Vector3 ReadJson(JsonReader reader, Type objectType, Vector3 existingValue, bool hasExistingValue, JsonSerializer serializer)
        {
            JToken token = JToken.Load(reader);
            if (token is JArray arr && arr.Count >= 3)
                return new Vector3(JsonScalarConversion.ReadFloat(arr[0]), JsonScalarConversion.ReadFloat(arr[1]), JsonScalarConversion.ReadFloat(arr[2]));
            if (token is not JObject jo)
                throw new JsonSerializationException($"Cannot deserialize Vector3 from {token.Type}: '{token}'");
            return new Vector3(JsonScalarConversion.ReadFloat(jo["x"]), JsonScalarConversion.ReadFloat(jo["y"]), JsonScalarConversion.ReadFloat(jo["z"]));
        }
    }

    public class Vector2Converter : JsonConverter<Vector2>
    {
        public override void WriteJson(JsonWriter writer, Vector2 value, JsonSerializer serializer)
        {
            writer.WriteStartObject();
            writer.WritePropertyName("x");
            writer.WriteValue(value.x);
            writer.WritePropertyName("y");
            writer.WriteValue(value.y);
            writer.WriteEndObject();
        }

        public override Vector2 ReadJson(JsonReader reader, Type objectType, Vector2 existingValue, bool hasExistingValue, JsonSerializer serializer)
        {
            JToken token = JToken.Load(reader);
            if (token is JArray arr && arr.Count >= 2)
                return new Vector2(JsonScalarConversion.ReadFloat(arr[0]), JsonScalarConversion.ReadFloat(arr[1]));
            if (token is not JObject jo)
                throw new JsonSerializationException($"Cannot deserialize Vector2 from {token.Type}: '{token}'");
            return new Vector2(JsonScalarConversion.ReadFloat(jo["x"]), JsonScalarConversion.ReadFloat(jo["y"]));
        }
    }

    public class QuaternionConverter : JsonConverter<Quaternion>
    {
        public override void WriteJson(JsonWriter writer, Quaternion value, JsonSerializer serializer)
        {
            writer.WriteStartObject();
            writer.WritePropertyName("x");
            writer.WriteValue(value.x);
            writer.WritePropertyName("y");
            writer.WriteValue(value.y);
            writer.WritePropertyName("z");
            writer.WriteValue(value.z);
            writer.WritePropertyName("w");
            writer.WriteValue(value.w);
            writer.WriteEndObject();
        }

        public override Quaternion ReadJson(JsonReader reader, Type objectType, Quaternion existingValue, bool hasExistingValue, JsonSerializer serializer)
        {
            JToken token = JToken.Load(reader);
            if (token is JArray arr && arr.Count >= 4)
                return new Quaternion(
                    JsonScalarConversion.ReadFloat(arr[0]),
                    JsonScalarConversion.ReadFloat(arr[1]),
                    JsonScalarConversion.ReadFloat(arr[2]),
                    JsonScalarConversion.ReadFloat(arr[3])
                );
            if (token is not JObject jo)
                throw new JsonSerializationException($"Cannot deserialize Quaternion from {token.Type}: '{token}'");
            return new Quaternion(
                JsonScalarConversion.ReadFloat(jo["x"]),
                JsonScalarConversion.ReadFloat(jo["y"]),
                JsonScalarConversion.ReadFloat(jo["z"]),
                JsonScalarConversion.ReadFloat(jo["w"])
            );
        }
    }

    public class ColorConverter : JsonConverter<Color>
    {
        public override void WriteJson(JsonWriter writer, Color value, JsonSerializer serializer)
        {
            writer.WriteStartObject();
            writer.WritePropertyName("r");
            writer.WriteValue(value.r);
            writer.WritePropertyName("g");
            writer.WriteValue(value.g);
            writer.WritePropertyName("b");
            writer.WriteValue(value.b);
            writer.WritePropertyName("a");
            writer.WriteValue(value.a);
            writer.WriteEndObject();
        }

        public override Color ReadJson(JsonReader reader, Type objectType, Color existingValue, bool hasExistingValue, JsonSerializer serializer)
        {
            JObject jo = JObject.Load(reader);
            return new Color(
                JsonScalarConversion.ReadFloat(jo["r"]),
                JsonScalarConversion.ReadFloat(jo["g"]),
                JsonScalarConversion.ReadFloat(jo["b"]),
                JsonScalarConversion.ReadFloat(jo["a"])
            );
        }
    }

    public class RectConverter : JsonConverter<Rect>
    {
        public override void WriteJson(JsonWriter writer, Rect value, JsonSerializer serializer)
        {
            writer.WriteStartObject();
            writer.WritePropertyName("x");
            writer.WriteValue(value.x);
            writer.WritePropertyName("y");
            writer.WriteValue(value.y);
            writer.WritePropertyName("width");
            writer.WriteValue(value.width);
            writer.WritePropertyName("height");
            writer.WriteValue(value.height);
            writer.WriteEndObject();
        }

        public override Rect ReadJson(JsonReader reader, Type objectType, Rect existingValue, bool hasExistingValue, JsonSerializer serializer)
        {
            JObject jo = JObject.Load(reader);
            return new Rect(
                JsonScalarConversion.ReadFloat(jo["x"]),
                JsonScalarConversion.ReadFloat(jo["y"]),
                JsonScalarConversion.ReadFloat(jo["width"]),
                JsonScalarConversion.ReadFloat(jo["height"])
            );
        }
    }

    public class BoundsConverter : JsonConverter<Bounds>
    {
        public override void WriteJson(JsonWriter writer, Bounds value, JsonSerializer serializer)
        {
            writer.WriteStartObject();
            writer.WritePropertyName("center");
            serializer.Serialize(writer, value.center); // Use serializer to handle nested Vector3
            writer.WritePropertyName("size");
            serializer.Serialize(writer, value.size); // Use serializer to handle nested Vector3
            writer.WriteEndObject();
        }

        public override Bounds ReadJson(JsonReader reader, Type objectType, Bounds existingValue, bool hasExistingValue, JsonSerializer serializer)
        {
            JObject jo = JObject.Load(reader);
            Vector3 center = jo["center"].ToObject<Vector3>(serializer); // Use serializer to handle nested Vector3
            Vector3 size = jo["size"].ToObject<Vector3>(serializer); // Use serializer to handle nested Vector3
            return new Bounds(center, size);
        }
    }

    public class Vector4Converter : JsonConverter<Vector4>
    {
        public override void WriteJson(JsonWriter writer, Vector4 value, JsonSerializer serializer)
        {
            writer.WriteStartObject();
            writer.WritePropertyName("x");
            writer.WriteValue(value.x);
            writer.WritePropertyName("y");
            writer.WriteValue(value.y);
            writer.WritePropertyName("z");
            writer.WriteValue(value.z);
            writer.WritePropertyName("w");
            writer.WriteValue(value.w);
            writer.WriteEndObject();
        }

        public override Vector4 ReadJson(JsonReader reader, Type objectType, Vector4 existingValue, bool hasExistingValue, JsonSerializer serializer)
        {
            JToken token = JToken.Load(reader);
            if (token is JArray arr && arr.Count >= 4)
                return new Vector4(
                    JsonScalarConversion.ReadFloat(arr[0]),
                    JsonScalarConversion.ReadFloat(arr[1]),
                    JsonScalarConversion.ReadFloat(arr[2]),
                    JsonScalarConversion.ReadFloat(arr[3])
                );
            if (token is not JObject jo)
                throw new JsonSerializationException($"Cannot deserialize Vector4 from {token.Type}: '{token}'");
            return new Vector4(
                JsonScalarConversion.ReadFloat(jo["x"]),
                JsonScalarConversion.ReadFloat(jo["y"]),
                JsonScalarConversion.ReadFloat(jo["z"]),
                JsonScalarConversion.ReadFloat(jo["w"])
            );
        }
    }

    /// <summary>
    /// Safe converter for Matrix4x4 that only accesses raw matrix elements (m00-m33).
    /// Avoids computed properties (lossyScale, rotation, inverse) that call ValidTRS()
    /// and can crash Unity on non-TRS matrices (common in Cinemachine components).
    /// Fixes: https://github.com/CoplayDev/unity-mcp/issues/478
    /// </summary>
    public class Matrix4x4Converter : JsonConverter<Matrix4x4>
    {
        public override void WriteJson(JsonWriter writer, Matrix4x4 value, JsonSerializer serializer)
        {
            writer.WriteStartObject();
            // Only access raw matrix elements - NEVER computed properties like lossyScale/rotation
            writer.WritePropertyName("m00");
            writer.WriteValue(value.m00);
            writer.WritePropertyName("m01");
            writer.WriteValue(value.m01);
            writer.WritePropertyName("m02");
            writer.WriteValue(value.m02);
            writer.WritePropertyName("m03");
            writer.WriteValue(value.m03);
            writer.WritePropertyName("m10");
            writer.WriteValue(value.m10);
            writer.WritePropertyName("m11");
            writer.WriteValue(value.m11);
            writer.WritePropertyName("m12");
            writer.WriteValue(value.m12);
            writer.WritePropertyName("m13");
            writer.WriteValue(value.m13);
            writer.WritePropertyName("m20");
            writer.WriteValue(value.m20);
            writer.WritePropertyName("m21");
            writer.WriteValue(value.m21);
            writer.WritePropertyName("m22");
            writer.WriteValue(value.m22);
            writer.WritePropertyName("m23");
            writer.WriteValue(value.m23);
            writer.WritePropertyName("m30");
            writer.WriteValue(value.m30);
            writer.WritePropertyName("m31");
            writer.WriteValue(value.m31);
            writer.WritePropertyName("m32");
            writer.WriteValue(value.m32);
            writer.WritePropertyName("m33");
            writer.WriteValue(value.m33);
            writer.WriteEndObject();
        }

        public override Matrix4x4 ReadJson(JsonReader reader, Type objectType, Matrix4x4 existingValue, bool hasExistingValue, JsonSerializer serializer)
        {
            if (reader.TokenType == JsonToken.Null)
                return new Matrix4x4(); // Return zero matrix for null (consistent with missing field defaults)

            if (reader.TokenType != JsonToken.StartObject)
                throw new JsonSerializationException($"Expected JSON object or null when deserializing Matrix4x4, got '{reader.TokenType}'.");

            JObject jo = JObject.Load(reader);
            var matrix = new Matrix4x4();
            matrix.m00 = (float)JsonScalarConversion.Read(jo["m00"], typeof(float));
            matrix.m01 = (float)JsonScalarConversion.Read(jo["m01"], typeof(float));
            matrix.m02 = (float)JsonScalarConversion.Read(jo["m02"], typeof(float));
            matrix.m03 = (float)JsonScalarConversion.Read(jo["m03"], typeof(float));
            matrix.m10 = (float)JsonScalarConversion.Read(jo["m10"], typeof(float));
            matrix.m11 = (float)JsonScalarConversion.Read(jo["m11"], typeof(float));
            matrix.m12 = (float)JsonScalarConversion.Read(jo["m12"], typeof(float));
            matrix.m13 = (float)JsonScalarConversion.Read(jo["m13"], typeof(float));
            matrix.m20 = (float)JsonScalarConversion.Read(jo["m20"], typeof(float));
            matrix.m21 = (float)JsonScalarConversion.Read(jo["m21"], typeof(float));
            matrix.m22 = (float)JsonScalarConversion.Read(jo["m22"], typeof(float));
            matrix.m23 = (float)JsonScalarConversion.Read(jo["m23"], typeof(float));
            matrix.m30 = (float)JsonScalarConversion.Read(jo["m30"], typeof(float));
            matrix.m31 = (float)JsonScalarConversion.Read(jo["m31"], typeof(float));
            matrix.m32 = (float)JsonScalarConversion.Read(jo["m32"], typeof(float));
            matrix.m33 = (float)JsonScalarConversion.Read(jo["m33"], typeof(float));
            return matrix;
        }
    }

    // Converter for UnityEngine.Object references (GameObjects, Components, Materials, Textures, etc.)
    // Intentionally internal: this converter is meant for MCP-internal serialization only.
    // Leaving it public lets third-party Newtonsoft converter scanners (e.g. jillejr's
    // newtonsoft-json-for-unity converters) instantiate it via reflection and bind it into
    // JsonConvert.DefaultSettings, which silently rewrites any UnityEngine.Object reference in
    // unrelated project code as an asset path string. See issue #1138.
    /// <summary>
    /// Serializes Unity.Mathematics value types (float2/3/4, int*, quaternion, float4x4, ...) as
    /// their public instance fields only. Those structs also expose hundreds of public swizzle
    /// properties (float3.xxy, .zyx, float4 has 336 of them), each returning a new struct with
    /// swizzles of its own, so the default object contract walks a combinatorial tree that never
    /// finishes in practice and freezes the Editor (issue #1415). Types are matched by namespace so
    /// the package does not need a dependency on com.unity.mathematics.
    /// </summary>
    public class UnityMathematicsConverter : JsonConverter
    {
        private const string MathematicsNamespace = "Unity.Mathematics";
        private static readonly Dictionary<Type, FieldInfo[]> _fieldCache = new Dictionary<Type, FieldInfo[]>();

        public override bool CanRead => false;

        public override bool CanConvert(Type objectType)
        {
            return objectType.IsValueType && !objectType.IsPrimitive && !objectType.IsEnum && objectType.Namespace == MathematicsNamespace;
        }

        public override void WriteJson(JsonWriter writer, object value, JsonSerializer serializer)
        {
            Type type = value.GetType();
            FieldInfo[] fields;
            lock (_fieldCache)
            {
                if (!_fieldCache.TryGetValue(type, out fields))
                {
                    fields = type.GetFields(BindingFlags.Public | BindingFlags.Instance);
                    _fieldCache[type] = fields;
                }
            }

            writer.WriteStartObject();
            foreach (FieldInfo field in fields)
            {
                writer.WritePropertyName(field.Name);
                // Nested math types (quaternion.value is a float4, float4x4 columns are float4) come back through this converter.
                serializer.Serialize(writer, field.GetValue(value));
            }
            writer.WriteEndObject();
        }

        public override object ReadJson(JsonReader reader, Type objectType, object existingValue, JsonSerializer serializer)
        {
            throw new NotSupportedException($"{nameof(UnityMathematicsConverter)} only serializes {MathematicsNamespace} types.");
        }
    }

    internal class UnityEngineObjectConverter : JsonConverter<UnityEngine.Object>
    {
        public override bool CanRead => true; // We need to implement ReadJson
        public override bool CanWrite => true;

        public override void WriteJson(JsonWriter writer, UnityEngine.Object value, JsonSerializer serializer)
        {
            if (value == null)
            {
                writer.WriteNull();
                return;
            }

#if UNITY_EDITOR // AssetDatabase and EditorUtility are Editor-only
            if (UnityEditor.AssetDatabase.Contains(value))
            {
                // It's an asset (Material, Texture, Prefab, etc.)
                string path = UnityEditor.AssetDatabase.GetAssetPath(value);
                if (!string.IsNullOrEmpty(path))
                {
                    writer.WriteValue(path);
                }
                else
                {
                    // Asset exists but path couldn't be found? Write minimal info.
                    writer.WriteStartObject();
                    writer.WritePropertyName("name");
                    writer.WriteValue(value.name);
                    WriteSerializedObjectId(writer, value);
                    writer.WritePropertyName("isAssetWithoutPath");
                    writer.WriteValue(true);
                    writer.WriteEndObject();
                }
            }
            else
            {
                // It's a scene object (GameObject, Component, etc.)
                writer.WriteStartObject();
                writer.WritePropertyName("name");
                writer.WriteValue(value.name);
                WriteSerializedObjectId(writer, value);
                writer.WriteEndObject();
            }
#else
            // Runtime fallback: Write basic info without AssetDatabase
            writer.WriteStartObject();
            writer.WritePropertyName("name");
            writer.WriteValue(value.name);
            WriteSerializedObjectId(writer, value);
            writer.WritePropertyName("warning");
            writer.WriteValue("UnityEngineObjectConverter running in non-Editor mode, asset path unavailable.");
            writer.WriteEndObject();
#endif
        }

        public override UnityEngine.Object ReadJson(
            JsonReader reader,
            Type objectType,
            UnityEngine.Object existingValue,
            bool hasExistingValue,
            JsonSerializer serializer
        )
        {
            if (reader.TokenType == JsonToken.Null)
            {
                return null;
            }

#if UNITY_EDITOR
            if (reader.TokenType == JsonToken.String)
            {
                string strValue = reader.Value.ToString();

                // Check if it looks like a GUID (32 hex chars, optionally with hyphens)
                if (IsValidGuid(strValue))
                {
                    string path = UnityEditor.AssetDatabase.GUIDToAssetPath(strValue.Replace("-", "").ToLowerInvariant());
                    if (!string.IsNullOrEmpty(path))
                    {
                        var asset = UnityEditor.AssetDatabase.LoadAssetAtPath(
                            MCPForUnity.Runtime.Helpers.UnityAssetPath.Resolve(path, allowPackages: true, allowBuiltIn: true),
                            objectType
                        );
                        if (asset != null)
                            return asset;
                    }
                    UnityEngine.Debug.LogWarning($"[UnityEngineObjectConverter] Could not load asset with GUID '{strValue}' as type '{objectType.Name}'.");
                    return null;
                }

                // Assume it's an asset path
                var loadedAsset = UnityEditor.AssetDatabase.LoadAssetAtPath(
                    MCPForUnity.Runtime.Helpers.UnityAssetPath.Resolve(strValue, allowPackages: true, allowBuiltIn: true),
                    objectType
                );
                if (loadedAsset == null)
                {
                    UnityEngine.Debug.LogWarning($"[UnityEngineObjectConverter] Could not load asset at path '{strValue}' as type '{objectType.Name}'.");
                }
                return loadedAsset;
            }

            if (reader.TokenType == JsonToken.StartObject)
            {
                JObject jo = JObject.Load(reader);
                // Reject malformed IDs before trying a different reference form.
                // A guid/path must not hide an invalid Boolean or fractional ID.
                if (jo.TryGetValue("instanceID", out JToken scalarId) && scalarId.Type != JTokenType.Null)
                    JsonScalarConversion.Read(scalarId, typeof(int));
                if (jo.TryGetValue("entityID", out JToken scalarEntityId) && scalarEntityId.Type != JTokenType.Null)
                    JsonScalarConversion.Read(scalarEntityId, typeof(ulong));

                // Try to resolve by GUID first (for assets like ScriptableObjects, Materials, etc.)
                if (jo.TryGetValue("guid", out JToken guidToken) && guidToken.Type == JTokenType.String)
                {
                    string guid = guidToken.ToString().Replace("-", "").ToLowerInvariant();
                    string path = UnityEditor.AssetDatabase.GUIDToAssetPath(guid);
                    if (!string.IsNullOrEmpty(path))
                    {
                        var asset = UnityEditor.AssetDatabase.LoadAssetAtPath(
                            MCPForUnity.Runtime.Helpers.UnityAssetPath.Resolve(path, allowPackages: true, allowBuiltIn: true),
                            objectType
                        );
                        if (asset != null)
                            return asset;
                    }
                    UnityEngine.Debug.LogWarning($"[UnityEngineObjectConverter] Could not load asset with GUID '{guidToken}' as type '{objectType.Name}'.");
                    return null;
                }

#if UNITY_6000_5_OR_NEWER
                // Try to resolve by entityID (Unity 6.5+). Falls through to instanceID/guid/path on failure.
                if (jo.TryGetValue("entityID", out JToken entityIdToken) && entityIdToken.Type == JTokenType.String)
                {
                    string serializedEntityId = entityIdToken.ToString();
                    if (ulong.TryParse(serializedEntityId, out ulong rawEntityId))
                    {
                        EntityId eid = EntityId.FromULong(rawEntityId);
                        UnityEngine.Object entityObj = UnityEditor.EditorUtility.EntityIdToObject(eid);
                        if (entityObj != null)
                        {
                            string assetPath = UnityEditor.AssetDatabase.GetAssetPath(entityObj);
                            if (!string.IsNullOrEmpty(assetPath))
                                MCPForUnity.Runtime.Helpers.UnityAssetPath.Resolve(assetPath, allowPackages: true, allowBuiltIn: true);
                            if (objectType.IsAssignableFrom(entityObj.GetType()))
                            {
                                return entityObj;
                            }

                            if (objectType == typeof(Transform) && entityObj is GameObject entityGo)
                            {
                                return entityGo.transform;
                            }

                            if (typeof(Component).IsAssignableFrom(objectType) && entityObj is GameObject entityGameObj)
                            {
                                var component = entityGameObj.GetComponent(objectType);
                                if (component != null)
                                {
                                    return component;
                                }
                            }
                        }
                    }

                    UnityEngine.Debug.LogWarning(
                        $"[UnityEngineObjectConverter] Could not resolve entityID '{serializedEntityId}' to a valid {objectType.Name}. Falling back to instanceID/guid/path."
                    );
                }
#endif

                // Try to resolve by instanceID
                if (jo.TryGetValue("instanceID", out JToken idToken) && idToken.Type != JTokenType.Null)
                {
                    int instanceId = (int)JsonScalarConversion.Read(idToken, typeof(int));
                    UnityEngine.Object obj = UnityObjectIdCompat.InstanceIDToObjectCompat(instanceId);
                    if (obj != null)
                    {
                        string assetPath = UnityEditor.AssetDatabase.GetAssetPath(obj);
                        if (!string.IsNullOrEmpty(assetPath))
                            MCPForUnity.Runtime.Helpers.UnityAssetPath.Resolve(assetPath, allowPackages: true, allowBuiltIn: true);
                        // Direct type match
                        if (objectType.IsAssignableFrom(obj.GetType()))
                        {
                            return obj;
                        }

                        // Special case: expecting Transform but got GameObject - get its transform
                        if (objectType == typeof(Transform) && obj is GameObject go)
                        {
                            return go.transform;
                        }

                        // Special case: expecting a Component type but got GameObject - try to get the component
                        if (typeof(Component).IsAssignableFrom(objectType) && obj is GameObject gameObj)
                        {
                            var component = gameObj.GetComponent(objectType);
                            if (component != null)
                            {
                                return component;
                            }
                            UnityEngine.Debug.LogWarning(
                                $"[UnityEngineObjectConverter] GameObject '{gameObj.name}' (ID: {instanceId}) does not have a '{objectType.Name}' component."
                            );
                            return null;
                        }

                        // Type mismatch with no automatic conversion available
                        UnityEngine.Debug.LogWarning(
                            $"[UnityEngineObjectConverter] Instance ID {instanceId} resolved to '{obj.GetType().Name}' but expected '{objectType.Name}'."
                        );
                        return null;
                    }
                    // Instance ID lookup failed - this can happen if the object was destroyed or ID is stale
                    string objectName = jo.TryGetValue("name", out JToken nameToken) ? nameToken.ToString() : "unknown";
                    UnityEngine.Debug.LogWarning(
                        $"[UnityEngineObjectConverter] Could not resolve instance ID {instanceId} (name: '{objectName}') to a valid {objectType.Name}. The object may have been destroyed or the ID is stale."
                    );
                    return null;
                }

                // Check if there's an asset path in the object
                if (jo.TryGetValue("path", out JToken pathToken) && pathToken.Type == JTokenType.String)
                {
                    string path = pathToken.ToString();
                    var asset = UnityEditor.AssetDatabase.LoadAssetAtPath(
                        MCPForUnity.Runtime.Helpers.UnityAssetPath.Resolve(path, allowPackages: true, allowBuiltIn: true),
                        objectType
                    );
                    if (asset != null)
                    {
                        return asset;
                    }
                    UnityEngine.Debug.LogWarning($"[UnityEngineObjectConverter] Could not load asset at path '{path}' as type '{objectType.Name}'.");
                    return null;
                }

                // Object format not recognized
                UnityEngine.Debug.LogWarning(
                    $"[UnityEngineObjectConverter] JSON object missing 'instanceID', 'entityID', 'guid', or 'path' field for {objectType.Name} deserialization. Object: {jo.ToString(Formatting.None)}"
                );
                return null;
            }

            // Unexpected token type
            UnityEngine.Debug.LogWarning(
                $"[UnityEngineObjectConverter] Unexpected token type '{reader.TokenType}' when deserializing {objectType.Name}. Expected Null, String, or Object."
            );
            return null;
#else
            // Runtime deserialization is tricky without AssetDatabase/EditorUtility
            UnityEngine.Debug.LogWarning("UnityEngineObjectConverter cannot deserialize complex objects in non-Editor mode.");
            // Skip the current token to avoid breaking the reader state
            reader.Skip();
            // Return existing value since we can't deserialize without Editor APIs
            return existingValue;
#endif
        }

        /// <summary>
        /// Checks if a string looks like a valid GUID (32 hex chars, with or without hyphens).
        /// </summary>
        private static bool IsValidGuid(string str)
        {
            if (string.IsNullOrEmpty(str))
                return false;
            string normalized = str.Replace("-", "");
            if (normalized.Length != 32)
                return false;
            foreach (char c in normalized)
            {
                if (!((c >= '0' && c <= '9') || (c >= 'a' && c <= 'f') || (c >= 'A' && c <= 'F')))
                    return false;
            }
            return true;
        }

        private static void WriteSerializedObjectId(JsonWriter writer, UnityEngine.Object value)
        {
            // Always emit instanceID so older consumers keep working.
            writer.WritePropertyName("instanceID");
            writer.WriteValue(value.GetInstanceIDCompat());
#if UNITY_6000_5_OR_NEWER
            // Additionally emit entityID on Unity 6.5+ as the stable ulong form
            // (per Unity docs, EntityId.ToString() is NOT a stable serialization format).
            writer.WritePropertyName("entityID");
            writer.WriteValue(EntityId.ToULong(value.GetEntityId()).ToString());
#endif
        }
    }
}
