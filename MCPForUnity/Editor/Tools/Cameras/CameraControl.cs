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
    internal static class CameraControl
    {
        internal static object ListCameras(JObject @params)
        {
            var unityCameras = UnityFindObjectsCompat.FindAll<UnityEngine.Camera>();
            var cameraList = new List<object>();
            var unityCamList = new List<object>();

            // Cinemachine cameras
            if (CameraHelpers.HasCinemachine)
            {
                foreach (Component cm in UnityFindObjectsCompat.FindAll(CameraHelpers.VirtualCameraBaseType))
                {
                    if (CameraHelpers.IsFreeLookRig(cm))
                        continue;
                    var parent = CameraHelpers.GetReflectionProperty(cm, "ParentCamera") as Component;
                    var follow = CameraHelpers.GetReflectionProperty(cm, "Follow") as Transform;
                    var lookAt = CameraHelpers.GetReflectionProperty(cm, "LookAt") as Transform;
                    var isLive = CameraHelpers.IsLive(cm);
                    var priority = CameraHelpers.ReadCinemachinePriority(cm);

                    var body = CameraHelpers.GetPipelineComponent(cm, "Body");
                    var aim = CameraHelpers.GetPipelineComponent(cm, "Aim");
                    var noise = CameraHelpers.GetPipelineComponent(cm, "Noise");

                    // Collect extensions
                    var extensions = new List<string>();
                    var cmExtBaseType = CameraHelpers.ResolveProviderType("CinemachineExtension");
                    if (cmExtBaseType != null)
                    {
                        foreach (var comp in cm.gameObject.GetComponents(cmExtBaseType))
                        {
                            if (comp != null)
                                extensions.Add(comp.GetType().Name);
                        }
                    }

                    cameraList.Add(
                        new
                        {
                            instanceID = cm.gameObject.GetInstanceIDCompat(),
                            name = cm.gameObject.name,
                            cameraType = cm.GetType().Name,
                            parentCameraID = parent == null ? (int?)null : parent.gameObject.GetInstanceIDCompat(),
                            isLive,
                            priority,
                            follow = follow != null ? new { name = follow.gameObject.name, instanceID = follow.gameObject.GetInstanceIDCompat() } : null,
                            lookAt = lookAt != null ? new { name = lookAt.gameObject.name, instanceID = lookAt.gameObject.GetInstanceIDCompat() } : null,
                            body = body?.GetType().Name,
                            aim = aim?.GetType().Name,
                            noise = noise?.GetType().Name,
                            extensions,
                        }
                    );
                }
            }

            // Unity cameras
            foreach (var cam in unityCameras)
            {
                bool hasBrain = CameraHelpers.HasCinemachine && cam.gameObject.GetComponent(CameraHelpers.CinemachineBrainType) != null;
                unityCamList.Add(
                    new
                    {
                        instanceID = cam.gameObject.GetInstanceIDCompat(),
                        name = cam.gameObject.name,
                        depth = cam.depth,
                        fieldOfView = cam.fieldOfView,
                        hasBrain,
                    }
                );
            }

            // Brain info
            object brainInfo = null;
            if (CameraHelpers.HasCinemachine)
            {
                var brain = CameraHelpers.FindBrain();
                if (brain != null)
                {
                    var activeCam = CameraHelpers.GetReflectionProperty(brain, "ActiveVirtualCamera");
                    var isBlending = CameraHelpers.GetReflectionProperty(brain, "IsBlending");

                    string activeName = null;
                    int? activeID = null;
                    if (activeCam != null)
                    {
                        var nameProp = activeCam.GetType().GetProperty("Name");
                        activeName = nameProp?.GetValue(activeCam) as string;

                        if (activeCam is Component activeComp)
                            activeID = activeComp.gameObject.GetInstanceIDCompat();
                    }

                    brainInfo = new
                    {
                        exists = true,
                        gameObject = brain.gameObject.name,
                        instanceID = brain.gameObject.GetInstanceIDCompat(),
                        activeCameraName = activeName,
                        activeCameraID = activeID,
                        isBlending = isBlending is bool bl && bl,
                    };
                }
            }

            return new
            {
                success = true,
                data = new
                {
                    brain = brainInfo,
                    cinemachineCameras = cameraList,
                    unityCameras = unityCamList,
                    cinemachineInstalled = CameraHelpers.HasCinemachine,
                },
            };
        }

        internal static object GetBrainStatus(JObject @params)
        {
            var brain = CameraHelpers.FindBrain();
            if (brain == null)
                return new ErrorResponse("No CinemachineBrain found in the scene.");

            var activeCam = CameraHelpers.GetReflectionProperty(brain, "ActiveVirtualCamera");
            var isBlending = CameraHelpers.GetReflectionProperty(brain, "IsBlending");
            var activeBlend = CameraHelpers.GetReflectionProperty(brain, "ActiveBlend");

            string activeName = null;
            int? activeID = null;
            if (activeCam != null)
            {
                var nameProp = activeCam.GetType().GetProperty("Name");
                activeName = nameProp?.GetValue(activeCam) as string;
                if (activeCam is Component comp)
                    activeID = comp.gameObject.GetInstanceIDCompat();
            }

            string blendDesc = null;
            if (activeBlend != null)
            {
                var descProp = activeBlend.GetType().GetProperty("Description");
                blendDesc = descProp?.GetValue(activeBlend) as string;
            }

            return new
            {
                success = true,
                data = new
                {
                    gameObject = brain.gameObject.name,
                    instanceID = brain.gameObject.GetInstanceIDCompat(),
                    activeCameraName = activeName,
                    activeCameraID = activeID,
                    isBlending = isBlending is bool b && b,
                    blendDescription = blendDesc,
                },
            };
        }

        internal static object SetBlend(JObject @params)
        {
            var brain = CameraHelpers.FindBrain();
            if (brain == null)
                return new ErrorResponse("No CinemachineBrain found. Use 'ensure_brain' first.");

            var props = CameraHelpers.ExtractProperties(@params) ?? new JObject();
            var error = WriteBlend(brain, ParamCoercion.CoerceString(props["style"], null), ParamCoercion.CoerceFloat(props["duration"], -1f));
            if (error != null)
                return error;
            CameraHelpers.MarkDirty(brain.gameObject);

            return new
            {
                success = true,
                message = "Default blend configured on CinemachineBrain.",
                data = new
                {
                    instanceID = brain.gameObject.GetInstanceIDCompat(),
                    verification = RenderingObservation.ReadKnown(brain, "DefaultBlend", "m_DefaultBlend"),
                },
            };
        }

        internal static ErrorResponse WriteBlend(Component brain, string style, float duration)
        {
            using var serialized = new SerializedObject(brain);
            using var blend = serialized.FindProperty("DefaultBlend") ?? serialized.FindProperty("m_DefaultBlend");
            if (blend == null)
                return new ErrorResponse("Could not find DefaultBlend on CinemachineBrain.");
            if (style != null)
            {
                using var field = blend.FindPropertyRelative("Style") ?? blend.FindPropertyRelative("m_Style");
                if (field?.propertyType != SerializedPropertyType.Enum)
                    return new ErrorResponse("Could not find the default blend style on CinemachineBrain.");
                int index = Array.FindIndex(field.enumNames, name => name.Equals(style, StringComparison.OrdinalIgnoreCase));
                if (index < 0)
                    return new ErrorResponse($"Unsupported Cinemachine blend style '{style}'.");
                field.enumValueIndex = index;
            }
            if (duration >= 0)
            {
                using var field = blend.FindPropertyRelative("Time") ?? blend.FindPropertyRelative("m_Time");
                if (field?.propertyType != SerializedPropertyType.Float)
                    return new ErrorResponse("Could not find the default blend duration on CinemachineBrain.");
                field.floatValue = duration;
            }
            serialized.ApplyModifiedProperties();
            return null;
        }

        private static int _overrideId = -1;
        private static Component _overrideBrain;

        // Early CM2 releases expose the same Timeline override contract internally.
        private const BindingFlags OverrideMethods = BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic;

        internal static object ForceCamera(JObject @params)
        {
            ClearDestroyedOverride();
            var brain = CameraHelpers.FindBrain();
            if (brain == null)
                return new ErrorResponse("No CinemachineBrain found. Use 'ensure_brain' first.");

            var cmCamera = CameraHelpers.FindCinemachineCamera(@params);
            if (cmCamera == null)
                return new ErrorResponse("Target CinemachineCamera not found.");

            if (_overrideId >= 0 && _overrideBrain != brain)
                return new ErrorResponse("Release the active override before forcing a different CinemachineBrain.");

            var brainType = brain.GetType();
            var cameraInterface = CameraHelpers.ResolveProviderType("ICinemachineCamera");
            if (
                cameraInterface == null
                || !cameraInterface.IsInstanceOfType(cmCamera)
                || brainType.GetMethod("ReleaseCameraOverride", OverrideMethods, null, new[] { typeof(int) }, null) == null
            )
                return new ErrorResponse("This CinemachineBrain has no compatible, releasable camera override API.");

            var method = brainType.GetMethod(
                "SetCameraOverride",
                OverrideMethods,
                null,
                new[] { typeof(int), typeof(int), cameraInterface, cameraInterface, typeof(float), typeof(float) },
                null
            );
            object[] arguments = { _overrideId, 999, cmCamera, cmCamera, 1f, -1f };
            if (method == null)
            {
                method = brainType.GetMethod(
                    "SetCameraOverride",
                    OverrideMethods,
                    null,
                    new[] { typeof(int), cameraInterface, cameraInterface, typeof(float), typeof(float) },
                    null
                );
                arguments = new object[] { _overrideId, cmCamera, cmCamera, 1f, -1f };
            }
            if (method?.ReturnType != typeof(int))
                return new ErrorResponse("This CinemachineBrain has no compatible SetCameraOverride API.");
            try
            {
                _overrideId = (int)method.Invoke(brain, arguments);
            }
            catch (TargetInvocationException ex)
            {
                string hint = brain is Behaviour behaviour && !behaviour.isActiveAndEnabled ? " Enable the selected brain before retrying." : "";
                return new ErrorResponse($"CinemachineBrain rejected the override: {ex.InnerException?.Message ?? ex.Message}.{hint}");
            }
            _overrideBrain = brain;

            return new
            {
                success = true,
                message = $"Camera overridden to '{cmCamera.gameObject.name}'.",
                data = new
                {
                    instanceID = cmCamera.gameObject.GetInstanceIDCompat(),
                    overrideId = _overrideId,
                    method = "override",
                    verification = RenderingObservation.Pending(
                        RenderingObservation.ReadKnown(cmCamera, "Priority", "m_Priority"),
                        "camera_blend_not_observed"
                    ),
                },
            };
        }

        internal static object ReleaseOverride(JObject @params)
        {
            if (ClearDestroyedOverride())
                return new { success = true, message = "The override owner was destroyed; override state cleared." };
            if (_overrideId < 0)
            {
                if (CameraHelpers.FindBrain() == null)
                    return new ErrorResponse("No CinemachineBrain found.");
                return new { success = true, message = "No active camera override to release." };
            }

            var brain = _overrideBrain;
            if (brain == null)
                return new ErrorResponse("The CinemachineBrain owning the active override is unavailable.");

            var method = brain.GetType().GetMethod("ReleaseCameraOverride", OverrideMethods, null, new[] { typeof(int) }, null);

            if (method != null)
            {
                method.Invoke(brain, new object[] { _overrideId });
                int releasedId = _overrideId;
                _overrideId = -1;
                _overrideBrain = null;
                return new
                {
                    success = true,
                    message = "Camera override released.",
                    data = new { releasedOverrideId = releasedId },
                };
            }

            return new ErrorResponse("Could not release the active override: ReleaseCameraOverride is unavailable.");
        }

        private static bool ClearDestroyedOverride()
        {
            if (_overrideId < 0 || ReferenceEquals(_overrideBrain, null) || _overrideBrain != null)
                return false;
            _overrideId = -1;
            _overrideBrain = null;
            return true;
        }
    }
}
