using System;
using System.Collections.Generic;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Editor.Tools.Graphics;
using MCPForUnity.Runtime.Helpers;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEngine;

namespace MCPForUnity.Editor.Tools.Cameras
{
    internal static class CameraCreate
    {
        private static readonly Dictionary<string, (string body, string aim)> Presets = new(StringComparer.OrdinalIgnoreCase)
        {
            ["follow"] = ("CinemachineFollow", "CinemachineRotationComposer"),
            ["third_person"] = ("CinemachineThirdPersonFollow", "CinemachineRotationComposer"),
            ["freelook"] = ("CinemachineOrbitalFollow", "CinemachineRotationComposer"),
            ["dolly"] = ("CinemachineSplineDolly", "CinemachineRotationComposer"),
            ["static"] = (null, "CinemachineHardLookAt"),
            ["top_down"] = ("CinemachineFollow", null),
            ["side_scroller"] = ("CinemachinePositionComposer", null),
        };

        internal static object CreateBasicCamera(JObject @params)
        {
            var props = CameraHelpers.ExtractProperties(@params) ?? new JObject();
            string name = ParamCoercion.CoerceString(props["name"], null) ?? "Camera";
            float fov = ParamCoercion.CoerceFloat(props["fieldOfView"], 60f);
            float near = ParamCoercion.CoerceFloat(props["nearClipPlane"], 0.3f);
            float far = ParamCoercion.CoerceFloat(props["farClipPlane"], 1000f);

            var go = new GameObject(name);
            bool completed = false;
            try
            {
                Undo.RegisterCreatedObjectUndo(go, $"Create Camera '{name}'");
                var cam = go.AddComponent<UnityEngine.Camera>();
                if (cam == null)
                    return new ErrorResponse("Could not add Camera component.");
                cam.fieldOfView = fov;
                cam.nearClipPlane = near;
                cam.farClipPlane = far;

                // Position near follow target if provided
                string follow = ParamCoercion.CoerceString(props["follow"], null);
                if (follow != null)
                {
                    var target = CameraHelpers.ResolveGameObjectRef(follow);
                    if (target != null)
                        go.transform.position = target.transform.position + new Vector3(0, 5, -10);
                }

                // Look at target if provided
                string lookAt = ParamCoercion.CoerceString(props["lookAt"] ?? props["look_at"], null);
                if (lookAt != null)
                {
                    var target = CameraHelpers.ResolveGameObjectRef(lookAt);
                    if (target != null)
                        go.transform.LookAt(target.transform);
                }

                CameraHelpers.MarkDirty(go);
                completed = true;
                return new
                {
                    success = true,
                    message = $"Created basic Camera '{name}' (Cinemachine not installed — using Unity Camera).",
                    data = new
                    {
                        instanceID = go.GetInstanceIDCompat(),
                        cinemachine = false,
                        verification = RenderingObservation.Camera(cam),
                        hint = "Install com.unity.cinemachine for presets, blending, and virtual camera features.",
                    },
                };
            }
            finally
            {
                if (!completed && go != null)
                    UnityEngine.Object.DestroyImmediate(go);
            }
        }

        internal static object CreateCinemachineCamera(JObject @params)
        {
            var props = CameraHelpers.ExtractProperties(@params) ?? new JObject();
            string name = ParamCoercion.CoerceString(props["name"], null) ?? "CM Camera";
            string preset = ParamCoercion.CoerceString(props["preset"], null) ?? "follow";
            int priority = ParamCoercion.CoerceInt(props["priority"], 10);

            if (!Presets.TryGetValue(preset, out var presetDef))
            {
                return new ErrorResponse($"Unknown preset '{preset}'. Valid presets: {string.Join(", ", Presets.Keys)}.");
            }

            var cmType = CameraHelpers.CinemachineCameraType;
            var bodyType = presetDef.body == null ? null : CameraHelpers.ResolveProviderType(presetDef.body);
            var aimType = presetDef.aim == null ? null : CameraHelpers.ResolveProviderType(presetDef.aim);
            if (cmType == null || (presetDef.body != null && bodyType == null) || (presetDef.aim != null && aimType == null))
                return new ErrorResponse($"Required Cinemachine components for preset '{preset}' are unavailable.");

            float? fieldOfView = ParamCoercion.CoerceFloatNullable(props["fieldOfView"]);
            float? nearClipPlane = ParamCoercion.CoerceFloatNullable(props["nearClipPlane"]);
            float? farClipPlane = ParamCoercion.CoerceFloatNullable(props["farClipPlane"]);

            var go = new GameObject(name);
            bool completed = false;
            try
            {
                Undo.RegisterCreatedObjectUndo(go, $"Create CinemachineCamera '{name}'");
                var cmCamera = go.AddComponent(cmType);
                if (cmCamera == null)
                    return new ErrorResponse("Could not add CinemachineCamera component.");

                var priorityError = CameraConfigure.SetPriority(cmCamera, priority);
                if (priorityError != null)
                    return priorityError;
                using (var so = new SerializedObject(cmCamera))
                {
                    if (props["fieldOfView"] != null || props["nearClipPlane"] != null || props["farClipPlane"] != null)
                    {
                        using var lensProp = so.FindProperty("Lens") ?? so.FindProperty("m_Lens");
                        if (lensProp == null)
                            return new ErrorResponse("Could not find Lens property on CinemachineCamera.");
                        foreach (
                            var (input, field, value) in new[]
                            {
                                ("fieldOfView", "FieldOfView", fieldOfView),
                                ("nearClipPlane", "NearClipPlane", nearClipPlane),
                                ("farClipPlane", "FarClipPlane", farClipPlane),
                            }
                        )
                        {
                            if (props[input] == null)
                                continue;
                            using var lensField = lensProp.FindPropertyRelative(field);
                            if (lensField == null)
                                return new ErrorResponse($"Could not find Lens.{field} property on CinemachineCamera.");
                            lensField.floatValue = value ?? lensField.floatValue;
                        }
                        so.ApplyModifiedProperties();
                    }
                }

                if (bodyType != null && CameraConfigure.SwapPipelineComponent(go, "Body", bodyType) == null)
                    return new ErrorResponse($"Could not add {presetDef.body} component.");
                if (aimType != null && CameraConfigure.SwapPipelineComponent(go, "Aim", aimType) == null)
                    return new ErrorResponse($"Could not add {presetDef.aim} component.");

                var followToken = props["follow"];
                if (followToken != null && followToken.Type != JTokenType.Null)
                    CameraHelpers.SetTransformTarget(cmCamera, "Follow", followToken);
                var lookAtToken = props["lookAt"] ?? props["look_at"];
                if (lookAtToken != null && lookAtToken.Type != JTokenType.Null)
                    CameraHelpers.SetTransformTarget(cmCamera, "LookAt", lookAtToken);

                CameraHelpers.MarkDirty(go);
                completed = true;
                return new
                {
                    success = true,
                    message = $"Created CinemachineCamera '{name}' with preset '{preset}'.",
                    data = new
                    {
                        instanceID = go.GetInstanceIDCompat(),
                        cinemachine = true,
                        verification = RenderingObservation.ReadKnown(cmCamera, "Target", "Lens", "m_Lens", "Priority", "m_Priority"),
                        preset,
                        priority,
                        body = bodyType?.Name,
                        aim = aimType?.Name,
                    },
                };
            }
            finally
            {
                if (!completed && go != null)
                    UnityEngine.Object.DestroyImmediate(go);
            }
        }

        internal static object EnsureBrain(JObject @params)
        {
            var props = CameraHelpers.ExtractProperties(@params) ?? new JObject();

            string cameraRef = ParamCoercion.CoerceString(props["camera"], null);
            UnityEngine.Camera cam = null;
            Component existingBrain;
            if (cameraRef != null)
            {
                var camGo = CameraHelpers.ResolveGameObjectRef(cameraRef);
                cam = camGo != null ? camGo.GetComponent<UnityEngine.Camera>() : null;
                if (cam == null)
                    return new ErrorResponse("No Camera found to add CinemachineBrain to.");
                existingBrain = cam.gameObject.GetComponent(CameraHelpers.CinemachineBrainType);
            }
            else
            {
                existingBrain = CameraHelpers.FindBrain();
            }
            if (existingBrain != null)
            {
                return new
                {
                    success = true,
                    message = $"CinemachineBrain already exists on '{existingBrain.gameObject.name}'.",
                    data = new { instanceID = existingBrain.gameObject.GetInstanceIDCompat(), alreadyExisted = true },
                };
            }

            // Find target camera
            if (cam == null)
                cam = CameraHelpers.FindMainCamera();

            if (cam == null)
                return new ErrorResponse("No Camera found to add CinemachineBrain to.");

            var brainType = CameraHelpers.CinemachineBrainType;
            string blendStyle = ParamCoercion.CoerceString(props["defaultBlendStyle"] ?? props["default_blend_style"], null);
            float blendDuration = ParamCoercion.CoerceFloat(props["defaultBlendDuration"] ?? props["default_blend_duration"], -1f);

            Undo.RecordObject(cam.gameObject, "Add CinemachineBrain");
            Component brain = null;
            bool completed = false;
            try
            {
                brain = cam.gameObject.AddComponent(brainType);
                if (brain == null)
                    return new ErrorResponse("Could not add CinemachineBrain component.");

                // Configure default blend if provided
                if (blendStyle != null || blendDuration >= 0)
                {
                    var error = CameraControl.WriteBlend(brain, blendStyle, blendDuration);
                    if (error != null)
                        return error;
                }

                CameraHelpers.MarkDirty(cam.gameObject);
                completed = true;
                return new
                {
                    success = true,
                    message = $"CinemachineBrain added to '{cam.gameObject.name}'.",
                    data = new
                    {
                        instanceID = cam.gameObject.GetInstanceIDCompat(),
                        alreadyExisted = false,
                        verification = RenderingObservation.ReadKnown(brain, "DefaultBlend", "m_DefaultBlend"),
                    },
                };
            }
            finally
            {
                if (!completed && brain != null)
                    UnityEngine.Object.DestroyImmediate(brain);
            }
        }
    }
}
