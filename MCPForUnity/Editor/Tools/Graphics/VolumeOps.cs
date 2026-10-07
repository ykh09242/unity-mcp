using System;
using System.Collections.Generic;
using System.Linq;
using System.Reflection;
using System.Runtime.CompilerServices;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Runtime.Helpers;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEngine;

namespace MCPForUnity.Editor.Tools.Graphics
{
    internal static class VolumeOps
    {
        // === volume_create ===
        // Params: name (string), is_global (bool, default true), weight (float, default 1),
        //         priority (float, default 0), profile_path (string, optional - path to save VolumeProfile asset),
        //         effects (array of {type, ...params}, optional - effects to add immediately)
        internal static object CreateVolume(JObject @params)
        {
            var p = new ToolParams(@params);
            string name = p.Get("name") ?? "Volume";
            bool isGlobal = p.GetBool("is_global", true);
            float weight = p.GetFloat("weight") ?? 1.0f;
            float priority = p.GetFloat("priority") ?? 0f;
            string profilePath = p.Get("profile_path");
            if (!string.IsNullOrEmpty(profilePath))
            {
                if (!profilePath.EndsWith(".asset", StringComparison.OrdinalIgnoreCase))
                    profilePath += ".asset";
                try
                {
                    profilePath = AssetPathUtility.GetContainedAssetPath(profilePath);
                }
                catch (Exception ex) when (ex is ArgumentException || ex is InvalidOperationException)
                {
                    return new ErrorResponse($"Invalid profile_path: {ex.Message}");
                }
            }

            object profile = null;
            if (!string.IsNullOrEmpty(profilePath))
            {
                var existing = AssetDatabase.LoadAssetAtPath<UnityEngine.Object>(profilePath);
                if (existing != null)
                {
                    if (!GraphicsHelpers.VolumeProfileType.IsInstanceOfType(existing))
                        return new ErrorResponse($"Asset at '{profilePath}' is not a VolumeProfile.");
                    profile = existing;
                }
                else if (System.IO.File.Exists(AssetPathUtility.GetFullAssetPath(profilePath)))
                    return new ErrorResponse($"A file already exists at '{profilePath}' and could not be loaded as a VolumeProfile.");
            }

            var effectsToken = p.GetRaw("effects") as JArray;
            var initialEffects = new List<(JObject definition, Type type, string name)>();
            var effectTypes = new HashSet<Type>();
            if (profile != null && GetProperty(profile, "components") is System.Collections.IList existingEffects)
                foreach (var existingEffect in existingEffects)
                    if (existingEffect != null)
                        effectTypes.Add(existingEffect.GetType());
            if (effectsToken != null)
            {
                foreach (var effectDefinition in effectsToken)
                {
                    if (effectDefinition is not JObject definition)
                        continue;
                    string effectName = ParamCoercion.CoerceString(definition["type"], null);
                    var type = GraphicsHelpers.ResolveVolumeComponentType(effectName);
                    if (type == null)
                        continue;
                    if (!effectTypes.Add(type))
                        return new ErrorResponse($"VolumeProfile already contains effect '{effectName}' or the request lists it more than once.");
                    initialEffects.Add((definition, type, effectName));
                }
            }

            using var folders = new AssetFolderScope();
            var go = new GameObject(name);
            UnityEngine.Object allocatedProfile = null;
            bool completed = false;
            try
            {
                Undo.RegisterCreatedObjectUndo(go, $"Create Volume '{name}'");

                // Add Volume component via reflection
                var volumeComp = go.AddComponent(GraphicsHelpers.VolumeType);
                if (volumeComp == null)
                    return new ErrorResponse("Could not add Volume component.");

                // Set properties via reflection
                SetProperty(volumeComp, "isGlobal", isGlobal);
                SetProperty(volumeComp, "weight", weight);
                SetProperty(volumeComp, "priority", priority);

                // Create or load VolumeProfile
                if (!string.IsNullOrEmpty(profilePath))
                {
                    // Load existing or create new profile asset
                    if (profile == null)
                    {
                        allocatedProfile = ScriptableObject.CreateInstance(GraphicsHelpers.VolumeProfileType);
                        profile = allocatedProfile;
                        if (profile == null)
                            return new ErrorResponse("Could not create VolumeProfile.");
                        folders.EnsureParentDirectory(profilePath);
                        AssetPathUtility.GetFullAssetPath(profilePath);
                        AssetDatabase.CreateAsset(allocatedProfile, profilePath);
                        if (!AssetDatabase.Contains(allocatedProfile) || AssetDatabase.GetAssetPath(allocatedProfile) != profilePath)
                            return new ErrorResponse($"Failed to create VolumeProfile asset at '{profilePath}'.");
                    }
                }
                else
                {
                    // Create embedded profile (not saved as asset)
                    allocatedProfile = ScriptableObject.CreateInstance(GraphicsHelpers.VolumeProfileType);
                    profile = allocatedProfile;
                    if (profile == null)
                        return new ErrorResponse("Could not create VolumeProfile.");
                }

                // Assign profile (sharedProfile is a public field, handled by SetProperty's field fallback)
                SetProperty(volumeComp, "sharedProfile", profile);

                // Add initial effects if provided
                var addedEffects = new List<string>();
                if (effectsToken != null)
                {
                    var addMethod = GraphicsHelpers.VolumeProfileType.GetMethod("Add", new[] { typeof(Type), typeof(bool) });
                    foreach (var effect in initialEffects)
                    {
                        if (addMethod != null)
                        {
                            var component = addMethod.Invoke(profile, new object[] { effect.type, true });
                            if (component != null)
                            {
                                // Set parameters — support both nested {"parameters": {...}} and flat fields
                                var paramObj = effect.definition["parameters"] as JObject;
                                if (paramObj != null)
                                {
                                    foreach (var pp in paramObj.Properties())
                                        SetVolumeParameter(component, pp.Name, pp.Value);
                                }
                                else
                                {
                                    foreach (var prop in effect.definition.Properties())
                                    {
                                        if (prop.Name == "type")
                                            continue;
                                        SetVolumeParameter(component, prop.Name, prop.Value);
                                    }
                                }
                                PersistAddedEffect(profile, component);
                                addedEffects.Add(effect.name);
                            }
                        }
                    }
                    if (profile is UnityEngine.Object profileObj)
                        EditorUtility.SetDirty(profileObj);
                }

                GraphicsHelpers.MarkDirty(volumeComp);
                folders.Complete();
                completed = true;

                return new
                {
                    success = true,
                    message = $"Created {(isGlobal ? "global" : "local")} Volume '{name}'"
                        + (addedEffects.Count > 0 ? $" with effects: {string.Join(", ", addedEffects)}" : ""),
                    data = new
                    {
                        instanceID = go.GetInstanceIDCompat(),
                        isGlobal,
                        weight,
                        priority,
                        profilePath = profilePath ?? "(embedded)",
                        effects = addedEffects,
                    },
                };
            }
            finally
            {
                if (!completed)
                {
                    if (go != null)
                        UnityEngine.Object.DestroyImmediate(go);
                    DestroyTransientProfile(allocatedProfile);
                }
            }
        }

        // === volume_add_effect ===
        // Params: target (string/int), effect (string - type name like "Bloom")
        internal static object AddEffect(JObject @params)
        {
            var p = new ToolParams(@params);
            string effectName = p.Get("effect");
            if (string.IsNullOrEmpty(effectName))
                return new ErrorResponse("'effect' parameter is required (e.g., 'Bloom', 'Vignette').");

            var volume = GraphicsHelpers.FindVolume(@params);
            if (volume == null)
                return new ErrorResponse("Volume not found. Specify 'target' (name or instance ID).");

            var effectType = GraphicsHelpers.ResolveVolumeComponentType(effectName);
            if (effectType == null)
            {
                var available = GraphicsHelpers.GetAvailableEffectTypes().Select(t => t.Name).ToList();
                return new ErrorResponse($"Effect type '{effectName}' not found. Available: {string.Join(", ", available.Take(20))}");
            }

            var profile = GetProperty(volume, "sharedProfile");
            if (profile == null)
                return new ErrorResponse("Volume has no profile assigned.");

            // Check if effect already exists
            var components = GetProperty(profile, "components") as System.Collections.IList;
            if (components != null)
            {
                foreach (var comp in components)
                {
                    if (comp != null && comp.GetType() == effectType)
                        return new ErrorResponse($"Effect '{effectName}' already exists on this Volume. Use volume_set_effect to modify it.");
                }
            }

            // profile.Add(effectType, true) -- 'true' means override all params
            var addMethod = GraphicsHelpers.VolumeProfileType.GetMethod("Add", new[] { typeof(Type), typeof(bool) });
            if (addMethod == null)
                return new ErrorResponse("Could not find VolumeProfile.Add method.");

            var component = addMethod.Invoke(profile, new object[] { effectType, true });
            if (component == null)
                return new ErrorResponse($"Failed to add effect '{effectName}'.");

            PersistAddedEffect(profile, component);

            if (profile is UnityEngine.Object profileObj)
                EditorUtility.SetDirty(profileObj);
            GraphicsHelpers.MarkDirty(volume);

            return new
            {
                success = true,
                message = $"Added '{effectName}' to Volume '{(volume as Component)?.gameObject.name}'.",
                data = new { effect = effectName, volumeInstanceID = (volume as Component)?.gameObject.GetInstanceIDCompat() },
            };
        }

        // === volume_set_effect ===
        // Params: target (string/int), effect (string), parameters (dict of field->value)
        internal static object SetEffect(JObject @params)
        {
            var p = new ToolParams(@params);
            string effectName = p.Get("effect");
            if (string.IsNullOrEmpty(effectName))
                return new ErrorResponse("'effect' parameter is required.");

            var volume = GraphicsHelpers.FindVolume(@params);
            if (volume == null)
                return new ErrorResponse("Volume not found. Specify 'target'.");

            var profile = GetProperty(volume, "sharedProfile");
            if (profile == null)
                return new ErrorResponse("Volume has no profile assigned.");

            var effectType = GraphicsHelpers.ResolveVolumeComponentType(effectName);
            if (effectType == null)
                return new ErrorResponse($"Effect type '{effectName}' not found.");

            // Find the effect component in the profile
            var components = GetProperty(profile, "components") as System.Collections.IList;
            if (components == null)
                return new ErrorResponse("Could not read profile components.");

            object targetComponent = null;
            foreach (var comp in components)
            {
                if (comp != null && comp.GetType() == effectType)
                {
                    targetComponent = comp;
                    break;
                }
            }

            if (targetComponent == null)
                return new ErrorResponse($"Effect '{effectName}' not found on this Volume. Use volume_add_effect first.");

            // Set parameters
            var parameters = p.GetRaw("parameters") as JObject;
            if (parameters == null)
                return new ErrorResponse("'parameters' dict is required.");

            var setParams = new List<string>();
            var failedParams = new List<string>();
            foreach (var prop in parameters.Properties())
            {
                if (SetVolumeParameter(targetComponent, prop.Name, prop.Value))
                    setParams.Add(prop.Name);
                else
                    failedParams.Add(prop.Name);
            }

            if (profile is UnityEngine.Object profileObj)
                EditorUtility.SetDirty(profileObj);

            var msg = $"Set {setParams.Count} parameter(s) on '{effectName}'";
            if (failedParams.Count > 0)
                msg += $". Failed: {string.Join(", ", failedParams)}";

            return new
            {
                success = true,
                message = msg,
                data = new
                {
                    effect = effectName,
                    set = setParams,
                    failed = failedParams,
                },
            };
        }

        // === volume_remove_effect ===
        // Params: target, effect
        internal static object RemoveEffect(JObject @params)
        {
            var p = new ToolParams(@params);
            string effectName = p.Get("effect");
            if (string.IsNullOrEmpty(effectName))
                return new ErrorResponse("'effect' parameter is required.");

            var volume = GraphicsHelpers.FindVolume(@params);
            if (volume == null)
                return new ErrorResponse("Volume not found.");

            var effectType = GraphicsHelpers.ResolveVolumeComponentType(effectName);
            if (effectType == null)
                return new ErrorResponse($"Effect type '{effectName}' not found.");

            var profile = GetProperty(volume, "sharedProfile");
            if (profile == null)
                return new ErrorResponse("Volume has no profile.");

            // Check if effect exists before removing
            bool found = false;
            var components = GetProperty(profile, "components") as System.Collections.IList;
            if (components != null)
            {
                foreach (var comp in components)
                {
                    if (comp != null && comp.GetType() == effectType)
                    {
                        found = true;
                        break;
                    }
                }
            }
            if (!found)
                return new ErrorResponse($"Effect '{effectName}' not found on this Volume.");

            var removeMethod = GraphicsHelpers.VolumeProfileType.GetMethod("Remove", new[] { typeof(Type) });
            if (removeMethod == null)
                return new ErrorResponse("Could not find VolumeProfile.Remove method.");

            removeMethod.Invoke(profile, new object[] { effectType });

            if (profile is UnityEngine.Object profileObj)
                EditorUtility.SetDirty(profileObj);
            GraphicsHelpers.MarkDirty(volume);

            return new
            {
                success = true,
                message = $"Removed '{effectName}' from Volume.",
                data = new { effect = effectName },
            };
        }

        // === volume_get_info ===
        // Params: target (optional -- if omitted, returns info for all volumes)
        internal static object GetInfo(JObject @params)
        {
            var volume = GraphicsHelpers.FindVolume(@params);
            if (volume == null)
                return new ErrorResponse("Volume not found.");

            var info = BuildVolumeInfo(volume);
            return new
            {
                success = true,
                message = $"Volume info for '{(volume as Component)?.gameObject.name}'.",
                data = info,
            };
        }

        // === volume_set_properties ===
        // Params: target, weight, priority, is_global, blend_distance
        //         OR properties dict with those keys
        internal static object SetProperties(JObject @params)
        {
            var p = new ToolParams(@params);
            var volume = GraphicsHelpers.FindVolume(@params);
            if (volume == null)
                return new ErrorResponse("Volume not found.");

            // Unpack "properties" dict into top-level params so callers can use either style
            var propsDict = p.GetRaw("properties") as JObject;
            if (propsDict != null)
            {
                foreach (var prop in propsDict.Properties())
                {
                    if (@params[prop.Name] == null)
                        @params[prop.Name] = prop.Value;
                }
                p = new ToolParams(@params);
            }

            var changed = new List<string>();

            var weight = p.GetFloat("weight");
            if (weight.HasValue)
            {
                SetProperty(volume, "weight", weight.Value);
                changed.Add("weight");
            }

            var priority = p.GetFloat("priority");
            if (priority.HasValue)
            {
                SetProperty(volume, "priority", priority.Value);
                changed.Add("priority");
            }

            if (p.Has("is_global"))
            {
                SetProperty(volume, "isGlobal", p.GetBool("is_global"));
                changed.Add("isGlobal");
            }

            var blendDist = p.GetFloat("blend_distance");
            if (blendDist.HasValue)
            {
                SetProperty(volume, "blendDistance", blendDist.Value);
                changed.Add("blendDistance");
            }

            if (changed.Count == 0)
                return new ErrorResponse("No properties specified. Use: weight, priority, is_global, blend_distance.");

            GraphicsHelpers.MarkDirty(volume);
            return new
            {
                success = true,
                message = $"Updated Volume properties: {string.Join(", ", changed)}",
                data = new { changed },
            };
        }

        // === volume_list_effects ===
        // No params needed -- lists all available VolumeComponent types
        internal static object ListEffects(JObject @params)
        {
            var types = GraphicsHelpers.GetAvailableEffectTypes();
            var effectList = types
                .Select(t => new
                {
                    name = t.Name,
                    fullName = t.FullName,
                    ns = t.Namespace,
                })
                .ToList();

            return new
            {
                success = true,
                message = $"Found {effectList.Count} available volume effects.",
                data = new { pipeline = GraphicsHelpers.GetPipelineName(), effects = effectList },
            };
        }

        // === volume_create_profile ===
        // Params: path (string -- asset path like "Assets/Settings/MyProfile.asset")
        internal static object CreateProfile(JObject @params)
        {
            var p = new ToolParams(@params);
            string path = p.Get("path");
            if (string.IsNullOrEmpty(path))
                return new ErrorResponse("'path' parameter is required (e.g., 'Settings/MyProfile' or 'Assets/Settings/MyProfile.asset').");

            if (!path.EndsWith(".asset", StringComparison.OrdinalIgnoreCase))
                path += ".asset";
            try
            {
                path = AssetPathUtility.GetContainedAssetPath(path);
            }
            catch (Exception ex) when (ex is ArgumentException || ex is InvalidOperationException)
            {
                return new ErrorResponse($"Invalid path: {ex.Message}");
            }

            if (System.IO.File.Exists(AssetPathUtility.GetFullAssetPath(path)) || AssetDatabase.LoadAssetAtPath<UnityEngine.Object>(path) != null)
                return new ErrorResponse($"An asset or file already exists at '{path}'.");

            using var folders = new AssetFolderScope();
            UnityEngine.Object profile = null;
            try
            {
                profile = ScriptableObject.CreateInstance(GraphicsHelpers.VolumeProfileType);
                if (profile == null)
                    return new ErrorResponse("Could not create VolumeProfile.");
                folders.EnsureParentDirectory(path);
                AssetDatabase.CreateAsset(profile, path);
                if (!AssetDatabase.Contains(profile) || AssetDatabase.GetAssetPath(profile) != path)
                    return new ErrorResponse($"Failed to create VolumeProfile asset at '{path}'.");
                AssetDatabase.SaveAssets();
                folders.Complete();

                return new
                {
                    success = true,
                    message = $"Created VolumeProfile at '{path}'.",
                    data = new { path },
                };
            }
            finally
            {
                DestroyTransientProfile(profile);
            }
        }

        // VolumeProfile.Add allocates a new component but does not add it to an asset file.
        private static void PersistAddedEffect(object profile, object component)
        {
            if (!(profile is UnityEngine.Object profileObj) || !AssetDatabase.Contains(profileObj) || !(component is UnityEngine.Object componentObj))
                return;

            try
            {
                AssetDatabase.AddObjectToAsset(componentObj, profileObj);
                if (!AssetDatabase.Contains(componentObj) || AssetDatabase.GetAssetPath(componentObj) != AssetDatabase.GetAssetPath(profileObj))
                    throw new InvalidOperationException("Failed to persist the added Volume effect in its profile asset.");
                EditorUtility.SetDirty(componentObj);
            }
            finally
            {
                // A failed addition may leave the new transient in the profile's managed list.
                // Remove only that exact reference, retaining actually persisted components.
                if (!AssetDatabase.Contains(componentObj))
                {
                    if (GetProperty(profile, "components") is System.Collections.IList components)
                    {
                        for (int i = components.Count - 1; i >= 0; i--)
                            if (ReferenceEquals(components[i], component))
                                components.RemoveAt(i);
                    }
                    UnityEngine.Object.DestroyImmediate(componentObj);
                }
            }
        }

        // Only a newly allocated profile is passed here. Borrowed and persisted assets are retained.
        private static void DestroyTransientProfile(UnityEngine.Object profile)
        {
            if (profile == null || AssetDatabase.Contains(profile))
                return;
            if (GetProperty(profile, "components") is System.Collections.IList components)
            {
                foreach (var component in components)
                    if (component is UnityEngine.Object obj && !AssetDatabase.Contains(obj))
                        UnityEngine.Object.DestroyImmediate(obj);
            }
            UnityEngine.Object.DestroyImmediate(profile);
        }

        // === ListVolumes (used by VolumesResource) ===
        internal static object ListVolumes(JObject @params)
        {
            if (!GraphicsHelpers.HasVolumeSystem)
                return new
                {
                    success = true,
                    message = "Volume system not available.",
                    data = new { volumes = new List<object>() },
                };

            var allVolumes = UnityFindObjectsCompat.FindAll(GraphicsHelpers.VolumeType);
            var volumeList = new List<object>();
            var profileEffects = new Dictionary<object, List<object>>(ProfileReferenceComparer.Instance);

            foreach (Component vol in allVolumes)
            {
                volumeList.Add(BuildVolumeInfo(vol, profileEffects));
            }

            return new
            {
                success = true,
                message = $"Found {volumeList.Count} volume(s).",
                data = new { pipeline = GraphicsHelpers.GetPipelineName(), volumes = volumeList },
            };
        }

        // --- Helper: Build info object for a single Volume ---
        private static object BuildVolumeInfo(object volumeComponent, Dictionary<object, List<object>> profileEffects = null)
        {
            var comp = volumeComponent as Component;
            if (comp == null)
                return null;

            bool isGlobal = GetPropertyValue<bool>(volumeComponent, "isGlobal", true);
            float weight = GetPropertyValue<float>(volumeComponent, "weight", 1f);
            float priority = GetPropertyValue<float>(volumeComponent, "priority", 0f);
            float blendDistance = GetPropertyValue<float>(volumeComponent, "blendDistance", 0f);

            var profile = GetProperty(volumeComponent, "sharedProfile");
            string profileName = profile is UnityEngine.Object profileObj2 ? profileObj2.name : null;
            string profilePath = profile is UnityEngine.Object po ? AssetDatabase.GetAssetPath(po) : null;

            return new
            {
                name = comp.gameObject.name,
                instance_id = comp.gameObject.GetInstanceIDCompat(),
                is_global = isGlobal,
                weight,
                priority,
                blend_distance = blendDistance,
                profile = profileName,
                profile_path = profilePath ?? "",
                effects = BuildProfileEffects(profile, profileEffects),
            };
        }

        private static List<object> BuildProfileEffects(object profile, Dictionary<object, List<object>> profileEffects = null)
        {
            if (profile != null && profileEffects != null && profileEffects.TryGetValue(profile, out var cached))
                return cached;

            var effectsList = new List<object>();
            if (profile != null)
            {
                var components = GetProperty(profile, "components") as System.Collections.IList;
                if (components != null)
                {
                    foreach (var effect in components)
                    {
                        if (effect == null)
                            continue;
                        var effectType = effect.GetType();
                        bool active = GetPropertyValue<bool>(effect, "active", true);

                        // Collect overridden parameters
                        var overriddenParams = new List<string>();
                        foreach (var field in effectType.GetFields(BindingFlags.Public | BindingFlags.Instance))
                        {
                            var fieldValue = field.GetValue(effect);
                            if (fieldValue == null)
                                continue;
                            var overrideProp = fieldValue.GetType().GetProperty("overrideState");
                            if (overrideProp != null)
                            {
                                bool overridden = (bool)overrideProp.GetValue(fieldValue);
                                if (overridden)
                                    overriddenParams.Add(field.Name);
                            }
                        }

                        effectsList.Add(
                            new
                            {
                                type = effectType.Name,
                                active,
                                overridden_params = overriddenParams,
                            }
                        );
                    }
                }
            }

            if (profile != null && profileEffects != null)
                profileEffects.Add(profile, effectsList);
            return effectsList;
        }

        private sealed class ProfileReferenceComparer : IEqualityComparer<object>
        {
            internal static readonly ProfileReferenceComparer Instance = new ProfileReferenceComparer();

            public new bool Equals(object x, object y) => ReferenceEquals(x, y);

            public int GetHashCode(object obj) => RuntimeHelpers.GetHashCode(obj);
        }

        // --- Helper: Set a VolumeParameter field value via reflection ---
        // VolumeParameter<T> has: overrideState (bool), value (T)
        internal static bool SetVolumeParameter(object component, string fieldName, JToken value)
        {
            if (component == null || string.IsNullOrEmpty(fieldName))
                return false;

            var field = component.GetType().GetField(fieldName, BindingFlags.Public | BindingFlags.Instance);
            if (field == null)
            {
                // Try camelCase conversion from snake_case
                string camelCase = StringCaseUtility.ToCamelCase(fieldName);
                field = component.GetType().GetField(camelCase, BindingFlags.Public | BindingFlags.Instance);
            }
            if (field == null)
                return false;

            var param = field.GetValue(component);
            if (param == null)
                return false;

            // Set value with type conversion, then enable override on success
            var valueProp = param.GetType().GetProperty("value");
            if (valueProp == null)
                return false;

            try
            {
                object converted = ConvertToParameterType(value, valueProp.PropertyType);
                valueProp.SetValue(param, converted);

                var overrideProp = param.GetType().GetProperty("overrideState");
                if (overrideProp != null)
                    overrideProp.SetValue(param, true);

                return true;
            }
            catch (Exception ex)
            {
                McpLog.Warn($"[VolumeOps] Failed to set '{fieldName}': {ex.Message}");
                return false;
            }
        }

        // --- Helper: Convert JToken to target parameter type ---
        private static object ConvertToParameterType(JToken value, Type targetType)
        {
            if (value == null || value.Type == JTokenType.Null)
                return null;

            // Handle Color
            if (targetType == typeof(Color))
            {
                if (value is JArray arr && arr.Count >= 3)
                {
                    float r = arr[0].ReadScalar<float>();
                    float g = arr[1].ReadScalar<float>();
                    float b = arr[2].ReadScalar<float>();
                    float a = arr.Count >= 4 ? arr[3].ReadScalar<float>() : 1f;
                    return new Color(r, g, b, a);
                }
                // Try hex string
                if (value.Type == JTokenType.String)
                {
                    if (ColorUtility.TryParseHtmlString(value.ToString(), out Color c))
                        return c;
                }
            }

            // Handle Vector2
            if (targetType == typeof(Vector2))
            {
                if (value is JArray arr && arr.Count >= 2)
                    return new Vector2(arr[0].ReadScalar<float>(), arr[1].ReadScalar<float>());
            }

            // Handle Vector3
            if (targetType == typeof(Vector3))
            {
                if (value is JArray arr && arr.Count >= 3)
                    return new Vector3(arr[0].ReadScalar<float>(), arr[1].ReadScalar<float>(), arr[2].ReadScalar<float>());
            }

            // Handle Vector4
            if (targetType == typeof(Vector4))
            {
                if (value is JArray arr && arr.Count >= 4)
                    return new Vector4(arr[0].ReadScalar<float>(), arr[1].ReadScalar<float>(), arr[2].ReadScalar<float>(), arr[3].ReadScalar<float>());
            }

            // Handle enums
            if (targetType.IsEnum)
            {
                string str = value.ToString();
                if (Enum.TryParse(targetType, str, true, out object enumVal))
                    return enumVal;
                // Try as int
                if (int.TryParse(str, out int intVal))
                    return Enum.ToObject(targetType, intVal);
            }

            // Handle bool
            if (targetType == typeof(bool))
                return ParamCoercion.CoerceBool(value, false);

            // Handle float
            if (targetType == typeof(float))
                return ParamCoercion.CoerceFloat(value, 0f);

            // Handle int
            if (targetType == typeof(int))
                return ParamCoercion.CoerceInt(value, 0);

            // Handle Texture2D (by asset path)
            if (targetType == typeof(Texture2D) || targetType == typeof(Texture))
            {
                string path = value.ToString();
                var texture = AssetDatabase.LoadAssetAtPath<Texture>(AssetPathUtility.GetAssetReferencePath(path, allowPackages: true, allowBuiltIn: true));
                if (texture == null || !targetType.IsInstanceOfType(texture))
                    throw new ArgumentException($"Texture at '{path}' was not found or is not a {targetType.Name}.");
                return texture;
            }

            return PropertyConversion.ConvertToType(value, targetType);
        }

        // --- Reflection helpers (with field fallback for Volume.sharedProfile etc.) ---
        private static object GetProperty(object obj, string name)
        {
            if (obj == null)
                return null;
            var prop = obj.GetType().GetProperty(name, BindingFlags.Public | BindingFlags.Instance);
            if (prop != null)
                return prop.GetValue(obj);
            // Fallback: try as a field (e.g., Volume.sharedProfile is a public field, not a property)
            var field = obj.GetType().GetField(name, BindingFlags.Public | BindingFlags.Instance);
            return field?.GetValue(obj);
        }

        private static T GetPropertyValue<T>(object obj, string name, T defaultValue)
        {
            var val = GetProperty(obj, name);
            if (val is T typed)
                return typed;
            return defaultValue;
        }

        private static void SetProperty(object obj, string name, object value)
        {
            if (obj == null)
                return;
            var prop = obj.GetType().GetProperty(name, BindingFlags.Public | BindingFlags.Instance);
            if (prop != null && prop.CanWrite)
            {
                prop.SetValue(obj, value);
                return;
            }
            // Fallback: try as a field (e.g., Volume.sharedProfile is a public field, not a property)
            var field = obj.GetType().GetField(name, BindingFlags.Public | BindingFlags.Instance);
            field?.SetValue(obj, value);
        }
    }
}
