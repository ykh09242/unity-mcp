using System;
using System.Globalization;
using System.Linq;
using System.Text;
using System.Threading;
using MCPForUnity.Runtime.Helpers;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEngine;
using Object = UnityEngine.Object;

namespace MCPForUnity.Editor.Helpers
{
    /// <summary>Bounded managed projection of a single native serialized instance. Native accessors may perform unbounded synchronous work.</summary>
    internal static class SerializedObjectInspection
    {
        internal const int MaxWork = 2048;
        internal const int MaxOutputBytes = 65536;
        private const int RecordBytesLimit = MaxOutputBytes - 12288;

        // Observes completed real operations; never substitutes a native operation.
        internal static Action<string, string> OperationForTests = null;

        internal sealed class Failure : Exception
        {
            internal readonly string Code;

            internal Failure(string code, string message)
                : base(message)
            {
                Code = code;
            }

            internal ErrorResponse Response() => new ErrorResponse(Code, new { reason = Code, message = Message });
        }

        private sealed class WorkLimit : Exception { }

        internal sealed class Options
        {
            internal int Id,
                PageSize = 32,
                Start;
            internal string Mode = "fields",
                Path = "",
                After;
            internal bool Values = true;

            internal static Options Parse(JObject input)
            {
                var names = new[] { "instance_id", "mode", "property_path", "page_size", "after_path", "start_index", "include_values" };
                if (input == null || input.Properties().Any(p => !names.Contains(p.Name)))
                    throw Invalid("Use only the exact inspector parameter names.");
                var result = new Options { Id = Integer(input["instance_id"], int.MinValue, int.MaxValue) };
                if (result.Id == 0)
                    throw Invalid("instance_id must be nonzero.");
                if (input["mode"] != null)
                    result.Mode = Text(input["mode"]);
                if (result.Mode != "fields" && result.Mode != "value" && result.Mode != "array")
                    throw Invalid("Unknown projection mode.");
                if (input["property_path"] != null)
                    result.Path = Text(input["property_path"]);
                ValidatePath(result.Path, result.Mode == "fields");
                if (input["include_values"] != null)
                {
                    if (input["include_values"].Type != JTokenType.Boolean)
                        throw Invalid("include_values must be a boolean.");
                    result.Values = (bool)input["include_values"];
                }
                if (input["page_size"] != null)
                {
                    if (result.Mode == "value")
                        throw Invalid("page_size does not apply to value.");
                    result.PageSize = Integer(input["page_size"], 1, 64);
                }
                if (input["after_path"] != null)
                {
                    if (result.Mode != "fields")
                        throw Invalid("after_path applies only to fields.");
                    result.After = Text(input["after_path"]);
                    ValidatePath(result.After, false);
                }
                if (input["start_index"] != null)
                {
                    if (result.Mode != "array")
                        throw Invalid("start_index applies only to array.");
                    result.Start = Integer(input["start_index"], 0, int.MaxValue);
                }
                return result;
            }
        }

        private static Failure Invalid(string message) => new Failure("invalid_parameters", message);

        private static string Text(JToken value)
        {
            if (value.Type != JTokenType.String)
                throw Invalid("Expected a string parameter.");
            return (string)value;
        }

        private static int Integer(JToken value, int minimum, int maximum)
        {
            if (
                value?.Type != JTokenType.Integer
                || !int.TryParse(value.ToString(), NumberStyles.AllowLeadingSign, CultureInfo.InvariantCulture, out int number)
                || number < minimum
                || number > maximum
            )
                throw Invalid("Expected an integer in the parameter's declared Int32 range.");
            return number;
        }

        private static bool Index(string part, out int index)
        {
            index = -1;
            if (!part.StartsWith("data[", StringComparison.Ordinal) || !part.EndsWith("]", StringComparison.Ordinal))
                return false;
            string digits = part.Substring(5, part.Length - 6);
            return digits.Length > 0
                && (digits.Length == 1 || digits[0] != '0')
                && digits.All(c => c >= '0' && c <= '9')
                && int.TryParse(digits, NumberStyles.None, CultureInfo.InvariantCulture, out index);
        }

        private static void ValidatePath(string path, bool empty)
        {
            if (path == null || path.Length > 512 || (!empty && path.Length == 0))
                throw Invalid("Use a canonical path of at most 512 UTF-16 units.");
            if (path.Length == 0)
                return;
            try
            {
                new UTF8Encoding(false, true).GetByteCount(path);
            }
            catch (EncoderFallbackException)
            {
                throw Invalid("Paths must contain valid Unicode.");
            }
            string[] parts = path.Split('.');
            int hops = 0;
            for (int i = 0; i < parts.Length; i++)
            {
                string part = parts[i];
                if (i > 0 && part == "Array" && i + 1 < parts.Length)
                {
                    if (parts[i + 1] != "size" && !Index(parts[i + 1], out _))
                        throw Invalid("Use canonical Array.data[index] syntax.");
                    i++;
                }
                else if (part.Length == 0 || part.Any(c => char.IsWhiteSpace(c) || char.IsControl(c) || c == '[' || c == ']'))
                    throw Invalid("Use exact field names and canonical array paths.");
                if (++hops > 16)
                    throw Invalid("Paths may resolve at most 16 property hops.");
            }
        }

        private sealed class Budget
        {
            internal readonly CancellationToken Token;
            internal int Total,
                Navigation,
                ArrayElements,
                Values,
                EnumNames;
            internal int Lookups,
                Constructions,
                Updates,
                AssetQueries;

            internal Budget(CancellationToken token)
            {
                Token = token;
            }

            internal T Read<T>(string kind, string path, Func<T> operation)
            {
                Token.ThrowIfCancellationRequested();
                if (Total == MaxWork)
                    throw new WorkLimit();
                Total++;
                if (kind == "find" || kind == "next" || kind == "index" || kind == "iterator")
                    Navigation++;
                if (kind == "index")
                    ArrayElements++;
                if (kind == "value")
                    Values++;
                T result = operation();
                try
                {
                    Observe(kind, path);
                    return result;
                }
                catch
                {
                    // Cancellation can arrive after a native iterator was allocated but before its caller owns it.
                    try
                    {
                        if (result is SerializedProperty property)
                            property.Dispose();
                    }
                    catch (Exception) { /* Keep the original cancellation if native invalidation also broke cleanup. */ }
                    throw;
                }
            }

            internal void Observe(string kind, string path)
            {
                OperationForTests?.Invoke(kind, path);
                Token.ThrowIfCancellationRequested();
            }

            internal JObject Describe() =>
                new JObject
                {
                    ["used"] = Total,
                    ["limit"] = MaxWork,
                    ["navigation"] = Navigation,
                    ["array_elements"] = ArrayElements,
                    ["value_reads"] = Values,
                    ["enum_names"] = EnumNames,
                    ["setup"] = new JObject
                    {
                        ["instance_lookups"] = Lookups,
                        ["serialized_objects"] = Constructions,
                        ["updates"] = Updates,
                        ["asset_metadata_queries"] = AssetQueries,
                    },
                };
        }

        private sealed class Target
        {
            internal Object Instance;
            internal JObject Description;
            internal string Route;
        }

        private static Target Resolve(int id, Budget budget)
        {
            budget.Token.ThrowIfCancellationRequested();
            budget.Lookups++;
            Object instance = UnityObjectIdCompat.InstanceIDToObjectCompat(id);
            budget.Observe("lookup", "");
            if (instance == null)
                throw new Failure("target_not_found", "The instance ID does not resolve to an available object.");
            var result = new Target
            {
                Instance = instance,
                Description = new JObject { ["instance_id"] = instance.GetInstanceIDCompat(), ["type"] = Short(instance.GetType().FullName, 256) },
            };
            if (instance is Component component)
            {
                ComponentSelection.EnsureTarget(component.gameObject);
                result.Description["kind"] = "component";
                result.Description["gameobject_instance_id"] = component.gameObject.GetInstanceIDCompat();
                result.Description["component_type"] = Short(component.GetType().FullName, 256);
                result.Route = "manage_components";
            }
            else if (instance is ScriptableObject)
            {
                result.Description["kind"] = "scriptable_object";
                budget.Token.ThrowIfCancellationRequested();
                budget.AssetQueries++;
                bool main = AssetDatabase.IsMainAsset(instance);
                budget.Observe("asset_metadata", "is_main");
                if (main)
                {
                    budget.AssetQueries++;
                    string path = AssetDatabase.GetAssetPath(instance);
                    budget.Observe("asset_metadata", "path");
                    // Guidance is limited to paths accepted by the existing setter containment guard; runtime permissions still apply.
                    if (EligibleAssetPath(path))
                    {
                        budget.AssetQueries++;
                        string guid = AssetDatabase.AssetPathToGUID(path);
                        budget.Observe("asset_metadata", "guid");
                        if (!string.IsNullOrEmpty(guid))
                        {
                            result.Route = "manage_scriptable_object";
                            result.Description["asset_guid"] = guid;
                        }
                    }
                }
            }
            else
                throw new Failure("invalid_target", "Inspection requires a Component or ScriptableObject instance.");
            budget.Token.ThrowIfCancellationRequested();
            return result;
        }

        private static bool EligibleAssetPath(string path)
        {
            if (string.IsNullOrEmpty(path) || path.Length > 2048 || !path.StartsWith("Assets/", StringComparison.Ordinal))
                return false;
            try
            {
                return AssetPathUtility.GetContainedAssetPath(path) == path;
            }
            catch (Exception)
            {
                return false;
            }
        }

        private static string Short(string value, int limit)
        {
            if (value == null || value.Length <= limit)
                return value;
            if (char.IsHighSurrogate(value[limit - 1]))
                limit--;
            return value.Substring(0, limit);
        }

        private static SerializedProperty ResolvePath(SerializedObject source, string path, Budget budget)
        {
            string[] parts = path.Split('.');
            SerializedProperty current = null;
            try
            {
                current = budget.Read("find", parts[0], () => source.FindProperty(parts[0]));
                if (current == null)
                    throw new Failure("property_not_found", "The exact property path is unavailable.");
                for (int i = 1; i < parts.Length; i++)
                {
                    var type = budget.Read("meta", path, () => current.propertyType);
                    if (type == SerializedPropertyType.ManagedReference)
                        throw new Failure("managed_reference_boundary", "Managed references are terminal descriptors.");
                    bool array = type != SerializedPropertyType.String && budget.Read("meta", path, () => current.isArray);
                    SerializedProperty next;
                    if (array)
                    {
                        if (parts[i] != "Array" || i + 1 >= parts.Length)
                            throw new Failure("invalid_projection", "Cross arrays only with a canonical element path.");
                        string element = parts[++i];
                        if (element == "size")
                            next = budget.Read("find", path, () => current.FindPropertyRelative("Array.size"));
                        else
                        {
                            if (!Index(element, out int index))
                                throw Invalid("Invalid array index syntax.");
                            int size = budget.Read("meta", path, () => current.arraySize);
                            if (index >= size)
                                throw new Failure("invalid_range", "The property index exceeds the current array size.");
                            next = budget.Read("index", path, () => current.GetArrayElementAtIndex(index));
                        }
                    }
                    else
                    {
                        if (type != SerializedPropertyType.Generic)
                            throw new Failure("invalid_projection", "This property cannot be traversed.");
                        string part = parts[i];
                        next = budget.Read("find", path, () => current.FindPropertyRelative(part));
                    }
                    current.Dispose();
                    current = next;
                    if (current == null)
                        throw new Failure("property_not_found", "The exact property path is unavailable.");
                }
                string actual = budget.Read("meta", path, () => current.propertyPath);
                if (actual != path)
                    throw new Failure("property_not_found", "The native property path does not match exactly.");
                return current;
            }
            catch
            {
                current?.Dispose();
                throw;
            }
        }

        private static SerializedProperty ResolveAfter(SerializedObject source, string path, Budget budget)
        {
            try
            {
                return ResolvePath(source, path, budget);
            }
            catch (Failure failure) when (failure.Code != "projection_limit")
            {
                throw new Failure("invalid_after_path", "after_path no longer identifies an immediate child of the selected parent.");
            }
        }

        internal static object Inspect(Options options, CancellationToken token)
        {
            var budget = new Budget(token);
            Target target = Resolve(options.Id, budget);
            budget.Token.ThrowIfCancellationRequested();
            budget.Constructions++;
            using var source = new SerializedObject(target.Instance);
            budget.Observe("construct", "");
            budget.Updates++;
            source.Update();
            budget.Observe("update", "");
            var records = new JArray();
            int bytes = 2;
            int? arraySize = null;
            string after = null,
                stop = null;
            int nextIndex = options.Start;
            bool complete = false;
            using var parent = options.Path.Length == 0 ? null : ResolvePath(source, options.Path, budget);
            bool Append(SerializedProperty property)
            {
                JObject record = Describe(property, target, options.Values, budget);
                int size = Encoding.UTF8.GetByteCount(record.ToString(Formatting.None)) + (records.Count == 0 ? 0 : 1);
                if (bytes + size > RecordBytesLimit)
                {
                    stop = "output_budget";
                    return false;
                }
                records.Add(record);
                bytes += size;
                after = (string)record["property_path"];
                return true;
            }
            try
            {
                if (options.Mode == "value")
                    complete = Append(parent);
                else if (options.Mode == "array")
                {
                    var type = budget.Read("meta", options.Path, () => parent.propertyType);
                    if (type == SerializedPropertyType.String || !budget.Read("meta", options.Path, () => parent.isArray))
                        throw new Failure("invalid_projection", "array requires an actual array or list property.");
                    arraySize = budget.Read("meta", options.Path, () => parent.arraySize);
                    if (options.Start > arraySize.Value)
                        throw new Failure("invalid_range", "start_index exceeds the current array size.");
                    while (nextIndex < arraySize.Value && records.Count < options.PageSize)
                    {
                        int index = nextIndex;
                        using var element = budget.Read(
                            "index",
                            options.Path + ".Array.data[" + index.ToString(CultureInfo.InvariantCulture) + "]",
                            () => parent.GetArrayElementAtIndex(index)
                        );
                        if (!Append(element))
                            break;
                        nextIndex++;
                    }
                    complete = nextIndex == arraySize.Value;
                }
                else
                {
                    int depth = -1;
                    if (parent != null)
                    {
                        var type = budget.Read("meta", options.Path, () => parent.propertyType);
                        if (type == SerializedPropertyType.ManagedReference)
                            throw new Failure("managed_reference_boundary", "Managed references are terminal descriptors.");
                        if (type != SerializedPropertyType.Generic || budget.Read("meta", options.Path, () => parent.isArray))
                            throw new Failure("invalid_projection", "fields requires the root or a non-array Generic property.");
                        depth = budget.Read("meta", options.Path, () => parent.depth);
                    }
                    using var end = parent == null ? null : budget.Read("iterator", options.Path, () => parent.GetEndProperty(false));
                    using var cursor =
                        options.After != null ? ResolveAfter(source, options.After, budget)
                        : parent == null ? budget.Read("iterator", "", () => source.GetIterator())
                        : budget.Read("iterator", options.Path, () => parent.Copy());
                    bool IsChild()
                    {
                        if (end != null && budget.Read("meta", options.Path, () => SerializedProperty.EqualContents(cursor, end)))
                            return false;
                        int currentDepth = budget.Read("meta", options.Path, () => cursor.depth);
                        if (currentDepth != depth + 1)
                            return false;
                        string path = budget.Read("meta", options.Path, () => cursor.propertyPath);
                        return parent == null || path.StartsWith(options.Path + ".", StringComparison.Ordinal);
                    }
                    if (options.After != null && !IsChild())
                        throw new Failure("invalid_after_path", "after_path must identify an immediate child of the selected parent.");
                    bool enter = options.After == null;
                    while (true)
                    {
                        bool hasNext = budget.Read("next", after ?? options.After ?? options.Path, () => cursor.Next(enter));
                        enter = false;
                        if (!hasNext || !IsChild())
                        {
                            complete = true;
                            break;
                        }
                        if (records.Count == options.PageSize)
                            break;
                        if (!Append(cursor))
                            break;
                    }
                }
            }
            catch (WorkLimit)
            {
                stop = "work_budget";
            }
            if (!complete && records.Count == 0)
                throw new Failure("projection_limit", "The first descriptor cannot fit the bounded projection. Request a narrower property.");
            token.ThrowIfCancellationRequested();
            if (target.Instance == null)
                throw new Failure("target_not_found", "The retained target was destroyed during inspection.");
            var data = new JObject
            {
                ["contractVersion"] = 1,
                ["target"] = target.Description,
                ["target_resolution"] = "instance_id_may_load",
                ["mode"] = options.Mode,
                ["property_path"] = options.Path,
                ["consistency"] = "live_serialized",
                ["records"] = records,
                ["returned"] = records.Count,
                ["complete"] = complete,
                ["stop_reason"] = complete ? "complete" : stop ?? "page_size",
                ["total"] = JValue.CreateNull(),
                ["work"] = budget.Describe(),
                ["max_output_bytes"] = MaxOutputBytes,
            };
            if (options.Mode == "fields")
                data["next_after_path"] = complete ? null : after;
            if (options.Mode == "array")
            {
                data["array_size"] = arraySize;
                data["next_start_index"] = complete ? JValue.CreateNull() : new JValue(nextIndex);
            }
            var response = new SuccessResponse("Serialized projection inspected", data);
            if (Encoding.UTF8.GetByteCount(JsonConvert.SerializeObject(response, Formatting.None)) > MaxOutputBytes)
                throw new Failure("projection_limit", "The bounded response envelope cannot represent this projection.");
            return response;
        }

        private static JObject Describe(SerializedProperty property, Target target, bool values, Budget budget)
        {
            string path = budget.Read("meta", "", () => property.propertyPath);
            if (path.Length > 512)
                throw new Failure("projection_limit", "A canonical property path exceeds the response path limit.");
            var type = budget.Read("meta", path, () => property.propertyType);
            string nativeType = budget.Read("meta", path, () => property.type);
            bool editable = budget.Read("meta", path, () => property.editable);
            bool array = type != SerializedPropertyType.String && budget.Read("meta", path, () => property.isArray);
            bool fixedBuffer = budget.Read("meta", path, () => property.isFixedBuffer);
            var record = new JObject
            {
                ["property_path"] = path,
                ["property_type"] = type.ToString(),
                ["serialized_type"] = Short(nativeType, 256),
                ["serialized_type_truncated"] = nativeType != null && nativeType.Length > 256,
                ["serialized_editable"] = editable,
                ["is_array"] = array,
                ["value_status"] = values ? "unsupported" : "omitted",
            };
            string[] shapes = Shapes(type, nativeType, target.Route);
            if (array || fixedBuffer || path.StartsWith("m_", StringComparison.Ordinal))
                shapes = Array.Empty<string>();
            record["write_contract"] = new JObject
            {
                ["status"] =
                    !editable ? "not_editable"
                    : target.Route == null ? "no_applicable_target"
                    : shapes.Length == 0 ? "not_advertised"
                    : "supported_shape",
                ["route"] = target.Route,
                ["property_key"] = path,
                ["input_shapes"] = new JArray(shapes),
                ["strategy"] = target.Route == "manage_components" ? "existing_component_setter_reflection_then_serialized" : "existing_serialized_setter",
                ["runtime_validation_required"] = true,
            };
            if (fixedBuffer)
            {
                record["value_status"] = "unsupported";
                return record;
            }
            if (array)
            {
                record["array_size"] = budget.Read("meta", path, () => property.arraySize);
                record["array_element_type"] = Short(budget.Read("meta", path, () => property.arrayElementType), 256);
                record["value_status"] = "container_not_expanded";
                return record;
            }
            if (type == SerializedPropertyType.Generic)
            {
                record["has_children"] = budget.Read("meta", path, () => property.hasChildren);
                record["value_status"] = "container_not_expanded";
                return record;
            }
            if (type == SerializedPropertyType.ManagedReference)
            {
                record["managed_reference_type"] = Short(budget.Read("meta", path, () => property.managedReferenceFullTypename), 256);
                record["managed_reference_field_type"] = Short(budget.Read("meta", path, () => property.managedReferenceFieldTypename), 256);
                if (values)
                    record["managed_reference_id"] = budget.Read("value", path, () => property.managedReferenceId).ToString(CultureInfo.InvariantCulture);
                record["value_status"] = "managed_reference_not_expanded";
                return record;
            }
            if (type == SerializedPropertyType.Enum)
            {
                string[] names = budget.Read("meta", path, () => property.enumNames);
                var choices = new JArray();
                bool omitted = false;
                int count = Math.Min(16, Math.Min(names.Length, 64 - budget.EnumNames));
                for (int i = 0; i < count; i++)
                {
                    string name = budget.Read("enum_name", path, () => names[i]);
                    budget.EnumNames++;
                    if (name.Length > 128)
                    {
                        omitted = true;
                        continue;
                    }
                    choices.Add(new JObject { ["name"] = name, ["index"] = i });
                }
                record["enum_choices"] = choices;
                record["enum_names_complete"] = !omitted && count == names.Length;
                if (!values)
                    return record;
                int index = budget.Read("value", path, () => property.enumValueIndex);
                int raw = budget.Read("value", path, () => property.intValue);
                record["value"] = new JObject
                {
                    ["index"] = index,
                    ["raw32"] = raw,
                    ["name"] = choices.OfType<JObject>().FirstOrDefault(c => (int)c["index"] == index)?["name"]?.DeepClone(),
                };
                record["value_status"] = "available";
                if (index < 0)
                {
                    record["write_contract"]["status"] = "not_advertised";
                    record["write_contract"]["input_shapes"] = new JArray();
                }
                return record;
            }
            if (!values)
                return record;
            JToken value = null;
            switch (type)
            {
                case SerializedPropertyType.Integer:
                    // Unity 2021.3 exposes unsigned fields through intValue/longValue.
                    // Preserve their raw bits instead of sign-extending or converting through double.
                    if (nativeType == "uint" || nativeType == "UInt32")
                        value = budget.Read("value", path, () => unchecked((uint)property.intValue));
                    else if (nativeType == "ulong" || nativeType == "UInt64")
                        value = budget.Read("value", path, () => unchecked((ulong)property.longValue));
                    else if (nativeType == "long" || nativeType == "SInt64")
                        value = budget.Read("value", path, () => property.longValue);
                    else if (SignedInteger(nativeType))
                        value = budget.Read("value", path, () => property.intValue);
                    else
                    {
                        record["value_status"] = "unsupported_integer_type";
                        return record;
                    }
                    break;
                case SerializedPropertyType.Boolean:
                    value = budget.Read("value", path, () => property.boolValue);
                    break;
                case SerializedPropertyType.Float:
                    double number =
                        nativeType == "double"
                            ? budget.Read("value", path, () => property.doubleValue)
                            : budget.Read("value", path, () => (double)property.floatValue);
                    if (double.IsNaN(number) || double.IsInfinity(number))
                    {
                        record["value_status"] = "nonfinite";
                        return record;
                    }
                    value = number;
                    break;
                case SerializedPropertyType.String:
                    string text = budget.Read("value", path, () => property.stringValue) ?? "";
                    value = Short(text, 512);
                    record["string_length"] = text.Length;
                    record["value_truncated"] = text.Length > 512;
                    break;
                case SerializedPropertyType.ObjectReference:
#if UNITY_6000_4_OR_NEWER
                    ulong rawId = budget.Read("value", path, () => EntityId.ToULong(property.objectReferenceEntityIdValue));
                    string idKind = "entity_id_raw64";
#else
                    int rawId = budget.Read("value", path, () => property.objectReferenceInstanceIDValue);
                    string idKind = "instance_id_raw32";
#endif
                    value = new JObject
                    {
                        ["raw_id"] = rawId.ToString(CultureInfo.InvariantCulture),
                        ["id_kind"] = idKind,
                        ["state"] = rawId == 0 ? "null_or_unresolved" : "nonzero",
                        ["writer_instance_id_compatible"] = false,
                    };
                    break;
                case SerializedPropertyType.Vector2:
                    Vector2 v2 = budget.Read("value", path, () => property.vector2Value);
                    value = Numbers(v2.x, v2.y);
                    break;
                case SerializedPropertyType.Vector3:
                    Vector3 v3 = budget.Read("value", path, () => property.vector3Value);
                    value = Numbers(v3.x, v3.y, v3.z);
                    break;
                case SerializedPropertyType.Vector4:
                    Vector4 v4 = budget.Read("value", path, () => property.vector4Value);
                    value = Numbers(v4.x, v4.y, v4.z, v4.w);
                    break;
                case SerializedPropertyType.Quaternion:
                    Quaternion q = budget.Read("value", path, () => property.quaternionValue);
                    value = Numbers(q.x, q.y, q.z, q.w);
                    break;
                case SerializedPropertyType.Color:
                    Color c = budget.Read("value", path, () => property.colorValue);
                    value = Numbers(c.r, c.g, c.b, c.a);
                    break;
                case SerializedPropertyType.Rect:
                    Rect rect = budget.Read("value", path, () => property.rectValue);
                    value = Numbers(rect.x, rect.y, rect.width, rect.height);
                    break;
                case SerializedPropertyType.Bounds:
                    Bounds bounds = budget.Read("value", path, () => property.boundsValue);
                    value = new JObject
                    {
                        ["center"] = Numbers(bounds.center.x, bounds.center.y, bounds.center.z),
                        ["size"] = Numbers(bounds.size.x, bounds.size.y, bounds.size.z),
                    };
                    break;
                default:
                    return record;
            }
            record["value"] = value;
            record["value_status"] = "available";
            return record;
        }

        private static JArray Numbers(params float[] numbers) =>
            new JArray(numbers.Select(n => float.IsNaN(n) || float.IsInfinity(n) ? (JToken)new JObject { ["value_status"] = "nonfinite" } : new JValue(n)));

        private static bool SignedInteger(string type) =>
            type == "int"
            || type == "SInt32"
            || type == "short"
            || type == "SInt16"
            || type == "sbyte"
            || type == "SInt8"
            || type == "byte"
            || type == "UInt8"
            || type == "ushort"
            || type == "UInt16";

        private static string[] Shapes(SerializedPropertyType type, string nativeType, string route)
        {
            switch (type)
            {
                case SerializedPropertyType.Integer:
                    return SignedInteger(nativeType) || nativeType == "long" || nativeType == "SInt64" ? new[] { "json_integer" } : Array.Empty<string>();
                case SerializedPropertyType.Boolean:
                    return new[] { "json_boolean" };
                case SerializedPropertyType.Float:
                    return new[] { "finite_json_number" };
                case SerializedPropertyType.String:
                    return new[] { "json_string" };
                case SerializedPropertyType.Enum:
                    return route == "manage_scriptable_object" ? new[] { "enum_name", "enum_value_index" } : new[] { "enum_name" };
                case SerializedPropertyType.ObjectReference:
                    return new[] { "null", "{instanceID:integer}", "{guid:string}" };
                default:
                    return Array.Empty<string>();
            }
        }
    }
}
