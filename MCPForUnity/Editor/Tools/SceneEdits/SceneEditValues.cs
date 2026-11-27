using System;
using System.Collections.Generic;
using System.Linq;
using System.Text;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEngine;

namespace MCPForUnity.Editor.Tools.SceneEdits
{
    // Only native fields with bounded value codecs belong in this allowlist. Never call
    // reflection setters or custom MonoBehaviour serialization callbacks from this tool.
    internal static class SceneEditValues
    {
        private static readonly Dictionary<Type, string[]> Paths = new Dictionary<Type, string[]>
        {
            [typeof(Transform)] = new[] { "m_LocalPosition", "m_LocalRotation", "m_LocalScale" },
#if MCP_FOR_UNITY_PHYSICS
            [typeof(BoxCollider)] = new[] { "m_Center", "m_Size", "m_IsTrigger", "m_Enabled" },
            [typeof(SphereCollider)] = new[] { "m_Center", "m_Radius", "m_IsTrigger", "m_Enabled" },
            [typeof(CapsuleCollider)] = new[] { "m_Center", "m_Radius", "m_Height", "m_IsTrigger", "m_Enabled" },
            [typeof(Rigidbody)] = new[] { "m_Mass", "m_UseGravity", "m_IsKinematic" },
#endif
        };

        internal static readonly Dictionary<string, Type> Types = Paths.Keys.ToDictionary(type => type.Name, StringComparer.Ordinal);

        internal static string ResolvePath(SerializedObject serialized, string requested)
        {
            if (requested.Length > 128)
                throw new ArgumentException("property must be at most 128 characters.");
            Type type = serialized.targetObject.GetType();
            string[] paths = Paths[type];
            if (Array.IndexOf(paths, requested) >= 0)
                return requested;
            var names = new List<(string path, string label)>();
            foreach (string path in paths)
            {
                using var property = serialized.FindProperty(path);
                if (property == null || !property.editable)
                    continue;
                names.Add((path, property.propertyPath));
                names.Add((path, property.displayName));
                names.Add((path, ObjectNames.NicifyVariableName(property.name)));
            }
            return MatchPropertyLabel(requested, names)
                ?? throw new ArgumentException(
                    $"Property '{requested}' is outside the scene-edit allowlist for {type.Name}. Supported paths: {string.Join(", ", paths)}."
                );
        }

        internal static string MatchPropertyLabel(string requested, IEnumerable<(string path, string label)> names)
        {
            string key = LabelKey(requested);
            string match = null;
            foreach (var (path, label) in names)
            {
                if (!string.Equals(key, LabelKey(label), StringComparison.OrdinalIgnoreCase))
                    continue;
                if (match != null && match != path)
                    throw new ArgumentException($"Ambiguous serialized property '{requested}'; specify an exact supported path.");
                match = path;
            }
            return match;
        }

        private static string LabelKey(string label) => label.Normalize(NormalizationForm.FormC).Replace(" ", "");

        internal static JToken Read(SerializedProperty property)
        {
            if (property == null)
                throw new InvalidOperationException("The serialized property no longer exists.");
            return property.propertyType switch
            {
                SerializedPropertyType.Boolean => new JValue(property.boolValue),
                SerializedPropertyType.Float => new JValue(property.floatValue),
                SerializedPropertyType.Vector3 => Vector(property.vector3Value),
                SerializedPropertyType.Quaternion => Rotation(property.quaternionValue),
                _ => throw new ArgumentException("Unsupported serialized property type."),
            };
        }

        internal static JToken Normalize(SerializedProperty property, JToken value)
        {
            if (value == null)
                throw new ArgumentException("Each edit requires a value.");
            switch (property.propertyType)
            {
                case SerializedPropertyType.Boolean:
                    if (value.Type != JTokenType.Boolean)
                        throw new ArgumentException("Boolean properties require a JSON boolean.");
                    return new JValue(value.Value<bool>());
                case SerializedPropertyType.Float:
                    float scalar = Number(value);
                    if (property.propertyPath == "m_Mass" && scalar <= 0)
                        throw new ArgumentException("Mass must be greater than zero.");
                    if ((property.propertyPath == "m_Radius" || property.propertyPath == "m_Height") && scalar < 0)
                        throw new ArgumentException("Radius and height must be nonnegative.");
                    return new JValue(scalar);
                case SerializedPropertyType.Vector3:
                    float[] vector = Numbers(value, 3);
                    if (property.propertyPath == "m_Size" && (vector[0] < 0 || vector[1] < 0 || vector[2] < 0))
                        throw new ArgumentException("Collider size must be nonnegative.");
                    return new JArray(vector[0], vector[1], vector[2]);
                case SerializedPropertyType.Quaternion:
                    float[] rotation = Numbers(value, 4);
                    double norm =
                        (double)rotation[0] * rotation[0]
                        + (double)rotation[1] * rotation[1]
                        + (double)rotation[2] * rotation[2]
                        + (double)rotation[3] * rotation[3];
                    if (Math.Abs(norm - 1) > 0.00001)
                        throw new ArgumentException("localRotation requires a unit quaternion [x, y, z, w].");
                    return new JArray(rotation[0], rotation[1], rotation[2], rotation[3]);
                default:
                    throw new ArgumentException("Unsupported serialized property type.");
            }
        }

        internal static void Write(SerializedProperty property, JToken value)
        {
            switch (property.propertyType)
            {
                case SerializedPropertyType.Boolean:
                    property.boolValue = value.Value<bool>();
                    break;
                case SerializedPropertyType.Float:
                    property.floatValue = value.Value<float>();
                    break;
                case SerializedPropertyType.Vector3:
                    property.vector3Value = new Vector3(value[0].Value<float>(), value[1].Value<float>(), value[2].Value<float>());
                    break;
                case SerializedPropertyType.Quaternion:
                    property.quaternionValue = new Quaternion(
                        value[0].Value<float>(),
                        value[1].Value<float>(),
                        value[2].Value<float>(),
                        value[3].Value<float>()
                    );
                    break;
                default:
                    throw new InvalidOperationException("The serialized property type changed after validation.");
            }
        }

        private static float[] Numbers(JToken value, int count)
        {
            if (!(value is JArray array) || array.Count != count)
                throw new ArgumentException($"Value must be an array of exactly {count} finite numbers.");
            var result = new float[count];
            for (int i = 0; i < count; i++)
                result[i] = Number(array[i]);
            return result;
        }

        private static float Number(JToken value)
        {
            if (value.Type != JTokenType.Float && value.Type != JTokenType.Integer)
                throw new ArgumentException("Numeric properties require finite JSON numbers.");
            float number = value.Value<float>();
            if (float.IsNaN(number) || float.IsInfinity(number))
                throw new ArgumentException("Numeric properties require finite float values.");
            return number;
        }

        private static JArray Vector(Vector3 value) => new JArray(value.x, value.y, value.z);

        private static JArray Rotation(Quaternion value) => new JArray(value.x, value.y, value.z, value.w);
    }
}
