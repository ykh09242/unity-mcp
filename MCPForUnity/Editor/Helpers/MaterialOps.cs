using System;
using System.Collections.Generic;
using System.Linq;
using MCPForUnity.Editor.Tools;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEngine;

namespace MCPForUnity.Editor.Helpers
{
    public static class MaterialOps
    {
        /// <summary>
        /// Applies a set of properties (JObject) to a material, handling aliases and structured formats.
        /// </summary>
        public static bool ApplyProperties(Material mat, JObject properties, JsonSerializer serializer)
        {
            if (mat == null || properties == null)
                return false;
            bool modified = false;

            // Helper for case-insensitive lookup
            JToken GetValue(string key)
            {
                return properties.Properties()
                    .FirstOrDefault(p => string.Equals(p.Name, key, StringComparison.OrdinalIgnoreCase))?.Value;
            }

            // --- Structured / Legacy Format Handling ---
            // Example: Set shader
            var shaderToken = GetValue("shader");
            if (shaderToken?.Type == JTokenType.String)
            {
                string shaderRequest = shaderToken.ToString();
                // Set shader
                Shader newShader = RenderPipelineUtility.ResolveShader(shaderRequest);
                if (newShader != null && mat.shader != newShader)
                {
                    mat.shader = newShader;
                    modified = true;
                }
            }

            // Example: Set color property (structured)
            var colorToken = GetValue("color");
            if (colorToken is JObject colorProps)
            {
                string propName = colorProps["name"]?.ToString() ?? GetMainColorPropertyName(mat);
                if (colorProps["value"] is JArray colArr && colArr.Count >= 3)
                {
                    try
                    {
                        Color newColor = ParseColor(colArr, serializer);
                        modified |= ApplyStructuredColor(mat, propName, newColor, colArr, serializer);
                    }
                    catch (Exception ex)
                    {
                        McpLog.Warn($"[MaterialOps] Failed to parse color for property '{propName}': {ex.Message}");
                    }
                }
            }
            else if (colorToken is JArray colorArr) // Structured shorthand
            {
                string propName = GetMainColorPropertyName(mat);
                try
                {
                    Color newColor = ParseColor(colorArr, serializer);
                    modified |= ApplyStructuredColor(mat, propName, newColor, colorArr, serializer);
                }
                catch (Exception ex)
                {
                    McpLog.Warn($"[MaterialOps] Failed to parse color array: {ex.Message}");
                }
            }

            // Example: Set float property (structured)
            var floatToken = GetValue("float");
            if (floatToken is JObject floatProps)
            {
                string propName = floatProps["name"]?.ToString();
                if (!string.IsNullOrEmpty(propName) &&
                   (floatProps["value"]?.Type == JTokenType.Float || floatProps["value"]?.Type == JTokenType.Integer))
                {
                    try
                    {
                        float newVal = floatProps["value"].ToObject<float>();
                        int propertyIndex = mat.shader.FindPropertyIndex(propName);
                        if (mat.HasProperty(propName) && propertyIndex >= 0)
                        {
                            var type = mat.shader.GetPropertyType(propertyIndex);
                            bool changed = type == UnityEngine.Rendering.ShaderPropertyType.Int
                                ? mat.GetInteger(propName) != floatProps["value"].ToObject<decimal>()
                                : (type == UnityEngine.Rendering.ShaderPropertyType.Float || type == UnityEngine.Rendering.ShaderPropertyType.Range) && mat.GetFloat(propName) != newVal;
                            if (changed)
                                modified |= TrySetShaderProperty(mat, propName, floatProps["value"], serializer);
                        }
                    }
                    catch (Exception ex)
                    {
                        McpLog.Warn($"[MaterialOps] Failed to set float property '{propName}': {ex.Message}");
                    }
                }
            }

            // Example: Set texture property (structured)
            {
                var texToken = GetValue("texture");
                if (texToken is JObject texProps)
                {
                    string rawName = (texProps["name"] ?? texProps["Name"])?.ToString();
                    string texPath = (texProps["path"] ?? texProps["Path"])?.ToString();
                    if (!string.IsNullOrEmpty(texPath))
                    {
                        var sanitizedPath = AssetPathUtility.SanitizeAssetPath(texPath);
                        var newTex = AssetDatabase.LoadAssetAtPath<Texture>(sanitizedPath);
                        if (newTex == null)
                            throw new ArgumentException($"Texture not found at path: {sanitizedPath}");
                        // Use ResolvePropertyName to handle aliases even for structured texture names
                        string candidateName = string.IsNullOrEmpty(rawName) ? "_BaseMap" : rawName;
                        string targetProp = ResolvePropertyName(mat, candidateName);

                        int propertyIndex = mat.shader.FindPropertyIndex(targetProp);
                        if (!string.IsNullOrEmpty(targetProp) && mat.HasProperty(targetProp) && propertyIndex >= 0 &&
                            mat.shader.GetPropertyType(propertyIndex) == UnityEngine.Rendering.ShaderPropertyType.Texture)
                        {
                            if (mat.GetTexture(targetProp) != newTex)
                            {
                                mat.SetTexture(targetProp, newTex);
                                modified = true;
                            }
                        }
                    }
                }
            }

            // --- Direct Property Assignment (Flexible) ---
            var reservedKeys = new HashSet<string>(StringComparer.OrdinalIgnoreCase) { "shader", "color", "float", "texture" };

            foreach (var prop in properties.Properties())
            {
                if (reservedKeys.Contains(prop.Name)) continue;
                string shaderProp = ResolvePropertyName(mat, prop.Name);
                JToken v = prop.Value;

                if (TrySetShaderProperty(mat, shaderProp, v, serializer))
                {
                    modified = true;
                }
            }

            return modified;
        }

        /// <summary>
        /// Resolves common property aliases (e.g. "metallic" -> "_Metallic").
        /// </summary>
        public static string ResolvePropertyName(Material mat, string name)
        {
            if (mat == null || string.IsNullOrEmpty(name)) return name;
            string[] candidates;
            var lower = name.ToLowerInvariant();
            switch (lower)
            {
                case "_color": candidates = new[] { "_Color", "_BaseColor" }; break;
                case "_basecolor": candidates = new[] { "_BaseColor", "_Color" }; break;
                case "_maintex": candidates = new[] { "_MainTex", "_BaseMap" }; break;
                case "_basemap": candidates = new[] { "_BaseMap", "_MainTex" }; break;
                case "_glossiness": candidates = new[] { "_Glossiness", "_Smoothness" }; break;
                case "_smoothness": candidates = new[] { "_Smoothness", "_Glossiness" }; break;
                // Friendly names → shader property names
                case "metallic": candidates = new[] { "_Metallic" }; break;
                case "smoothness": candidates = new[] { "_Smoothness", "_Glossiness" }; break;
                case "albedo": candidates = new[] { "_BaseMap", "_MainTex" }; break;
                default: candidates = new[] { name }; break; // keep original as-is
            }
            foreach (var candidate in candidates)
            {
                if (mat.HasProperty(candidate)) return candidate;
            }
            return name;
        }

        private static bool ApplyStructuredColor(Material material, string propertyName, Color color, JArray value, JsonSerializer serializer)
        {
            int index = material.shader.FindPropertyIndex(propertyName);
            if (index < 0 || !material.HasProperty(propertyName)) return false;
            var type = material.shader.GetPropertyType(index);
            if (type == UnityEngine.Rendering.ShaderPropertyType.Color)
                return material.GetColor(propertyName) != color && TrySetShaderProperty(material, propertyName, value, serializer);
            if (type == UnityEngine.Rendering.ShaderPropertyType.Vector)
            {
                // Structured color retains its alpha default even when targeting a vector property.
                var vector = new Vector4(color.r, color.g, color.b, color.a);
                return material.GetVector(propertyName) != vector && TrySetShaderProperty(material, propertyName,
                    new JArray(vector.x, vector.y, vector.z, vector.w), serializer);
            }
            return false;
        }

        /// <summary>
        /// Auto-detects the main color property name for a material's shader.
        /// </summary>
        public static string GetMainColorPropertyName(Material mat)
        {
            if (mat == null || mat.shader == null)
                return "_Color";

            string[] commonColorProps = { "_BaseColor", "_Color", "_MainColor", "_Tint", "_TintColor" };
            foreach (var prop in commonColorProps)
            {
                if (mat.HasProperty(prop))
                    return prop;
            }
            return "_Color";
        }

        /// <summary>
        /// Tries to set a shader property on a material based on a JToken value.
        /// Handles Colors, Vectors, Floats, Ints, Booleans, and Textures.
        /// </summary>
        public static bool TrySetShaderProperty(Material material, string propertyName, JToken value, JsonSerializer serializer)
        {
            if (!TryPrepareShaderProperty(material, propertyName, value, serializer, out Action apply))
                return false;
            try
            {
                apply();
                return true;
            }
            catch (Exception ex)
            {
                McpLog.Warn($"[MaterialOps] Failed to set property '{propertyName}': {ex.Message}");
                return false;
            }
        }

        /// <summary>
        /// Resolves and converts one declared shader property without mutating the material.
        /// The returned setter retains the caller's Undo/dirty ordering.
        /// </summary>
        public static bool TryPrepareShaderProperty(Material material, string propertyName, JToken value, JsonSerializer serializer, out Action apply)
        {
            apply = null;
            if (material == null || material.shader == null || string.IsNullOrEmpty(propertyName) || value == null)
                return false;
            int index = material.shader.FindPropertyIndex(propertyName);
            if (index < 0 || !material.HasProperty(propertyName))
                return false;

            try
            {
                if (value.Type == JTokenType.String)
                {
                    string text = value.ToString();
                    if (text.TrimStart().StartsWith("[") || text.TrimStart().StartsWith("{"))
                        value = JToken.Parse(text);
                }

                switch (material.shader.GetPropertyType(index))
                {
                    case UnityEngine.Rendering.ShaderPropertyType.Color:
                        // Retain the accepted two-component shorthand's original Vector2 setter semantics.
                        if (value is JArray colorArray && colorArray.Count == 2)
                        {
                            Vector2 pair = colorArray.ToObject<Vector2>(serializer);
                            apply = () => material.SetVector(propertyName, pair);
                            return true;
                        }
                        if (value is not JArray && value is not JObject) return false;
                        Color color = ParseColor(value, serializer);
                        apply = () => material.SetColor(propertyName, color);
                        return true;
                    case UnityEngine.Rendering.ShaderPropertyType.Vector:
                        Vector4 vector;
                        if (value is JArray array)
                        {
                            if (array.Count == 2) vector = array.ToObject<Vector2>(serializer);
                            else if (array.Count == 3) vector = array.ToObject<Vector3>(serializer);
                            else if (array.Count == 4) vector = array.ToObject<Vector4>(serializer);
                            else return false;
                        }
                        else if (value is JObject obj)
                        {
                            if (obj["w"] != null) vector = obj.ToObject<Vector4>(serializer);
                            else if (obj["z"] != null) vector = obj.ToObject<Vector3>(serializer);
                            else vector = obj.ToObject<Vector2>(serializer);
                        }
                        else return false;
                        apply = () => material.SetVector(propertyName, vector);
                        return true;
                    case UnityEngine.Rendering.ShaderPropertyType.Float:
                    case UnityEngine.Rendering.ShaderPropertyType.Range:
                        if (value.Type != JTokenType.Float && value.Type != JTokenType.Integer && value.Type != JTokenType.Boolean)
                            return false;
                        float number = value.Type == JTokenType.Boolean ? (value.ToObject<bool>(serializer) ? 1f : 0f) : value.ToObject<float>(serializer);
                        apply = () => material.SetFloat(propertyName, number);
                        return true;
                    case UnityEngine.Rendering.ShaderPropertyType.Int:
                        if (value.Type != JTokenType.Float && value.Type != JTokenType.Integer && value.Type != JTokenType.Boolean)
                            return false;
                        decimal integer = value.Type == JTokenType.Boolean ? (value.ToObject<bool>(serializer) ? 1m : 0m) : value.ToObject<decimal>(serializer);
                        if (integer != decimal.Truncate(integer) || integer < int.MinValue || integer > int.MaxValue)
                            return false;
                        int count = (int)integer;
                        apply = () => material.SetInteger(propertyName, count);
                        return true;
                    case UnityEngine.Rendering.ShaderPropertyType.Texture:
                        Texture texture = null;
                        if (value.Type == JTokenType.String)
                        {
                            string path = value.ToString();
                            if (!string.IsNullOrEmpty(path) && path.Contains("/"))
                                texture = AssetDatabase.LoadAssetAtPath<Texture>(AssetPathUtility.SanitizeAssetPath(path));
                        }
                        else if (value is JObject)
                            texture = value.ToObject<Texture>(serializer);
                        if (texture == null) return false;
                        apply = () => material.SetTexture(propertyName, texture);
                        return true;
                    default:
                        return false;
                }
            }
            catch (Exception ex)
            {
                McpLog.Warn($"[MaterialOps] Failed to convert property '{propertyName}': {ex.Message}");
                return false;
            }
        }

        /// <summary>
        /// Helper to parse color from JToken (array or object).
        /// </summary>
        public static Color ParseColor(JToken token, JsonSerializer serializer)
        {
            if (token.Type == JTokenType.String)
            {
                string s = token.ToString();
                if (s.TrimStart().StartsWith("[") || s.TrimStart().StartsWith("{"))
                {
                    try
                    {
                        return ParseColor(JToken.Parse(s), serializer);
                    }
                    catch { }
                }
            }

            if (token is JArray jArray)
            {
                if (jArray.Count == 4)
                {
                    return new Color(
                        (float)jArray[0],
                        (float)jArray[1],
                        (float)jArray[2],
                        (float)jArray[3]
                    );
                }
                else if (jArray.Count == 3)
                {
                    return new Color(
                        (float)jArray[0],
                        (float)jArray[1],
                        (float)jArray[2],
                        1f
                    );
                }
                else
                {
                    throw new ArgumentException("Color array must have 3 or 4 elements.");
                }
            }

            try
            {
                return token.ToObject<Color>(serializer);
            }
            catch (Exception ex)
            {
                McpLog.Warn($"[MaterialOps] Failed to parse color from token: {ex.Message}");
                throw;
            }
        }
    }
}
