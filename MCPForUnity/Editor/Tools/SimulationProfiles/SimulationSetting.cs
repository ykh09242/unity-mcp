using System;
using System.Linq;
using System.Reflection;
using MCPForUnity.Runtime.Helpers;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEngine;

namespace MCPForUnity.Editor.Tools.SimulationProfiles
{
    internal sealed class SimulationSetting
    {
        internal readonly string Key;
        internal readonly string Domain;
        private readonly Type ownerType;
        private readonly Func<object> owner;
        private readonly string propertyName;
        private readonly string unavailable;
        private readonly double minimum;
        private readonly double maximum;

        internal SimulationSetting(string key, Type type, string property, double min, double max, Func<object> instance = null, string reason = null)
        {
            Key = key;
            Domain = key.Split('.')[0];
            ownerType = type;
            propertyName = property;
            minimum = min;
            maximum = max;
            owner = instance;
            unavailable = reason;
        }

        private PropertyInfo Property =>
            ownerType?.GetProperty(propertyName, BindingFlags.Public | (owner == null ? BindingFlags.Static : BindingFlags.Instance));

        internal string Availability()
        {
            if (unavailable != null)
                return unavailable;
            PropertyInfo property = Property;
            if (property == null || !property.CanRead || !property.CanWrite)
                return "Public setting is unavailable in this installed module/version.";
            if (owner != null && owner() == null)
                return "Input System settings instance is unavailable.";
            return null;
        }

        internal JToken Normalize(JToken value)
        {
            Type type = Property?.PropertyType;
            if (type == typeof(bool))
            {
                if (value?.Type != JTokenType.Boolean)
                    throw new ArgumentException(Key + " must be boolean.");
                return new JValue(value.Value<bool>());
            }
            if (type == typeof(Vector2) || type == typeof(Vector3))
            {
                int size = type == typeof(Vector2) ? 2 : 3;
                if (!(value is JArray a) || a.Count != size)
                    throw new ArgumentException(Key + " requires " + size + " finite components.");
                return new JArray(a.Select(Number));
            }
            if (type == typeof(int))
            {
                if (value?.Type != JTokenType.Integer || !long.TryParse(value.ToString(), out long integer) || integer < minimum || integer > maximum)
                    throw new ArgumentException(Key + " must be an integer in the supported range.");
                return new JValue((int)integer);
            }
            if (type == typeof(float))
                return Number(value);
            throw new InvalidOperationException(Key + " has an unsupported public property type.");
        }

        private JValue Number(JToken value)
        {
            if (value == null || (value.Type != JTokenType.Integer && value.Type != JTokenType.Float))
                throw new ArgumentException(Key + " requires a finite number.");
            double number = value.Value<double>();
            if (double.IsNaN(number) || double.IsInfinity(number) || number < minimum || number > maximum)
                throw new ArgumentException(Key + " is outside the supported finite range [" + minimum + ", " + maximum + "].");
            return new JValue((float)number);
        }

        internal JToken Read()
        {
            string reason = Availability();
            if (reason != null)
                throw new InvalidOperationException(Key + ": " + reason);
            object value = Property.GetValue(owner?.Invoke());
            if (value is Vector3 v3)
                return Normalize(new JArray(v3.x, v3.y, v3.z));
            if (value is Vector2 v2)
                return Normalize(new JArray(v2.x, v2.y));
            return Normalize(JToken.FromObject(value));
        }

        internal void Write(JToken value)
        {
            string reason = Availability();
            if (reason != null)
                throw new InvalidOperationException(Key + ": " + reason);
            JToken normalized = Normalize(value);
            Type type = Property.PropertyType;
            object converted;
            if (type == typeof(Vector3))
                converted = new Vector3(normalized[0].Value<float>(), normalized[1].Value<float>(), normalized[2].Value<float>());
            else if (type == typeof(Vector2))
                converted = new Vector2(normalized[0].Value<float>(), normalized[1].Value<float>());
            else
                converted = normalized.ToObject(type);
            Property.SetValue(owner?.Invoke(), converted);
        }

        internal string OwnerIdentity()
        {
            if (owner == null)
                return "static:" + ownerType.FullName;
            object instance = owner();
            if (!(instance is UnityEngine.Object asset))
                throw new InvalidOperationException(Key + " owner identity is unavailable.");
            string path = AssetDatabase.GetAssetPath(asset);
            string guid = string.IsNullOrEmpty(path) ? null : AssetDatabase.AssetPathToGUID(path);
            return string.IsNullOrEmpty(guid) ? "session:" + asset.GetInstanceIDCompat() : "asset:" + guid;
        }

        internal bool Equal(JToken a, JToken b) => JToken.DeepEquals(Normalize(a), Normalize(b));
    }

    internal static class SimulationInputSettings
    {
        internal static Type Find(string name) => UnityAssembliesCompat.GetLoadedAssemblies().Select(a => a.GetType(name)).FirstOrDefault(t => t != null);

        internal static SimulationSetting[] Create()
        {
#if ENABLE_LEGACY_INPUT_MANAGER
            const string legacyReason = null;
#else
            const string legacyReason = "Legacy input backend is not enabled (ENABLE_LEGACY_INPUT_MANAGER is absent).";
#endif
#if ENABLE_INPUT_SYSTEM
            const string systemReason = null;
#else
            const string systemReason = "Input System backend is not enabled (ENABLE_INPUT_SYSTEM is absent).";
#endif
            Type legacy = Find("UnityEngine.Input");
            Type system = Find("UnityEngine.InputSystem.InputSystem");
            Type settings = Find("UnityEngine.InputSystem.InputSettings");
            Func<object> instance = () => system?.GetProperty("settings", BindingFlags.Public | BindingFlags.Static)?.GetValue(null);
            return new[]
            {
                new SimulationSetting("input.legacy.simulateMouseWithTouches", legacy, "simulateMouseWithTouches", 0, 1, reason: legacyReason),
                new SimulationSetting("input.legacy.multiTouchEnabled", legacy, "multiTouchEnabled", 0, 1, reason: legacyReason),
                new SimulationSetting("input.system.defaultButtonPressPoint", settings, "defaultButtonPressPoint", 0.000001, 1, instance, systemReason),
                new SimulationSetting("input.system.defaultDeadzoneMin", settings, "defaultDeadzoneMin", 0, 1, instance, systemReason),
                new SimulationSetting("input.system.defaultDeadzoneMax", settings, "defaultDeadzoneMax", 0, 1, instance, systemReason),
            };
        }
    }
}
