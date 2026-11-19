using System;
using System.Collections.Generic;
using System.Reflection;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Editor.Tools.Graphics;
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

            if (!go.TryGetComponent(out UnityEngine.Camera cam))
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
            CameraHelpers.RecordPrefabInstanceChanges(go.transform);
            CameraHelpers.MarkDirty(go);

            return new
            {
                success = true,
                message = $"Camera '{go.name}' now looking at '{target.name}'.",
                data = new { instanceID = go.GetInstanceIDCompat(), verification = RenderingObservation.Camera(cam) },
            };
        }

        internal static object SetBasicCameraLens(JObject @params)
        {
            var go = CameraHelpers.FindTargetGameObject(@params);
            if (go == null)
                return new ErrorResponse("Target Camera not found.");

            if (!go.TryGetComponent(out UnityEngine.Camera cam))
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

            CameraHelpers.RecordPrefabInstanceChanges(cam);
            CameraHelpers.MarkDirty(go);
            return new
            {
                success = true,
                message = $"Lens properties set on Camera '{go.name}'.",
                data = new { instanceID = go.GetInstanceIDCompat(), verification = RenderingObservation.Camera(cam, props) },
            };
        }

        internal static object SetBasicCameraPriority(JObject @params)
        {
            var go = CameraHelpers.FindTargetGameObject(@params);
            if (go == null)
                return new ErrorResponse("Target Camera not found.");

            if (!go.TryGetComponent(out UnityEngine.Camera cam))
                return new ErrorResponse($"No Camera component on '{go.name}'.");

            var props = CameraHelpers.ExtractProperties(@params) ?? new JObject();
            float depth = ParamCoercion.CoerceFloat(props["priority"], cam.depth);

            Undo.RecordObject(cam, "Set Camera Depth");
            cam.depth = depth;
            CameraHelpers.RecordPrefabInstanceChanges(cam);
            CameraHelpers.MarkDirty(go);

            return new
            {
                success = true,
                message = $"Camera '{go.name}' depth set to {depth}.",
                data = new
                {
                    instanceID = go.GetInstanceIDCompat(),
                    depth,
                    verification = RenderingObservation.Camera(cam, new JObject { ["depth"] = depth }),
                },
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

            CameraHelpers.RecordPrefabInstanceChanges(cmCamera);
            CameraHelpers.MarkDirty(cmCamera.gameObject);

            return new
            {
                success = true,
                message = $"Targets set on CinemachineCamera '{cmCamera.gameObject.name}'.",
                data = new
                {
                    instanceID = cmCamera.gameObject.GetInstanceIDCompat(),
                    verification = RenderingObservation.ReadKnown(cmCamera, "Target", "m_Follow", "m_LookAt"),
                },
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
            using var commonLens = so.FindProperty("m_CommonLens");
            if (commonLens?.propertyType == SerializedPropertyType.Boolean && !commonLens.boolValue)
                return new ErrorResponse("This FreeLook camera uses individual rig lenses. Select the rig that owns the lens.");
            using var lensProp = so.FindProperty("Lens") ?? so.FindProperty("m_Lens");
            if (lensProp == null)
                return new ErrorResponse("Could not find Lens property on CinemachineCamera.");

            foreach (
                var (input, field) in new[]
                {
                    ("fieldOfView", "FieldOfView"),
                    ("nearClipPlane", "NearClipPlane"),
                    ("farClipPlane", "FarClipPlane"),
                    ("orthographicSize", "OrthographicSize"),
                    ("dutch", "Dutch"),
                }
            )
            {
                var value = props[input];
                if (value == null || value.Type == JTokenType.Null)
                    continue;
                using var sub = lensProp.FindPropertyRelative(field) ?? lensProp.FindPropertyRelative("m_" + field);
                if (sub?.propertyType != SerializedPropertyType.Float)
                    return new ErrorResponse($"Could not find Lens.{field} on CinemachineCamera.");
                sub.floatValue = ParamCoercion.CoerceFloat(value, sub.floatValue);
            }

            so.ApplyModifiedProperties();
            CameraHelpers.MarkDirty(cmCamera.gameObject);

            return new
            {
                success = true,
                message = $"Lens properties set on CinemachineCamera '{cmCamera.gameObject.name}'.",
                data = new
                {
                    instanceID = cmCamera.gameObject.GetInstanceIDCompat(),
                    verification = RenderingObservation.ReadKnown(cmCamera, "Lens", "m_Lens"),
                },
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
                data = new
                {
                    instanceID = cmCamera.gameObject.GetInstanceIDCompat(),
                    priority,
                    verification = RenderingObservation.ReadKnown(cmCamera, "Priority", "m_Priority"),
                },
            };
        }

        internal static ErrorResponse SetPriority(Component cmCamera, int priority)
        {
            // PrioritySettings is a struct with Enabled + m_Value — use SerializedProperty.
            using var so = new SerializedObject(cmCamera);
            using var priorityProp = so.FindProperty("Priority") ?? so.FindProperty("m_Priority");
            if (priorityProp?.propertyType == SerializedPropertyType.Integer && priorityProp.propertyPath == "m_Priority")
            {
                priorityProp.intValue = priority;
                so.ApplyModifiedProperties();
                return null;
            }
            if (priorityProp != null)
            {
                using var enabledProp = priorityProp.FindPropertyRelative("Enabled");
                using var valueProp = priorityProp.FindPropertyRelative("m_Value");
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
                CameraHelpers.RecordPrefabInstanceChanges(cmCamera);
            }
            return null;
        }

        internal static object SetBody(JObject @params)
        {
            var cmCamera = CameraHelpers.FindCinemachineCamera(@params);
            if (cmCamera == null)
                return new ErrorResponse("Target CinemachineCamera not found.");

            if (!CameraHelpers.HasEditablePipeline(cmCamera))
                return new ErrorResponse($"{cmCamera.GetType().Name} has no single editable pipeline. Select the camera that owns the stage.");

            var props = CameraHelpers.ExtractProperties(@params) ?? new JObject();
            var go = cmCamera.gameObject;

            // Optionally swap body component
            string bodyTypeName = ParamCoercion.CoerceString(props["bodyType"] ?? props["body_type"], null);
            Component bodyComponent;
            List<Action<Component>> setters;
            ErrorResponse propertyError;

            if (bodyTypeName != null)
            {
                var bodyType = CameraHelpers.ResolveComponentType(bodyTypeName, cmCamera);
                if (bodyType == null || !IsConcreteCinemachineType(bodyType, "CinemachineComponentBase", "Body"))
                    return new ErrorResponse($"Could not resolve body component type '{bodyTypeName}'.");
                var owner = CameraHelpers.PipelineOwner(cmCamera);
                propertyError = PrepareComponentPropertiesForTarget(
                    bodyType,
                    owner != null ? owner.GetComponent(bodyType) : null,
                    props,
                    new[] { "bodyType", "body_type" },
                    out setters,
                    owner
                );
                if (propertyError != null)
                    return propertyError;
                propertyError = ConfigureComponentAtomically(() => SwapPipelineComponent(go, "Body", bodyType), setters, out bodyComponent);
                if (propertyError != null)
                    return propertyError;
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
                    bodyComponent.gameObject
                );
                if (propertyError != null)
                    return propertyError;
            }

            // Set properties on body component
            propertyError = bodyTypeName == null ? ApplyPreparedComponentProperties(bodyComponent, setters) : null;
            if (propertyError != null)
                return PostEditVerification.Attach(propertyError, RenderingObservation.Properties(bodyComponent, props));
            CameraHelpers.MarkDirty(go);

            return new
            {
                success = true,
                message = $"Body configured on CinemachineCamera '{go.name}'.",
                data = new
                {
                    instanceID = go.GetInstanceIDCompat(),
                    body = bodyComponent.GetType().Name,
                    verification = RenderingObservation.Properties(bodyComponent, props),
                },
            };
        }

        internal static object SetAim(JObject @params)
        {
            var cmCamera = CameraHelpers.FindCinemachineCamera(@params);
            if (cmCamera == null)
                return new ErrorResponse("Target CinemachineCamera not found.");

            if (!CameraHelpers.HasEditablePipeline(cmCamera))
                return new ErrorResponse($"{cmCamera.GetType().Name} has no single editable pipeline. Select the camera that owns the stage.");

            var props = CameraHelpers.ExtractProperties(@params) ?? new JObject();
            var go = cmCamera.gameObject;

            string aimTypeName = ParamCoercion.CoerceString(props["aimType"] ?? props["aim_type"], null);
            Component aimComponent;
            List<Action<Component>> setters;
            ErrorResponse propertyError;

            if (aimTypeName != null)
            {
                var aimType = CameraHelpers.ResolveComponentType(aimTypeName, cmCamera);
                if (aimType == null || !IsConcreteCinemachineType(aimType, "CinemachineComponentBase", "Aim"))
                    return new ErrorResponse($"Could not resolve aim component type '{aimTypeName}'.");
                var owner = CameraHelpers.PipelineOwner(cmCamera);
                propertyError = PrepareComponentPropertiesForTarget(
                    aimType,
                    owner != null ? owner.GetComponent(aimType) : null,
                    props,
                    new[] { "aimType", "aim_type" },
                    out setters,
                    owner
                );
                if (propertyError != null)
                    return propertyError;
                propertyError = ConfigureComponentAtomically(() => SwapPipelineComponent(go, "Aim", aimType), setters, out aimComponent);
                if (propertyError != null)
                    return propertyError;
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
                    aimComponent.gameObject
                );
                if (propertyError != null)
                    return propertyError;
            }

            propertyError = aimTypeName == null ? ApplyPreparedComponentProperties(aimComponent, setters) : null;
            if (propertyError != null)
                return PostEditVerification.Attach(propertyError, RenderingObservation.Properties(aimComponent, props));
            CameraHelpers.MarkDirty(go);

            return new
            {
                success = true,
                message = $"Aim configured on CinemachineCamera '{go.name}'.",
                data = new
                {
                    instanceID = go.GetInstanceIDCompat(),
                    aim = aimComponent.GetType().Name,
                    verification = RenderingObservation.Properties(aimComponent, props),
                },
            };
        }

        internal static object SetNoise(JObject @params)
        {
            var cmCamera = CameraHelpers.FindCinemachineCamera(@params);
            if (cmCamera == null)
                return new ErrorResponse("Target CinemachineCamera not found.");

            if (!CameraHelpers.HasEditablePipeline(cmCamera))
                return new ErrorResponse($"{cmCamera.GetType().Name} has no single editable pipeline. Select the camera that owns the stage.");

            var props = CameraHelpers.ExtractProperties(@params) ?? new JObject();
            var go = cmCamera.gameObject;

            // Get or add noise component
            var noiseType = CameraHelpers.ResolveProviderType("CinemachineBasicMultiChannelPerlin");
            if (noiseType == null)
                return new ErrorResponse("CinemachineBasicMultiChannelPerlin type not found.");
            // The public tool accepts the same gains on both package families.
            if (noiseType.GetField("m_AmplitudeGain") != null)
            {
                props = (JObject)props.DeepClone();
                foreach (var key in new[] { "AmplitudeGain", "FrequencyGain", "NoiseProfile" })
                {
                    var input = props.Property(key, StringComparison.OrdinalIgnoreCase);
                    if (input == null)
                        continue;
                    string field = "m_" + key;
                    if (props.Property(field, StringComparison.OrdinalIgnoreCase) != null)
                        return new ErrorResponse($"Duplicate noise field '{field}'.");
                    input.Replace(new JProperty(field, input.Value));
                }
            }
            var owner = CameraHelpers.PipelineOwner(cmCamera);
            var noiseComponent = owner != null ? owner.GetComponent(noiseType) : null;
            var propertyError = PrepareComponentPropertiesForTarget(noiseType, noiseComponent, props, Array.Empty<string>(), out var setters, owner);
            if (propertyError != null)
                return propertyError;

            bool added = noiseComponent == null;
            propertyError = added
                ? ConfigureComponentAtomically(() => SwapPipelineComponent(go, "Noise", noiseType), setters, out noiseComponent)
                : ApplyPreparedComponentProperties(noiseComponent, setters);
            if (propertyError != null)
                return PostEditVerification.Attach(propertyError, RenderingObservation.Properties(noiseComponent, props));
            CameraHelpers.MarkDirty(go);

            return new
            {
                success = true,
                message = added ? $"Added noise to CinemachineCamera '{go.name}'." : $"Noise configured on CinemachineCamera '{go.name}'.",
                data = new
                {
                    instanceID = go.GetInstanceIDCompat(),
                    added,
                    verification = RenderingObservation.Properties(noiseComponent, props),
                },
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

            var extType = ResolveExtensionType(extTypeName, out string confinerMode);
            if (extType == null)
                return new ErrorResponse($"Extension type '{extTypeName}' not found.");
            if (!IsConcreteCinemachineType(extType, "CinemachineExtension"))
                return new ErrorResponse($"Type '{extTypeName}' is not a concrete CinemachineExtension.");

            var go = cmCamera.gameObject;
            var existing = go.GetComponent(extType);
            if (existing != null && !MatchesConfinerMode(existing, confinerMode))
                return new ErrorResponse($"The existing {extType.Name} is configured for another confinement mode.");
            if (existing != null && props.Count > 1)
                return new ErrorResponse($"Extension '{extTypeName}' already exists; supplied properties were not applied.");
            if (existing != null)
                return new { success = true, message = $"Extension '{extTypeName}' already exists on '{go.name}'." };

            var propertyError = PrepareComponentPropertiesForTarget(extType, null, props, new[] { "extensionType", "extension_type" }, out var setters, go);
            if (propertyError != null)
                return propertyError;
            if (confinerMode != null)
            {
                foreach (var property in props.Properties())
                    if (property.Name.Equals("m_ConfineMode", StringComparison.OrdinalIgnoreCase))
                        return new ErrorResponse($"The extension type '{extTypeName}' already selects the confinement mode.");
                var mode = extType.GetField("m_ConfineMode");
                if (mode != null)
                    setters.Insert(0, component => mode.SetValue(component, Enum.Parse(mode.FieldType, confinerMode)));
            }
            propertyError = ConfigureComponentAtomically(() => Undo.AddComponent(go, extType), setters, out var ext);
            if (propertyError != null)
                return propertyError;
            CameraHelpers.MarkDirty(go);

            return new
            {
                success = true,
                message = $"Extension '{extTypeName}' added to CinemachineCamera '{go.name}'.",
                data = new
                {
                    instanceID = go.GetInstanceIDCompat(),
                    extensionType = extTypeName,
                    componentType = ext.GetType().FullName,
                    verification = RenderingObservation.Properties(ext, props),
                },
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

            var extType = ResolveExtensionType(extTypeName, out string confinerMode);
            if (extType == null)
                return new ErrorResponse($"Extension type '{extTypeName}' not found.");
            if (!IsConcreteCinemachineType(extType, "CinemachineExtension"))
                return new ErrorResponse($"Type '{extTypeName}' is not a concrete CinemachineExtension.");

            var go = cmCamera.gameObject;
            var ext = go.GetComponent(extType);
            if (ext == null || !MatchesConfinerMode(ext, confinerMode))
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

        private static Type ResolveExtensionType(string name, out string confinerMode)
        {
            confinerMode = null;
            if (name != "CinemachineConfiner2D" && name != "CinemachineConfiner3D")
                return CameraHelpers.ResolveComponentType(name);
            var exact = CameraHelpers.ResolveProviderType(name);
            if (exact != null)
                return exact;
            var legacy = CameraHelpers.ResolveProviderType("CinemachineConfiner");
            bool twoDimensional = name == "CinemachineConfiner2D";
            if (legacy?.GetField(twoDimensional ? "m_BoundingShape2D" : "m_BoundingVolume") == null)
                return null;
            confinerMode = twoDimensional ? "Confine2D" : "Confine3D";
            var field = legacy.GetField("m_ConfineMode");
            // A build with only one physics module has its matching bounding field and no mode enum.
            return field == null || (field.FieldType.IsEnum && Enum.IsDefined(field.FieldType, confinerMode)) ? legacy : null;
        }

        private static bool MatchesConfinerMode(Component component, string mode)
        {
            var field = mode == null ? null : component.GetType().GetField("m_ConfineMode");
            return field == null || field.GetValue(component)?.ToString() == mode;
        }

        private static ErrorResponse ConfigureComponentAtomically(Func<Component> acquire, List<Action<Component>> setters, out Component component)
        {
            Undo.FlushUndoRecordObjects();
            Undo.IncrementCurrentGroup();
            int group = Undo.GetCurrentGroup();
            component = null;
            ErrorResponse error;
            try
            {
                component = acquire();
                error = ApplyPreparedComponentProperties(component, setters);
            }
            catch (Exception ex)
            {
                error = new ErrorResponse($"Failed to configure Cinemachine component: {ex.Message}");
            }

            Undo.FlushUndoRecordObjects();
            if (error != null)
            {
                Undo.RevertAllDownToGroup(group);
                component = null;
            }
            else
                Undo.CollapseUndoOperations(group);
            Undo.IncrementCurrentGroup();
            return error;
        }

        internal static Component SwapPipelineComponent(GameObject go, string stage, Type newType)
        {
            var cmCamera = CameraHelpers.FindVirtualCamera(go);
            var owner = CameraHelpers.PipelineOwner(cmCamera, create: true);
            if (owner == null)
                throw new InvalidOperationException("Cinemachine pipeline owner is unavailable.");
            var existing = CameraHelpers.GetPipelineComponent(cmCamera, stage);
            if (existing != null && existing.GetType() == newType)
                return existing;
            if (!owner.TryGetComponent(newType, out var comp))
                comp = Undo.AddComponent(owner, newType);
            if (CameraHelpers.GetReflectionProperty(comp, "Stage")?.ToString() != stage)
                throw new InvalidOperationException($"{newType.Name} does not implement the {stage} stage.");
            if (existing != null)
                Undo.DestroyObjectImmediate(existing);
            cmCamera.GetType().GetMethod("InvalidateComponentPipeline", Type.EmptyTypes)?.Invoke(cmCamera, null);
            return comp;
        }

        private static bool IsConcreteCinemachineType(Type type, string baseTypeName, string stageName = null)
        {
            var baseType = CameraHelpers.ResolveProviderType(baseTypeName);
            if (baseType == null || !baseType.IsAssignableFrom(type) || type.IsAbstract || type.ContainsGenericParameters)
                return false;
            if (stageName == null)
                return true;

            // CM2 predates CameraPipelineAttribute. Its built-in components declare a
            // constant Stage; check it before touching the existing camera or its undo state.
            if (baseType.Namespace == "Cinemachine" && type.Assembly == baseType.Assembly)
            {
                var stage = type.Name switch
                {
                    "CinemachineTransposer"
                    or "CinemachineFramingTransposer"
                    or "CinemachineOrbitalTransposer"
                    or "CinemachineTrackedDolly"
                    or "CinemachineHardLockToTarget"
                    or "Cinemachine3rdPersonFollow" => "Body",
                    "CinemachineComposer" or "CinemachineGroupComposer" or "CinemachinePOV" or "CinemachineHardLookAt" or "CinemachineSameAsFollowTarget" =>
                        "Aim",
                    "CinemachineBasicMultiChannelPerlin" => "Noise",
                    _ => null,
                };
                if (stage != null)
                    return stage == stageName;
            }

            // Read declared stages without creating a component or invoking its native-backed Stage getter.
            var pipelineAttributeType = CameraHelpers.ResolveProviderType("CameraPipelineAttribute");
            foreach (var attribute in type.GetCustomAttributesData())
            {
                if (attribute.AttributeType != pipelineAttributeType || attribute.ConstructorArguments.Count != 1)
                    continue;
                var stage = attribute.ConstructorArguments[0];
                return string.Equals(Enum.GetName(stage.ArgumentType, stage.Value), stageName, StringComparison.OrdinalIgnoreCase);
            }
            // Custom components may omit this metadata; retain their existing compatibility.
            return true;
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
                CameraHelpers.RecordPrefabInstanceChanges(component);
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
