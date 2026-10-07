using System;
using System.Collections.Generic;
using System.Reflection;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Runtime.Helpers;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEngine;

namespace MCPForUnity.Editor.Tools.Cameras
{
    internal static class CameraConfigure
    {
        #region Tier 1 — Basic Camera

        internal static object SetBasicCameraTarget(JObject @params)
        {
            var go = CameraHelpers.FindTargetGameObject(@params);
            if (go == null)
                return new ErrorResponse("Target Camera not found.");

            var cam = go.GetComponent<UnityEngine.Camera>();
            if (cam == null)
                return new ErrorResponse($"No Camera component on '{go.name}'.");

            var props = CameraHelpers.ExtractProperties(@params) ?? new JObject();
            var lookAtToken = props["lookAt"] ?? props["look_at"] ?? props["follow"];
            if (lookAtToken == null)
                return new ErrorResponse("'follow' or 'lookAt' property is required.");

            var target = CameraHelpers.ResolveGameObjectRef(lookAtToken);
            if (target == null)
                return new ErrorResponse($"Target '{lookAtToken}' not found.");

            Undo.RecordObject(go.transform, "Set Camera Target");
            go.transform.LookAt(target.transform);
            CameraHelpers.MarkDirty(go);

            return new
            {
                success = true,
                message = $"Camera '{go.name}' now looking at '{target.name}'.",
                data = new { instanceID = go.GetInstanceIDCompat() },
            };
        }

        internal static object SetBasicCameraLens(JObject @params)
        {
            var go = CameraHelpers.FindTargetGameObject(@params);
            if (go == null)
                return new ErrorResponse("Target Camera not found.");

            var cam = go.GetComponent<UnityEngine.Camera>();
            if (cam == null)
                return new ErrorResponse($"No Camera component on '{go.name}'.");

            var props = CameraHelpers.ExtractProperties(@params) ?? new JObject();
            float fieldOfView = ParamCoercion.CoerceFloat(props["fieldOfView"], cam.fieldOfView);
            float nearClipPlane = ParamCoercion.CoerceFloat(props["nearClipPlane"], cam.nearClipPlane);
            float farClipPlane = ParamCoercion.CoerceFloat(props["farClipPlane"], cam.farClipPlane);
            float orthographicSize = ParamCoercion.CoerceFloat(props["orthographicSize"], cam.orthographicSize);

            Undo.RecordObject(cam, "Set Camera Lens");

            if (props["fieldOfView"] != null)
                cam.fieldOfView = fieldOfView;
            if (props["nearClipPlane"] != null)
                cam.nearClipPlane = nearClipPlane;
            if (props["farClipPlane"] != null)
                cam.farClipPlane = farClipPlane;
            if (props["orthographicSize"] != null)
                cam.orthographicSize = orthographicSize;

            CameraHelpers.MarkDirty(go);
            return new
            {
                success = true,
                message = $"Lens properties set on Camera '{go.name}'.",
                data = new { instanceID = go.GetInstanceIDCompat() },
            };
        }

        internal static object SetBasicCameraPriority(JObject @params)
        {
            var go = CameraHelpers.FindTargetGameObject(@params);
            if (go == null)
                return new ErrorResponse("Target Camera not found.");

            var cam = go.GetComponent<UnityEngine.Camera>();
            if (cam == null)
                return new ErrorResponse($"No Camera component on '{go.name}'.");

            var props = CameraHelpers.ExtractProperties(@params) ?? new JObject();
            float depth = ParamCoercion.CoerceFloat(props["priority"], cam.depth);

            Undo.RecordObject(cam, "Set Camera Depth");
            cam.depth = depth;
            CameraHelpers.MarkDirty(go);

            return new
            {
                success = true,
                message = $"Camera '{go.name}' depth set to {depth}.",
                data = new { instanceID = go.GetInstanceIDCompat(), depth },
            };
        }

        #endregion

        #region Tier 2 — Cinemachine

        internal static object SetCinemachineTarget(JObject @params)
        {
            var cmCamera = CameraHelpers.FindCinemachineCamera(@params);
            if (cmCamera == null)
                return new ErrorResponse("Target CinemachineCamera not found.");

            var props = CameraHelpers.ExtractProperties(@params) ?? new JObject();

            var targets = new List<KeyValuePair<string, JToken>>();
            if (props.ContainsKey("follow"))
                targets.Add(new KeyValuePair<string, JToken>("Follow", props["follow"]));
            if (props.ContainsKey("lookAt") || props.ContainsKey("look_at"))
                targets.Add(new KeyValuePair<string, JToken>("LookAt", props["lookAt"] ?? props["look_at"]));

            var setters = new List<Action>();
            foreach (var target in targets)
            {
                var property = cmCamera.GetType().GetProperty(target.Key, BindingFlags.Public | BindingFlags.Instance);
                if (property == null || !property.CanWrite || property.PropertyType != typeof(Transform))
                    return new ErrorResponse($"Could not set {target.Key} on CinemachineCamera.");

                Transform transform = null;
                if (target.Value != null && target.Value.Type != JTokenType.Null)
                {
                    var resolved = CameraHelpers.ResolveGameObjectRef(target.Value);
                    if (resolved == null)
                        return new ErrorResponse($"Target '{target.Value}' not found for {target.Key}.");
                    transform = resolved.transform;
                }
                setters.Add(() => property.SetValue(cmCamera, transform));
            }

            Undo.RecordObject(cmCamera, "Set Cinemachine Target");
            foreach (var set in setters)
                set();

            CameraHelpers.MarkDirty(cmCamera.gameObject);

            return new
            {
                success = true,
                message = $"Targets set on CinemachineCamera '{cmCamera.gameObject.name}'.",
                data = new { instanceID = cmCamera.gameObject.GetInstanceIDCompat() },
            };
        }

        internal static object SetCinemachineLens(JObject @params)
        {
            var cmCamera = CameraHelpers.FindCinemachineCamera(@params);
            if (cmCamera == null)
                return new ErrorResponse("Target CinemachineCamera not found.");

            var props = CameraHelpers.ExtractProperties(@params) ?? new JObject();
            Undo.RecordObject(cmCamera, "Set Cinemachine Lens");

            // Lens is a struct field — use SerializedProperty for reliable setting
            using var so = new SerializedObject(cmCamera);
            var lensProp = so.FindProperty("Lens") ?? so.FindProperty("m_Lens");
            if (lensProp == null)
                return new ErrorResponse("Could not find Lens property on CinemachineCamera.");

            SetFloatSubProp(lensProp, "FieldOfView", props["fieldOfView"]);
            SetFloatSubProp(lensProp, "NearClipPlane", props["nearClipPlane"]);
            SetFloatSubProp(lensProp, "FarClipPlane", props["farClipPlane"]);
            SetFloatSubProp(lensProp, "OrthographicSize", props["orthographicSize"]);
            SetFloatSubProp(lensProp, "Dutch", props["dutch"]);

            so.ApplyModifiedProperties();
            CameraHelpers.MarkDirty(cmCamera.gameObject);

            return new
            {
                success = true,
                message = $"Lens properties set on CinemachineCamera '{cmCamera.gameObject.name}'.",
                data = new { instanceID = cmCamera.gameObject.GetInstanceIDCompat() },
            };
        }

        internal static object SetCinemachinePriority(JObject @params)
        {
            var cmCamera = CameraHelpers.FindCinemachineCamera(@params);
            if (cmCamera == null)
                return new ErrorResponse("Target CinemachineCamera not found.");

            var props = CameraHelpers.ExtractProperties(@params) ?? new JObject();
            int priority = ParamCoercion.CoerceInt(props["priority"], 10);

            var error = SetPriority(cmCamera, priority);
            if (error != null)
                return error;
            CameraHelpers.MarkDirty(cmCamera.gameObject);

            return new
            {
                success = true,
                message = $"Priority set to {priority} on CinemachineCamera '{cmCamera.gameObject.name}'.",
                data = new { instanceID = cmCamera.gameObject.GetInstanceIDCompat(), priority },
            };
        }

        internal static ErrorResponse SetPriority(Component cmCamera, int priority)
        {
            // PrioritySettings is a struct with Enabled + m_Value — use SerializedProperty.
            using var so = new SerializedObject(cmCamera);
            var priorityProp = so.FindProperty("Priority");
            if (priorityProp != null)
            {
                var enabledProp = priorityProp.FindPropertyRelative("Enabled");
                var valueProp = priorityProp.FindPropertyRelative("m_Value");
                if (
                    enabledProp == null
                    || enabledProp.propertyType != SerializedPropertyType.Boolean
                    || valueProp == null
                    || valueProp.propertyType != SerializedPropertyType.Integer
                )
                    return new ErrorResponse("Could not find writable Priority fields on CinemachineCamera.");
                enabledProp.boolValue = true;
                valueProp.intValue = priority;
                so.ApplyModifiedProperties();
            }
            else
            {
                var property = cmCamera.GetType().GetProperty("Priority", BindingFlags.Public | BindingFlags.Instance);
                if (property == null || !property.CanWrite || property.PropertyType != typeof(int))
                    return new ErrorResponse("Could not find writable Priority on CinemachineCamera.");
                Undo.RecordObject(cmCamera, "Set Cinemachine Priority");
                property.SetValue(cmCamera, priority);
            }
            return null;
        }

        internal static object SetBody(JObject @params)
        {
            var cmCamera = CameraHelpers.FindCinemachineCamera(@params);
            if (cmCamera == null)
                return new ErrorResponse("Target CinemachineCamera not found.");

            var props = CameraHelpers.ExtractProperties(@params) ?? new JObject();
            var go = cmCamera.gameObject;

            // Optionally swap body component
            string bodyTypeName = ParamCoercion.CoerceString(props["bodyType"] ?? props["body_type"], null);
            Component bodyComponent;
            List<Action<Component>> setters;
            ErrorResponse propertyError;

            if (bodyTypeName != null)
            {
                var bodyType = CameraHelpers.ResolveComponentType(bodyTypeName);
                if (bodyType == null || !IsConcreteCinemachineType(bodyType, "CinemachineComponentBase"))
                    return new ErrorResponse($"Could not resolve body component type '{bodyTypeName}'.");
                propertyError = PrepareComponentPropertiesForTarget(
                    bodyType,
                    go.GetComponent(bodyType),
                    props,
                    new[] { "bodyType", "body_type" },
                    out setters,
                    go
                );
                if (propertyError != null)
                    return propertyError;
                bodyComponent = SwapPipelineComponent(go, "Body", bodyType);
            }
            else
            {
                bodyComponent = CameraHelpers.GetPipelineComponent(cmCamera, "Body");
                if (bodyComponent == null)
                    return new ErrorResponse("No Body component found on this CinemachineCamera. Provide 'bodyType' to add one.");
                propertyError = PrepareComponentPropertiesForTarget(
                    bodyComponent.GetType(),
                    bodyComponent,
                    props,
                    new[] { "bodyType", "body_type" },
                    out setters,
                    go
                );
                if (propertyError != null)
                    return propertyError;
            }

            // Set properties on body component
            propertyError = ApplyPreparedComponentProperties(bodyComponent, setters);
            if (propertyError != null)
                return propertyError;
            CameraHelpers.MarkDirty(go);

            return new
            {
                success = true,
                message = $"Body configured on CinemachineCamera '{go.name}'.",
                data = new { instanceID = go.GetInstanceIDCompat(), body = bodyComponent.GetType().Name },
            };
        }

        internal static object SetAim(JObject @params)
        {
            var cmCamera = CameraHelpers.FindCinemachineCamera(@params);
            if (cmCamera == null)
                return new ErrorResponse("Target CinemachineCamera not found.");

            var props = CameraHelpers.ExtractProperties(@params) ?? new JObject();
            var go = cmCamera.gameObject;

            string aimTypeName = ParamCoercion.CoerceString(props["aimType"] ?? props["aim_type"], null);
            Component aimComponent;
            List<Action<Component>> setters;
            ErrorResponse propertyError;

            if (aimTypeName != null)
            {
                var aimType = CameraHelpers.ResolveComponentType(aimTypeName);
                if (aimType == null || !IsConcreteCinemachineType(aimType, "CinemachineComponentBase"))
                    return new ErrorResponse($"Could not resolve aim component type '{aimTypeName}'.");
                propertyError = PrepareComponentPropertiesForTarget(aimType, go.GetComponent(aimType), props, new[] { "aimType", "aim_type" }, out setters, go);
                if (propertyError != null)
                    return propertyError;
                aimComponent = SwapPipelineComponent(go, "Aim", aimType);
            }
            else
            {
                aimComponent = CameraHelpers.GetPipelineComponent(cmCamera, "Aim");
                if (aimComponent == null)
                    return new ErrorResponse("No Aim component found. Provide 'aimType' to add one.");
                propertyError = PrepareComponentPropertiesForTarget(
                    aimComponent.GetType(),
                    aimComponent,
                    props,
                    new[] { "aimType", "aim_type" },
                    out setters,
                    go
                );
                if (propertyError != null)
                    return propertyError;
            }

            propertyError = ApplyPreparedComponentProperties(aimComponent, setters);
            if (propertyError != null)
                return propertyError;
            CameraHelpers.MarkDirty(go);

            return new
            {
                success = true,
                message = $"Aim configured on CinemachineCamera '{go.name}'.",
                data = new { instanceID = go.GetInstanceIDCompat(), aim = aimComponent.GetType().Name },
            };
        }

        internal static object SetNoise(JObject @params)
        {
            var cmCamera = CameraHelpers.FindCinemachineCamera(@params);
            if (cmCamera == null)
                return new ErrorResponse("Target CinemachineCamera not found.");

            var props = CameraHelpers.ExtractProperties(@params) ?? new JObject();
            var go = cmCamera.gameObject;

            // Get or add noise component
            var noiseType = CameraHelpers.ResolveComponentType("CinemachineBasicMultiChannelPerlin");
            if (noiseType == null)
                return new ErrorResponse("CinemachineBasicMultiChannelPerlin type not found.");
            var noiseComponent = go.GetComponent(noiseType);
            var propertyError = PrepareComponentPropertiesForTarget(noiseType, noiseComponent, props, Array.Empty<string>(), out var setters, go);
            if (propertyError != null)
                return propertyError;

            bool added = false;
            if (noiseComponent == null)
            {
                noiseComponent = Undo.AddComponent(go, noiseType);
                added = true;
            }

            propertyError = ApplyPreparedComponentProperties(noiseComponent, setters);
            if (propertyError != null)
                return propertyError;
            CameraHelpers.MarkDirty(go);

            return new
            {
                success = true,
                message = added ? $"Added noise to CinemachineCamera '{go.name}'." : $"Noise configured on CinemachineCamera '{go.name}'.",
                data = new { instanceID = go.GetInstanceIDCompat(), added },
            };
        }

        internal static object AddExtension(JObject @params)
        {
            var cmCamera = CameraHelpers.FindCinemachineCamera(@params);
            if (cmCamera == null)
                return new ErrorResponse("Target CinemachineCamera not found.");

            var props = CameraHelpers.ExtractProperties(@params) ?? new JObject();
            string extTypeName = ParamCoercion.CoerceString(props["extensionType"] ?? props["extension_type"], null);
            if (string.IsNullOrEmpty(extTypeName))
                return new ErrorResponse("'extensionType' property is required.");

            var extType = CameraHelpers.ResolveComponentType(extTypeName);
            if (extType == null)
                return new ErrorResponse($"Extension type '{extTypeName}' not found.");
            if (!IsConcreteCinemachineType(extType, "CinemachineExtension"))
                return new ErrorResponse($"Type '{extTypeName}' is not a concrete CinemachineExtension.");

            var go = cmCamera.gameObject;
            var existing = go.GetComponent(extType);
            if (existing != null)
                return new { success = true, message = $"Extension '{extTypeName}' already exists on '{go.name}'." };

            var propertyError = PrepareComponentPropertiesForTarget(extType, null, props, new[] { "extensionType", "extension_type" }, out var setters, go);
            if (propertyError != null)
                return propertyError;
            var ext = Undo.AddComponent(go, extType);
            propertyError = ApplyPreparedComponentProperties(ext, setters);
            if (propertyError != null)
                return propertyError;
            CameraHelpers.MarkDirty(go);

            return new
            {
                success = true,
                message = $"Extension '{extTypeName}' added to CinemachineCamera '{go.name}'.",
                data = new { instanceID = go.GetInstanceIDCompat(), extensionType = extTypeName },
            };
        }

        internal static object RemoveExtension(JObject @params)
        {
            var cmCamera = CameraHelpers.FindCinemachineCamera(@params);
            if (cmCamera == null)
                return new ErrorResponse("Target CinemachineCamera not found.");

            var props = CameraHelpers.ExtractProperties(@params) ?? new JObject();
            string extTypeName = ParamCoercion.CoerceString(props["extensionType"] ?? props["extension_type"], null);
            if (string.IsNullOrEmpty(extTypeName))
                return new ErrorResponse("'extensionType' property is required.");

            var extType = CameraHelpers.ResolveComponentType(extTypeName);
            if (extType == null)
                return new ErrorResponse($"Extension type '{extTypeName}' not found.");
            if (!IsConcreteCinemachineType(extType, "CinemachineExtension"))
                return new ErrorResponse($"Type '{extTypeName}' is not a concrete CinemachineExtension.");

            var go = cmCamera.gameObject;
            var ext = go.GetComponent(extType);
            if (ext == null)
                return new ErrorResponse($"Extension '{extTypeName}' not found on '{go.name}'.");

            Undo.DestroyObjectImmediate(ext);
            CameraHelpers.MarkDirty(go);

            return new
            {
                success = true,
                message = $"Extension '{extTypeName}' removed from CinemachineCamera '{go.name}'.",
                data = new { instanceID = go.GetInstanceIDCompat() },
            };
        }

        #endregion

        #region Helpers

        private static void SetFloatSubProp(SerializedProperty parent, string subPropName, JToken value)
        {
            if (value == null || value.Type == JTokenType.Null)
                return;
            var sub = parent.FindPropertyRelative(subPropName) ?? parent.FindPropertyRelative("m_" + subPropName);
            if (sub != null && sub.propertyType == SerializedPropertyType.Float)
                sub.floatValue = ParamCoercion.CoerceFloat(value, sub.floatValue);
        }

        private static Component SwapPipelineComponent(GameObject go, string stage, Type newType)
        {
            // Remove existing component of same pipeline stage
            var cmCamera = go.GetComponent(CameraHelpers.CinemachineCameraType);
            if (cmCamera != null)
            {
                var existing = CameraHelpers.GetPipelineComponent(cmCamera, stage);
                if (existing != null && existing.GetType() != newType)
                    Undo.DestroyObjectImmediate(existing);
            }

            // Add new component if not already present
            var comp = go.GetComponent(newType);
            if (comp == null)
                comp = Undo.AddComponent(go, newType);

            return comp;
        }

        private static bool IsConcreteCinemachineType(Type type, string baseTypeName)
        {
            var baseType = CameraHelpers.CinemachineCameraType?.Assembly.GetType("Unity.Cinemachine." + baseTypeName);
            return baseType != null && baseType.IsAssignableFrom(type) && !type.IsAbstract && !type.ContainsGenericParameters;
        }

        private static ErrorResponse PrepareComponentProperties(Type type, JObject props, string[] skipKeys, out List<Action<Component>> setters)
        {
            setters = new List<Action<Component>>();
            var skipSet = new HashSet<string>(skipKeys, StringComparer.OrdinalIgnoreCase);
            foreach (var kv in props)
            {
                if (skipSet.Contains(kv.Key))
                    continue;
                if (!ComponentOps.TryPrepareProperty(type, kv.Key, kv.Value, out var setter, out string error))
                    return new ErrorResponse($"Failed to set '{kv.Key}' on {type.Name}: {error}");
                setters.Add(setter);
            }
            return null;
        }

        private static ErrorResponse PrepareComponentPropertiesForTarget(
            Type type,
            Component component,
            JObject props,
            string[] skipKeys,
            out List<Action<Component>> setters,
            GameObject targetContext = null
        )
        {
            var error = PrepareComponentProperties(type, props, skipKeys, out setters);
            if (error != null)
                return error;
            var skipSet = new HashSet<string>(skipKeys, StringComparer.OrdinalIgnoreCase);
            var writes = new JObject();
            foreach (var property in props)
                if (!skipSet.Contains(property.Key))
                    writes.Add(property.Key, property.Value.DeepClone());
            if (!ComponentOps.TryValidatePropertyOwners(type, component, writes, out string ownerError, targetContext))
                return new ErrorResponse($"Failed to configure {type.Name}: {ownerError}");
            return null;
        }

        private static ErrorResponse ApplyPreparedComponentProperties(Component component, List<Action<Component>> setters)
        {
            if (component == null)
                return new ErrorResponse("Could not add the requested Cinemachine component.");
            if (setters.Count == 0)
                return null;

            Undo.RecordObject(component, $"Configure {component.GetType().Name}");
            try
            {
                foreach (var setter in setters)
                    setter(component);
                return null;
            }
            catch (Exception ex)
            {
                return new ErrorResponse($"Failed to configure {component.GetType().Name}: {ex.Message}");
            }
        }

        private static ErrorResponse SetComponentProperties(Component component, JObject props, string[] skipKeys)
        {
            if (component == null || props == null)
                return null;

            var skipSet = new System.Collections.Generic.HashSet<string>(skipKeys, StringComparer.OrdinalIgnoreCase);

            Undo.RecordObject(component, $"Configure {component.GetType().Name}");

            foreach (var kv in props)
            {
                if (skipSet.Contains(kv.Key))
                    continue;
                if (!ComponentOps.SetProperty(component, kv.Key, kv.Value, out string error))
                    return new ErrorResponse($"Failed to set '{kv.Key}' on {component.GetType().Name}: {error}");
            }
            return null;
        }

        #endregion
    }
}
