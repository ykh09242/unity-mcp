using System;
using System.Collections.Generic;
using System.Linq;
using System.Text;
using MCPForUnity.Editor.Helpers;
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

        internal sealed class LimitExceededException : ArgumentException
        {
            internal readonly string Scope;
            internal readonly string Kind;
            internal readonly int Limit;
            internal readonly int Observed;

            internal LimitExceededException(string scope, string kind, int limit, int observed)
                : base($"Prefab preview {kind} limit exceeded ({observed} > {limit}).")
            {
                Scope = scope;
                Kind = kind;
                Limit = limit;
                Observed = observed;
            }
        }

        internal sealed class Snapshot
        {
            internal readonly SortedDictionary<string, JToken> Values = new(StringComparer.Ordinal);
            internal string Scope;
            internal int Unsupported;
            internal int Objects;
            internal int Properties;
            internal int Bytes;
        }

        internal static string GetScope(JObject parameters, bool previewOnly)
        {
            JToken token = parameters["previewScope"];
            if (token == null)
                return "prefab";
            if (!previewOnly || token.Type != JTokenType.String || (token.ToString() != "prefab" && token.ToString() != "components"))
                throw new ArgumentException("preview_scope must be prefab or components and requires preview_only=true.");
            string scope = token.ToString();
            if (scope == "components" && !IsComponentOnly(parameters))
                throw new ArgumentException("preview_scope=components requires nonempty component_properties and accepts only component-property edits.");
            return scope;
        }

        private static bool IsComponentOnly(JObject parameters)
        {
            if (parameters["componentProperties"] is not JObject properties || properties.Count == 0)
                return false;
            return !new[]
            {
                "name",
                "tag",
                "layer",
                "position",
                "rotation",
                "scale",
                "setActive",
                "parent",
                "componentsToAdd",
                "componentsToRemove",
                "createChild",
                "deleteChild",
            }.Any(key => parameters[key] != null && parameters[key].Type != JTokenType.Null);
        }

        internal static ErrorResponse LimitResponse(LimitExceededException error, JObject parameters)
        {
            var followUp = new JObject
            {
                ["action"] = "narrow_preview_scope",
                ["message"] = "Inspect a smaller component-property selection. Full-preview hierarchy edits require a prefab within the reported limit.",
            };
            if (error.Scope == "prefab" && IsComponentOnly(parameters))
            {
                var arguments = new JObject
                {
                    ["action"] = "modify_contents",
                    ["prefab_path"] = parameters["prefabPath"] ?? parameters["path"],
                    ["component_properties"] = parameters["componentProperties"].DeepClone(),
                    ["preview_only"] = true,
                    ["preview_scope"] = "components",
                };
                if (parameters["target"] != null)
                    arguments["target"] = parameters["target"].DeepClone();
                if (Encoding.UTF8.GetByteCount(arguments.ToString(Formatting.None)) <= 8192)
                    followUp["next_call"] = new JObject { ["tool"] = "prefab_contents", ["arguments"] = arguments };
            }
            return new ErrorResponse(
                "prefab_preview_limit_exceeded",
                new JObject
                {
                    ["message"] = error.Message,
                    ["scope"] = error.Scope,
                    ["limit_kind"] = error.Kind,
                    ["limit"] = error.Limit,
                    ["observed"] = error.Observed,
                    ["follow_up"] = followUp,
                }
            );
        }

        internal static Snapshot Capture(GameObject root)
        {
            var objects = new Dictionary<Object, string>();
            var pending = new Stack<(Transform transform, string path)>();
            pending.Push((root.transform, "$"));
            void Add(Object target, string path)
            {
                if (objects.Count == MaxObjects)
                    throw new LimitExceededException("prefab", "objects", MaxObjects, objects.Count + 1);
                if (path.Length > 2048)
                    throw new LimitExceededException("prefab", "path_characters", 2048, path.Length);
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
                        throw new LimitExceededException("prefab", "objects", MaxObjects, objects.Count + pending.Count + 1);
                    pending.Push((item.transform.GetChild(i), item.path + "/" + i));
                }
            }

            return CaptureObjects(root, objects, "prefab");
        }

        internal static Snapshot CaptureComponents(GameObject root, GameObject target, JObject properties)
        {
            var objects = new Dictionary<Object, string>();
            foreach (JProperty property in properties.Properties())
            {
                if (!ComponentResolver.TryResolve(property.Name, out Type type, out string error))
                    throw new ArgumentException(error);
                Component[] matches = target.GetComponents(type);
                if (matches.Length != 1)
                    throw new ArgumentException(
                        $"preview_scope=components requires exactly one '{property.Name}' component on the target; found {matches.Length}."
                    );
                Component component = matches[0];
                if (!objects.ContainsKey(component))
                {
                    if (objects.Count == MaxObjects)
                        throw new LimitExceededException("components", "objects", MaxObjects, objects.Count + 1);
                    objects.Add(component, LocalPath(component, root));
                }
            }
            return CaptureObjects(root, objects, "components");
        }

        private static string LocalPath(Object reference, GameObject root)
        {
            GameObject gameObject = reference as GameObject;
            if (reference is Component component)
                gameObject = component.gameObject;
            if (gameObject == null || !gameObject.transform.IsChildOf(root.transform))
                return null;
            var indices = new Stack<int>();
            for (Transform current = gameObject.transform; current != root.transform; current = current.parent)
            {
                if (indices.Count == 128)
                    throw new LimitExceededException("components", "reference_depth", 128, indices.Count + 1);
                indices.Push(current.GetSiblingIndex());
            }
            string path = "$";
            foreach (int index in indices)
                path += "/" + index;
            if (reference is Component selected)
            {
                int occurrence = 0;
                foreach (Component candidate in gameObject.GetComponents(selected.GetType()))
                {
                    if (candidate == selected)
                        break;
                    if (candidate.GetType() == selected.GetType())
                        occurrence++;
                }
                path += "/" + selected.GetType().FullName + "[" + occurrence + "]";
            }
            else
                path += "/GameObject";
            if (path.Length > 2048)
                throw new LimitExceededException("components", "path_characters", 2048, path.Length);
            return path;
        }

        private static Snapshot CaptureObjects(GameObject root, Dictionary<Object, string> objects, string scope)
        {
            var result = new Snapshot { Scope = scope, Objects = objects.Count };
            foreach (var item in objects)
            {
                using var serialized = new SerializedObject(item.Key);
                using var property = serialized.GetIterator();
                bool enter = true;
                while (property.Next(enter))
                {
                    if (++result.Properties > MaxProperties)
                        throw new LimitExceededException(scope, "properties", MaxProperties, result.Properties);
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
                    JToken value = Value(property, objects, root);
                    if (value == null)
                    {
                        result.Unsupported++;
                        continue;
                    }
                    string key = item.Value + ":" + path;
                    result.Bytes += Encoding.UTF8.GetByteCount(key) + Encoding.UTF8.GetByteCount(value.ToString(Formatting.None));
                    if (result.Bytes > MaxSnapshotBytes)
                        throw new LimitExceededException(scope, "snapshot_bytes", MaxSnapshotBytes, result.Bytes);
                    result.Values.Add(key, value);
                }
            }
            return result;
        }

        private static JToken Value(SerializedProperty property, Dictionary<Object, string> objects, GameObject root)
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
                    local = LocalPath(reference, root);
                    if (local != null)
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
            bool complete = before.Unsupported == 0 && after.Unsupported == 0 && changes.Count == count;
            return new JObject
            {
                ["scope"] = before.Scope,
                ["observed"] = new JObject
                {
                    ["before"] = new JObject
                    {
                        ["objects"] = before.Objects,
                        ["properties"] = before.Properties,
                        ["snapshot_bytes"] = before.Bytes,
                    },
                    ["after"] = new JObject
                    {
                        ["objects"] = after.Objects,
                        ["properties"] = after.Properties,
                        ["snapshot_bytes"] = after.Bytes,
                    },
                },
                ["changes"] = changes,
                ["change_count"] = count,
                ["truncated"] = changes.Count != count,
                ["unsupported_before"] = before.Unsupported,
                ["unsupported_after"] = after.Unsupported,
                ["complete"] = complete,
                ["whole_prefab_complete"] = before.Scope == "prefab" && complete,
                ["save_callbacks_observed"] = false,
                ["basis"] = "source_asset_to_modified_contents_before_save",
            };
        }
    }
}
