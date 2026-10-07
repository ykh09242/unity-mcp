#nullable disable
using System;
using System.Collections.Generic;
using System.Linq;
using System.Reflection;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Editor.Tools;
using MCPForUnity.Runtime.Helpers;
using MCPForUnity.Runtime.Serialization;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEngine;

namespace MCPForUnity.Editor.Tools.GameObjects
{
    internal static class GameObjectComponentHelpers
    {
        internal static string ValidateComponentType(string typeName, bool removal, out Type componentType)
        {
            componentType = FindType(typeName);
            if (componentType == null)
                return removal ? $"Component type '{typeName}' not found for removal." : $"Component type '{typeName}' not found or is not a valid Component.";
            if (!typeof(Component).IsAssignableFrom(componentType))
                return $"Type '{typeName}' is not a Component.";
            if (!removal && (componentType.IsAbstract || componentType.ContainsGenericParameters))
                return $"Component type '{typeName}' must be concrete and have no unbound generic parameters.";
            if (componentType == typeof(Transform))
                return removal ? "Cannot remove the Transform component." : "Cannot add another Transform component.";
            return null;
        }

        internal static object AddComponentInternal(GameObject targetGo, string typeName, JObject properties)
        {
            string typeError = ValidateComponentType(typeName, false, out Type componentType);
            if (typeError != null)
                return new ErrorResponse(typeError);

            bool isAdding2DPhysics = typeof(Rigidbody2D).IsAssignableFrom(componentType) || typeof(Collider2D).IsAssignableFrom(componentType);
            bool isAdding3DPhysics = typeof(Rigidbody).IsAssignableFrom(componentType) || typeof(Collider).IsAssignableFrom(componentType);

            if (isAdding2DPhysics)
            {
                if (targetGo.GetComponent<Rigidbody>() != null || targetGo.GetComponent<Collider>() != null)
                {
                    return new ErrorResponse(
                        $"Cannot add 2D physics component '{typeName}' because the GameObject '{targetGo.name}' already has a 3D Rigidbody or Collider."
                    );
                }
            }
            else if (isAdding3DPhysics)
            {
                if (targetGo.GetComponent<Rigidbody2D>() != null || targetGo.GetComponent<Collider2D>() != null)
                {
                    return new ErrorResponse(
                        $"Cannot add 3D physics component '{typeName}' because the GameObject '{targetGo.name}' already has a 2D Rigidbody or Collider."
                    );
                }
            }

            Component existingComponent = targetGo.GetComponent(componentType);
            if (existingComponent != null && !AllowsMultiple(componentType))
            {
                return new ErrorResponse($"Component '{typeName}' already exists on '{targetGo.name}' and this type does not allow multiple instances.");
            }

            var plannedTypes = targetGo.GetComponents<Component>().Where(component => component != null).Select(component => component.GetType()).ToList();
            string planError = ValidateAdditionPlan(plannedTypes, new[] { componentType });
            if (planError != null)
                return new ErrorResponse(planError);
            string propertyError = ValidateAdditionProperties(
                componentType,
                properties,
                targetGo.transform.GetType(),
                out bool needsRuntimeValidation,
                (type, value) => IsPlannedTargetReference(type, value, targetGo.name, targetGo, plannedTypes),
                targetGo
            );
            if (propertyError != null)
                return new ErrorResponse(propertyError);

            var originalComponents = targetGo.GetComponents<Component>();
            var originalBindings = targetGo.GetComponents<MeshFilter>().ToDictionary(filter => filter, filter => filter.sharedMesh);
            var originalMeshes = needsRuntimeValidation ? new HashSet<Mesh>(UnityEngine.Resources.FindObjectsOfTypeAll<Mesh>()) : null;
            bool completed = false;
            try
            {
                Component newComponent = Undo.AddComponent(targetGo, componentType);
                if (newComponent == null)
                {
                    if (targetGo.GetComponent(componentType) != null && !AllowsMultiple(componentType))
                    {
                        return new ErrorResponse(
                            $"Component '{typeName}' already exists on '{targetGo.name}' and this type does not allow multiple instances."
                        );
                    }

                    return new ErrorResponse(
                        $"Failed to add component '{typeName}' to '{targetGo.name}'. Unity may restrict this component on the current target."
                    );
                }

                if (newComponent is Light light)
                {
                    light.type = LightType.Directional;
                }

                if (properties != null)
                {
                    var setResult = SetComponentPropertiesInternal(targetGo, typeName, properties, newComponent);
                    if (setResult != null)
                    {
                        return setResult;
                    }
                }

                completed = true;
                return null;
            }
            catch (Exception e)
            {
                return new ErrorResponse($"Error adding component '{typeName}' to '{targetGo.name}': {e.Message}");
            }
            finally
            {
                if (!completed)
                {
                    var newMeshes = new HashSet<Mesh>();
                    foreach (MeshFilter filter in targetGo.GetComponents<MeshFilter>())
                    {
                        Mesh mesh = filter.sharedMesh;
                        if (mesh != null && originalMeshes != null && !originalMeshes.Contains(mesh) && !AssetDatabase.Contains(mesh))
                            newMeshes.Add(mesh);
                        // Keep borrowed inputs alive if a component's OnDestroy deletes its binding.
                        filter.sharedMesh = null;
                    }
                    foreach (Component component in targetGo.GetComponents<Component>().Reverse())
                        if (component != null && !originalComponents.Contains(component) && !(component is Transform))
                            Undo.DestroyObjectImmediate(component);
                    foreach (var binding in originalBindings)
                        if (binding.Key != null)
                            binding.Key.sharedMesh = binding.Value;
                    foreach (Mesh mesh in newMeshes)
                        if (mesh != null)
                            UnityEngine.Object.DestroyImmediate(mesh);
                }
            }
        }

        // Shared by request preflight and Add: no component getters or allocation are needed.
        internal static string ValidateAdditionProperties(
            Type componentType,
            JObject properties,
            Type targetTransformType,
            out bool needsRuntimeValidation,
            Func<Type, JToken, bool> canResolveAfterAddition = null,
            GameObject targetContext = null
        )
        {
            needsRuntimeValidation = false;
            if (properties != null)
            {
                foreach (JProperty property in properties.Properties())
                {
                    string error;
                    if (property.Name.Contains('.') || property.Name.Contains('['))
                    {
                        // A prefab's Transform subtype is known once its asset is resolved.
                        if (targetTransformType == null && property.Name.StartsWith("transform.", StringComparison.OrdinalIgnoreCase))
                        {
                            needsRuntimeValidation = true;
                            continue;
                        }
                        if (
                            !TryPrepareNestedProperty(
                                componentType,
                                property.Name,
                                property.Value,
                                out _,
                                out bool nestedPrepared,
                                out error,
                                typeof(Transform).IsAssignableFrom(componentType) ? componentType : targetTransformType
                            )
                        )
                            return error;
                        if (!nestedPrepared && RequiresTransformReplacement(componentType))
                            return $"Cannot validate '{property.Name}' before replacing the target Transform.";
                        needsRuntimeValidation |= !nestedPrepared;
                    }
                    else if (!ComponentOps.TryPrepareProperty(componentType, property.Name, property.Value, out _, out error))
                    {
                        Type memberType = ComponentOps.ResolveMemberType(componentType, property.Name, ParamCoercion.NormalizePropertyName(property.Name));
                        if (
                            canResolveAfterAddition != null
                            && memberType != null
                            && ValidateDeferredReferenceValue(memberType, property.Value, canResolveAfterAddition)
                            && ComponentOps.TryPrepareProperty(componentType, property.Name, JValue.CreateNull(), out _, out _)
                        )
                        {
                            needsRuntimeValidation = true;
                            continue;
                        }
                        bool unprovenNativeField =
                            property.Name.StartsWith("m_", StringComparison.OrdinalIgnoreCase)
                            && ComponentOps.ResolveMemberType(componentType, property.Name, ParamCoercion.NormalizePropertyName(property.Name)) == null;
                        // Native-only schema cannot be inspected without creating a component and its dependencies.
                        // Keep addition preflight free of those side effects; existing-component serialized editing remains supported.
                        return unprovenNativeField
                            ? $"Cannot validate native-only serialized property '{property.Name}' before adding '{componentType.Name}'. Add the component explicitly, then edit this property on the existing component."
                            : error;
                    }
                }
            }

            if (
                properties != null
                && !ComponentOps.TryValidatePropertyOwners(componentType, null, properties, out string ownerError, targetContext, canResolveAfterAddition)
            )
                return ownerError;
            return null;
        }

        private static bool ValidateDeferredReferenceValue(Type type, JToken value, Func<Type, JToken, bool> canResolveAfterAddition)
        {
            if (typeof(UnityEngine.Object).IsAssignableFrom(type))
                return ComponentOps.TryPreparePropertyValue(type, value, out _, out _) || canResolveAfterAddition(type, value);
            Type elementType =
                type.IsArray ? type.GetElementType()
                : type.IsGenericType && typeof(System.Collections.IList).IsAssignableFrom(type) ? type.GetGenericArguments()[0]
                : null;
            if (elementType == null || !(value is JArray array))
                return false;
            foreach (JToken item in array)
                if (
                    !ComponentOps.TryPreparePropertyValue(elementType, item, out _, out _)
                    && !ValidateDeferredReferenceValue(elementType, item, canResolveAfterAddition)
                )
                    return false;
            return true;
        }

        internal static bool IsPlannedTargetReference(Type expectedType, JToken value, string finalName, GameObject target, IEnumerable<Type> plannedTypes)
        {
            if (!typeof(UnityEngine.Object).IsAssignableFrom(expectedType) || value == null)
                return false;
            string name = value.Type == JTokenType.String ? value.ToString() : (value as JObject)?["name"]?.ToString();
            JToken id = value.Type == JTokenType.Integer ? value : (value as JObject)?["instanceID"];
            if (value is JObject reference && (reference["path"] != null || reference["guid"] != null))
                return false;
            bool matches =
                id != null
                    ? target != null && int.TryParse(id.ToString(), out int instanceId) && instanceId == target.GetInstanceIDCompat()
                    : !string.IsNullOrEmpty(finalName) && name == finalName;
            if (!matches && target != null && value.Type == JTokenType.String && int.TryParse(name, out int stringId))
                matches = stringId == target.GetInstanceIDCompat();
            if (!matches)
                return false;
            string filter = (value as JObject)?["component"]?.ToString();
            if (!string.IsNullOrEmpty(filter))
                return plannedTypes.Any(type =>
                    (
                        string.Equals(type.Name, filter, StringComparison.OrdinalIgnoreCase)
                        || string.Equals(type.FullName, filter, StringComparison.OrdinalIgnoreCase)
                    ) && expectedType.IsAssignableFrom(type)
                );
            return expectedType.IsAssignableFrom(typeof(GameObject)) || plannedTypes.Any(expectedType.IsAssignableFrom);
        }

        private static bool RequiresTransformReplacement(Type componentType)
        {
            return RequiresTransformReplacement(componentType, new HashSet<Type>());
        }

        internal static string ValidateAdditionPlan(List<Type> presentTypes, IEnumerable<Type> additions)
        {
            foreach (Type addition in additions)
            {
                string error = ValidatePlannedType(presentTypes, addition, false, new HashSet<Type>());
                if (error != null)
                    return error;
            }
            return null;
        }

        private static string ValidatePlannedType(List<Type> presentTypes, Type type, bool dependency, HashSet<Type> visiting)
        {
            if (type == null || (dependency && presentTypes.Any(type.IsAssignableFrom)) || !visiting.Add(type))
                return null;
            Type disallowedRoot = null;
            for (Type current = type; current != null && typeof(Component).IsAssignableFrom(current); current = current.BaseType)
                if (Attribute.IsDefined(current, typeof(DisallowMultipleComponent), inherit: false))
                    disallowedRoot = current;
            if (disallowedRoot != null && presentTypes.Any(disallowedRoot.IsAssignableFrom))
                return $"Component '{type.Name}' already exists or is already planned and this type does not allow multiple instances.";

            bool is2D = typeof(Rigidbody2D).IsAssignableFrom(type) || typeof(Collider2D).IsAssignableFrom(type);
            bool is3D = typeof(Rigidbody).IsAssignableFrom(type) || typeof(Collider).IsAssignableFrom(type);
            if (
                (is2D && presentTypes.Any(t => typeof(Rigidbody).IsAssignableFrom(t) || typeof(Collider).IsAssignableFrom(t)))
                || (is3D && presentTypes.Any(t => typeof(Rigidbody2D).IsAssignableFrom(t) || typeof(Collider2D).IsAssignableFrom(t)))
            )
                return $"Cannot add physics component '{type.Name}' because the current or planned components mix 2D and 3D physics.";

            foreach (RequireComponent requirement in type.GetCustomAttributes(typeof(RequireComponent), true))
            foreach (Type required in new[] { requirement.m_Type0, requirement.m_Type1, requirement.m_Type2 })
            {
                string error = ValidatePlannedType(presentTypes, required, true, visiting);
                if (error != null)
                    return error;
            }
            presentTypes.Add(type);
            visiting.Remove(type);
            return null;
        }

        private static bool RequiresTransformReplacement(Type componentType, HashSet<Type> visited)
        {
            if (componentType == null || !visited.Add(componentType))
                return false;
            if (typeof(Transform).IsAssignableFrom(componentType) && componentType != typeof(Transform))
                return true;
            foreach (RequireComponent requirement in componentType.GetCustomAttributes(typeof(RequireComponent), true))
                if (
                    RequiresTransformReplacement(requirement.m_Type0, visited)
                    || RequiresTransformReplacement(requirement.m_Type1, visited)
                    || RequiresTransformReplacement(requirement.m_Type2, visited)
                )
                    return true;
            return false;
        }

        private static bool AllowsMultiple(Type componentType)
        {
            if (componentType == null)
            {
                return false;
            }

            return !Attribute.IsDefined(componentType, typeof(DisallowMultipleComponent), inherit: true);
        }

        internal static object RemoveComponentInternal(GameObject targetGo, string typeName)
        {
            if (targetGo == null)
            {
                return new ErrorResponse("Target GameObject is null.");
            }

            string typeError = ValidateComponentType(typeName, true, out Type componentType);
            if (typeError != null)
                return new ErrorResponse(typeError);

            Component componentToRemove = targetGo.GetComponent(componentType);
            if (componentToRemove == null)
            {
                return new ErrorResponse($"Component '{typeName}' not found on '{targetGo.name}' to remove.");
            }

            try
            {
                Undo.DestroyObjectImmediate(componentToRemove);
                return null;
            }
            catch (Exception e)
            {
                return new ErrorResponse($"Error removing component '{typeName}' from '{targetGo.name}': {e.Message}");
            }
        }

        /// <summary>
        /// Applies a "componentProperties" object (as accepted by 'modify') to every named
        /// component already present on <paramref name="targetGo"/>. Shared by 'create' and
        /// 'modify' so the argument behaves identically on both actions.
        /// </summary>
        /// <param name="modified">Set to true if at least one property was set successfully.</param>
        /// <returns>An ErrorResponse aggregating any per-component failures, or null if all
        /// (or none) of the requested properties were applied successfully.</returns>
        internal static object ApplyComponentProperties(GameObject targetGo, JObject componentPropertiesObj, out bool modified)
        {
            modified = false;
            if (componentPropertiesObj == null)
            {
                return null;
            }

            var componentErrors = new List<object>();
            foreach (var prop in componentPropertiesObj.Properties())
            {
                string compName = prop.Name;
                JObject propertiesToSet = prop.Value as JObject;
                if (propertiesToSet != null)
                {
                    var setResult = SetComponentPropertiesInternal(targetGo, compName, propertiesToSet);
                    if (setResult != null)
                    {
                        componentErrors.Add(setResult);
                    }
                    else
                    {
                        modified = true;
                    }
                }
            }

            if (componentErrors.Count == 0)
            {
                return null;
            }

            var aggregatedErrors = new List<string>();
            foreach (var errorObj in componentErrors)
            {
                try
                {
                    var dataProp = errorObj?.GetType().GetProperty("data");
                    var dataVal = dataProp?.GetValue(errorObj);
                    if (dataVal != null)
                    {
                        var errorsProp = dataVal.GetType().GetProperty("errors");
                        var errorsEnum = errorsProp?.GetValue(dataVal) as System.Collections.IEnumerable;
                        if (errorsEnum != null)
                        {
                            foreach (var item in errorsEnum)
                            {
                                var s = item?.ToString();
                                if (!string.IsNullOrEmpty(s))
                                    aggregatedErrors.Add(s);
                            }
                        }
                    }
                }
                catch (Exception ex)
                {
                    McpLog.Warn($"[ManageGameObject] Error aggregating component errors: {ex.Message}");
                }
            }

            return new ErrorResponse(
                $"One or more component property operations failed on '{targetGo.name}'.",
                new { componentErrors = componentErrors, errors = aggregatedErrors }
            );
        }

        internal static object SetComponentPropertiesInternal(
            GameObject targetGo,
            string componentTypeName,
            JObject properties,
            Component targetComponentInstance = null
        )
        {
            Component targetComponent = targetComponentInstance;
            if (targetComponent == null)
            {
                if (ComponentResolver.TryResolve(componentTypeName, out var compType, out var compError))
                {
                    targetComponent = targetGo.GetComponent(compType);
                }
                else
                {
                    targetComponent = targetGo.GetComponent(componentTypeName);
                }
            }
            if (targetComponent == null)
            {
                return new ErrorResponse($"Component '{componentTypeName}' not found on '{targetGo.name}' to set properties.");
            }

            Undo.RecordObject(targetComponent, "Set Component Properties");

            var failures = new List<string>();
            bool anyPropertySet = false;
            foreach (var prop in properties.Properties())
            {
                string propName = prop.Name;
                JToken propValue = prop.Value;

                try
                {
                    bool setResult;
                    string setError;

                    // Nested paths (e.g. "transform.position") need local handling
                    // since ComponentOps doesn't support dot/bracket notation.
                    if (propName.Contains('.') || propName.Contains('['))
                    {
                        setResult = SetNestedProperty(targetComponent, propName, propValue, InputSerializer, out setError);
                    }
                    else
                    {
                        // ComponentOps handles reflection + SerializedProperty fallback
                        setResult = ComponentOps.SetProperty(targetComponent, propName, propValue, out setError);
                    }

                    if (!setResult)
                    {
                        string msg = setError;
                        if (msg == null || msg.Contains("not found"))
                        {
                            var availableProperties = ComponentResolver.GetAllComponentProperties(targetComponent.GetType());
                            var suggestions = ComponentResolver.GetFuzzyPropertySuggestions(propName, availableProperties);
                            msg = suggestions.Any()
                                ? $"Property '{propName}' not found. Did you mean: {string.Join(", ", suggestions)}? Available: [{string.Join(", ", availableProperties)}]"
                                : $"Property '{propName}' not found. Available: [{string.Join(", ", availableProperties)}]";
                        }
                        McpLog.Warn($"[ManageGameObject] {msg}");
                        failures.Add(msg);
                    }
                    else
                    {
                        anyPropertySet = true;
                    }
                }
                catch (Exception e)
                {
                    McpLog.Error($"[ManageGameObject] Error setting property '{propName}' on '{componentTypeName}': {e.Message}");
                    failures.Add($"Error setting '{propName}': {e.Message}");
                }
            }

            if (anyPropertySet)
                EditorUtility.SetDirty(targetComponent);
            return failures.Count == 0 ? null : new ErrorResponse($"One or more properties failed on '{componentTypeName}'.", new { errors = failures });
        }

        private static JsonSerializer InputSerializer => UnityJsonSerializer.Instance;

        private static bool TryPrepareNestedProperty(
            Type type,
            string path,
            JToken value,
            out object converted,
            out bool prepared,
            out string error,
            Type transformType = null,
            object runtimeTarget = null
        )
        {
            converted = null;
            prepared = false;
            error = null;
            string[] parts = SplitPropertyPath(path);
            BindingFlags flags = BindingFlags.Public | BindingFlags.Instance | BindingFlags.IgnoreCase;
            object runtimeOwner = runtimeTarget;
            bool materialGetter = false;
            for (int i = 0; i < parts.Length; i++)
            {
                string name = parts[i];
                bool indexed = false;
                int arrayIndex = -1;
                int bracket = name.IndexOf('[');
                if (bracket > 0 && name.EndsWith("]") && int.TryParse(name.Substring(bracket + 1, name.Length - bracket - 2), out arrayIndex))
                {
                    indexed = true;
                    name = name.Substring(0, bracket);
                }
                if (i == parts.Length - 1 && typeof(Material).IsAssignableFrom(type) && name.StartsWith("_", StringComparison.Ordinal))
                    return true; // The shader determines this member; retain the existing shader-property path.
                var property = type.GetProperty(name, flags);
                var field = property == null ? type.GetField(name, flags) ?? ComponentOps.FindSerializedFieldInHierarchy(type, name) : null;
                Type memberType = property?.PropertyType ?? field?.FieldType;
                if (
                    memberType != null
                    && transformType != null
                    && typeof(Component).IsAssignableFrom(type)
                    && string.Equals(name, "transform", StringComparison.OrdinalIgnoreCase)
                )
                    memberType = transformType;
                if (memberType == null)
                {
                    // A declared base type may hide a runtime member (for example Transform -> RectTransform).
                    // Material getters instantiate native resources, so their known type must be checked first.
                    if (!materialGetter && !typeof(UnityEngine.Object).IsAssignableFrom(type) && !type.IsValueType && !type.IsSealed)
                        return true;
                    error = $"Property or field '{name}' not found on type '{type.Name}' in path '{path}'.";
                    return false;
                }
                if (i == parts.Length - 1)
                {
                    if ((property != null && !property.CanWrite) || indexed)
                    {
                        error = $"Property '{name}' is not writable in path '{path}'.";
                        return false;
                    }
                    prepared = ComponentOps.TryPreparePropertyValue(memberType, value, out converted, out error);
                    return prepared;
                }
                materialGetter |=
                    typeof(Renderer).IsAssignableFrom(type)
                    && (
                        string.Equals(name, "material", StringComparison.OrdinalIgnoreCase)
                        || string.Equals(name, "materials", StringComparison.OrdinalIgnoreCase)
                    );
                type = memberType;
                if (runtimeOwner != null)
                {
                    if (runtimeOwner is Mesh source && memberType.IsArray && !source.isReadable)
                    {
                        error = $"Mesh '{source.name}' must be readable to access '{name}'.";
                        return false;
                    }
                    runtimeOwner = ComponentOps.TryReadBorrowedMember(runtimeOwner, (MemberInfo)property ?? field, out object borrowed) ? borrowed : null;
                }
                if (indexed)
                {
                    if (type.IsArray)
                        type = type.GetElementType();
                    else if (type.IsGenericType && typeof(System.Collections.IList).IsAssignableFrom(type))
                        type = type.GetGenericArguments()[0];
                    else
                        return true;
                    if (runtimeOwner is System.Collections.IList list)
                    {
                        if (arrayIndex < 0 || arrayIndex >= list.Count)
                        {
                            error = $"Index {arrayIndex} is out of range in path '{path}'.";
                            return false;
                        }
                        runtimeOwner = list[arrayIndex];
                    }
                    else
                        runtimeOwner = null;
                }
                if (runtimeOwner != null)
                    type = runtimeOwner.GetType();
            }
            return true;
        }

        private static bool SetNestedProperty(object target, string path, JToken value, JsonSerializer inputSerializer, out string error)
        {
            error = null;
            try
            {
                if (
                    !TryPrepareNestedProperty(
                        target.GetType(),
                        path,
                        value,
                        out object preparedValue,
                        out bool prepared,
                        out error,
                        (target as Component)?.transform.GetType(),
                        target
                    )
                )
                    return false;
                string[] pathParts = SplitPropertyPath(path);
                if (pathParts.Length == 0)
                {
                    error = $"Invalid nested property path '{path}'.";
                    return false;
                }

                object currentObject = target;
                Type currentType = currentObject.GetType();
                BindingFlags flags = BindingFlags.Public | BindingFlags.Instance | BindingFlags.IgnoreCase;

                for (int i = 0; i < pathParts.Length - 1; i++)
                {
                    string part = pathParts[i];
                    bool isArray = false;
                    int arrayIndex = -1;

                    if (part.Contains("["))
                    {
                        int startBracket = part.IndexOf('[');
                        int endBracket = part.IndexOf(']');
                        if (startBracket > 0 && endBracket > startBracket)
                        {
                            string indexStr = part.Substring(startBracket + 1, endBracket - startBracket - 1);
                            if (int.TryParse(indexStr, out arrayIndex))
                            {
                                isArray = true;
                                part = part.Substring(0, startBracket);
                            }
                        }
                    }

                    PropertyInfo propInfo = currentType.GetProperty(part, flags);
                    FieldInfo fieldInfo = null;
                    if (propInfo == null)
                    {
                        fieldInfo = currentType.GetField(part, flags);
                        if (fieldInfo == null)
                        {
                            error = $"Could not find property or field '{part}' on type '{currentType.Name}' in path '{path}'.";
                            return false;
                        }
                    }

                    if (
                        currentObject is Renderer renderer
                        && (
                            string.Equals(part, "material", StringComparison.OrdinalIgnoreCase)
                            || string.Equals(part, "materials", StringComparison.OrdinalIgnoreCase)
                        )
                    )
                    {
                        Material borrowed = null;
                        if (string.Equals(part, "material", StringComparison.OrdinalIgnoreCase))
                            borrowed = renderer.sharedMaterial;
                        else if (string.Equals(part, "materials", StringComparison.OrdinalIgnoreCase))
                        {
                            Material[] materials = renderer.sharedMaterials;
                            if (isArray && arrayIndex >= 0 && arrayIndex < materials.Length)
                                borrowed = materials[arrayIndex];
                        }
                        if (borrowed == null)
                        {
                            error = $"Material or material index is unavailable in path '{path}'.";
                            return false;
                        }
                        if (
                            i == pathParts.Length - 2
                            && pathParts[pathParts.Length - 1].StartsWith("_", StringComparison.Ordinal)
                            && !MaterialOps.TryPrepareShaderProperty(borrowed, pathParts[pathParts.Length - 1], value, inputSerializer, out _)
                        )
                        {
                            error = $"Invalid shader property or value '{pathParts[pathParts.Length - 1]}' in path '{path}'.";
                            return false;
                        }
                    }

                    if (currentObject is MeshFilter meshFilter && string.Equals(part, "mesh", StringComparison.OrdinalIgnoreCase))
                    {
                        Mesh borrowed = meshFilter.sharedMesh;
                        if (borrowed == null)
                        {
                            error = $"Shared mesh is unavailable in path '{path}'.";
                            return false;
                        }
                        if (!ValidateBorrowedMeshIndices(borrowed, pathParts, i + 1, out error))
                            return false;
                    }

                    currentObject = propInfo != null ? propInfo.GetValue(currentObject) : fieldInfo.GetValue(currentObject);
                    if (currentObject == null)
                    {
                        error = $"Property '{part}' is null in path '{path}', cannot access nested properties.";
                        return false;
                    }

                    if (isArray)
                    {
                        if (currentObject is Material[])
                        {
                            var materials = currentObject as Material[];
                            if (materials.Length == 0)
                            {
                                error = $"Material array is empty in path '{path}', cannot access index {arrayIndex}.";
                                return false;
                            }
                            if (arrayIndex < 0 || arrayIndex >= materials.Length)
                            {
                                error = $"Material index {arrayIndex} out of range (0-{materials.Length - 1}) in path '{path}'.";
                                return false;
                            }
                            currentObject = materials[arrayIndex];
                        }
                        else if (currentObject is System.Collections.IList)
                        {
                            var list = currentObject as System.Collections.IList;
                            if (list.Count == 0)
                            {
                                error = $"List is empty in path '{path}', cannot access index {arrayIndex}.";
                                return false;
                            }
                            if (arrayIndex < 0 || arrayIndex >= list.Count)
                            {
                                error = $"Index {arrayIndex} out of range (0-{list.Count - 1}) in path '{path}'.";
                                return false;
                            }
                            currentObject = list[arrayIndex];
                        }
                        else
                        {
                            error = $"Property '{part}' is not an array or list in path '{path}', cannot access by index.";
                            return false;
                        }
                    }

                    currentType = currentObject.GetType();
                }

                string finalPart = pathParts[pathParts.Length - 1];

                if (currentObject is Material material && finalPart.StartsWith("_"))
                {
                    if (!MaterialOps.TrySetShaderProperty(material, finalPart, value, inputSerializer))
                    {
                        error = $"Failed to set shader property '{finalPart}' on material '{material.name}' in path '{path}'.";
                        return false;
                    }
                    return true;
                }

                PropertyInfo finalPropInfo = currentType.GetProperty(finalPart, flags);
                if (finalPropInfo != null && finalPropInfo.CanWrite)
                {
                    object convertedValue = prepared ? preparedValue : ConvertJTokenToType(value, finalPropInfo.PropertyType, inputSerializer);
                    if (convertedValue != null || value.Type == JTokenType.Null)
                    {
                        finalPropInfo.SetValue(currentObject, convertedValue);
                        return true;
                    }
                    error = $"Failed to convert value for '{finalPart}' to type '{finalPropInfo.PropertyType.Name}' in path '{path}'.";
                    return false;
                }

                FieldInfo finalFieldInfo = currentType.GetField(finalPart, flags);
                if (finalFieldInfo != null)
                {
                    object convertedValue = prepared ? preparedValue : ConvertJTokenToType(value, finalFieldInfo.FieldType, inputSerializer);
                    if (convertedValue != null || value.Type == JTokenType.Null)
                    {
                        finalFieldInfo.SetValue(currentObject, convertedValue);
                        return true;
                    }
                    error = $"Failed to convert value for '{finalPart}' to type '{finalFieldInfo.FieldType.Name}' in path '{path}'.";
                    return false;
                }

                // Try non-public [SerializeField] fields (nested paths need this too)
                FieldInfo serializedField = ComponentOps.FindSerializedFieldInHierarchy(currentType, finalPart);
                if (serializedField != null)
                {
                    object convertedValue = prepared ? preparedValue : ConvertJTokenToType(value, serializedField.FieldType, inputSerializer);
                    if (convertedValue != null || value.Type == JTokenType.Null)
                    {
                        serializedField.SetValue(currentObject, convertedValue);
                        return true;
                    }
                    error = $"Failed to convert value for '{finalPart}' to type '{serializedField.FieldType.Name}' in path '{path}'.";
                    return false;
                }

                error = $"Property or field '{finalPart}' not found on type '{currentType.Name}' in path '{path}'.";
            }
            catch (Exception ex)
            {
                error = $"Error setting nested property '{path}': {ex.Message}";
            }

            return false;
        }

        private static bool ValidateBorrowedMeshIndices(Mesh mesh, string[] parts, int start, out string error)
        {
            error = null;
            object current = mesh;
            var flags = BindingFlags.Public | BindingFlags.Instance | BindingFlags.IgnoreCase;
            for (int i = start; i < parts.Length - 1; i++)
            {
                string name = parts[i];
                int bracket = name.IndexOf('[');
                if (bracket <= 0 || !name.EndsWith("]") || !int.TryParse(name.Substring(bracket + 1, name.Length - bracket - 2), out int index))
                    return true;
                // Only native Mesh arrays are inspected; arbitrary component getters are not evaluated.
                if (!(current is Mesh source))
                    return true;
                if (!source.isReadable)
                {
                    error = $"Mesh '{source.name}' must be readable to access '{name}'.";
                    return false;
                }
                string member = name.Substring(0, bracket);
                var array = typeof(Mesh).GetProperty(member, flags)?.GetValue(source) as System.Collections.IList;
                if (array == null || index < 0 || index >= array.Count)
                {
                    error = $"Mesh array index {index} is out of range in '{name}'.";
                    return false;
                }
                current = array[index];
            }
            return true;
        }

        private static string[] SplitPropertyPath(string path) => ComponentOps.SplitPropertyPath(path);

        private static object ConvertJTokenToType(JToken token, Type targetType, JsonSerializer inputSerializer)
        {
            return PropertyConversion.ConvertToType(token, targetType);
        }

        private static Type FindType(string typeName)
        {
            if (ComponentResolver.TryResolve(typeName, out Type resolvedType, out string error))
            {
                return resolvedType;
            }

            if (!string.IsNullOrEmpty(error))
            {
                McpLog.Warn($"[FindType] {error}");
            }

            return null;
        }
    }
}
