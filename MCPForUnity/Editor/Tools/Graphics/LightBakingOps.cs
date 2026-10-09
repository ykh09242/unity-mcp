using System;
using System.Collections.Generic;
using System.Linq;
using System.Reflection;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Runtime.Helpers;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEngine;
using UnityEngine.Rendering;

namespace MCPForUnity.Editor.Tools.Graphics
{
    internal static class LightBakingOps
    {
        // === bake_start ===
        // Params: async (bool, default true)
        internal static object StartBake(JObject @params)
        {
            if (Application.isPlaying)
                return new ErrorResponse("Light baking requires Edit mode.");

            var p = new ToolParams(@params);
            bool async_ = p.GetBool("async", true);

            if (async_)
            {
                if (!Lightmapping.BakeAsync())
                    return new ErrorResponse("Light bake could not be started (async). Check the Unity Console for details.");
                return new PendingResponse(
                    "Light bake started (async). Use bake_status to check progress.",
                    pollIntervalSeconds: 2.0,
                    data: new { mode = "async" }
                );
            }

            if (!Lightmapping.Bake())
                return new ErrorResponse("Light bake failed (synchronous). Check the Unity Console for details.");
            return new
            {
                success = true,
                message = "Light bake completed (synchronous).",
                data = new { mode = "sync", lightmapCount = LightmapSettings.lightmaps.Length },
            };
        }

        // === bake_cancel ===
        internal static object CancelBake(JObject @params)
        {
            Lightmapping.Cancel();
            return new { success = true, message = "Light bake cancelled." };
        }

        // === bake_get_status ===
        internal static object GetStatus(JObject @params)
        {
            bool running = Lightmapping.isRunning;
#if UNITY_6000_7_OR_NEWER
            if (!Lightmapping.TryGetLightingSettings(out var settings))
                settings = Lightmapping.lightingSettingsDefaults;
            if (!TryReadRealtimeGI(settings, out bool realtimeGI))
                return new ErrorResponse(RealtimeGIUnavailable);
#endif
            return new
            {
                success = true,
                message = running ? "Light bake in progress." : "No bake running.",
                data = new
                {
                    isRunning = running,
                    bakedGI = Lightmapping.bakedGI,
#if UNITY_6000_7_OR_NEWER
                    realtimeGI,
#else
                    realtimeGI = Lightmapping.realtimeGI,
#endif
                    lightmapCount = LightmapSettings.lightmaps.Length,
                },
            };
        }

        // === bake_clear ===
        internal static object ClearBake(JObject @params)
        {
            Lightmapping.Clear();
            Lightmapping.ClearLightingDataAsset();
            return new { success = true, message = "Cleared all baked lighting data and lighting data asset." };
        }

        // === bake_reflection_probe ===
        // Params: target (name or instanceID of GameObject with ReflectionProbe)
        internal static object BakeReflectionProbe(JObject @params)
        {
            if (Application.isPlaying)
                return new ErrorResponse("Reflection probe baking requires Edit mode.");

            var p = new ToolParams(@params);
            string target = p.Get("target");
            if (string.IsNullOrEmpty(target))
                return new ErrorResponse("'target' parameter is required (name or instanceID of a GameObject with ReflectionProbe).");

            var go = FindGameObject(target);
            if (go == null)
                return new ErrorResponse($"GameObject '{target}' not found.");

            var probe = go.GetComponent<ReflectionProbe>();
            if (probe == null)
                return new ErrorResponse($"GameObject '{go.name}' does not have a ReflectionProbe component.");

            string dir = "Assets/Lightmaps";
            string outputPath = AssetPathUtility.GetContainedAssetPath($"{dir}/{probe.name}_ReflectionProbe.exr");
            using var folders = new AssetFolderScope();
            folders.EnsureParentDirectory(outputPath);

            bool result = Lightmapping.BakeReflectionProbe(probe, outputPath);
            if (!result)
                return new ErrorResponse($"Failed to bake reflection probe '{probe.name}'.");

            folders.Complete();
            return new
            {
                success = true,
                message = $"Baked reflection probe '{probe.name}' to '{outputPath}'.",
                data = new
                {
                    probeName = probe.name,
                    outputPath,
                    instanceID = go.GetInstanceIDCompat(),
                },
            };
        }

        // === bake_get_settings ===
        internal static object GetSettings(JObject @params)
        {
            if (!Lightmapping.TryGetLightingSettings(out var settings))
                settings = Lightmapping.lightingSettingsDefaults;
            if (settings == null)
                return new ErrorResponse("LightingSettings are unavailable. Open Window > Rendering > Lighting manually.");
#if UNITY_6000_7_OR_NEWER
            if (!TryReadRealtimeGI(settings, out bool realtimeGI))
                return new ErrorResponse(RealtimeGIUnavailable);
#endif
#if UNITY_7000_0_OR_NEWER
            if (!TryReadEffectiveLightmapper(out var lightmapper))
                return new ErrorResponse("The project uses an unsupported light baker.");
#else
            var lightmapper = settings.lightmapper;
#endif

            var data = new Dictionary<string, object>
            {
                ["name"] = settings.name,
                ["path"] = AssetDatabase.GetAssetPath(settings),
                ["bakedGI"] = settings.bakedGI,
#if UNITY_6000_7_OR_NEWER
                ["realtimeGI"] = realtimeGI,
#else
                ["realtimeGI"] = settings.realtimeGI,
#endif
                ["lightmapper"] = lightmapper.ToString(),
                ["lightmapResolution"] = settings.lightmapResolution,
                ["lightmapMaxSize"] = settings.lightmapMaxSize,
                ["directSampleCount"] = settings.directSampleCount,
                ["indirectSampleCount"] = settings.indirectSampleCount,
                ["environmentSampleCount"] = settings.environmentSampleCount,
                ["mixedBakeMode"] = settings.mixedBakeMode.ToString(),
                ["lightmapCompression"] = settings.lightmapCompression.ToString(),
                ["ao"] = settings.ao,
                ["aoMaxDistance"] = settings.aoMaxDistance,
            };

            // bounceCount vs maxBounces — name varies by Unity version
            ReadBounceCount(settings, data);

            return new
            {
                success = true,
                message = $"Lighting settings: {lightmapper}, resolution {settings.lightmapResolution}.",
                data,
            };
        }

        // === bake_set_settings ===
        // Params: settings (dict of property name -> value)
        internal static object SetSettings(JObject @params)
        {
            var p = new ToolParams(@params);
            var settingsToken = p.GetRaw("settings") as JObject;
            if (settingsToken == null || !settingsToken.HasValues)
                return new ErrorResponse("'settings' parameter is required (dict of property name to value).");

            var prepared = new List<(string name, Func<LightingSettings, bool> apply)>();
            foreach (var prop in settingsToken.Properties())
            {
                Func<LightingSettings, bool> apply = null;
                try
                {
                    TryPrepareLightingSetting(prop.Name, prop.Value, out apply);
                }
                catch (ArgumentException) { /* Invalid input belongs in the failed list, before settings are created. */ }
                prepared.Add((prop.Name, apply));
            }
            if (prepared.All(entry => entry.apply == null))
                return new ErrorResponse($"Failed to set any settings. Invalid properties: {string.Join(", ", prepared.Select(entry => entry.name))}");

#if UNITY_6000_7_OR_NEWER
            if (
                prepared.Any(entry =>
                    entry.apply != null
                    && (
                        string.Equals(entry.name, "realtimeGI", StringComparison.OrdinalIgnoreCase)
                        || string.Equals(entry.name, "realtime_gi", StringComparison.OrdinalIgnoreCase)
                    )
                )
            )
            {
                if (!Lightmapping.TryGetLightingSettings(out var current))
                    current = Lightmapping.lightingSettingsDefaults;
                if (!TryReadRealtimeGI(current, out _))
                    return new ErrorResponse(RealtimeGIUnavailable);
            }
#endif
#if UNITY_7000_0_OR_NEWER
            if (prepared.Any(entry => entry.apply != null && string.Equals(entry.name, "lightmapper", StringComparison.OrdinalIgnoreCase)))
            {
                if (!Lightmapping.TryGetLightingSettings(out var current))
                    current = Lightmapping.lightingSettingsDefaults;
                if (GraphicsSettings.GetGraphicsSettings() == null || !TryReadSerializedLightmapper(current, out _))
                    return new ErrorResponse(LightmapperUnavailable);
            }
#endif
            var lightingSettings = EnsureLightingSettings();
            if (lightingSettings == null)
                return new ErrorResponse("Failed to create LightingSettings. Open Window > Rendering > Lighting manually.");

            Undo.RecordObject(lightingSettings, "Modify Lighting Settings");

            var changed = new List<string>();
            var failed = new List<string>();

            foreach (var (name, apply) in prepared)
            {
                try
                {
                    if (apply != null && apply(lightingSettings))
                        changed.Add(name);
                    else
                        failed.Add(name);
                }
                catch (Exception ex)
                {
                    McpLog.Warn($"[LightBakingOps] Failed to set '{name}': {ex.Message}");
                    failed.Add(name);
                }
            }

            if (changed.Count == 0 && failed.Count > 0)
                return new ErrorResponse($"Failed to set any settings. Invalid properties: {string.Join(", ", failed)}");

            EditorUtility.SetDirty(lightingSettings);

            var msg = $"Updated {changed.Count} lighting setting(s)";
            if (failed.Count > 0)
                msg += $". Failed: {string.Join(", ", failed)}";

            return new
            {
                success = true,
                message = msg,
                data = new { changed, failed },
            };
        }

        // === bake_create_light_probe_group ===
        // Params: name, position, grid_size, spacing
        internal static object CreateLightProbeGroup(JObject @params)
        {
            var p = new ToolParams(@params);
            string name = p.Get("name") ?? "Light Probes";
            float spacing = p.GetFloat("spacing") ?? 2.0f;

            var posToken = p.GetRaw("position") as JArray;
            Vector3 position =
                posToken != null && posToken.Count >= 3
                    ? new Vector3(posToken[0].ReadScalar<float>(), posToken[1].ReadScalar<float>(), posToken[2].ReadScalar<float>())
                    : Vector3.zero;

            var gridToken = p.GetRaw("grid_size") as JArray;
            int gridX = gridToken != null && gridToken.Count >= 1 ? gridToken[0].ReadScalar<int>() : 3;
            int gridY = gridToken != null && gridToken.Count >= 2 ? gridToken[1].ReadScalar<int>() : 2;
            int gridZ = gridToken != null && gridToken.Count >= 3 ? gridToken[2].ReadScalar<int>() : 3;

            var go = new GameObject(name);
            bool completed = false;
            try
            {
                go.transform.position = position;
                Undo.RegisterCreatedObjectUndo(go, $"Create Light Probe Group '{name}'");
                var probeGroup = go.AddComponent<LightProbeGroup>();
                if (probeGroup == null)
                    return new ErrorResponse("Could not add LightProbeGroup component.");

                var positions = new List<Vector3>();
                float halfX = (gridX - 1) * spacing * 0.5f;
                float halfY = (gridY - 1) * spacing * 0.5f;
                float halfZ = (gridZ - 1) * spacing * 0.5f;

                for (int x = 0; x < gridX; x++)
                {
                    for (int y = 0; y < gridY; y++)
                    {
                        for (int z = 0; z < gridZ; z++)
                        {
                            positions.Add(new Vector3(x * spacing - halfX, y * spacing - halfY, z * spacing - halfZ));
                        }
                    }
                }

                probeGroup.probePositions = positions.ToArray();
                GraphicsHelpers.MarkDirty(probeGroup);
                completed = true;
                return new
                {
                    success = true,
                    message = $"Created Light Probe Group '{name}' with {positions.Count} probes ({gridX}x{gridY}x{gridZ} grid, spacing {spacing}).",
                    data = new
                    {
                        instanceID = go.GetInstanceIDCompat(),
                        probeCount = positions.Count,
                        gridSize = new[] { gridX, gridY, gridZ },
                        spacing,
                        position = new[] { position.x, position.y, position.z },
                    },
                };
            }
            finally
            {
                if (!completed && go != null)
                    UnityEngine.Object.DestroyImmediate(go);
            }
        }

        // === bake_create_reflection_probe ===
        // Params: name, position, size, resolution, mode, hdr, box_projection
        internal static object CreateReflectionProbe(JObject @params)
        {
            var p = new ToolParams(@params);
            string name = p.Get("name") ?? "Reflection Probe";
            int resolution = p.GetInt("resolution") ?? 256;
            bool hdr = p.GetBool("hdr", true);
            bool boxProjection = p.GetBool("box_projection", false);
            string modeStr = p.Get("mode") ?? "Baked";

            var posToken = p.GetRaw("position") as JArray;
            Vector3 position =
                posToken != null && posToken.Count >= 3
                    ? new Vector3(posToken[0].ReadScalar<float>(), posToken[1].ReadScalar<float>(), posToken[2].ReadScalar<float>())
                    : Vector3.zero;

            var sizeToken = p.GetRaw("size") as JArray;
            Vector3 size =
                sizeToken != null && sizeToken.Count >= 3
                    ? new Vector3(sizeToken[0].ReadScalar<float>(), sizeToken[1].ReadScalar<float>(), sizeToken[2].ReadScalar<float>())
                    : new Vector3(10f, 10f, 10f);

            if (!Enum.TryParse<ReflectionProbeMode>(modeStr, true, out var mode) || !Enum.IsDefined(typeof(ReflectionProbeMode), mode))
                return new ErrorResponse($"Invalid mode '{modeStr}'. Valid values: Baked, Realtime, Custom.");

            var go = new GameObject(name);
            bool completed = false;
            try
            {
                go.transform.position = position;
                Undo.RegisterCreatedObjectUndo(go, $"Create Reflection Probe '{name}'");
                var probe = go.AddComponent<ReflectionProbe>();
                if (probe == null)
                    return new ErrorResponse("Could not add ReflectionProbe component.");
                probe.size = size;
                probe.resolution = resolution;
                probe.mode = mode;
                probe.hdr = hdr;
                probe.boxProjection = boxProjection;

                GraphicsHelpers.MarkDirty(probe);
                completed = true;
                return new
                {
                    success = true,
                    message = $"Created Reflection Probe '{name}' (mode: {mode}, resolution: {resolution}, HDR: {hdr}).",
                    data = new
                    {
                        instanceID = go.GetInstanceIDCompat(),
                        mode = mode.ToString(),
                        resolution,
                        hdr,
                        boxProjection,
                        size = new[] { size.x, size.y, size.z },
                        position = new[] { position.x, position.y, position.z },
                    },
                };
            }
            finally
            {
                if (!completed && go != null)
                    UnityEngine.Object.DestroyImmediate(go);
            }
        }

        // === bake_set_probe_positions ===
        // Params: target (name/instanceID), positions (array of [x,y,z])
        internal static object SetProbePositions(JObject @params)
        {
            var p = new ToolParams(@params);
            string target = p.Get("target");
            if (string.IsNullOrEmpty(target))
                return new ErrorResponse("'target' parameter is required (name or instanceID of a GameObject with LightProbeGroup).");

            var go = FindGameObject(target);
            if (go == null)
                return new ErrorResponse($"GameObject '{target}' not found.");

            var probeGroup = go.GetComponent<LightProbeGroup>();
            if (probeGroup == null)
                return new ErrorResponse($"GameObject '{go.name}' does not have a LightProbeGroup component.");

            var positionsToken = p.GetRaw("positions") as JArray;
            if (positionsToken == null || positionsToken.Count == 0)
                return new ErrorResponse("'positions' parameter is required (array of [x,y,z] arrays).");

            var positions = new Vector3[positionsToken.Count];
            for (int i = 0; i < positionsToken.Count; i++)
            {
                var arr = positionsToken[i] as JArray;
                if (arr == null || arr.Count < 3)
                    return new ErrorResponse($"Position at index {i} must be an array of [x, y, z].");
                positions[i] = new Vector3(arr[0].ReadScalar<float>(), arr[1].ReadScalar<float>(), arr[2].ReadScalar<float>());
            }

            Undo.RecordObject(probeGroup, "Set Light Probe Positions");
            probeGroup.probePositions = positions;
            GraphicsHelpers.MarkDirty(probeGroup);

            return new
            {
                success = true,
                message = $"Set {positions.Length} probe positions on '{go.name}'.",
                data = new { instanceID = go.GetInstanceIDCompat(), probeCount = positions.Length },
            };
        }

#if UNITY_6000_7_OR_NEWER
        private const string RealtimeGIUnavailable =
            "realtimeGI is unavailable: LightingSettings does not expose the Boolean m_EnableRealtimeLightmaps property.";

        // Unity's Lighting Inspector edits this serialized field after the 6.7 API deprecation.
        private static bool TryReadRealtimeGI(LightingSettings settings, out bool value)
        {
            value = false;
            if (settings == null)
                return false;
            using var serializedSettings = new SerializedObject(settings);
            using var property = serializedSettings.FindProperty("m_EnableRealtimeLightmaps");
            if (property == null || property.propertyType != SerializedPropertyType.Boolean)
                return false;
            value = property.boolValue;
            return true;
        }

        private static bool TrySetRealtimeGI(LightingSettings settings, JToken value)
        {
            using var serializedSettings = new SerializedObject(settings);
            using var property = serializedSettings.FindProperty("m_EnableRealtimeLightmaps");
            if (property == null || property.propertyType != SerializedPropertyType.Boolean)
                return false;
            property.boolValue = ParamCoercion.CoerceBool(value, property.boolValue);
            serializedSettings.ApplyModifiedPropertiesWithoutUndo();
            return true;
        }
#endif

        private const string LightmapperUnavailable =
            "lightmapper is unavailable: GraphicsSettings or the integer/enum LightingSettings.m_BakeBackend property could not be accessed.";

        // Serialized scene backend access; Unity 7 also requires the project-wide LightBaker to select the effective baker.
        internal static bool TryReadSerializedLightmapper(LightingSettings settings, out LightingSettings.Lightmapper value)
        {
            value = default;
            if (settings == null)
                return false;
            using var serializedSettings = new SerializedObject(settings);
            using var property = serializedSettings.FindProperty("m_BakeBackend");
            if (property == null || (property.propertyType != SerializedPropertyType.Integer && property.propertyType != SerializedPropertyType.Enum))
                return false;
            value = (LightingSettings.Lightmapper)property.intValue;
            return true;
        }

        internal static bool TrySetSerializedLightmapper(LightingSettings settings, LightingSettings.Lightmapper value)
        {
            if (settings == null || !Enum.IsDefined(typeof(LightingSettings.Lightmapper), value))
                return false;
            using var serializedSettings = new SerializedObject(settings);
            using var property = serializedSettings.FindProperty("m_BakeBackend");
            if (property == null || (property.propertyType != SerializedPropertyType.Integer && property.propertyType != SerializedPropertyType.Enum))
                return false;
            property.intValue = (int)value;
            serializedSettings.ApplyModifiedPropertiesWithoutUndo();
            return true;
        }

#if UNITY_7000_0_OR_NEWER
        private static bool TryReadEffectiveLightmapper(out LightingSettings.Lightmapper value)
        {
            switch (UnityEditor.Rendering.EditorGraphicsSettings.defaultLightBaker)
            {
                case UnityEditor.Rendering.LightBaker.ProgressiveLightBaker:
                    value = LightingSettings.Lightmapper.ProgressiveGPU;
                    return true;
                case UnityEditor.Rendering.LightBaker.UnityComputeLightBaker:
                    value = LightingSettings.Lightmapper.UnityComputeGPU;
                    return true;
                default:
                    value = default;
                    return false;
            }
        }

        private static bool TryParseLightBaker(JToken value, out LightingSettings.Lightmapper lightmapper, out UnityEditor.Rendering.LightBaker baker)
        {
            baker = default;
            // Keep numeric inputs in the existing scene enum: 1 must never silently select Compute.
            if (string.Equals(value.ToString(), "ProgressiveLightBaker", StringComparison.OrdinalIgnoreCase))
                lightmapper = LightingSettings.Lightmapper.ProgressiveGPU;
            else if (string.Equals(value.ToString(), "UnityComputeLightBaker", StringComparison.OrdinalIgnoreCase))
                lightmapper = LightingSettings.Lightmapper.UnityComputeGPU;
            else if (!TryParseEnum(value, out lightmapper))
                return false;

            switch (lightmapper)
            {
                case LightingSettings.Lightmapper.ProgressiveGPU:
                    baker = UnityEditor.Rendering.LightBaker.ProgressiveLightBaker;
                    return true;
                case LightingSettings.Lightmapper.UnityComputeGPU:
                    baker = UnityEditor.Rendering.LightBaker.UnityComputeLightBaker;
                    return true;
                default:
                    return false;
            }
        }

        private static bool TrySetLightBaker(LightingSettings settings, LightingSettings.Lightmapper lightmapper, UnityEditor.Rendering.LightBaker baker)
        {
            var graphicsSettings = GraphicsSettings.GetGraphicsSettings();
            if (settings == null || graphicsSettings == null)
                return false;
            using var serializedSettings = new SerializedObject(settings);
            using var property = serializedSettings.FindProperty("m_BakeBackend");
            if (property == null || (property.propertyType != SerializedPropertyType.Integer && property.propertyType != SerializedPropertyType.Enum))
                return false;

            var previousBaker = UnityEditor.Rendering.EditorGraphicsSettings.defaultLightBaker;
            int previousBackend = property.intValue;
            Undo.RecordObject(graphicsSettings, "Modify Light Baker");
            try
            {
                // Unity 7 selects the active baker project-wide and synchronizes this scene field in its Inspector.
                UnityEditor.Rendering.EditorGraphicsSettings.defaultLightBaker = baker;
                property.intValue = (int)lightmapper;
                serializedSettings.ApplyModifiedPropertiesWithoutUndo();
                EditorUtility.SetDirty(graphicsSettings);
                return true;
            }
            catch
            {
                // Roll back only this setting; earlier successful keys keep the best-effort contract.
                if (UnityEditor.Rendering.EditorGraphicsSettings.defaultLightBaker != previousBaker)
                    UnityEditor.Rendering.EditorGraphicsSettings.defaultLightBaker = previousBaker;
                if (property.intValue != previousBackend)
                {
                    property.intValue = previousBackend;
                    serializedSettings.ApplyModifiedPropertiesWithoutUndo();
                }
                throw;
            }
        }
#endif

        // --- Helper: Ensure a LightingSettings asset exists ---
        private static LightingSettings EnsureLightingSettings()
        {
            try
            {
                var settings = Lightmapping.lightingSettings;
                if (settings != null)
                    return settings;
            }
            catch { /* getter throws when no asset exists */ }

            LightingSettings created = null;
            try
            {
                created = new LightingSettings { name = "LightingSettings" };
                Lightmapping.lightingSettings = created;
                return Lightmapping.TryGetLightingSettings(out var assigned) && assigned == created ? created : null;
            }
            catch
            {
                return null;
            }
            finally
            {
                if (created != null && (!Lightmapping.TryGetLightingSettings(out var assigned) || assigned != created))
                    UnityEngine.Object.DestroyImmediate(created);
            }
        }

        // --- Helper: Find a GameObject by name or instanceID ---
        private static GameObject FindGameObject(string target)
        {
            if (string.IsNullOrEmpty(target))
                return null;

            if (int.TryParse(target, out int instanceId))
            {
                var byId = GameObjectLookup.ResolveInstanceID(instanceId) as GameObject;
                if (byId != null)
                    return byId;
            }

            return GameObject.Find(target);
        }

        // --- Helper: Read bounceCount with version fallback ---
        private static void ReadBounceCount(LightingSettings settings, Dictionary<string, object> data)
        {
            var type = typeof(LightingSettings);

            // Try bounceCount first (Unity 2022+)
            var prop = type.GetProperty("bounceCount", BindingFlags.Public | BindingFlags.Instance);
            if (prop != null)
            {
                data["bounceCount"] = prop.GetValue(settings);
                return;
            }

            // Fallback to maxBounces (older Unity versions)
            prop = type.GetProperty("maxBounces", BindingFlags.Public | BindingFlags.Instance);
            if (prop != null)
                data["maxBounces"] = prop.GetValue(settings);
        }

        // Prepare supported settings before creating or modifying LightingSettings.
        // Scalar fallback values are read at application time to retain ordered alias behavior.
        private static bool TryPrepareLightingSetting(string name, JToken value, out Func<LightingSettings, bool> apply)
        {
            apply = null;
            switch (name.ToLowerInvariant())
            {
                case "bakedgi":
                case "baked_gi":
                    ParamCoercion.CoerceBool(value, false);
                    apply = settings =>
                    {
                        settings.bakedGI = ParamCoercion.CoerceBool(value, settings.bakedGI);
                        return true;
                    };
                    return true;

                case "realtimegi":
                case "realtime_gi":
                    ParamCoercion.CoerceBool(value, false);
#if UNITY_6000_7_OR_NEWER
                    apply = settings => TrySetRealtimeGI(settings, value);
#else
                    apply = settings =>
                    {
                        settings.realtimeGI = ParamCoercion.CoerceBool(value, settings.realtimeGI);
                        return true;
                    };
#endif
                    return true;

                case "lightmapper":
                    if (value == null || (value.Type != JTokenType.String && value.Type != JTokenType.Integer))
                        return false;
#if UNITY_7000_0_OR_NEWER
                    if (!TryParseLightBaker(value, out var lm, out var baker))
                        return false;
                    apply = settings => TrySetLightBaker(settings, lm, baker);
                    return true;
#else
                    if (TryParseEnum<LightingSettings.Lightmapper>(value, out var lm) && Enum.IsDefined(typeof(LightingSettings.Lightmapper), lm))
                    {
                        apply = settings =>
                        {
                            settings.lightmapper = lm;
                            return true;
                        };
                        return true;
                    }
                    return false;
#endif

                case "lightmapresolution":
                case "lightmap_resolution":
                    ParamCoercion.CoerceFloat(value, 0);
                    apply = settings =>
                    {
                        settings.lightmapResolution = ParamCoercion.CoerceFloat(value, settings.lightmapResolution);
                        return true;
                    };
                    return true;

                case "lightmapmaxsize":
                case "lightmap_max_size":
                    ParamCoercion.CoerceInt(value, 0);
                    apply = settings =>
                    {
                        settings.lightmapMaxSize = ParamCoercion.CoerceInt(value, settings.lightmapMaxSize);
                        return true;
                    };
                    return true;

                case "directsamplecount":
                case "direct_sample_count":
                    ParamCoercion.CoerceInt(value, 0);
                    apply = settings =>
                    {
                        settings.directSampleCount = ParamCoercion.CoerceInt(value, settings.directSampleCount);
                        return true;
                    };
                    return true;

                case "indirectsamplecount":
                case "indirect_sample_count":
                    ParamCoercion.CoerceInt(value, 0);
                    apply = settings =>
                    {
                        settings.indirectSampleCount = ParamCoercion.CoerceInt(value, settings.indirectSampleCount);
                        return true;
                    };
                    return true;

                case "environmentsamplecount":
                case "environment_sample_count":
                    ParamCoercion.CoerceInt(value, 0);
                    apply = settings =>
                    {
                        settings.environmentSampleCount = ParamCoercion.CoerceInt(value, settings.environmentSampleCount);
                        return true;
                    };
                    return true;

                case "bouncecount":
                case "bounce_count":
                case "maxbounces":
                case "max_bounces":
                    var bounceProperty = typeof(LightingSettings).GetProperty("bounceCount", BindingFlags.Public | BindingFlags.Instance);
                    if (bounceProperty == null || !bounceProperty.CanWrite)
                        bounceProperty = typeof(LightingSettings).GetProperty("maxBounces", BindingFlags.Public | BindingFlags.Instance);
                    if (bounceProperty == null || !bounceProperty.CanWrite)
                        return false;
                    int bounceCount = ParamCoercion.CoerceInt(value, 2);
                    apply = settings => TrySetBounceCount(settings, bounceCount);
                    return true;

                case "mixedbakemode":
                case "mixed_bake_mode":
                    if (TryParseEnum<MixedLightingMode>(value, out var mlm) && Enum.IsDefined(typeof(MixedLightingMode), mlm))
                    {
                        apply = settings =>
                        {
                            settings.mixedBakeMode = mlm;
                            return true;
                        };
                        return true;
                    }
                    return false;

                case "compresslightmaps":
                case "compress_lightmaps":
                case "lightmapcompression":
                case "lightmap_compression":
                    var strVal = value?.ToString() ?? "";
                    if (!System.Enum.TryParse<LightmapCompression>(strVal, true, out var compression))
                    {
                        if (bool.TryParse(strVal, out var boolVal))
                            compression = boolVal ? LightmapCompression.NormalQuality : LightmapCompression.None;
                        else if (int.TryParse(strVal, out var intVal))
                            compression = (LightmapCompression)intVal;
                        else
                            return false;
                    }
                    if (!Enum.IsDefined(typeof(LightmapCompression), compression))
                        return false;
                    apply = settings =>
                    {
                        settings.lightmapCompression = compression;
                        return true;
                    };
                    return true;

                case "ao":
                    ParamCoercion.CoerceBool(value, false);
                    apply = settings =>
                    {
                        settings.ao = ParamCoercion.CoerceBool(value, settings.ao);
                        return true;
                    };
                    return true;

                case "aomaxdistance":
                case "ao_max_distance":
                    ParamCoercion.CoerceFloat(value, 0);
                    apply = settings =>
                    {
                        settings.aoMaxDistance = ParamCoercion.CoerceFloat(value, settings.aoMaxDistance);
                        return true;
                    };
                    return true;

                default:
                    return false;
            }
        }

        // --- Helper: Set bounceCount with version fallback ---
        private static bool TrySetBounceCount(LightingSettings settings, int value)
        {
            var type = typeof(LightingSettings);

            // Try bounceCount first (Unity 2022+)
            var prop = type.GetProperty("bounceCount", BindingFlags.Public | BindingFlags.Instance);
            if (prop != null && prop.CanWrite)
            {
                prop.SetValue(settings, value);
                return true;
            }

            // Fallback to maxBounces (older Unity versions)
            prop = type.GetProperty("maxBounces", BindingFlags.Public | BindingFlags.Instance);
            if (prop != null && prop.CanWrite)
            {
                prop.SetValue(settings, value);
                return true;
            }

            return false;
        }

        // --- Helper: Parse enum from JToken (string name or int value) ---
        private static bool TryParseEnum<T>(JToken value, out T result)
            where T : struct, Enum
        {
            result = default;
            if (value == null || value.Type == JTokenType.Null)
                return false;

            string str = value.ToString();

            // Try parse by name
            if (Enum.TryParse(str, true, out result))
                return true;

            // Try parse by int value
            if (int.TryParse(str, out int intVal))
            {
                result = (T)Enum.ToObject(typeof(T), intVal);
                return true;
            }

            return false;
        }
    }
}
