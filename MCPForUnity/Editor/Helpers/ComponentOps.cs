using System;
using System.Collections.Generic;
using System.Linq;
using System.Reflection;
using MCPForUnity.Runtime.Helpers;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEngine;
using UnityEngine.Events;

namespace MCPForUnity.Editor.Helpers
{
    /// <summary>
    /// Low-level component operations extracted from ManageGameObject and ManageComponents.
    /// Provides pure C# operations without JSON parsing or response formatting.
    /// </summary>
    public static class ComponentOps
    {
        /// <summary>
        /// Adds a component to a GameObject with Undo support.
        /// </summary>
        /// <param name="target">The target GameObject</param>
        /// <param name="componentType">The type of component to add</param>
        /// <param name="error">Error message if operation fails</param>
        /// <returns>The added component, or null if failed</returns>
        public static Component AddComponent(GameObject target, Type componentType, out string error)
        {
            error = null;

            if (target == null)
            {
                error = "Target GameObject is null.";
                return null;
            }

            if (componentType == null || !typeof(Component).IsAssignableFrom(componentType))
            {
                error = $"Type '{componentType?.Name ?? "null"}' is not a valid Component type.";
                return null;
            }

            // Prevent adding duplicate Transform
            if (componentType == typeof(Transform))
            {
                error = "Cannot add another Transform component.";
                return null;
            }

            // Check for 2D/3D physics conflicts
            string conflictError = CheckPhysicsConflict(target, componentType);
            if (conflictError != null)
            {
                error = conflictError;
                return null;
            }

            // Produce a clearer error when this component already exists and cannot be duplicated.
            Component existingComponent = target.GetComponent(componentType);
            if (existingComponent != null && !AllowsMultiple(target, componentType))
            {
                error = $"Component '{componentType.Name}' already exists on '{target.name}' and this type does not allow multiple instances.";
                return null;
            }

            try
            {
                Component newComponent = Undo.AddComponent(target, componentType);
                if (newComponent == null)
                {
                    if (target.GetComponent(componentType) != null && !AllowsMultiple(target, componentType))
                    {
                        error = $"Component '{componentType.Name}' already exists on '{target.name}' and this type does not allow multiple instances.";
                    }
                    else
                    {
                        error = $"Failed to add component '{componentType.Name}' to '{target.name}'. Unity may restrict this component on the current target.";
                    }
                    return null;
                }

                // Apply default values for specific component types
                ApplyDefaultValues(newComponent);

                return newComponent;
            }
            catch (Exception ex)
            {
                error = $"Error adding component '{componentType.Name}': {ex.Message}";
                return null;
            }
        }

        /// <summary>
        /// Removes a component from a GameObject with Undo support.
        /// </summary>
        /// <param name="target">The target GameObject</param>
        /// <param name="componentType">The type of component to remove</param>
        /// <param name="error">Error message if operation fails</param>
        /// <returns>True if component was removed successfully</returns>
        public static bool RemoveComponent(GameObject target, Type componentType, out string error)
        {
            error = null;

            if (target == null)
            {
                error = "Target GameObject is null.";
                return false;
            }

            if (componentType == null)
            {
                error = "Component type is null.";
                return false;
            }

            // Prevent removing Transform
            if (componentType == typeof(Transform))
            {
                error = "Cannot remove Transform component.";
                return false;
            }

            Component component = target.GetComponent(componentType);
            if (component == null)
            {
                error = $"Component '{componentType.Name}' not found on '{target.name}'.";
                return false;
            }

            if (component is Transform)
            {
                error = "Cannot remove Transform or RectTransform components.";
                return false;
            }

            try
            {
                Undo.DestroyObjectImmediate(component);
                return true;
            }
            catch (Exception ex)
            {
                error = $"Error removing component '{componentType.Name}': {ex.Message}";
                return false;
            }
        }

        /// <summary>
        /// Sets a property value on a component using reflection.
        /// </summary>
        /// <param name="component">The target component</param>
        /// <param name="propertyName">The property or field name</param>
        /// <param name="value">The value to set (JToken)</param>
        /// <param name="error">Error message if operation fails</param>
        /// <returns>True if property was set successfully</returns>
        public static bool SetProperty(Component component, string propertyName, JToken value, out string error)
        {
            error = null;

            if (component == null)
            {
                error = "Component is null.";
                return false;
            }

            if (string.IsNullOrEmpty(propertyName))
            {
                error = "Property name is null or empty.";
                return false;
            }

            Type type = component.GetType();
            BindingFlags flags = BindingFlags.Public | BindingFlags.Instance | BindingFlags.IgnoreCase;
            string normalizedName = ParamCoercion.NormalizePropertyName(propertyName);

            // UnityEventBase-derived types must be set via SerializedProperty, not reflection.
            // Reflection creates a disconnected object that Unity's serialization layer doesn't track,
            // causing m_PersistentCalls to be empty when the scene is saved.
            Type memberType = ResolveMemberType(type, propertyName, normalizedName);
            if (memberType != null && typeof(UnityEventBase).IsAssignableFrom(memberType))
            {
                return SetViaSerializedProperty(component, propertyName, normalizedName, value, out error);
            }

            // Try reflection first (property, field, then non-public serialized field)
            if (TrySetViaReflection(component, type, propertyName, normalizedName, flags, value, out error, out bool rejectedValue))
                return true;
            if (rejectedValue)
                return false;

            // Reflection failed — fall back to SerializedProperty which handles arrays,
            // custom serialization (e.g. UdonSharp), and types reflection can't convert.
            string reflectionError = error;
            if (SetViaSerializedProperty(component, propertyName, normalizedName, value, out error))
                return true;

            // Both paths failed. If reflection found the member but couldn't convert,
            // report that (more useful than the SerializedProperty error).
            // If reflection didn't find it at all, report the SerializedProperty error.
            if (reflectionError != null && !reflectionError.Contains("not found"))
                error = reflectionError;

            return false;
        }

        /// <summary>Prepares a reflected write from type metadata without constructing a component or evaluating getters.</summary>
        internal static bool TryPrepareProperty(Type componentType, string propertyName, JToken value, out Action<Component> apply, out string error)
        {
            apply = null;
            error = null;
            if (componentType == null || string.IsNullOrEmpty(propertyName))
            {
                error = "Invalid component type or property name.";
                return false;
            }
            if (propertyName.Contains('.'))
                return TryPrepareNestedWrite(componentType, propertyName, value, out apply, out error);
            var flags = BindingFlags.Public | BindingFlags.Instance | BindingFlags.IgnoreCase;
            string normalized = ParamCoercion.NormalizePropertyName(propertyName);
            PropertyInfo property = componentType.GetProperty(propertyName, flags) ?? componentType.GetProperty(normalized, flags);
            if (property == null && propertyName.StartsWith("m_", StringComparison.OrdinalIgnoreCase))
                property = componentType.GetProperty(propertyName.Substring(2), flags);
            if (property != null && property.CanWrite && property.GetIndexParameters().Length == 0)
            {
                if (typeof(UnityEventBase).IsAssignableFrom(property.PropertyType))
                    return TryPrepareUnityEvent(property.PropertyType, propertyName, value, out apply, out error);
                if (!TryPreparePropertyValue(property.PropertyType, value, out object converted, out error))
                    return false;
                apply = component => property.SetValue(component, converted);
                return true;
            }
            FieldInfo field =
                componentType.GetField(propertyName, flags)
                ?? componentType.GetField(normalized, flags)
                ?? FindSerializedFieldInHierarchy(componentType, propertyName)
                ?? FindSerializedFieldInHierarchy(componentType, normalized);
            if (field != null && !field.IsInitOnly)
            {
                if (typeof(UnityEventBase).IsAssignableFrom(field.FieldType))
                    return TryPrepareUnityEvent(field.FieldType, propertyName, value, out apply, out error);
                if (!TryPreparePropertyValue(field.FieldType, value, out object converted, out error))
                    return false;
                apply = component => field.SetValue(component, converted);
                return true;
            }
            error = $"Writable property or field '{propertyName}' not found on '{componentType.Name}'.";
            return false;
        }

        private static bool TryPrepareUnityEvent(Type eventType, string propertyName, JToken value, out Action<Component> apply, out string error)
        {
            apply = null;
            if (!(value is JObject))
            {
                error = "UnityEvent properties require an object containing serialized event fields.";
                return false;
            }
            if (!TryPrepareSerializedValue(eventType, value, out error))
                return false;
            JToken prepared = value.DeepClone();
            apply = component =>
            {
                if (!SetProperty(component, propertyName, prepared, out string writeError))
                    throw new ArgumentException(writeError);
            };
            return true;
        }

        private static bool TryPrepareSerializedValue(Type type, JToken value, out string error)
        {
            error = null;
            if (value is JArray array)
            {
                Type element =
                    type.IsArray ? type.GetElementType()
                    : type.IsGenericType && typeof(System.Collections.IList).IsAssignableFrom(type) ? type.GetGenericArguments()[0]
                    : null;
                if (element == null)
                {
                    error = $"'{type.Name}' is not a serialized array or list.";
                    return false;
                }
                foreach (JToken item in array)
                    if (!TryPrepareSerializedValue(element, item, out error))
                        return false;
                return true;
            }
            if (value is JObject fields && !typeof(UnityEngine.Object).IsAssignableFrom(type))
            {
                foreach (JProperty entry in fields.Properties())
                {
                    FieldInfo field = null;
                    for (Type current = type; current != null && field == null; current = current.BaseType)
                    {
                        foreach (
                            FieldInfo candidate in current.GetFields(
                                BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.DeclaredOnly
                            )
                        )
                        {
                            if (
                                (candidate.IsPublic || candidate.GetCustomAttribute<SerializeField>() != null)
                                && string.Equals(candidate.Name.Replace("_", ""), entry.Name.Replace("_", ""), StringComparison.OrdinalIgnoreCase)
                            )
                            {
                                field = candidate;
                                break;
                            }
                        }
                    }
                    if (field == null)
                    {
                        error = $"Serialized field '{entry.Name}' not found on '{type.Name}'.";
                        return false;
                    }
                    if (!TryPrepareSerializedValue(field.FieldType, entry.Value, out error))
                        return false;
                }
                return true;
            }
            return TryPreparePropertyValue(type, value, out _, out error);
        }

        internal static bool TryValidatePropertyOwners(
            Type componentType,
            Component existing,
            JObject properties,
            out string error,
            GameObject targetContext = null,
            Func<Type, JToken, bool> canResolveAfterAddition = null
        )
        {
            error = null;
            var shadow = new Dictionary<string, object>(StringComparer.OrdinalIgnoreCase);
            try
            {
                JProperty[] inputs = properties.Properties().ToArray();
                for (int inputIndex = 0; inputIndex < inputs.Length; inputIndex++)
                {
                    JProperty input = inputs[inputIndex];
                    Type type = componentType;
                    object owner = existing;
                    string prefix = "";
                    string[] parts = SplitPropertyPath(input.Name);
                    for (int i = 0; i < parts.Length; i++)
                    {
                        ParsePreparedPathPart(parts[i], out string memberName, out int? index);
                        MemberInfo member = FindPreparedMember(type, memberName);
                        if (member == null)
                        {
                            error = $"Property or field '{parts[i]}' not found on '{type.Name}'.";
                            return false;
                        }
                        Type memberType = member is PropertyInfo property ? property.PropertyType : ((FieldInfo)member).FieldType;
                        prefix = prefix.Length == 0 ? member.Name : prefix + "." + member.Name;
                        if (i == parts.Length - 1)
                        {
                            // The prepared setters already validate events using serialized fields.
                            bool needsShadow = inputs.Skip(inputIndex + 1).Any(future => IsPreparedAncestor(componentType, prefix, future.Name));
                            if (needsShadow && !typeof(UnityEventBase).IsAssignableFrom(memberType))
                            {
                                if (!TryPreparePropertyValue(memberType, input.Value, out object converted, out error))
                                {
                                    if (canResolveAfterAddition == null || !canResolveAfterAddition(memberType, input.Value))
                                        return false;
                                    converted = memberType; // A declared planned reference exists after addition, without allocating it now.
                                    error = null;
                                }
                                foreach (string descendant in shadow.Keys.Where(key => IsPathDescendant(prefix, key)).ToArray())
                                    shadow.Remove(descendant);
                                shadow[prefix] = converted;
                            }
                            break;
                        }
                        bool guaranteedOwner = false;
                        if (!shadow.TryGetValue(prefix, out object next))
                        {
                            if (owner != null)
                            {
                                if (!TryReadBorrowedMember(owner, member, out next))
                                {
                                    if (!memberType.IsValueType)
                                    {
                                        error = $"Cannot validate owner '{prefix}' before applying '{input.Name}'.";
                                        return false;
                                    }
                                    next = null;
                                }
                            }
                            else if (
                                member is PropertyInfo self
                                && (
                                    (typeof(Component).IsAssignableFrom(self.DeclaringType) && (self.Name == "gameObject" || self.Name == "transform"))
                                    || typeof(GameObject).IsAssignableFrom(self.DeclaringType) && self.Name == "transform"
                                )
                            )
                            {
                                next =
                                    targetContext == null ? null
                                    : self.Name == "gameObject" ? (object)targetContext
                                    : targetContext.transform;
                                guaranteedOwner = true;
                            }
                            else
                                next = null;
                        }
                        if (index.HasValue)
                        {
                            Type elementType =
                                memberType.IsArray ? memberType.GetElementType()
                                : memberType.IsGenericType && typeof(System.Collections.IList).IsAssignableFrom(memberType)
                                    ? memberType.GetGenericArguments()[0]
                                : null;
                            if (elementType == null || !(next is System.Collections.IList list) || index.Value < 0 || index.Value >= list.Count)
                            {
                                error = $"Index {index.Value} is out of range or unavailable in path '{input.Name}'.";
                                return false;
                            }
                            next = list[index.Value];
                            memberType = elementType;
                            prefix += "[" + index.Value + "]";
                        }
                        if (next is Type plannedReference && typeof(UnityEngine.Object).IsAssignableFrom(memberType))
                        {
                            memberType = plannedReference;
                            next = null;
                            guaranteedOwner = true;
                        }
                        if (!guaranteedOwner && !memberType.IsValueType && (next == null || next is UnityEngine.Object unityObject && unityObject == null))
                        {
                            error = $"Nested owner '{prefix}' is null or unavailable before applying '{input.Name}'.";
                            return false;
                        }
                        owner = next;
                        type = next?.GetType() ?? memberType;
                    }
                }
                return true;
            }
            catch (Exception ex)
            {
                error = $"Cannot validate nested property owners: {ex.Message}";
                return false;
            }
        }

        private static bool IsPreparedAncestor(Type type, string prefix, string path)
        {
            string canonical = "";
            foreach (string part in SplitPropertyPath(path))
            {
                ParsePreparedPathPart(part, out string name, out int? index);
                MemberInfo member = FindPreparedMember(type, name);
                if (member == null)
                    return false;
                canonical = canonical.Length == 0 ? member.Name : canonical + "." + member.Name;
                type = member is PropertyInfo property ? property.PropertyType : ((FieldInfo)member).FieldType;
                if (index.HasValue)
                {
                    canonical += "[" + index.Value + "]";
                    type =
                        type.IsArray ? type.GetElementType()
                        : type.IsGenericType && typeof(System.Collections.IList).IsAssignableFrom(type) ? type.GetGenericArguments()[0]
                        : null;
                    if (type == null)
                        return false;
                }
                if (IsPathDescendant(prefix, canonical))
                    return true;
            }
            return false;
        }

        private static bool IsPathDescendant(string prefix, string path) =>
            path.StartsWith(prefix + ".", StringComparison.OrdinalIgnoreCase) || path.StartsWith(prefix + "[", StringComparison.OrdinalIgnoreCase);

        private static void ParsePreparedPathPart(string part, out string name, out int? index)
        {
            name = part;
            index = null;
            int bracket = part.IndexOf('[');
            if (bracket > 0 && part.EndsWith("]") && int.TryParse(part.Substring(bracket + 1, part.Length - bracket - 2), out int parsed))
            {
                name = part.Substring(0, bracket);
                index = parsed;
            }
        }

        internal static string[] SplitPropertyPath(string path)
        {
            var parts = new List<string>();
            int startIndex = 0;
            bool inBrackets = false;
            for (int i = 0; i < path.Length; i++)
            {
                if (path[i] == '[')
                    inBrackets = true;
                else if (path[i] == ']')
                    inBrackets = false;
                else if (path[i] == '.' && !inBrackets)
                {
                    parts.Add(path.Substring(startIndex, i - startIndex));
                    startIndex = i + 1;
                }
            }
            if (startIndex < path.Length)
                parts.Add(path.Substring(startIndex));
            return parts.ToArray();
        }

        private static MemberInfo FindPreparedMember(Type type, string name)
        {
            var flags = BindingFlags.Public | BindingFlags.Instance | BindingFlags.IgnoreCase;
            string normalized = ParamCoercion.NormalizePropertyName(name);
            PropertyInfo property = type.GetProperty(name, flags) ?? type.GetProperty(normalized, flags);
            if (property == null && name.StartsWith("m_", StringComparison.OrdinalIgnoreCase))
                property = type.GetProperty(name.Substring(2), flags);
            return (MemberInfo)property
                ?? type.GetField(name, flags)
                ?? type.GetField(normalized, flags)
                ?? FindSerializedFieldInHierarchy(type, name)
                ?? FindSerializedFieldInHierarchy(type, normalized);
        }

        // Read known borrowed native references without invoking Unity's instantiating getters.
        internal static bool TryReadBorrowedMember(object owner, MemberInfo member, out object value)
        {
            value = null;
            if (member is FieldInfo field)
            {
                value = field.GetValue(owner);
                return true;
            }
            var property = (PropertyInfo)member;
            if (!property.CanRead || property.GetIndexParameters().Length != 0)
                return false;
            if (owner is Renderer renderer)
            {
                if (string.Equals(property.Name, "material", StringComparison.OrdinalIgnoreCase))
                {
                    value = renderer.sharedMaterial;
                    return true;
                }
                if (string.Equals(property.Name, "materials", StringComparison.OrdinalIgnoreCase))
                {
                    value = renderer.sharedMaterials;
                    return true;
                }
            }
            if (owner is MeshFilter filter && string.Equals(property.Name, "mesh", StringComparison.OrdinalIgnoreCase))
            {
                value = filter.sharedMesh;
                return true;
            }
            if (owner is Collider collider && string.Equals(property.Name, "material", StringComparison.OrdinalIgnoreCase))
            {
                value = collider.sharedMaterial;
                return true;
            }
            string assemblyName = property.DeclaringType.Assembly.GetName().Name;
            if (!owner.GetType().IsValueType && assemblyName != "UnityEngine" && !assemblyName.StartsWith("UnityEngine.", StringComparison.Ordinal))
                return false;
            value = property.GetValue(owner);
            return true;
        }

        private static bool TryPrepareNestedWrite(Type type, string path, JToken value, out Action<Component> apply, out string error)
        {
            apply = null;
            error = null;
            var members = new List<MemberInfo>();
            var flags = BindingFlags.Public | BindingFlags.Instance | BindingFlags.IgnoreCase;
            foreach (string part in path.Split('.'))
            {
                PropertyInfo property = type.GetProperty(part, flags) ?? type.GetProperty(ParamCoercion.NormalizePropertyName(part), flags);
                FieldInfo field = property == null ? type.GetField(part, flags) ?? FindSerializedFieldInHierarchy(type, part) : null;
                if (property == null && field == null)
                {
                    error = $"Property or field '{part}' not found on '{type.Name}' in '{path}'.";
                    return false;
                }
                Type nextType = property?.PropertyType ?? field.FieldType;
                if (
                    (property != null && (property.GetIndexParameters().Length != 0 || !property.CanRead))
                    || (property != null && nextType.IsValueType && !property.CanWrite)
                    || (field != null && nextType.IsValueType && field.IsInitOnly)
                )
                {
                    error = $"Property '{part}' cannot be updated in nested path '{path}'.";
                    return false;
                }
                members.Add((MemberInfo)property ?? field);
                type = nextType;
            }
            MemberInfo last = members[members.Count - 1];
            if (last is PropertyInfo leaf && !leaf.CanWrite)
            {
                error = $"Property '{leaf.Name}' is not writable.";
                return false;
            }
            if (last is FieldInfo leafField && leafField.IsInitOnly)
            {
                error = $"Field '{leafField.Name}' is not writable.";
                return false;
            }
            if (!TryPreparePropertyValue(type, value, out object converted, out error))
                return false;
            apply = component =>
            {
                var owners = new object[members.Count];
                owners[0] = component;
                for (int i = 0; i < members.Count - 1; i++)
                    owners[i + 1] = members[i] is PropertyInfo property ? property.GetValue(owners[i]) : ((FieldInfo)members[i]).GetValue(owners[i]);
                void Set(int i, object writeValue)
                {
                    if (members[i] is PropertyInfo property)
                        property.SetValue(owners[i], writeValue);
                    else
                        ((FieldInfo)members[i]).SetValue(owners[i], writeValue);
                }
                Set(members.Count - 1, converted);
                for (int i = members.Count - 2; i >= 0; i--)
                    if (owners[i + 1].GetType().IsValueType)
                        Set(i, owners[i + 1]);
            };
            return true;
        }

        internal static bool TryPreparePropertyValue(Type expectedType, JToken value, out object converted, out string error)
        {
            converted = null;
            error = null;
            try
            {
                if (typeof(UnityEngine.Object).IsAssignableFrom(expectedType))
                {
                    UnityEngine.Object prepared = null;
                    bool Assign(UnityEngine.Object resolved, string filter, out string assignmentError)
                    {
                        if (resolved == null && (value == null || value.Type == JTokenType.Null))
                        {
                            assignmentError = null;
                            return true;
                        }
                        return TryMatchObjectReference(expectedType, resolved, filter, out prepared, out assignmentError);
                    }
                    bool AssignExact(UnityEngine.Object resolved, string filter, out string assignmentError)
                    {
                        assignmentError = null;
                        if (resolved != null && expectedType.IsInstanceOfType(resolved))
                        {
                            prepared = resolved;
                            return true;
                        }
                        assignmentError = $"Explicit object reference is not compatible with '{expectedType.Name}'.";
                        return false;
                    }
                    if (!ResolveObjectReference(value, Assign, out error, AssignExact))
                        return false;
                    converted = prepared;
                    return true;
                }
                if (value is JArray array && expectedType.IsArray)
                {
                    Type elementType = expectedType.GetElementType();
                    Array prepared = Array.CreateInstance(elementType, array.Count);
                    for (int i = 0; i < array.Count; i++)
                    {
                        if (!TryPreparePropertyValue(elementType, array[i], out object element, out error))
                            return false;
                        prepared.SetValue(element, i);
                    }
                    converted = prepared;
                    return true;
                }
                if (value is JArray list && expectedType.IsGenericType && expectedType.GetGenericTypeDefinition() == typeof(List<>))
                {
                    Type elementType = expectedType.GetGenericArguments()[0];
                    var prepared = (System.Collections.IList)Activator.CreateInstance(expectedType);
                    foreach (JToken token in list)
                    {
                        if (!TryPreparePropertyValue(elementType, token, out object element, out error))
                            return false;
                        prepared.Add(element);
                    }
                    converted = prepared;
                    return true;
                }
                converted = PropertyConversion.ConvertToType(value, expectedType);
                if (converted != null || value == null || value.Type == JTokenType.Null)
                    return true;
                error = $"Failed to convert value to '{expectedType.Name}'.";
            }
            catch (Exception ex)
            {
                error = ex.Message;
            }
            return false;
        }

        private static bool TrySetViaReflection(
            object component,
            Type type,
            string propertyName,
            string normalizedName,
            BindingFlags flags,
            JToken value,
            out string error,
            out bool rejectedValue
        )
        {
            error = null;
            rejectedValue = false;

            // Resolve integer IDs through the same typed SerializedProperty path as object forms.
            // The shared JSON converter accepts object-form IDs but cannot read bare integers.
            bool isObjectReferenceValue = value != null && (value.Type == JTokenType.Object || value.Type == JTokenType.Integer);

            // Try property first
            PropertyInfo propInfo = type.GetProperty(propertyName, flags) ?? type.GetProperty(normalizedName, flags);
            if (propInfo != null && propInfo.CanWrite)
            {
                if (isObjectReferenceValue && typeof(UnityEngine.Object).IsAssignableFrom(propInfo.PropertyType))
                {
                    // Let SerializedProperty path handle complex object references.
                    return false;
                }

                try
                {
                    object convertedValue = PropertyConversion.ConvertToType(value, propInfo.PropertyType);
                    if (convertedValue == null && value.Type != JTokenType.Null)
                    {
                        rejectedValue = true;
                        error = $"Failed to convert value for property '{propertyName}' to type '{propInfo.PropertyType.Name}'.";
                        return false;
                    }
                    propInfo.SetValue(component, convertedValue);
                    return true;
                }
                catch (Exception ex)
                {
                    rejectedValue = true;
                    error = $"Failed to set property '{propertyName}': {ex.Message}";
                    return false;
                }
            }

            // Try field
            FieldInfo fieldInfo = type.GetField(propertyName, flags) ?? type.GetField(normalizedName, flags);
            if (fieldInfo != null && !fieldInfo.IsInitOnly)
            {
                if (isObjectReferenceValue && typeof(UnityEngine.Object).IsAssignableFrom(fieldInfo.FieldType))
                {
                    // Let SerializedProperty path handle complex object references.
                    return false;
                }

                try
                {
                    object convertedValue = PropertyConversion.ConvertToType(value, fieldInfo.FieldType);
                    if (convertedValue == null && value.Type != JTokenType.Null)
                    {
                        rejectedValue = true;
                        error = $"Failed to convert value for field '{propertyName}' to type '{fieldInfo.FieldType.Name}'.";
                        return false;
                    }
                    fieldInfo.SetValue(component, convertedValue);
                    return true;
                }
                catch (Exception ex)
                {
                    rejectedValue = true;
                    error = $"Failed to set field '{propertyName}': {ex.Message}";
                    return false;
                }
            }

            // Try non-public serialized fields — traverse inheritance hierarchy
            fieldInfo = FindSerializedFieldInHierarchy(type, propertyName) ?? FindSerializedFieldInHierarchy(type, normalizedName);
            if (fieldInfo != null)
            {
                if (isObjectReferenceValue && typeof(UnityEngine.Object).IsAssignableFrom(fieldInfo.FieldType))
                {
                    // Let SerializedProperty path handle complex object references.
                    return false;
                }

                try
                {
                    object convertedValue = PropertyConversion.ConvertToType(value, fieldInfo.FieldType);
                    if (convertedValue == null && value.Type != JTokenType.Null)
                    {
                        rejectedValue = true;
                        error = $"Failed to convert value for serialized field '{propertyName}' to type '{fieldInfo.FieldType.Name}'.";
                        return false;
                    }
                    fieldInfo.SetValue(component, convertedValue);
                    return true;
                }
                catch (Exception ex)
                {
                    rejectedValue = true;
                    error = $"Failed to set serialized field '{propertyName}': {ex.Message}";
                    return false;
                }
            }

            error = $"Property or field '{propertyName}' not found on component '{type.Name}'.";
            return false;
        }

        /// <summary>
        /// Gets all public properties and fields from a component type.
        /// </summary>
        public static List<string> GetAccessibleMembers(Type componentType)
        {
            var members = new List<string>();
            if (componentType == null)
                return members;

            BindingFlags flags = BindingFlags.Public | BindingFlags.Instance;

            foreach (var prop in componentType.GetProperties(flags))
            {
                if (prop.CanWrite && prop.GetSetMethod() != null)
                {
                    members.Add(prop.Name);
                }
            }

            foreach (var field in componentType.GetFields(flags))
            {
                if (!field.IsInitOnly)
                {
                    members.Add(field.Name);
                }
            }

            // Include private [SerializeField] fields - traverse inheritance hierarchy
            // Type.GetFields with NonPublic only returns fields declared directly on that type,
            // so we need to walk up the chain to find inherited private serialized fields
            var seenFieldNames = new HashSet<string>(members); // Avoid duplicates with public fields
            Type currentType = componentType;
            while (currentType != null && currentType != typeof(object))
            {
                foreach (var field in currentType.GetFields(BindingFlags.NonPublic | BindingFlags.Instance | BindingFlags.DeclaredOnly))
                {
                    if (field.GetCustomAttribute<SerializeField>() != null && !seenFieldNames.Contains(field.Name))
                    {
                        members.Add(field.Name);
                        seenFieldNames.Add(field.Name);
                    }
                }
                currentType = currentType.BaseType;
            }

            members.Sort();
            return members;
        }

        // --- Private Helpers ---

        /// <summary>
        /// Searches for a non-public [SerializeField] field through the entire inheritance hierarchy.
        /// Type.GetField() with NonPublic only returns fields declared directly on that type,
        /// so this method walks up the chain to find inherited private serialized fields.
        /// </summary>
        internal static FieldInfo FindSerializedFieldInHierarchy(Type type, string fieldName)
        {
            if (type == null || string.IsNullOrEmpty(fieldName))
                return null;

            BindingFlags privateFlags = BindingFlags.NonPublic | BindingFlags.Instance | BindingFlags.DeclaredOnly;
            Type currentType = type;

            // Walk up the inheritance chain
            while (currentType != null && currentType != typeof(object))
            {
                // Search for the field on this specific type (case-insensitive)
                foreach (var field in currentType.GetFields(privateFlags))
                {
                    if (string.Equals(field.Name, fieldName, StringComparison.OrdinalIgnoreCase) && field.GetCustomAttribute<SerializeField>() != null)
                    {
                        return field;
                    }
                }
                currentType = currentType.BaseType;
            }

            return null;
        }

        private static string CheckPhysicsConflict(GameObject target, Type componentType)
        {
            bool isAdding2DPhysics = typeof(Rigidbody2D).IsAssignableFrom(componentType) || typeof(Collider2D).IsAssignableFrom(componentType);

            bool isAdding3DPhysics = typeof(Rigidbody).IsAssignableFrom(componentType) || typeof(Collider).IsAssignableFrom(componentType);

            if (isAdding2DPhysics)
            {
                if (target.GetComponent<Rigidbody>() != null || target.GetComponent<Collider>() != null)
                {
                    return $"Cannot add 2D physics component '{componentType.Name}' because the GameObject '{target.name}' already has a 3D Rigidbody or Collider.";
                }
            }
            else if (isAdding3DPhysics)
            {
                if (target.GetComponent<Rigidbody2D>() != null || target.GetComponent<Collider2D>() != null)
                {
                    return $"Cannot add 3D physics component '{componentType.Name}' because the GameObject '{target.name}' already has a 2D Rigidbody or Collider.";
                }
            }

            return null;
        }

        private static void ApplyDefaultValues(Component component)
        {
            // Default newly added Lights to Directional
            if (component is Light light)
            {
                light.type = LightType.Directional;
            }
        }

        private static bool AllowsMultiple(GameObject target, Type componentType)
        {
            if (target == null || componentType == null)
            {
                return false;
            }

            if (Attribute.IsDefined(componentType, typeof(DisallowMultipleComponent), inherit: true))
            {
                return false;
            }

            return true;
        }

        // --- UnityEvent SerializedProperty support ---

        internal static Type ResolveMemberType(Type componentType, string propertyName, string normalizedName)
        {
            BindingFlags flags = BindingFlags.Public | BindingFlags.Instance | BindingFlags.IgnoreCase;

            PropertyInfo propInfo = componentType.GetProperty(propertyName, flags) ?? componentType.GetProperty(normalizedName, flags);
            if (propInfo == null && propertyName.StartsWith("m_", StringComparison.OrdinalIgnoreCase))
                propInfo = componentType.GetProperty(propertyName.Substring(2), flags);
            if (propInfo != null)
                return propInfo.PropertyType;

            FieldInfo fieldInfo = componentType.GetField(propertyName, flags) ?? componentType.GetField(normalizedName, flags);
            if (fieldInfo != null)
                return fieldInfo.FieldType;

            fieldInfo = FindSerializedFieldInHierarchy(componentType, propertyName) ?? FindSerializedFieldInHierarchy(componentType, normalizedName);
            if (fieldInfo != null)
                return fieldInfo.FieldType;

            return null;
        }

        private static bool SetViaSerializedProperty(Component component, string propertyName, string normalizedName, JToken value, out string error)
        {
            error = null;
            using var so = new SerializedObject(component);

            using var prop = FindTopLevelProperty(so, propertyName, normalizedName, out error);
            if (prop == null)
            {
                error ??= $"SerializedProperty '{propertyName}' not found on component '{component.GetType().Name}'.";
                return false;
            }

            if (!SetSerializedPropertyRecursive(prop, value, out error, 0))
                return false;

            so.ApplyModifiedProperties();

            // Readback verification for ObjectReference — these can silently fail
            if (prop.propertyType == SerializedPropertyType.ObjectReference && value != null && !(value is JValue jv && jv.Type == JTokenType.Null))
            {
                string propertyPath = prop.propertyPath;
                so.Update();
                using var verifyProp = so.FindProperty(propertyPath);
                if (verifyProp != null && verifyProp.propertyType == SerializedPropertyType.ObjectReference && verifyProp.objectReferenceValue == null)
                {
                    error =
                        $"Property '{propertyName}' was set but the object reference did not persist. "
                        + "Check that the referenced object exists and is the correct type.";
                    return false;
                }
            }

            return true;
        }

        private static bool SetSerializedPropertyRecursive(SerializedProperty prop, JToken value, out string error, int depth)
        {
            error = null;
            const int MaxDepth = 20;
            if (depth > MaxDepth)
            {
                error = $"Maximum recursion depth ({MaxDepth}) exceeded.";
                return false;
            }

            try
            {
                // Array + JArray
                if (prop.isArray && prop.propertyType != SerializedPropertyType.String && value is JArray jArray)
                {
                    prop.arraySize = jArray.Count;

                    for (int i = 0; i < jArray.Count; i++)
                    {
                        using var element = prop.GetArrayElementAtIndex(i);
                        if (!SetSerializedPropertyRecursive(element, jArray[i], out error, depth + 1))
                            return false;
                    }
                    return true;
                }

                // Generic (struct/class) + JObject
                if (prop.propertyType == SerializedPropertyType.Generic && !prop.isArray && value is JObject jObj)
                {
                    foreach (var kvp in jObj)
                    {
                        using var child = FindPropertyRelativeFuzzy(prop, kvp.Key);
                        if (child == null)
                        {
                            error = $"Sub-property '{kvp.Key}' not found under '{prop.propertyPath}'.";
                            return false;
                        }
                        if (!SetSerializedPropertyRecursive(child, kvp.Value, out error, depth + 1))
                            return false;
                    }
                    return true;
                }

                // ObjectReference
                if (prop.propertyType == SerializedPropertyType.ObjectReference)
                    return SetObjectReference(prop, value, out error);

                // Leaf types
                switch (prop.propertyType)
                {
                    case SerializedPropertyType.Integer:
                        if (value == null || value.Type == JTokenType.Null || (value.Type != JTokenType.Integer && value.Type != JTokenType.String))
                        {
                            error = "Expected integer value.";
                            return false;
                        }
                        if (prop.type == "long")
                            prop.longValue = ParamCoercion.CoerceLong(value, 0);
                        else
                            prop.intValue = ParamCoercion.CoerceInt(value, 0);
                        return true;

                    case SerializedPropertyType.Boolean:
                        bool? boolVal = ParamCoercion.CoerceBoolNullable(value);
                        if (!boolVal.HasValue)
                        {
                            error = "Expected boolean value.";
                            return false;
                        }
                        prop.boolValue = boolVal.Value;
                        return true;

                    case SerializedPropertyType.Float:
                        if (prop.type == "double")
                        {
                            double? doubleVal = value.ReadScalar<double?>();
                            if (!doubleVal.HasValue)
                            {
                                error = "Expected double value.";
                                return false;
                            }
                            prop.doubleValue = doubleVal.Value;
                            return true;
                        }
                        float floatVal = ParamCoercion.CoerceFloat(value, float.NaN);
                        if (float.IsNaN(floatVal))
                        {
                            error = "Expected float value.";
                            return false;
                        }
                        prop.floatValue = floatVal;
                        return true;

                    case SerializedPropertyType.String:
                        prop.stringValue = value == null || value.Type == JTokenType.Null ? string.Empty : value.ToString();
                        return true;

                    case SerializedPropertyType.Enum:
                        return SetEnum(prop, value, out error);

                    default:
                        error = $"Unsupported SerializedPropertyType: {prop.propertyType} at '{prop.propertyPath}'.";
                        return false;
                }
            }
            catch (Exception ex)
            {
                error = $"Error setting '{prop.propertyPath}': {ex.Message}";
                return false;
            }
        }

        internal static bool SetObjectReference(SerializedProperty prop, JToken value, out string error)
        {
            bool Assign(UnityEngine.Object resolved, string filter, out string assignmentError)
            {
                if (resolved == null && (value == null || value.Type == JTokenType.Null))
                {
                    prop.objectReferenceValue = null;
                    assignmentError = null;
                    return true;
                }
                var original = prop.objectReferenceValue;
                bool assigned = AssignObjectReference(prop, resolved, filter, out assignmentError);
                if (!assigned)
                    prop.objectReferenceValue = original;
                return assigned;
            }
            bool AssignExact(UnityEngine.Object resolved, string filter, out string assignmentError)
            {
                var original = prop.objectReferenceValue;
                prop.objectReferenceValue = resolved;
                if (prop.objectReferenceValue != null)
                {
                    assignmentError = null;
                    return true;
                }
                prop.objectReferenceValue = original;
                assignmentError = "Explicit object reference is not compatible with the property type.";
                return false;
            }
            return ResolveObjectReference(value, Assign, out error, AssignExact);
        }

        private delegate bool ObjectReferenceAssignment(UnityEngine.Object resolved, string componentFilter, out string error);

        private static bool ResolveObjectReference(
            JToken value,
            ObjectReferenceAssignment assign,
            out string error,
            ObjectReferenceAssignment assignExact = null
        )
        {
            error = null;

            if (value == null || value.Type == JTokenType.Null)
            {
                return assign(null, null, out error);
            }

            if (value.Type == JTokenType.Integer)
            {
                int id = value.Value<int>();
                var resolved = GameObjectLookup.ResolveInstanceID(id);
                if (resolved == null)
                {
                    error = $"No object found with instanceID {id}.";
                    return false;
                }
                return assign(resolved, null, out error);
            }

            if (value is JObject jObj)
            {
                // Optional component type filter — e.g. {"instanceID": 123, "component": "Button"}
                string componentFilter = jObj["component"]?.ToString();

                var idToken = jObj["instanceID"];
                if (idToken != null)
                {
                    int id = ParamCoercion.CoerceInt(idToken, 0);
                    var resolved = GameObjectLookup.ResolveInstanceID(id);
                    if (resolved == null)
                    {
                        error = $"No object found with instanceID {id}.";
                        return false;
                    }
                    return assign(resolved, componentFilter, out error);
                }

                var guidToken = jObj["guid"];
                if (guidToken != null)
                {
                    string path = AssetPathUtility.GetAssetPathFromGuid(guidToken.ToString(), allowPackages: true, allowBuiltIn: true);
                    if (string.IsNullOrEmpty(path))
                    {
                        error = $"No asset found for GUID '{guidToken}'.";
                        return false;
                    }

                    var spriteNameToken = jObj["spriteName"];
                    if (spriteNameToken != null)
                    {
                        string spriteName = spriteNameToken.ToString();
                        var allAssets = AssetDatabase.LoadAllAssetsAtPath(path);
                        foreach (var asset in allAssets)
                        {
                            if (asset is Sprite sprite && sprite.name == spriteName)
                            {
                                return (assignExact ?? assign)(sprite, null, out error);
                            }
                        }

                        error = $"Sprite '{spriteName}' not found in atlas '{path}'.";
                        return false;
                    }

                    var fileIdToken = jObj["fileID"];
                    if (fileIdToken != null)
                    {
                        long targetFileId = fileIdToken.ReadScalar<long>();
                        if (targetFileId != 0)
                        {
                            var allAssets = AssetDatabase.LoadAllAssetsAtPath(path);
                            foreach (var asset in allAssets)
                            {
                                if (asset is Sprite sprite)
                                {
                                    long spriteFileId = GetSpriteFileId(sprite);
                                    if (spriteFileId == targetFileId)
                                    {
                                        return (assignExact ?? assign)(sprite, null, out error);
                                    }
                                }
                            }
                        }

                        error = $"Sprite with fileID '{targetFileId}' not found in atlas '{path}'.";
                        return false;
                    }

                    var loaded = AssetDatabase.LoadAssetAtPath<UnityEngine.Object>(path);
                    return assign(loaded, componentFilter, out error);
                }

                var pathToken = jObj["path"];
                if (pathToken != null)
                {
                    string sanitized = AssetPathUtility.GetAssetReferencePath(pathToken.ToString(), allowPackages: true, allowBuiltIn: true);
                    var resolved = AssetDatabase.LoadAssetAtPath<UnityEngine.Object>(sanitized);
                    if (resolved == null)
                    {
                        error = $"No asset found at path '{pathToken}'.";
                        return false;
                    }
                    return assign(resolved, componentFilter, out error);
                }

                var nameToken = jObj["name"];
                if (nameToken != null)
                {
                    return ResolveSceneObjectByName(assign, nameToken.ToString(), componentFilter, out error);
                }

                error = "Object reference must contain 'instanceID', 'guid', 'path', or 'name'.";
                return false;
            }

            if (value.Type == JTokenType.String)
            {
                string strVal = value.ToString();

                // Try as instanceID if the string is purely numeric
                if (int.TryParse(strVal, out int parsedId))
                {
                    var resolved = GameObjectLookup.ResolveInstanceID(parsedId);
                    if (resolved != null)
                        return assign(resolved, null, out error);
                    // Not a valid instanceID — fall through to path/name resolution
                }

                if (strVal.StartsWith("Assets/", StringComparison.OrdinalIgnoreCase) || strVal.Contains("/"))
                {
                    string sanitized = AssetPathUtility.GetAssetReferencePath(strVal, allowPackages: true, allowBuiltIn: true);
                    var resolved = AssetDatabase.LoadAssetAtPath<UnityEngine.Object>(sanitized);
                    if (resolved == null)
                    {
                        error = $"No asset found at path '{strVal}'.";
                        return false;
                    }
                    return assign(resolved, null, out error);
                }

                // Try as asset GUID (32-char hex string)
                if (strVal.Length == 32 && IsHexString(strVal))
                {
                    string assetPath = AssetPathUtility.GetAssetPathFromGuid(strVal, allowPackages: true, allowBuiltIn: true);
                    if (!string.IsNullOrEmpty(assetPath))
                    {
                        var resolved = AssetDatabase.LoadAssetAtPath<UnityEngine.Object>(assetPath);
                        if (resolved != null)
                            return assign(resolved, null, out error);
                    }
                }

                // Fall back to scene hierarchy lookup by name.
                return ResolveSceneObjectByName(assign, strVal, null, out error);
            }

            error = $"Unsupported object reference format: {value.Type}.";
            return false;
        }

        private static bool TryMatchObjectReference(
            Type expectedType,
            UnityEngine.Object resolved,
            string filter,
            out UnityEngine.Object matched,
            out string error
        )
        {
            matched = null;
            error = null;
            if (resolved == null)
            {
                error = "Resolved object is null.";
                return false;
            }
            string path = AssetDatabase.GetAssetPath(resolved);
            if (!string.IsNullOrEmpty(path))
                path = AssetPathUtility.GetAssetReferencePath(path, allowPackages: true, allowBuiltIn: true);

            if (resolved is GameObject filtered && !string.IsNullOrEmpty(filter))
            {
                foreach (Component component in filtered.GetComponents<Component>())
                    if (
                        component != null
                        && expectedType.IsInstanceOfType(component)
                        && (
                            string.Equals(component.GetType().Name, filter, StringComparison.OrdinalIgnoreCase)
                            || string.Equals(component.GetType().FullName, filter, StringComparison.OrdinalIgnoreCase)
                        )
                    )
                    {
                        matched = component;
                        return true;
                    }
                error = $"Compatible component '{filter}' not found on '{filtered.name}'.";
                return false;
            }
            if (expectedType.IsInstanceOfType(resolved))
            {
                matched = resolved;
                return true;
            }
            if (!string.IsNullOrEmpty(path))
            {
                foreach (UnityEngine.Object sub in AssetDatabase.LoadAllAssetsAtPath(path))
                {
                    if (sub == null || sub == resolved || !expectedType.IsInstanceOfType(sub))
                        continue;
                    if (matched != null)
                    {
                        matched = null;
                        error = $"Multiple compatible sub-assets found in '{path}'. Specify spriteName or fileID.";
                        return false;
                    }
                    matched = sub;
                }
                if (matched != null)
                    return true;
            }
            if (resolved is GameObject go && typeof(Component).IsAssignableFrom(expectedType))
            {
                matched = go.GetComponent(expectedType);
                if (matched != null)
                    return true;
            }
            error = $"Object '{resolved.name}' is not compatible with '{expectedType.Name}'.";
            return false;
        }

        /// <summary>
        /// Assigns a resolved object to a SerializedProperty, with automatic component fallback.
        /// If the resolved object is a GameObject but the property expects a Component type,
        /// searches the GameObject's components for a compatible one.
        /// Optionally filters by component type name (e.g. "Button", "Rigidbody").
        /// </summary>
        private static bool AssignObjectReference(SerializedProperty prop, UnityEngine.Object resolved, string componentFilter, out string error)
        {
            error = null;
            if (resolved == null)
            {
                error = "Resolved object is null.";
                return false;
            }

            string resolvedAssetPath = AssetDatabase.GetAssetPath(resolved);
            if (!string.IsNullOrEmpty(resolvedAssetPath))
                AssetPathUtility.GetAssetReferencePath(resolvedAssetPath, allowPackages: true, allowBuiltIn: true);

            // If a component filter is specified and the resolved object is a GameObject,
            // find the specific component by type name.
            if (!string.IsNullOrEmpty(componentFilter) && resolved is GameObject filterGo)
            {
                var components = filterGo.GetComponents<Component>();
                foreach (var comp in components)
                {
                    if (comp == null)
                        continue;
                    if (
                        string.Equals(comp.GetType().Name, componentFilter, StringComparison.OrdinalIgnoreCase)
                        || string.Equals(comp.GetType().FullName, componentFilter, StringComparison.OrdinalIgnoreCase)
                    )
                    {
                        prop.objectReferenceValue = comp;
                        if (prop.objectReferenceValue != null)
                            return true;
                    }
                }
                error = $"Component '{componentFilter}' not found on GameObject '{filterGo.name}'.";
                return false;
            }

            // Try direct assignment first
            prop.objectReferenceValue = resolved;
            if (prop.objectReferenceValue != null)
                return true;

            // Sub-asset fallback: e.g., Texture2D → Sprite
            string subAssetPath = AssetDatabase.GetAssetPath(resolved);
            if (!string.IsNullOrEmpty(subAssetPath))
            {
                subAssetPath = AssetPathUtility.GetAssetReferencePath(subAssetPath, allowPackages: true, allowBuiltIn: true);
                var subAssets = AssetDatabase.LoadAllAssetsAtPath(subAssetPath);
                UnityEngine.Object match = null;
                int matchCount = 0;
                foreach (var sub in subAssets)
                {
                    if (sub == null || sub == resolved)
                        continue;
                    prop.objectReferenceValue = sub;
                    if (prop.objectReferenceValue != null)
                    {
                        match = sub;
                        matchCount++;
                        if (matchCount > 1)
                            break;
                    }
                }

                if (matchCount == 1)
                {
                    prop.objectReferenceValue = match;
                    return true;
                }

                // Clean up: probing may have left the property dirty
                prop.objectReferenceValue = null;

                if (matchCount > 1)
                {
                    error =
                        $"Multiple compatible sub-assets found in '{subAssetPath}'. "
                        + "Use {\"guid\": \"...\", \"spriteName\": \"<name>\"} or "
                        + "{\"guid\": \"...\", \"fileID\": <id>} for precise selection.";
                    return false;
                }
            }

            // If the resolved object is a GameObject but the property expects a Component,
            // try each component on the GameObject until one is accepted.
            if (resolved is GameObject go)
            {
                var components = go.GetComponents<Component>();
                foreach (var comp in components)
                {
                    if (comp == null)
                        continue;
                    prop.objectReferenceValue = comp;
                    if (prop.objectReferenceValue != null)
                        return true;
                }
                error = $"GameObject '{go.name}' found but no compatible component for the property type.";
                return false;
            }

            error = $"Object '{resolved.name}' (type: {resolved.GetType().Name}) is not compatible with the property type.";
            return false;
        }

        /// <summary>
        /// Resolves a scene GameObject by name and assigns it (or a component on it)
        /// to a SerializedProperty. Uses GameObjectLookup for robust search
        /// including inactive objects and prefab stage support.
        /// </summary>
        private static bool ResolveSceneObjectByName(ObjectReferenceAssignment assign, string name, string componentFilter, out string error)
        {
            error = null;
            if (string.IsNullOrWhiteSpace(name))
            {
                error = "Cannot resolve object reference from empty name.";
                return false;
            }

            var ids = GameObjectLookup.SearchGameObjects(GameObjectLookup.SearchMethod.ByName, name, includeInactive: true, maxResults: 1);

            if (ids.Count == 0)
            {
                error = $"No GameObject named '{name}' found in scene.";
                return false;
            }

            var go = GameObjectLookup.FindById(ids[0]);
            if (go == null)
            {
                error = $"GameObject '{name}' found but could not be resolved.";
                return false;
            }

            return assign(go, componentFilter, out error);
        }

        /// <summary>
        /// Finds a top-level SerializedProperty by name. Built-in components often back a public
        /// property with a differently named native field (SpriteRenderer.sprite is m_Sprite), so
        /// fall back to comparing names case-insensitively with the m_ prefix and underscores removed.
        /// </summary>
        private static SerializedProperty FindTopLevelProperty(SerializedObject so, string propertyName, string normalizedName, out string error)
        {
            error = null;
            var prop = so.FindProperty(propertyName) ?? so.FindProperty(normalizedName);
            if (prop != null)
                return prop;

            // Compare against the raw name as well: NormalizePropertyName turns "m_sprite" into
            // "mSprite", which strips to "msprite" rather than "sprite".
            string rawKey = StripSerializedFieldPrefix(propertyName);
            string normalizedKey = StripSerializedFieldPrefix(normalizedName);
            string match = null;
            using var iter = so.GetIterator();
            bool enterChildren = true;
            while (iter.Next(enterChildren))
            {
                enterChildren = false;
                string candidate = StripSerializedFieldPrefix(iter.name);
                if (candidate != rawKey && candidate != normalizedKey)
                    continue;

                // Two fields that collapse to the same key (target_ and m_Target) would make
                // this pick whichever comes first; refuse rather than write the wrong one.
                if (match != null)
                {
                    error = $"Property '{propertyName}' matches more than one serialized field " + $"('{match}', '{iter.name}'); use the exact field name.";
                    return null;
                }
                match = iter.name;
            }

            return match != null ? so.FindProperty(match) : null;
        }

        private static string StripSerializedFieldPrefix(string name)
        {
            if (name.StartsWith("m_", StringComparison.OrdinalIgnoreCase))
                name = name.Substring(2);
            return name.Replace("_", "").ToLowerInvariant();
        }

        /// <summary>
        /// Finds a child SerializedProperty by name, falling back to underscore-insensitive matching.
        /// The batch_execute transport can strip underscores from JSON keys
        /// (e.g. m_PersistentCalls → mPersistentCalls), so we iterate immediate children
        /// and compare with underscores removed.
        /// </summary>
        private static SerializedProperty FindPropertyRelativeFuzzy(SerializedProperty parent, string key)
        {
            var child = parent.FindPropertyRelative(key);
            if (child != null)
                return child;

            string normalizedKey = key.Replace("_", "").ToLowerInvariant();

            using var end = parent.GetEndProperty();
            using var iter = parent.Copy();
            if (!iter.Next(true))
                return null;

            while (!SerializedProperty.EqualContents(iter, end))
            {
                if (iter.depth == parent.depth + 1)
                {
                    string normalizedName = iter.name.Replace("_", "").ToLowerInvariant();
                    if (normalizedName == normalizedKey)
                        return parent.FindPropertyRelative(iter.name);
                }
                if (!iter.Next(false))
                    break;
            }

            return null;
        }

        private static bool SetEnum(SerializedProperty prop, JToken value, out string error)
        {
            error = null;
            var names = prop.enumNames;
            if (names == null || names.Length == 0)
            {
                error = "Enum has no names.";
                return false;
            }

            if (value.Type == JTokenType.Integer)
            {
                int idx = value.Value<int>();
                if (idx < 0 || idx >= names.Length)
                {
                    error = $"Enum index out of range: {idx}.";
                    return false;
                }
                prop.enumValueIndex = idx;
                return true;
            }

            string s = value.ToString();
            for (int i = 0; i < names.Length; i++)
            {
                if (string.Equals(names[i], s, StringComparison.OrdinalIgnoreCase))
                {
                    prop.enumValueIndex = i;
                    return true;
                }
            }
            error = $"Unknown enum name '{s}'.";
            return false;
        }

        private static long GetSpriteFileId(Sprite sprite)
        {
            if (sprite == null)
                return 0;

            try
            {
                var globalId = GlobalObjectId.GetGlobalObjectIdSlow(sprite);
                return unchecked((long)globalId.targetObjectId);
            }
            catch (Exception ex)
            {
                McpLog.Warn($"Failed to get fileID for sprite '{sprite.name}' (instanceID={sprite.GetInstanceIDCompat()}): {ex.Message}");
                return 0;
            }
        }

        private static bool IsHexString(string str)
        {
            foreach (char c in str)
            {
                if (!((c >= '0' && c <= '9') || (c >= 'a' && c <= 'f') || (c >= 'A' && c <= 'F')))
                    return false;
            }
            return true;
        }
    }
}
