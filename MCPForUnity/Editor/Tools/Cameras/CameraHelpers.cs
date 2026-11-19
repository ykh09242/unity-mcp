using System;
using System.Collections.Generic;
using System.Linq;
using System.Reflection;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Runtime.Helpers;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEngine;

namespace MCPForUnity.Editor.Tools.Cameras
{
    internal static class CameraHelpers
    {
        private static bool? _hasCinemachine;
        private static Type _cmCameraType;
        private static Type _cmBrainType;

        internal static bool HasCinemachine
        {
            get
            {
                if (_hasCinemachine == null)
                    DetectCinemachine();
                return _hasCinemachine.Value;
            }
        }

        internal static Type CinemachineCameraType
        {
            get
            {
                if (_hasCinemachine == null)
                    DetectCinemachine();
                return _cmCameraType;
            }
        }

        internal static Type CinemachineBrainType
        {
            get
            {
                if (_hasCinemachine == null)
                    DetectCinemachine();
                return _cmBrainType;
            }
        }

        private static void DetectCinemachine()
        {
            _cmCameraType = null;
            _cmBrainType = null;
            foreach (var identity in new[] { "Unity.Cinemachine.CinemachineCamera, Unity.Cinemachine", "Cinemachine.CinemachineVirtualCamera, Cinemachine" })
            {
                var camera = Type.GetType(identity);
                if (camera == null || UnityEditor.PackageManager.PackageInfo.FindForAssembly(camera.Assembly)?.name != "com.unity.cinemachine")
                    continue;
                var brain = camera.Assembly.GetType(camera.Namespace + ".CinemachineBrain");
                if (!typeof(Component).IsAssignableFrom(camera) || !typeof(Component).IsAssignableFrom(brain))
                    continue;
                _cmCameraType = camera;
                _cmBrainType = brain;
                break;
            }
            _hasCinemachine = _cmCameraType != null && _cmBrainType != null;
        }

        internal static string GetCinemachineVersion()
        {
            if (!HasCinemachine || _cmCameraType == null)
                return null;

            try
            {
                return UnityEditor.PackageManager.PackageInfo.FindForAssembly(_cmCameraType.Assembly)?.version;
            }
            catch
            {
                return "unknown";
            }
        }

        internal static GameObject FindTargetGameObject(JObject @params)
        {
            var targetToken = @params["target"];
            if (targetToken == null)
                return null;

            string searchMethod = ParamCoercion.CoerceString(@params["searchMethod"] ?? @params["search_method"], null);

            if (searchMethod != null)
                return GameObjectLookup.FindByTarget(targetToken, searchMethod, true);

            if (targetToken.Type == JTokenType.Integer)
            {
                int instanceId = targetToken.ReadScalar<int>();
                return GameObjectLookup.FindById(instanceId);
            }

            string targetStr = targetToken.ToString();
            if (int.TryParse(targetStr, out int parsedId))
            {
                var byId = GameObjectLookup.FindById(parsedId);
                if (byId != null)
                    return byId;
            }

            return GameObjectLookup.FindByTarget(targetToken, targetStr.Contains("/") ? "by_path" : "by_name", true);
        }

        internal static GameObject ResolveGameObjectRef(object reference)
        {
            if (reference == null)
                return null;

            if (reference is JToken jt)
            {
                if (jt.Type == JTokenType.Integer)
                    return GameObjectLookup.FindById(jt.ReadScalar<int>());
                if (jt.Type == JTokenType.String)
                {
                    string str = jt.ToString();
                    if (int.TryParse(str, out int id))
                    {
                        var byId = GameObjectLookup.FindById(id);
                        if (byId != null)
                            return byId;
                    }
                    return GameObjectLookup.FindByTarget(jt, str.Contains("/") ? "by_path" : "by_name", true);
                }
            }

            if (reference is string s)
            {
                if (int.TryParse(s, out int id))
                {
                    var byId = GameObjectLookup.FindById(id);
                    if (byId != null)
                        return byId;
                }
                var ids = GameObjectLookup.SearchGameObjects(
                    s.Contains("/") ? GameObjectLookup.SearchMethod.ByPath : GameObjectLookup.SearchMethod.ByName,
                    s,
                    includeInactive: true,
                    maxResults: 1
                );
                return ids.Count > 0 ? GameObjectLookup.FindById(ids[0]) : null;
            }

            return null;
        }

        internal static Component FindCinemachineCamera(JObject @params)
        {
            if (!HasCinemachine)
                return null;
            var go = FindTargetGameObject(@params);
            return FindVirtualCamera(go);
        }

        internal static Component FindVirtualCamera(GameObject go)
        {
            if (go == null || !HasCinemachine)
                return null;
            return go.GetComponent(VirtualCameraBaseType);
        }

        internal static Type VirtualCameraBaseType => ResolveProviderType("CinemachineVirtualCameraBase") ?? CinemachineCameraType;

        internal static Type LegacyCameraType => ResolveProviderType("CinemachineVirtualCamera");

        internal static bool UsesLegacyPipeline(Component camera) => camera != null && LegacyCameraType?.IsInstanceOfType(camera) == true;

        internal static bool HasEditablePipeline(Component camera) =>
            camera != null && (CinemachineCameraType.IsInstanceOfType(camera) || UsesLegacyPipeline(camera));

        internal static bool IsFreeLookRig(Component camera)
        {
            if (!UsesLegacyPipeline(camera) || camera.transform.parent == null)
                return false;
            var freeLook = ResolveProviderType("CinemachineFreeLook");
            var parent = freeLook == null ? null : camera.transform.parent.GetComponent(freeLook);
            if (parent == null)
                return false;

            // GetRig() populates the pipeline on demand. Inspect the existing ownership
            // references without materializing any rigs while listing cameras.
            using var serialized = new SerializedObject(parent);
            using var rigs = serialized.FindProperty("m_Rigs");
            if (rigs != null && rigs.isArray)
                for (int index = 0; index < rigs.arraySize; index++)
                {
                    using var rig = rigs.GetArrayElementAtIndex(index);
                    if (rig.objectReferenceValue == camera)
                        return true;
                }
            return false;
        }

        internal static Component FindBrain()
        {
            if (!HasCinemachine || _cmBrainType == null)
                return null;

            return UnityFindObjectsCompat.FindAny(_cmBrainType) as Component;
        }

        internal static UnityEngine.Camera FindMainCamera()
        {
            var main = UnityEngine.Camera.main;
            if (main != null)
                return main;

            var allCams = UnityFindObjectsCompat.FindAll<UnityEngine.Camera>();
            return allCams.Length > 0 ? allCams[0] : null;
        }

        internal static JObject ExtractProperties(JObject @params)
        {
            var token = @params["properties"];
            if (token == null || token.Type == JTokenType.Null)
                return null;
            if (token is JObject props)
                return props;
            if (token.Type == JTokenType.String)
            {
                try
                {
                    return JObject.Parse(token.Value<string>());
                }
                catch (Newtonsoft.Json.JsonException ex)
                {
                    throw new ArgumentException($"'properties' must be a JSON object: {ex.Message}", ex);
                }
            }
            throw new ArgumentException("'properties' must be a JSON object.");
        }

        internal static object GetReflectionProperty(Component component, string propertyName)
        {
            if (component == null)
                return null;
            var type = component.GetType();
            var prop = type.GetProperty(propertyName, BindingFlags.Public | BindingFlags.Instance);
            return prop?.GetValue(component);
        }

        /// <summary>Read priority int from a CinemachineCamera component via SerializedObject.</summary>
        internal static int ReadCinemachinePriority(Component cmCamera)
        {
            if (cmCamera == null)
                return 0;
            using var so = new SerializedObject(cmCamera);
            using var priorityProp = so.FindProperty("Priority") ?? so.FindProperty("m_Priority");
            if (priorityProp == null)
                return 0;
            if (priorityProp.propertyType == SerializedPropertyType.Integer)
                return priorityProp.intValue;
            using var enabledProp = priorityProp.FindPropertyRelative("Enabled");
            using var valueProp = priorityProp.FindPropertyRelative("m_Value");
            if (enabledProp != null && !enabledProp.boolValue)
                return 0;
            return valueProp?.intValue ?? 0;
        }

        internal static bool SetReflectionProperty(Component component, string propertyName, object value)
        {
            if (component == null)
                return false;
            var type = component.GetType();
            var prop = type.GetProperty(propertyName, BindingFlags.Public | BindingFlags.Instance);
            if (prop == null || !prop.CanWrite)
                return false;
            prop.SetValue(component, value);
            return true;
        }

        internal static void SetTransformTarget(Component cmCamera, string propertyName, JToken targetRef)
        {
            if (cmCamera == null)
                return;

            if (targetRef == null || targetRef.Type == JTokenType.Null)
            {
                SetReflectionProperty(cmCamera, propertyName, null);
                return;
            }

            var go = ResolveGameObjectRef(targetRef);
            if (go != null)
                SetReflectionProperty(cmCamera, propertyName, go.transform);
        }

        internal static Type ResolveComponentType(string typeName, Component camera = null)
        {
            return ResolveProviderType(typeName, camera) ?? UnityTypeResolver.ResolveComponent(typeName);
        }

        internal static Type ResolveProviderType(string name, Component camera = null)
        {
            // The brain and all built-in pipeline stages belong to the same package assembly.
            var brain = CinemachineBrainType;
            if (brain == null)
                return null;
            if (brain.Namespace == "Cinemachine" || (camera != null && UsesLegacyPipeline(camera)))
                name = name switch
                {
                    "CinemachineFollow" => "CinemachineTransposer",
                    "CinemachineRotationComposer" => "CinemachineComposer",
                    "CinemachineThirdPersonFollow" => "Cinemachine3rdPersonFollow",
                    "CinemachineOrbitalFollow" => "CinemachineOrbitalTransposer",
                    "CinemachineSplineDolly" => "CinemachineTrackedDolly",
                    "CinemachinePositionComposer" => "CinemachineFramingTransposer",
                    "CinemachinePanTilt" => "CinemachinePOV",
                    "CinemachineThirdPersonAim" => "Cinemachine3rdPersonAim",
                    "CinemachineDeoccluder" => "CinemachineCollider",
                    _ => name,
                };
            return brain.Assembly.GetType(name.Contains(".") ? name : brain.Namespace + "." + name);
        }

        internal static GameObject PipelineOwner(Component camera, bool create = false)
        {
            if (!HasEditablePipeline(camera))
                return null;
            if (!UsesLegacyPipeline(camera))
                return camera.gameObject;

            // Asking CM2 for GetComponentOwner (including through GetCinemachineComponent)
            // creates its hidden child. Reads and property preflight must leave the scene intact.
            var pipelineType = ResolveProviderType("CinemachinePipeline");
            if (pipelineType == null)
                return null;
            foreach (Transform child in camera.transform)
                if (child.GetComponent(pipelineType) != null)
                    return child.gameObject;
            if (!create)
                return null;
            var method = camera.GetType().GetMethod("GetComponentOwner", Type.EmptyTypes);
            return (method?.Invoke(camera, null) as Transform)?.gameObject;
        }

        internal static bool IsLive(Component camera)
        {
            var core = ResolveProviderType("CinemachineCore");
            var api = ResolveProviderType("ICinemachineCamera");
            var isLive = api == null ? null : core?.GetMethod("IsLive", new[] { api });
            if (isLive == null)
                return false;
            var receiver = isLive.IsStatic ? null : core.GetProperty("Instance")?.GetValue(null);
            return (isLive.IsStatic || receiver != null) && (bool)isLive.Invoke(receiver, new object[] { camera });
        }

        internal static Component GetPipelineComponent(Component cmCamera, string stageName)
        {
            if (!HasEditablePipeline(cmCamera))
                return null;
            var type = cmCamera.GetType();

            // CinemachineCamera.GetCinemachineComponent(CinemachineCore.Stage stage)
            if (UsesLegacyPipeline(cmCamera))
            {
                var owner = PipelineOwner(cmCamera);
                var baseType = ResolveProviderType("CinemachineComponentBase");
                return owner == null || baseType == null
                    ? null
                    : owner.GetComponents(baseType).FirstOrDefault(c => GetReflectionProperty(c, "Stage")?.ToString() == stageName);
            }
            var stageEnumType = ResolveProviderType("CinemachineCore+Stage");

            if (stageEnumType == null)
                return null;

            object stageEnum;
            try
            {
                stageEnum = Enum.Parse(stageEnumType, stageName, true);
            }
            catch
            {
                return null;
            }

            var method = type.GetMethod("GetCinemachineComponent", BindingFlags.Public | BindingFlags.Instance, null, new[] { stageEnumType }, null);

            if (method == null)
                return null;
            return method.Invoke(cmCamera, new[] { stageEnum }) as Component;
        }

        internal static string GetFallbackSuggestion(string action)
        {
            return action switch
            {
                "set_body" or "set_aim" => "Use 'set_lens' and 'set_target' for basic camera configuration.",
                "set_blend" => "Without Cinemachine, switch cameras by enabling/disabling Camera components.",
                "set_noise" => "Camera shake without Cinemachine requires a custom script.",
                "ensure_brain" => "CinemachineBrain requires the Cinemachine package. Basic Camera does not need a Brain.",
                "get_brain_status" => "No CinemachineBrain available. Cinemachine package not installed.",
                _ => "Install Cinemachine via Window > Package Manager.",
            };
        }

        internal static void RecordPrefabInstanceChanges(UnityEngine.Object target)
        {
            if (target != null && PrefabUtility.IsPartOfPrefabInstance(target))
                PrefabUtility.RecordPrefabInstancePropertyModifications(target);
        }

        internal static void MarkDirty(GameObject go)
        {
            if (go == null)
                return;
            EditorUtility.SetDirty(go);
            if (go.scene.IsValid() && go.scene.isLoaded)
                UnityEditor.SceneManagement.EditorSceneManager.MarkSceneDirty(go.scene);
        }
    }
}
