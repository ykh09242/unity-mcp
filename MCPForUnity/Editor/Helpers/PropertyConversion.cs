using System;
using System.Collections.Generic;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Runtime.Serialization;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using Newtonsoft.Json.Serialization;
using UnityEditor;
using UnityEngine;

namespace MCPForUnity.Editor.Helpers
{
    /// <summary>
    /// Unified property conversion from JSON to Unity types.
    /// Uses UnityJsonSerializer for consistent type handling.
    /// </summary>
    public static class PropertyConversion
    {
        /// <summary>
        /// Converts a JToken to the specified target type using Unity type converters.
        /// </summary>
        /// <param name="token">The JSON token to convert</param>
        /// <param name="targetType">The target type to convert to</param>
        /// <returns>The converted object, or null if conversion fails</returns>
        public static object ConvertToType(JToken token, Type targetType)
        {
            if (token == null || token.Type == JTokenType.Null)
            {
                if (targetType.IsValueType && Nullable.GetUnderlyingType(targetType) == null)
                {
                    McpLog.Warn($"[PropertyConversion] Cannot assign null to non-nullable value type {targetType.Name}. Returning default value.");
                    return Activator.CreateInstance(targetType);
                }
                return null;
            }

            try
            {
                // Use the shared Unity serializer with custom converters
                JToken input = token is JArray && HasNullableUnityArrayTarget(targetType, null) ? NormalizeNullableUnityArrays(token, targetType) : token;
                return input.ToObject(targetType, UnityJsonSerializer.Instance);
            }
            catch (InvalidUnityObjectReferenceException)
            {
                // Expected input rejection is returned by the tool as an error.
                // Do not report it as an unexpected Unity execution failure.
                throw;
            }
            catch (Exception ex)
            {
                McpLog.Error($"Error converting token to {targetType.FullName}: {ex.Message}\nToken: {token.ToString(Formatting.None)}");
                throw;
            }
        }

        private static bool IsNullableUnityArrayType(Type type)
        {
            Type underlying = Nullable.GetUnderlyingType(type);
            return underlying == typeof(Vector2) || underlying == typeof(Vector3) || underlying == typeof(Vector4) || underlying == typeof(Quaternion);
        }

        private static bool HasNullableUnityArrayTarget(Type type, HashSet<Type> visited)
        {
            if (IsNullableUnityArrayType(type))
                return true;
            // Multidimensional arrays retain their existing Json.NET handling.
            if (type.IsArray && type.GetArrayRank() != 1)
                return false;
            var contract = UnityJsonSerializer.Instance.ContractResolver.ResolveContract(type) as JsonArrayContract;
            if (contract?.CollectionItemType == null)
                return false;
            visited ??= new HashSet<Type>();
            return visited.Add(type) && HasNullableUnityArrayTarget(contract.CollectionItemType, visited);
        }

        private static JToken NormalizeNullableUnityArrays(JToken token, Type targetType)
        {
            if (token is not JArray array)
                return token;
            if (IsNullableUnityArrayType(targetType))
            {
                // Reuse the existing converter's length/numeric rules, then leave
                // nullable object/null/reference metadata handling to Json.NET.
                object value = token.ToObject(Nullable.GetUnderlyingType(targetType), UnityJsonSerializer.Instance);
                return JToken.FromObject(value, UnityJsonSerializer.Instance);
            }

            var contract = (JsonArrayContract)UnityJsonSerializer.Instance.ContractResolver.ResolveContract(targetType);
            JArray normalized = null;
            for (int i = 0; i < array.Count; i++)
            {
                JToken item = NormalizeNullableUnityArrays(array[i], contract.CollectionItemType);
                if (ReferenceEquals(item, array[i]))
                    continue;
                normalized ??= (JArray)array.DeepClone();
                normalized[i] = item;
            }
            return normalized ?? token;
        }

        /// <summary>
        /// Tries to convert a JToken to the specified target type.
        /// Returns null and logs warning on failure (does not throw).
        /// </summary>
        public static object TryConvertToType(JToken token, Type targetType)
        {
            try
            {
                return ConvertToType(token, targetType);
            }
            catch
            {
                return null;
            }
        }

        /// <summary>
        /// Generic version of ConvertToType.
        /// </summary>
        public static T ConvertTo<T>(JToken token)
        {
            return (T)ConvertToType(token, typeof(T));
        }

        /// <summary>
        /// Converts a JToken to a Unity asset by loading from path.
        /// </summary>
        /// <param name="token">JToken containing asset path</param>
        /// <param name="targetType">Expected asset type</param>
        /// <returns>The loaded asset, or null if not found</returns>
        public static UnityEngine.Object LoadAssetFromToken(JToken token, Type targetType)
        {
            if (token == null || token.Type != JTokenType.String)
                return null;

            string assetPath = AssetPathUtility.GetAssetReferencePath(token.ToString(), allowPackages: true, allowBuiltIn: true);
            UnityEngine.Object loadedAsset = AssetDatabase.LoadAssetAtPath(assetPath, targetType);

            if (loadedAsset == null)
            {
                McpLog.Warn($"[PropertyConversion] Could not load asset of type {targetType.Name} from path: {assetPath}");
            }

            return loadedAsset;
        }
    }
}
