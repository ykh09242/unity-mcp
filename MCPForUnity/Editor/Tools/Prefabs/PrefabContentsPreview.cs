using System;
using System.Collections.Generic;
using System.Linq;
using System.Text;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEngine;
using Object = UnityEngine.Object;

namespace MCPForUnity.Editor.Tools.Prefabs
{
    /// <summary>Bounded serialized-field comparison before SaveAsPrefabAsset. Does not predict save callbacks.</summary>
    internal static class PrefabContentsPreview
    {
        private const int MaxObjects = 2048;
        private const int MaxProperties = 20000;
        private const int MaxSnapshotBytes = 4 * 1024 * 1024;
        private const int MaxChanges = 200;
        private const int MaxReportBytes = 65536;

        internal sealed class Snapshot
        {
            internal readonly SortedDictionary<string, JToken> Values = new(StringComparer.Ordinal);
            internal int Unsupported;
        }

        internal static Snapshot Capture(GameObject root)
        {
            var objects = new Dictionary<Object, string>();
            var pending = new Stack<(Transform transform, string path)>();
            pending.Push((root.transform, "$"));
            void Add(Object target, string path)
            {
                if (objects.Count == MaxObjects || path.Length > 2048)
                    throw new ArgumentException("Prefab preview object/path limit exceeded; narrow the prefab before previewing.");
                if (target == null)
                    throw new ArgumentException("Prefab preview cannot inspect missing scripts.");
                objects.Add(target, path);
            }
            while (pending.Count > 0)
            {
                var item = pending.Pop();
                Add(item.transform.gameObject, item.path + "/GameObject");
                var occurrences = new Dictionary<Type, int>();
                foreach (Component component in item.transform.GetComponents<Component>())
                {
                    if (component == null)
                        throw new ArgumentException("Prefab preview cannot inspect missing scripts.");
                    Type type = component.GetType();
                    occurrences.TryGetValue(type, out int occurrence);
                    occurrences[type] = occurrence + 1;
                    Add(component, item.path + "/" + type.FullName + "[" + occurrence + "]");
                }
                for (int i = item.transform.childCount - 1; i >= 0; i--)
                {
                    if (objects.Count + pending.Count >= MaxObjects)
                        throw new ArgumentException("Prefab preview object limit exceeded.");
                    pending.Push((item.transform.GetChild(i), item.path + "/" + i));
                }
            }

            var result = new Snapshot();
            int properties = 0,
                bytes = 0;
            foreach (var item in objects)
            {
                using var serialized = new SerializedObject(item.Key);
                using var property = serialized.GetIterator();
                bool enter = true;
                while (property.Next(enter))
                {
                    if (++properties > MaxProperties)
                        throw new ArgumentException("Prefab preview serialized-property limit exceeded.");
                    string path = property.propertyPath;
                    enter = false;
                    // Unity's load-context bookkeeping is not authored prefab content.
                    if (path == "m_ObjectHideFlags" || path == "m_CorrespondingSourceObject" || path == "m_PrefabInstance" || path == "m_PrefabAsset")
                        continue;
                    if (property.propertyType == SerializedPropertyType.Generic)
                    {
                        enter = true;
                        continue;
                    }
                    JToken value = Value(property, objects);
                    if (value == null)
                    {
                        result.Unsupported++;
                        continue;
                    }
                    string key = item.Value + ":" + path;
                    bytes += Encoding.UTF8.GetByteCount(key) + Encoding.UTF8.GetByteCount(value.ToString(Formatting.None));
                    if (bytes > MaxSnapshotBytes)
                        throw new ArgumentException("Prefab preview snapshot byte limit exceeded.");
                    result.Values.Add(key, value);
                }
            }
            return result;
        }

        private static JToken Value(SerializedProperty property, Dictionary<Object, string> objects)
        {
            switch (property.propertyType)
            {
                case SerializedPropertyType.Integer:
                    return new JValue(property.longValue);
                case SerializedPropertyType.ArraySize:
                case SerializedPropertyType.Character:
                case SerializedPropertyType.LayerMask:
                case SerializedPropertyType.Enum:
                    return new JValue(property.intValue);
                case SerializedPropertyType.Boolean:
                    return new JValue(property.boolValue);
                case SerializedPropertyType.Float:
                    return Number(property.doubleValue);
                case SerializedPropertyType.String:
                    string value = property.stringValue ?? "";
                    if (value.Length > 4096)
                        return null;
                    return new JValue(value);
                case SerializedPropertyType.ObjectReference:
                    Object reference = property.objectReferenceValue;
                    if (reference == null)
                    {
                        // A missing reference is not equivalent to an explicitly empty field.
#if UNITY_6000_4_OR_NEWER
                        return EntityId.ToULong(property.objectReferenceEntityIdValue) == 0 ? JValue.CreateNull() : null;
#else
                        return property.objectReferenceInstanceIDValue == 0 ? JValue.CreateNull() : null;
#endif
                    }
                    if (objects.TryGetValue(reference, out string local))
                        return new JObject { ["prefab_object"] = local };
                    if (AssetDatabase.TryGetGUIDAndLocalFileIdentifier(reference, out string guid, out long id))
                        return new JObject { ["guid"] = guid, ["file_id"] = id };
                    return null;
                case SerializedPropertyType.Vector2:
                    Vector2 v2 = property.vector2Value;
                    return Numbers(v2.x, v2.y);
                case SerializedPropertyType.Vector3:
                    Vector3 v3 = property.vector3Value;
                    return Numbers(v3.x, v3.y, v3.z);
                case SerializedPropertyType.Vector4:
                    Vector4 v4 = property.vector4Value;
                    return Numbers(v4.x, v4.y, v4.z, v4.w);
                case SerializedPropertyType.Quaternion:
                    Quaternion q = property.quaternionValue;
                    return Numbers(q.x, q.y, q.z, q.w);
                case SerializedPropertyType.Color:
                    Color c = property.colorValue;
                    return Numbers(c.r, c.g, c.b, c.a);
                case SerializedPropertyType.Rect:
                    Rect r = property.rectValue;
                    return Numbers(r.x, r.y, r.width, r.height);
                case SerializedPropertyType.Bounds:
                    Bounds b = property.boundsValue;
                    return new JObject { ["center"] = Numbers(b.center.x, b.center.y, b.center.z), ["size"] = Numbers(b.size.x, b.size.y, b.size.z) };
                default:
                    return null;
            }
        }

        private static JToken Number(double value) =>
            double.IsNaN(value) || double.IsInfinity(value)
                ? new JObject { ["nonfinite"] = value.ToString(System.Globalization.CultureInfo.InvariantCulture) }
                : new JValue(value);

        private static JArray Numbers(params float[] values) => new JArray(values.Select(value => Number(value)));

        internal static JObject Compare(Snapshot before, Snapshot after)
        {
            var changes = new JArray();
            int count = 0,
                bytes = 0;
            foreach (string path in before.Values.Keys.Union(after.Values.Keys).OrderBy(path => path, StringComparer.Ordinal))
            {
                bool existed = before.Values.TryGetValue(path, out JToken oldValue);
                bool exists = after.Values.TryGetValue(path, out JToken newValue);
                if (existed == exists && JToken.DeepEquals(oldValue, newValue))
                    continue;
                count++;
                var change = new JObject
                {
                    ["path"] = path,
                    ["kind"] =
                        !existed ? "added"
                        : !exists ? "removed"
                        : "changed",
                    ["before"] = oldValue?.DeepClone(),
                    ["after"] = newValue?.DeepClone(),
                };
                int size = Encoding.UTF8.GetByteCount(change.ToString(Formatting.None));
                if (changes.Count == MaxChanges || bytes + size > MaxReportBytes)
                    continue;
                bytes += size;
                changes.Add(change);
            }
            return new JObject
            {
                ["changes"] = changes,
                ["change_count"] = count,
                ["truncated"] = changes.Count != count,
                ["unsupported_before"] = before.Unsupported,
                ["unsupported_after"] = after.Unsupported,
                ["complete"] = before.Unsupported == 0 && after.Unsupported == 0 && changes.Count == count,
                ["save_callbacks_observed"] = false,
                ["basis"] = "source_asset_to_modified_contents_before_save",
            };
        }
    }
}
