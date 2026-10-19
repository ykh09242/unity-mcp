using System;
using System.Collections.Generic;
using System.Globalization;
using System.Linq;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Runtime.Helpers;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;
using UnityEngine.Audio;

namespace MCPForUnity.Editor.Tools
{
    internal static class AudioInspection
    {
        private const int MaxListeners = 128;
        private const int MaxGroups = 256;

        internal static object Handle(JObject request, string action)
        {
            try
            {
                var allowed = new HashSet<string> { "action", "target" };
                if (action == "read_import_settings" || action == "apply_import_settings")
                    allowed.Add("platform");
                else
                {
                    allowed.Add("searchMethod");
                    allowed.Add("search_method");
                }
                if (action == "get_routing" || action == "set_routing")
                    allowed.Add("mixer_path");
                if (action == "set_routing")
                {
                    allowed.Add("group_id");
                    allowed.Add("clear_routing");
                }
                if (action == "apply_import_settings")
                {
                    allowed.Add("settings");
                    allowed.Add("clear_override");
                }
                if (request.Properties().Any(p => !allowed.Contains(p.Name)))
                    throw new ArgumentException("Unsupported parameter for this audio action.");
                if (action == "read_import_settings" || action == "apply_import_settings")
                    return ImportSettings(request, action == "apply_import_settings");
                AudioSource source = ResolveSource(request);
                if (action == "diagnose")
                    return Diagnose(source);
                return Routing(request, source, action == "set_routing");
            }
            catch (Exception error)
            {
                return new ErrorResponse(error.Message);
            }
        }

        private static AudioSource ResolveSource(JObject request)
        {
            JToken target = request["target"];
            if (target == null || (target.Type != JTokenType.String && target.Type != JTokenType.Integer) || string.IsNullOrWhiteSpace(target.ToString()))
                throw new ArgumentException("target must identify one loaded scene GameObject.");
            JToken methodToken = request["searchMethod"] ?? request["search_method"];
            if (methodToken != null && methodToken.Type != JTokenType.Null && methodToken.Type != JTokenType.String)
                throw new ArgumentException("search_method must be a string.");
            string method = methodToken?.Value<string>();
            if (string.IsNullOrEmpty(method) || method == "by_id_or_name_or_path")
                method =
                    int.TryParse(target.ToString(), out _) ? "by_id"
                    : target.ToString().Contains("/") ? "by_path"
                    : "by_name";
            if (method != "by_id" && method != "by_name" && method != "by_path")
                throw new ArgumentException("search_method must be by_id, by_name or by_path.");
            var matches = GameObjectLookup.SearchGameObjects(method, target.ToString(), includeInactive: true, maxResults: 2);
            if (matches.Count != 1)
                throw new ArgumentException("target must resolve to exactly one GameObject.");
            var gameObject = GameObjectLookup.FindById(matches[0]);
            if (gameObject == null || EditorUtility.IsPersistent(gameObject) || !gameObject.scene.IsValid() || !gameObject.scene.isLoaded)
                throw new ArgumentException("target must be in a loaded scene.");
            var sources = gameObject.GetComponents<AudioSource>();
            if (sources.Length != 1)
                throw new ArgumentException("target must have exactly one AudioSource.");
            return sources[0];
        }

        private static string Bound(string value, int limit = 512) => value == null || value.Length <= limit ? value : value.Substring(0, limit);

        private static JObject Identity(UnityEngine.Object asset)
        {
            if (asset == null)
                return null;
            string path = AssetDatabase.GetAssetPath(asset);
            AssetDatabase.TryGetGUIDAndLocalFileIdentifier(asset, out string guid, out long id);
            return new JObject
            {
                ["name"] = Bound(asset.name, 256),
                ["path"] = Bound(path, 1024),
                ["guid"] = guid,
                ["group_id"] = id.ToString(CultureInfo.InvariantCulture),
            };
        }

        private static object Diagnose(AudioSource source)
        {
            // The native enumeration is indivisible; retained output is bounded. No simulation,
            // clip loading, playback request, listener edits, or output-device probing occurs.
            var all = UnityEngine
                .Resources.FindObjectsOfTypeAll<AudioListener>()
                .Where(l => !EditorUtility.IsPersistent(l) && l.gameObject.scene.IsValid() && l.gameObject.scene.isLoaded)
                .ToArray();
            bool truncated = all.Length > MaxListeners;
            var listeners = all.OrderBy(l => l.GetInstanceIDCompat()).Take(MaxListeners).ToArray();
            var active = listeners.Where(l => l.isActiveAndEnabled).ToArray();
            var causes = new List<string>();
            if (!truncated && active.Length == 0)
                causes.Add("missing_listener");
            if (active.Length > 1)
                causes.Add("multiple_active_listeners");
            if (!source.isActiveAndEnabled)
                causes.Add("source_inactive_or_disabled");
            if (source.mute)
                causes.Add("source_muted");
            if (source.volume == 0)
                causes.Add("source_volume_zero");
            if (AudioListener.volume == 0 && !source.ignoreListenerVolume)
                causes.Add("listener_volume_zero");
            if (AudioListener.pause && !source.ignoreListenerPause)
                causes.Add("listener_paused");
            if (!source.isPlaying)
                causes.Add("source_not_playing");
            AudioClip clip = source.clip;
            if (clip == null)
                causes.Add("clip_missing");
            else
            {
                if (clip.loadState == AudioDataLoadState.Failed)
                    causes.Add("clip_load_failed");
                if (clip.loadState == AudioDataLoadState.Unloaded)
                    causes.Add("clip_unloaded");
                if (clip.loadState == AudioDataLoadState.Loading)
                    causes.Add("clip_loading");
            }
            if (
                !truncated
                && active.Length > 0
                && source.spatialBlend > 0
                && active.All(l => Vector3.Distance(source.transform.position, l.transform.position) > source.maxDistance)
            )
                causes.Add("beyond_max_distance");
            return new SuccessResponse(
                "Audio state observed; causes are candidates, not an output-device audibility test.",
                new
                {
                    source = new
                    {
                        instance_id = source.gameObject.GetInstanceIDCompat(),
                        name = Bound(source.gameObject.name, 256),
                        scene = Bound(source.gameObject.scene.path),
                        active = source.gameObject.activeInHierarchy,
                        source.enabled,
                        source.mute,
                        source.volume,
                        is_playing = source.isPlaying,
                        spatial_blend = source.spatialBlend,
                        min_distance = source.minDistance,
                        max_distance = source.maxDistance,
                        rolloff_mode = source.rolloffMode.ToString(),
                        ignore_listener_volume = source.ignoreListenerVolume,
                        ignore_listener_pause = source.ignoreListenerPause,
                    },
                    clip = clip == null
                        ? null
                        : new
                        {
                            path = Bound(AssetDatabase.GetAssetPath(clip), 1024),
                            name = Bound(clip.name, 256),
                            load_state = clip.loadState.ToString(),
                            load_type = clip.loadType.ToString(),
                            clip.channels,
                            clip.frequency,
                            clip.length,
                        },
                    routing = Identity(source.outputAudioMixerGroup),
                    listeners = listeners
                        .Select(l => new
                        {
                            instance_id = l.gameObject.GetInstanceIDCompat(),
                            name = Bound(l.gameObject.name, 256),
                            scene = Bound(l.gameObject.scene.path),
                            active = l.isActiveAndEnabled,
                            distance = Vector3.Distance(source.transform.position, l.transform.position),
                        })
                        .ToArray(),
                    listener_scope = "all_loaded_scenes",
                    listeners_truncated = truncated,
                    listener_count = all.Length,
                    retained_active_listener_count = active.Length,
                    listener_volume = AudioListener.volume,
                    listener_paused = AudioListener.pause,
                    causes,
                    output_device_audibility = "unverified",
                    distance_is_audibility_proof = false,
                    limitations = new[]
                    {
                        "No output-device, mixer gain/effects/snapshot, user script, or acoustic measurement.",
                        "Max distance is a rolloff observation, not proof of silence; custom and logarithmic curves may remain audible.",
                        "Listener enumeration is a native indivisible call; retained listener output is capped at 128.",
                        "Observed at command time; edit-mode isPlaying does not establish runtime output.",
                    },
                }
            );
        }

        private static string AssetPath(JToken token, bool packages)
        {
            if (token?.Type != JTokenType.String)
                throw new ArgumentException("An explicit asset path string is required.");
            string raw = token.Value<string>().Replace('\\', '/');
            if (
                raw.Length > 1024
                || (!raw.StartsWith("Assets/", StringComparison.Ordinal) && !(packages && raw.StartsWith("Packages/", StringComparison.Ordinal)))
            )
                throw new ArgumentException("An explicit Assets/ (or supported Packages/) path up to 1024 characters is required.");
            string path = AssetPathUtility.GetAssetReferencePath(raw, allowPackages: packages);
            if (string.IsNullOrEmpty(path))
                throw new ArgumentException("Invalid or uncontained asset path.");
            return path;
        }

        private static object Routing(JObject request, AudioSource source, bool apply)
        {
            AudioMixerGroup selected = null;
            AudioMixerGroup[] groups = Array.Empty<AudioMixerGroup>();
            bool clear = false;
            if (request["clear_routing"] != null && request["clear_routing"].Type != JTokenType.Boolean)
                throw new ArgumentException("clear_routing must be a Boolean.");
            clear = request.Value<bool?>("clear_routing") ?? false;
            if (clear && (request["mixer_path"] != null || request["group_id"] != null))
                throw new ArgumentException("clear_routing cannot be combined with mixer_path/group_id.");
            if (!apply && clear)
                throw new ArgumentException("clear_routing requires set_routing.");
            if (request["mixer_path"] != null)
            {
                string path = AssetPath(request["mixer_path"], packages: true);
                var mixer = AssetDatabase.LoadAssetAtPath<AudioMixer>(path);
                if (mixer == null)
                    throw new ArgumentException("mixer_path must identify an existing AudioMixer.");
                groups = mixer.FindMatchingGroups("");
                if (groups.Length > MaxGroups)
                    throw new ArgumentException("Mixer exceeds the 256 group inventory limit.");
            }
            if (apply && !clear)
            {
                if (
                    request["group_id"]?.Type != JTokenType.String
                    || !long.TryParse(request.Value<string>("group_id"), NumberStyles.Integer, CultureInfo.InvariantCulture, out long id)
                )
                    throw new ArgumentException("set_routing requires mixer_path and exact group_id from get_routing.");
                var matches = groups.Where(g => AssetDatabase.TryGetGUIDAndLocalFileIdentifier(g, out string _, out long local) && local == id).ToArray();
                if (matches.Length != 1)
                    throw new ArgumentException("group_id must resolve to exactly one group in mixer_path.");
                selected = matches[0];
            }
            if (apply)
            {
                Undo.RecordObject(source, "Set audio mixer routing");
                source.outputAudioMixerGroup = selected;
                EditorUtility.SetDirty(source);
                PrefabUtility.RecordPrefabInstancePropertyModifications(source);
                EditorSceneManager.MarkSceneDirty(source.gameObject.scene);
            }
            return new SuccessResponse(
                apply ? "Audio routing assigned; save the scene explicitly to persist." : "Audio routing observed.",
                new
                {
                    routing = Identity(source.outputAudioMixerGroup),
                    available_groups = groups.Select(Identity).ToArray(),
                    scene_dirty = source.gameObject.scene.isDirty,
                    scene_saved = false,
                    undo_recorded = apply,
                }
            );
        }

        private static T EnumValue<T>(JToken token)
            where T : struct, Enum
        {
            if (
                token.Type != JTokenType.String
                || !Enum.TryParse<T>(token.Value<string>(), false, out var value)
                || !Enum.IsDefined(typeof(T), value)
                || value.ToString() != token.Value<string>()
            )
                throw new ArgumentException("Unknown " + typeof(T).Name + " name.");
            return value;
        }

        private static float Number(JToken token, float min, float max)
        {
            if (token.Type != JTokenType.Float && token.Type != JTokenType.Integer)
                throw new ArgumentException("A numeric setting is required.");
            float value = token.Value<float>();
            if (float.IsNaN(value) || float.IsInfinity(value) || value < min || value > max)
                throw new ArgumentException("Audio setting is outside its supported range.");
            return value;
        }

        private static bool Boolean(JToken token)
        {
            if (token.Type != JTokenType.Boolean)
                throw new ArgumentException("A Boolean setting is required.");
            return token.Value<bool>();
        }

        private static object Snapshot(AudioImporter importer, string platform)
        {
            bool defaults = platform == "Default";
            var sample = defaults ? importer.defaultSampleSettings : importer.GetOverrideSampleSettings(platform);
            return new
            {
                path = importer.assetPath,
                guid = AssetDatabase.AssetPathToGUID(importer.assetPath),
                platform,
                overridden = !defaults && importer.ContainsSampleSettingsOverride(platform),
                force_to_mono = importer.forceToMono,
                load_in_background = importer.loadInBackground,
                sample_settings = new
                {
                    load_type = sample.loadType.ToString(),
                    compression_format = sample.compressionFormat.ToString(),
                    sample.quality,
                    sample_rate_setting = sample.sampleRateSetting.ToString(),
                    sample_rate_override = sample.sampleRateOverride,
                },
            };
        }

        private static object ImportSettings(JObject request, bool apply)
        {
            string path = AssetPath(request["target"], packages: false);
            var importer = AssetImporter.GetAtPath(path) as AudioImporter;
            if (importer == null)
                throw new ArgumentException("target must identify an AudioImporter asset under Assets/.");
            if (request["platform"] != null && request["platform"].Type != JTokenType.String)
                throw new ArgumentException("platform must be Default or a BuildTargetGroup name.");
            string platform = request.Value<string>("platform") ?? "Default";
            if (
                platform != "Default"
                && (
                    !Enum.TryParse<BuildTargetGroup>(platform, false, out var group)
                    || group == BuildTargetGroup.Unknown
                    || !Enum.IsDefined(typeof(BuildTargetGroup), group)
                    || group.ToString() != platform
                )
            )
                throw new ArgumentException("platform must be Default or a supported BuildTargetGroup name.");
            if (!apply)
                return new SuccessResponse(
                    "Audio import settings observed; platform values may be Unity-translated defaults when no override exists.",
                    Snapshot(importer, platform)
                );
            if (EditorApplication.isPlaying)
                throw new ArgumentException("Audio importer edits require Edit mode.");
            if (request["clear_override"] != null && request["clear_override"].Type != JTokenType.Boolean)
                throw new ArgumentException("clear_override must be a Boolean.");
            bool clear = request.Value<bool?>("clear_override") ?? false;
            JToken settingsToken = request["settings"];
            if (settingsToken != null && settingsToken.Type != JTokenType.Object)
                throw new ArgumentException("Supplied settings must be an object; omit settings entirely when clearing an override.");
            var settings = settingsToken as JObject;
            if (clear && (platform == "Default" || settingsToken != null))
                throw new ArgumentException("clear_override requires a platform override and no settings.");
            if (!clear && (settings == null || settings.Count < 1 || settings.Count > 7))
                throw new ArgumentException("settings requires 1..7 explicit audio import properties.");
            var sample = platform == "Default" ? importer.defaultSampleSettings : importer.GetOverrideSampleSettings(platform);
            bool mono = importer.forceToMono;
            bool background = importer.loadInBackground;
            if (settings != null)
                foreach (var property in settings.Properties())
                {
                    switch (property.Name)
                    {
                        case "load_type":
                            sample.loadType = EnumValue<AudioClipLoadType>(property.Value);
                            break;
                        case "compression_format":
                            sample.compressionFormat = EnumValue<AudioCompressionFormat>(property.Value);
                            break;
                        case "quality":
                            sample.quality = Number(property.Value, 0, 1);
                            break;
                        case "sample_rate_setting":
                            sample.sampleRateSetting = EnumValue<AudioSampleRateSetting>(property.Value);
                            break;
                        case "sample_rate_override":
                            float rate = Number(property.Value, 8000, 192000);
                            if (rate != Math.Truncate(rate))
                                throw new ArgumentException("sample_rate_override must be an integer.");
                            sample.sampleRateOverride = (uint)rate;
                            break;
                        case "force_to_mono":
                            if (platform != "Default")
                                throw new ArgumentException("force_to_mono is global; use platform Default explicitly.");
                            mono = Boolean(property.Value);
                            break;
                        case "load_in_background":
                            if (platform != "Default")
                                throw new ArgumentException("load_in_background is global; use platform Default explicitly.");
                            background = Boolean(property.Value);
                            break;
                        default:
                            throw new ArgumentException("Unsupported audio import setting: " + property.Name);
                    }
                }
            object before = Snapshot(importer, platform);
            Undo.RegisterCompleteObjectUndo(importer, "Apply audio import settings");
            if (clear)
            {
                bool removed = importer.ClearSampleSettingOverride(platform);
                if (!removed && importer.ContainsSampleSettingsOverride(platform))
                    return new ErrorResponse("Unity did not clear the requested override.");
            }
            else if (platform == "Default")
                importer.defaultSampleSettings = sample;
            else if (!importer.SetOverrideSampleSettings(platform, sample))
                return new ErrorResponse("Unity rejected the platform sample settings; no global settings were changed.");
            importer.forceToMono = mono;
            importer.loadInBackground = background;
            try
            {
                importer.SaveAndReimport();
            }
            catch (Exception error)
            {
                return new ErrorResponse(
                    "Audio import/reimport failed; inspect current settings before retrying.",
                    new
                    {
                        before,
                        persistence = "unknown",
                        error = error.Message,
                    }
                );
            }
            var readback = AssetImporter.GetAtPath(path) as AudioImporter;
            if (readback == null)
                return new ErrorResponse("Audio importer was unavailable after reimport.", new { before, persistence = "unknown" });
            return new SuccessResponse(
                "Audio import policy saved and reimported; native settings read back.",
                new
                {
                    before,
                    after = Snapshot(readback, platform),
                    reimported = true,
                    undo_recorded = true,
                    undo_limitation = "Importer Undo is recorded; native reimport can limit restoration. No transactional rollback is promised.",
                }
            );
        }
    }
}
