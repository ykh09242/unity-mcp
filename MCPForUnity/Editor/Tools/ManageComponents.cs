using System;
using System.Collections.Generic;
using System.Linq;
using System.Reflection;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Editor.Services;
using MCPForUnity.Runtime.Helpers;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;

namespace MCPForUnity.Editor.Tools
{
    /// <summary>
    /// Tool for managing components on GameObjects.
    /// Actions: add, remove, set_property
    ///
    /// This is a focused tool for component lifecycle operations.
    /// For reading component data, use the unity://scene/gameobject/{id}/components resource.
    /// </summary>
    [McpForUnityTool("manage_components_selected", AutoRegister = false)]
    public static class ManageComponentsSelected
    {
        public static object HandleCommand(JObject parameters) => ManageComponents.HandleSelected(parameters);
    }

    [McpForUnityTool("manage_components")]
    public static class ManageComponents
    {
        /// <summary>
        /// Handles the manage_components command.
        /// </summary>
        /// <param name="params">Command parameters</param>
        /// <returns>Result of the component operation</returns>
        public static object HandleCommand(JObject @params)
        {
            if (@params == null)
            {
                return new ErrorResponse("Parameters cannot be null.");
            }

            try
            {
                if (ComponentSelection.IsRequested(@params, false))
                    return HandleSelected(@params);
            }
            catch (ComponentSelection.Failure failure)
            {
                return failure.Response();
            }

            string action = ParamCoercion.CoerceString(@params["action"], null)?.ToLowerInvariant();
            if (string.IsNullOrEmpty(action))
            {
                return new ErrorResponse("'action' parameter is required (add, remove, set_property).");
            }

            // Target resolution
            JToken targetToken = @params["target"];
            string searchMethod = ParamCoercion.CoerceString(@params["searchMethod"] ?? @params["search_method"], null);

            if (targetToken == null)
            {
                return new ErrorResponse("'target' parameter is required.");
            }

            try
            {
                return action switch
                {
                    "add" => AddComponent(@params, targetToken, searchMethod),
                    "remove" => RemoveComponent(@params, targetToken, searchMethod),
                    "set_property" => SetProperty(@params, targetToken, searchMethod),
                    _ => new ErrorResponse($"Unknown action: '{action}'. Supported actions: add, remove, set_property"),
                };
            }
            catch (ArgumentException e)
            {
                return new ErrorResponse(e.Message);
            }
            catch (Exception e)
            {
                McpLog.Error($"[ManageComponents] Action '{action}' failed: {e}");
                return new ErrorResponse($"Internal error processing action '{action}': {e.Message}");
            }
        }

        internal static object HandleSelected(JObject parameters)
        {
            try
            {
                if (!MCPServiceLocator.ToolDiscovery.IsToolEnabled("manage_components"))
                    return new ComponentSelection.Failure("tool_disabled", "Tool 'manage_components' is disabled in the Unity Editor.").Response();
                if (parameters == null)
                    return new ComponentSelection.Failure("invalid_selection", "Parameters cannot be null.").Response();
                ComponentSelection.ValidateSelectors(parameters, out int? index, out int? componentId);
                JToken actionToken = parameters["action"];
                if (actionToken?.Type != JTokenType.String)
                    throw new ComponentSelection.Failure("invalid_selection", "action must be add, remove or set_property.");
                string action = actionToken.ToString().ToLowerInvariant();
                if (action != "add" && action != "remove" && action != "set_property")
                    throw new ComponentSelection.Failure("invalid_selection", "action must be add, remove or set_property.");
                if (action == "add" && (index.HasValue || componentId.HasValue))
                    throw new ComponentSelection.Failure("invalid_selection", "add creates a new component and cannot select an existing component.");
                JToken typeToken = ComponentSelection.Alias(parameters, "componentType", "component_type");
                if (typeToken?.Type != JTokenType.String)
                    throw new ComponentSelection.Failure("invalid_component_type", "component_type must be a canonical component type name.");
                string typeName = typeToken.ToString();
                ComponentSelection.BeforeTargetLookupForTests?.Invoke();
                GameObject target = FindUniqueTarget(parameters);
                Type addType = action == "add" ? ComponentSelection.ResolveType(typeName) : null;
                ComponentSelection.Result selection = action == "add" ? null : ComponentSelection.Resolve(target, typeName, index, componentId);
                JObject description = selection?.Describe();
                object raw = action switch
                {
                    "add" => AddComponent(parameters, parameters["target"], null, target, addType),
                    "remove" => RemoveComponent(parameters, parameters["target"], null, selection),
                    _ => SetProperty(parameters, parameters["target"], null, selection),
                };
                JObject response = JObject.FromObject(raw);
                if (description != null)
                {
                    var data = response["data"] as JObject ?? new JObject();
                    data["selection"] = description;
                    response["data"] = data;
                }
                return response;
            }
            catch (ComponentSelection.Failure failure)
            {
                return failure.Response();
            }
            catch (ArgumentException exception)
            {
                return new ErrorResponse(exception.Message);
            }
            catch (Exception exception)
            {
                McpLog.Error($"[ManageComponents] Selected operation failed: {exception}");
                return new ErrorResponse($"Internal error processing selected component operation: {exception.Message}");
            }
        }

        private static GameObject FindUniqueTarget(JObject parameters)
        {
            JToken target = parameters["target"];
            if (
                target == null
                || (target.Type != JTokenType.Integer && target.Type != JTokenType.String)
                || string.IsNullOrWhiteSpace(target.ToString())
                || target.ToString().Length > 1024
            )
                throw new ComponentSelection.Failure("invalid_target", "target must be an Int32 ID or a name/path of at most 1024 characters.");
            bool numeric = int.TryParse(target.ToString(), System.Globalization.NumberStyles.Integer, System.Globalization.CultureInfo.InvariantCulture, out _);
            if (target.Type == JTokenType.Integer && !numeric)
                throw new ComponentSelection.Failure("invalid_target", "target ID is outside the Int32 range.");
            JToken methodToken = ComponentSelection.Alias(parameters, "searchMethod", "search_method");
            if (methodToken != null && methodToken.Type != JTokenType.Null && methodToken.Type != JTokenType.String)
                throw new ComponentSelection.Failure("invalid_selection", "search_method must be by_id, by_name or by_path.");
            string text = target.ToString().Trim();
            string digits = text.StartsWith("+", StringComparison.Ordinal) || text.StartsWith("-", StringComparison.Ordinal) ? text.Substring(1) : text;
            bool numericText = digits.Length > 0 && digits.All(character => character >= '0' && character <= '9');
            string method =
                ComponentSelection.HasValue(methodToken) ? methodToken.ToString().ToLowerInvariant()
                : numericText ? "by_id"
                : "by_name";
            if (method != "by_id" && method != "by_name" && method != "by_path")
                throw new ComponentSelection.Failure("invalid_selection", "search_method must be by_id, by_name or by_path.");
            if (method == "by_id" && !numeric)
                throw new ComponentSelection.Failure("invalid_target", "by_id requires an Int32 instance ID.");
            var matches = GameObjectLookup.SearchGameObjects(method, target.ToString(), true, 2);
            if (matches.Count == 0)
                throw new ComponentSelection.Failure("target_not_found", "No GameObject matches the selected target.");
            if (matches.Count != 1)
                throw new ComponentSelection.Failure(
                    "ambiguous_target",
                    "Target is ambiguous within the active scene/current prefab stage; use an instance ID or unique path.",
                    new JObject
                    {
                        ["matchCountLowerBound"] = 2,
                        ["countExact"] = false,
                        ["instanceIDs"] = new JArray(matches),
                    }
                );
            GameObject resolved = GameObjectLookup.FindById(matches[0]);
            ComponentSelection.EnsureTarget(resolved);
            return resolved;
        }

        #region Action Implementations

        private static object AddComponent(JObject @params, JToken targetToken, string searchMethod, GameObject selectedTarget = null, Type selectedType = null)
        {
            GameObject targetGo = selectedTarget != null ? selectedTarget : FindTarget(targetToken, searchMethod);
            if (targetGo == null)
            {
                return new ErrorResponse($"Target GameObject ('{targetToken}') not found using method '{searchMethod ?? "default"}'.");
            }

            string componentTypeName = ParamCoercion.CoerceString(@params["componentType"] ?? @params["component_type"], null);
            if (string.IsNullOrEmpty(componentTypeName))
            {
                return new ErrorResponse("'componentType' parameter is required for 'add' action.");
            }

            // Resolve component type using unified type resolver
            Type type = selectedType ?? UnityTypeResolver.ResolveComponent(componentTypeName);
            if (type == null)
            {
                return new ErrorResponse($"Component type '{componentTypeName}' not found. Use a fully-qualified name if needed.");
            }

            JObject properties = @params["properties"] as JObject ?? @params["componentProperties"] as JObject;
            if (properties == null)
            {
                JToken propertiesToken = @params["properties"];
                if (propertiesToken == null || propertiesToken.Type == JTokenType.Null)
                    propertiesToken = @params["componentProperties"];
                if (propertiesToken != null && propertiesToken.Type != JTokenType.Null)
                    return new ErrorResponse("'properties' or 'componentProperties' must be an object when supplied.");
            }

            // Use ComponentOps for the actual operation
            Component newComponent = ComponentOps.AddComponent(targetGo, type, out string error);
            if (newComponent == null)
            {
                return new ErrorResponse(error ?? $"Failed to add component '{componentTypeName}'.");
            }

            // Capture the new identity before property callbacks can alter the component list.
            JObject createdSelection =
                selectedType == null
                    ? null
                    : new ComponentSelection.Result(targetGo, newComponent, type, Array.IndexOf(targetGo.GetComponents(type), newComponent)).Describe();

            // When adding VFX-related components (ParticleSystem, LineRenderer, TrailRenderer),
            // ensure the renderer has a material compatible with the active render pipeline.
            // Without this, newly added ParticleSystems in URP/HDRP projects get Unity's default
            // Built-in RP particle material, which renders as magenta.
            EnsureVfxRendererMaterial(targetGo, newComponent);

            // Set properties if provided
            var propertyErrors = new List<string>();
            if (properties != null && properties.HasValues)
            {
                // Record for undo before modifying properties
                Undo.RecordObject(newComponent, "Modify Component Properties");
                propertyErrors = SetPropertiesOnComponent(newComponent, properties);
            }

            EditorUtility.SetDirty(targetGo);
            MarkOwningSceneDirty(targetGo);

            if (propertyErrors.Count > 0)
            {
                object failure = new ErrorResponse(
                    $"Component '{componentTypeName}' added to '{targetGo.name}', but some properties failed to set.",
                    new
                    {
                        instanceID = targetGo.GetInstanceIDCompat(),
                        componentType = type.FullName,
                        componentInstanceID = newComponent.GetInstanceIDCompat(),
                        componentAdded = true,
                        errors = propertyErrors,
                    }
                );
                return WithSelection(failure, createdSelection);
            }

            object success = new
            {
                success = true,
                message = $"Component '{componentTypeName}' added to '{targetGo.name}'.",
                data = new
                {
                    instanceID = targetGo.GetInstanceIDCompat(),
                    componentType = type.FullName,
                    componentInstanceID = newComponent.GetInstanceIDCompat(),
                },
            };
            return WithSelection(success, createdSelection);
        }

        private static object WithSelection(object response, JObject selection)
        {
            if (selection == null)
                return response;
            JObject json = JObject.FromObject(response);
            var data = json["data"] as JObject ?? new JObject();
            data["selection"] = selection;
            json["data"] = data;
            return json;
        }

        private static object RemoveComponent(JObject @params, JToken targetToken, string searchMethod, ComponentSelection.Result selection = null)
        {
            GameObject targetGo = selection != null ? selection.Target : FindTarget(targetToken, searchMethod);
            if (targetGo == null)
            {
                return new ErrorResponse($"Target GameObject ('{targetToken}') not found using method '{searchMethod ?? "default"}'.");
            }

            string componentTypeName = ParamCoercion.CoerceString(@params["componentType"] ?? @params["component_type"], null);
            if (string.IsNullOrEmpty(componentTypeName))
            {
                return new ErrorResponse("'componentType' parameter is required for 'remove' action.");
            }

            // Resolve component type using unified type resolver
            Type type = selection != null ? selection.RequestedType : UnityTypeResolver.ResolveComponent(componentTypeName);
            if (type == null)
            {
                return new ErrorResponse($"Component type '{componentTypeName}' not found.");
            }

            JToken componentIndexToken = @params["componentIndex"] ?? @params["component_index"];
            int? componentIndex = selection != null ? selection.Index : ParamCoercion.CoerceIntNullable(componentIndexToken);
            if (
                !componentIndex.HasValue
                && componentIndexToken != null
                && componentIndexToken.Type != JTokenType.Null
                && !string.IsNullOrWhiteSpace(componentIndexToken.ToString())
            )
                return new ErrorResponse("'component_index' must be a valid integer when supplied.");
            if (componentIndex.HasValue)
            {
                var components = selection == null ? targetGo.GetComponents(type) : null;
                if (selection == null && (componentIndex.Value < 0 || componentIndex.Value >= components.Length))
                    return new ErrorResponse(
                        $"component_index {componentIndex.Value} out of range. Found {components.Length} '{componentTypeName}' component(s)."
                    );
                Component selected = selection != null ? selection.Component : components[componentIndex.Value];
                if (selected is Transform)
                    return new ErrorResponse("Cannot remove Transform or RectTransform components.");
                Undo.DestroyObjectImmediate(selected);
                EditorUtility.SetDirty(targetGo);
                MarkOwningSceneDirty(targetGo);
                return new
                {
                    success = true,
                    message = $"Component '{componentTypeName}' (index {componentIndex.Value}) removed from '{targetGo.name}'.",
                    data = new { instanceID = targetGo.GetInstanceIDCompat(), componentIndex = componentIndex.Value },
                };
            }

            // Use ComponentOps for the actual operation (removes first instance)
            bool removed = ComponentOps.RemoveComponent(targetGo, type, out string error);
            if (!removed)
            {
                return new ErrorResponse(error ?? $"Failed to remove component '{componentTypeName}'.");
            }

            EditorUtility.SetDirty(targetGo);
            MarkOwningSceneDirty(targetGo);

            return new
            {
                success = true,
                message = $"Component '{componentTypeName}' removed from '{targetGo.name}'.",
                data = new { instanceID = targetGo.GetInstanceIDCompat() },
            };
        }

        private static object SetProperty(JObject @params, JToken targetToken, string searchMethod, ComponentSelection.Result selection = null)
        {
            GameObject targetGo = selection != null ? selection.Target : FindTarget(targetToken, searchMethod);
            if (targetGo == null)
            {
                return new ErrorResponse($"Target GameObject ('{targetToken}') not found using method '{searchMethod ?? "default"}'.");
            }

            string componentType = ParamCoercion.CoerceString(@params["componentType"] ?? @params["component_type"], null);
            if (string.IsNullOrEmpty(componentType))
            {
                return new ErrorResponse("'componentType' parameter is required for 'set_property' action.");
            }

            // Resolve component type using unified type resolver
            Type type = selection != null ? selection.RequestedType : UnityTypeResolver.ResolveComponent(componentType);
            if (type == null)
            {
                return new ErrorResponse($"Component type '{componentType}' not found.");
            }

            JToken componentIndexToken = @params["componentIndex"] ?? @params["component_index"];
            int? componentIndex = ParamCoercion.CoerceIntNullable(componentIndexToken);
            if (
                !componentIndex.HasValue
                && componentIndexToken != null
                && componentIndexToken.Type != JTokenType.Null
                && !string.IsNullOrWhiteSpace(componentIndexToken.ToString())
            )
                return new ErrorResponse("'component_index' must be a valid integer when supplied.");
            Component component;
            if (selection != null)
            {
                component = selection.Component;
            }
            else if (componentIndex.HasValue)
            {
                var components = targetGo.GetComponents(type);
                if (componentIndex.Value < 0 || componentIndex.Value >= components.Length)
                    return new ErrorResponse($"component_index {componentIndex.Value} out of range. Found {components.Length} '{componentType}' component(s).");
                component = components[componentIndex.Value];
            }
            else
            {
                component = targetGo.GetComponent(type);
            }
            if (component == null)
            {
                return new ErrorResponse($"Component '{componentType}' not found on '{targetGo.name}'.");
            }

            // Get property and value
            string propertyName = ParamCoercion.CoerceString(@params["property"], null);
            JToken valueToken = @params["value"];

            // Support both single property or properties object
            JObject properties = @params["properties"] as JObject;

            if (string.IsNullOrEmpty(propertyName) && (properties == null || !properties.HasValues))
            {
                return new ErrorResponse("Either 'property'+'value' or 'properties' object is required for 'set_property' action.");
            }

            if (!string.IsNullOrEmpty(propertyName) && valueToken == null)
            {
                return new ErrorResponse("'value' parameter is required when 'property' is supplied. Use JSON null to clear an object reference.");
            }

            var errors = new List<string>();
            bool anyPropertySet = false;

            try
            {
                Undo.RecordObject(component, $"Set property on {componentType}");

                if (!string.IsNullOrEmpty(propertyName) && valueToken != null)
                {
                    // Single property mode
                    var error = TrySetProperty(component, propertyName, valueToken);
                    if (error != null)
                    {
                        errors.Add(error);
                    }
                    else
                    {
                        anyPropertySet = true;
                    }
                }

                if (properties != null && properties.HasValues)
                {
                    // Multiple properties mode
                    foreach (var prop in properties.Properties())
                    {
                        var error = TrySetProperty(component, prop.Name, prop.Value);
                        if (error != null)
                        {
                            errors.Add(error);
                        }
                        else
                        {
                            anyPropertySet = true;
                        }
                    }
                }

                if (anyPropertySet)
                {
                    EditorUtility.SetDirty(component);
                    MarkOwningSceneDirty(targetGo);
                }

                if (errors.Count > 0)
                {
                    return new ErrorResponse(
                        $"Some properties failed to set on '{componentType}'.",
                        new { instanceID = targetGo.GetInstanceIDCompat(), errors = errors }
                    );
                }

                return new
                {
                    success = true,
                    message = $"Properties set on component '{componentType}' on '{targetGo.name}'.",
                    data = new { instanceID = targetGo.GetInstanceIDCompat() },
                };
            }
            catch (Exception e)
            {
                return new ErrorResponse($"Error setting properties on component '{componentType}': {e.Message}");
            }
        }

        #endregion

        #region Helpers

        /// <summary>
        /// When a VFX-capable component is added (ParticleSystem, LineRenderer, TrailRenderer),
        /// ensures its renderer material is valid for the active render pipeline.
        /// This prevents magenta rendering in URP/HDRP projects where the default built-in
        /// particle/line materials use incompatible shaders.
        /// </summary>
        private static void EnsureVfxRendererMaterial(GameObject go, Component addedComponent)
        {
            Renderer renderer = null;

            if (addedComponent is ParticleSystem ps)
            {
                renderer = go.GetComponent<ParticleSystemRenderer>();

                // Apply sensible defaults so newly added ParticleSystems aren't oversized.
                // These are overridden by any subsequent particle_set_* calls.
                RendererHelpers.SetSensibleParticleDefaults(ps);
            }
            else if (addedComponent is Renderer r)
            {
                // Covers LineRenderer, TrailRenderer, and any other Renderer subclass
                renderer = r;
            }

            if (renderer != null)
            {
                var result = RendererHelpers.EnsureMaterial(renderer);
                if (result.MaterialReplaced)
                {
                    McpLog.Info(
                        $"[ManageComponents] Auto-assigned pipeline-compatible material to {renderer.GetType().Name} on '{go.name}' (reason: {result.ReplacementReason})."
                    );
                }
            }
        }

        /// <summary>
        /// Marks the appropriate scene as dirty for the given GameObject.
        /// Handles both regular scenes and prefab stages.
        /// </summary>
        private static void MarkOwningSceneDirty(GameObject targetGo)
        {
            if (EditorApplication.isPlaying)
                return;

            var prefabStage = PrefabStageUtility.GetCurrentPrefabStage();
            if (prefabStage != null)
            {
                EditorSceneManager.MarkSceneDirty(prefabStage.scene);
            }
            else
            {
                EditorSceneManager.MarkSceneDirty(targetGo.scene);
            }
        }

        private static GameObject FindTarget(JToken targetToken, string searchMethod)
        {
            if (targetToken == null)
                return null;

            if (!string.IsNullOrEmpty(searchMethod))
                return GameObjectLookup.FindByTarget(targetToken, searchMethod, true);

            // Try instance ID first
            if (targetToken.Type == JTokenType.Integer)
            {
                int instanceId = targetToken.ReadScalar<int>();
                return GameObjectLookup.FindById(instanceId);
            }

            string targetStr = targetToken.ToString();

            // Try parsing as instance ID
            if (int.TryParse(targetStr, out int parsedId))
            {
                var byId = GameObjectLookup.FindById(parsedId);
                if (byId != null)
                    return byId;
            }

            // Use GameObjectLookup for search
            return GameObjectLookup.FindByTarget(targetToken, searchMethod ?? "by_name", true);
        }

        private static List<string> SetPropertiesOnComponent(Component component, JObject properties)
        {
            var errors = new List<string>();
            if (component == null || properties == null)
                return errors;

            foreach (var prop in properties.Properties())
            {
                var error = TrySetProperty(component, prop.Name, prop.Value);
                if (error != null)
                    errors.Add(error);
            }

            if (errors.Count > 0)
            {
                McpLog.Warn($"[ManageComponents] Some properties failed to set on {component.GetType().Name}: {string.Join(", ", errors)}");
            }
            return errors;
        }

        /// <summary>
        /// Attempts to set a property or field on a component.
        /// Delegates to ComponentOps.SetProperty for unified implementation.
        /// </summary>
        private static string TrySetProperty(Component component, string propertyName, JToken value)
        {
            if (component == null || string.IsNullOrEmpty(propertyName))
                return "Invalid component or property name";

            if (ComponentOps.SetProperty(component, propertyName, value, out string error))
            {
                return null; // Success
            }

            McpLog.Warn($"[ManageComponents] {error}");
            return error;
        }

        #endregion
    }
}
