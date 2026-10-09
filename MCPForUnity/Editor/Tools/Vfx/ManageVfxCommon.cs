using MCPForUnity.Editor.Helpers;
using Newtonsoft.Json.Linq;
using UnityEngine;

namespace MCPForUnity.Editor.Tools.Vfx
{
    internal static class ManageVfxCommon
    {
        public static Color ParseColor(JToken token) => VectorParsing.ParseColorOrDefault(token);

        public static Vector3 ParseVector3(JToken token) => VectorParsing.ParseVector3OrDefault(token);

        public static Vector4 ParseVector4(JToken token) => VectorParsing.ParseVector4OrDefault(token);

        public static Gradient ParseGradient(JToken token) => VectorParsing.ParseGradientOrDefault(token);

        public static AnimationCurve ParseAnimationCurve(JToken token, float defaultValue = 1f) =>
            VectorParsing.ParseAnimationCurveOrDefault(token, defaultValue);

        public static GameObject FindTargetGameObject(JObject @params) =>
            ObjectResolver.ResolveGameObject(@params["target"], @params["searchMethod"]?.ToString());

        public static Material FindMaterialByPath(string path) => ObjectResolver.ResolveMaterial(path);

        public static T FindComponent<T>(JObject @params)
            where T : Component
        {
            return FindComponentCore<T>(@params, out _, out _, out _);
        }

        public static T FindComponent<T>(JObject @params, out string error)
            where T : Component
        {
            var component = FindComponentCore<T>(@params, out var go, out var idx, out var count);
            error = null;
            if (component == null)
            {
                string typeName = typeof(T).Name;
                error =
                    go != null && idx.HasValue && (idx.Value < 0 || idx.Value >= count)
                        ? $"component_index {idx.Value} out of range. Found {count} {typeName} component(s) on '{go.name}'."
                        : $"{typeName} not found";
            }
            return component;
        }

        private static T FindComponentCore<T>(JObject @params, out GameObject go, out int? idx, out int count)
            where T : Component
        {
            idx = null;
            count = 0;
            go = FindTargetGameObject(@params);
            if (go == null)
                return null;
            idx = ParamCoercion.CoerceIntNullable(@params["componentIndex"] ?? @params["component_index"]);
            if (idx.HasValue)
            {
                var all = go.GetComponents<T>();
                count = all.Length;
                return (idx.Value >= 0 && idx.Value < all.Length) ? all[idx.Value] : null;
            }
            return go.GetComponent<T>();
        }

        public static string FindComponentError<T>(JObject @params)
            where T : Component
        {
            string typeName = typeof(T).Name;
            GameObject go = FindTargetGameObject(@params);
            if (go == null)
                return $"{typeName} not found";
            int? idx = ParamCoercion.CoerceIntNullable(@params["componentIndex"] ?? @params["component_index"]);
            if (idx.HasValue)
            {
                int count = go.GetComponents<T>().Length;
                if (idx.Value < 0 || idx.Value >= count)
                    return $"component_index {idx.Value} out of range. Found {count} {typeName} component(s) on '{go.name}'.";
            }
            return $"{typeName} not found";
        }
    }
}
