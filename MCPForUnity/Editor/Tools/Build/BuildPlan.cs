using System;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Security.Cryptography;
using System.Text;
using MCPForUnity.Editor.Helpers;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEditor.Build;
using UnityEngine;

namespace MCPForUnity.Editor.Tools.Build
{
    // A detached observation, not a reservation or a complete project/settings fingerprint.
    internal sealed class BuildPlan
    {
        internal const string SchemaVersion = "unity-mcp/build-plan@1";
        private const int MaxPlanBytes = 65536;
        private readonly JObject data;
        private readonly BuildPlayerOptions options;

        internal string Sha256 { get; }
        internal ScriptingImplementation? RequestedBackend { get; }
        internal NamedBuildTarget NamedTarget { get; }

        private BuildPlan(JObject snapshot, BuildPlayerOptions resolvedOptions, ScriptingImplementation? backend, NamedBuildTarget namedTarget)
        {
            options = resolvedOptions;
            options.scenes = (string[])resolvedOptions.scenes.Clone();
            RequestedBackend = backend;
            NamedTarget = namedTarget;
            Sha256 = Hash(Canonical(snapshot));
            data = (JObject)snapshot.DeepClone();
            // Hash the exact define value, but expose only its count/digest.
            ((JObject)data["observed"]).Remove("defines_exact");
            data["plan_sha256"] = Sha256;
        }

        internal JObject ToData() => (JObject)data.DeepClone();

        internal BuildPlayerOptions CopyOptions()
        {
            var copy = options;
            copy.scenes = (string[])options.scenes.Clone();
            return copy;
        }

        internal static bool IsExpectedHash(string value) =>
            value?.Length == 64 && value.All(c => (c >= '0' && c <= '9') || (c >= 'a' && c <= 'f') || (c >= 'A' && c <= 'F'));

        internal static void ValidateRequestSize(JObject request)
        {
            if (Encoding.UTF8.GetByteCount(Canonical(request)) > MaxPlanBytes)
                throw new BuildPlanException("build_plan_limit", "The plan request exceeds 64 KiB. Reduce the scene/options/path input.");
        }

        internal static BuildPlan Capture(JObject request)
        {
            ValidateRequestSize(request);
            var p = new ToolParams(request);
            foreach (string key in new[] { "profile", "profiles", "targets", "property", "value", "activate", "output_dir", "job_id", "dry_run" })
            {
                JToken token = p.GetRaw(key);
                if (token == null || token.Type == JTokenType.Null)
                    continue;
                if (
                    (key == "profile" && token.Type == JTokenType.String && token.Value<string>().Length == 0)
                    || ((key == "profiles" || key == "targets") && token is JArray array && array.Count == 0)
                )
                    continue;
                throw new BuildPlanException(
                    "build_plan_scope_unsupported",
                    $"'{key}' is not supported by a single ordinary build plan. Use the existing profile/batch action separately."
                );
            }
            var partial = new JObject { ["schema_version"] = SchemaVersion, ["ready_to_schedule"] = false };
            try
            {
                if (BuildPipeline.isBuildingPlayer)
                    throw new BuildPlanException("build_plan_busy", "A build is already in progress. Observe its status before creating a plan.");
                bool defaultTarget = IsMissing(p.GetRaw("target"));
                string targetName = defaultTarget ? null : p.Get("target");
                if (!BuildTargetMapping.TryResolveBuildTarget(targetName, out BuildTarget target))
                    throw new ArgumentException(BuildTargetMapping.GetUnknownBuildTargetMessage(targetName));
                BuildTargetGroup group = BuildTargetMapping.GetTargetGroup(target);
                if (group == BuildTargetGroup.Unknown)
                    throw new ArgumentException($"Build target group is unavailable for '{target}'.");
                int subtarget = ManageBuild.ReadSubtarget(p);
                NamedBuildTarget namedTarget = BuildTargetMapping.GetNamedBuildTarget(target, (StandaloneBuildSubtarget)subtarget);
                partial["target"] = new JObject
                {
                    ["name"] = target.ToString(),
                    ["raw"] = (int)target,
                    ["group"] = group.ToString(),
                    ["named_target"] = namedTarget.TargetName,
                    ["subtarget"] = group == BuildTargetGroup.Standalone ? subtarget : 0,
                    ["requested_subtarget"] = subtarget,
                    ["subtarget_scope"] = group == BuildTargetGroup.Standalone ? "standalone" : "platform_default",
                    ["source"] = defaultTarget ? "active_target" : "explicit",
                };

                bool development = p.GetBool("development");
                string[] optionNames = ManageBuild.ReadBuildOptionNames(p.GetRaw("options"));
                if (optionNames?.Length > 32)
                    throw new BuildPlanException("build_plan_limit", "A plan supports at most 32 option inputs.", partial);
                BuildOptions flags = BuildRunner.ParseBuildOptions(optionNames, development);
                string requestedBackend = IsMissing(p.GetRaw("scripting_backend")) ? null : p.Get("scripting_backend")?.ToLowerInvariant();
                ScriptingImplementation? desiredBackend = null;
                if (requestedBackend != null)
                {
                    if (requestedBackend != "mono" && requestedBackend != "il2cpp")
                        throw new ArgumentException("'scripting_backend' must be mono or il2cpp.");
                    desiredBackend = requestedBackend == "il2cpp" ? ScriptingImplementation.IL2CPP : ScriptingImplementation.Mono2x;
                }

                JToken rawScenes = p.GetRaw("scenes");
                ManageBuild.ValidateSceneArgument(rawScenes);
                bool defaultScenes = IsMissing(rawScenes);
                string[] scenes = defaultScenes
                    ? EditorBuildSettings.scenes.Where(s => s.enabled).Select(s => s.path).ToArray()
                    : p.GetStringArray("scenes", strict: true) ?? Array.Empty<string>();
                scenes = (string[])scenes.Clone();
                if (scenes.Length > 128)
                    throw new BuildPlanException("build_plan_limit", "A plan supports at most 128 scenes.", partial);
                if (scenes.Length == 0)
                    throw new BuildPlanException(
                        "build_plan_empty_scenes",
                        "Choose at least one existing scene, or enable a build scene and preview again.",
                        partial
                    );
                foreach (string scene in scenes)
                    ValidatePathBound(scene, "scenes");
                BuildRunner.ValidateScenePaths(scenes);
                var sceneRows = new JArray();
                foreach (string scene in scenes)
                {
                    string guid = AssetDatabase.AssetPathToGUID(scene.Replace('\\', '/'));
                    if (string.IsNullOrEmpty(guid))
                        throw new BuildPlanException("build_plan_observation_unavailable", $"Scene GUID is unavailable: {scene}", partial);
                    sceneRows.Add(new JObject { ["path"] = scene, ["guid"] = guid });
                }

                bool defaultOutput = IsMissing(p.GetRaw("output_path"));
                string output = defaultOutput ? BuildTargetMapping.GetDefaultOutputPath(target, PlayerSettings.productName) : p.Get("output_path");
                ValidatePathBound(output, "output_path");
                BuildRunner.ValidateOutputPath(output);
                bool supported;
                try
                {
                    supported = BuildPipeline.IsBuildTargetSupported(group, target);
                }
                catch (Exception)
                {
                    partial["module_check"] = ModuleCheck("unavailable");
                    throw new BuildPlanException(
                        "build_plan_module_unavailable",
                        "The Editor could not observe target support. Check the target module and preview again.",
                        partial
                    );
                }
                partial["module_check"] = ModuleCheck(supported ? "supported" : "unsupported");
                if (!supported)
                    throw new BuildPlanException(
                        "build_plan_module_unsupported",
                        $"Platform '{target}' is not installed. Install the required module via Unity Hub, then preview again.",
                        partial
                    );

                string defines = PlayerSettings.GetScriptingDefineSymbols(namedTarget);
                ScriptingImplementation observedBackend = PlayerSettings.GetScriptingBackend(namedTarget);
                var resolvedOptions = BuildRunner.CreateBuildOptions(target, output, scenes, flags, subtarget);
                var warnings = new JArray(
                    "Module support does not prove SDK, license, backend or subtarget toolchain readiness.",
                    "Output syntax is valid; writability and free disk space are unverified.",
                    "The hash covers declared observations, not all settings, source/dependency contents or dirty scene contents.",
                    "After guarded admission the existing build saves assets and saved dirty scenes; build callbacks may change state."
                );
                if (
                    target != EditorUserBuildSettings.activeBuildTarget
                    || (group == BuildTargetGroup.Standalone && subtarget != (int)EditorUserBuildSettings.standaloneBuildSubtarget)
                )
                    warnings.Add(
                        "The requested target/subtarget differs from the active Editor. Compilation symbols and platform preparation remain normal Unity build limitations; preview does not switch targets."
                    );
                if (group != BuildTargetGroup.Standalone && subtarget != (int)StandaloneBuildSubtarget.Player)
                    warnings.Add(
                        "Player/server subtarget applies only to Standalone. This target retains the existing BuildPlayerOptions platform-default subtarget."
                    );
                partial["project"] = new JObject
                {
                    ["root"] = Path.GetFullPath(Path.Combine(Application.dataPath, "..")),
                    ["unity_version"] = Application.unityVersion,
                };
                partial["scenes_source"] = defaultScenes ? "enabled_build_scenes" : "explicit";
                partial["scenes"] = sceneRows;
                partial["output"] = new JObject
                {
                    ["path"] = output,
                    ["absolute_path"] = Path.GetFullPath(output),
                    ["source"] = defaultOutput ? "default" : "explicit",
                    ["extension"] = Path.GetExtension(output),
                    ["android_app_bundle"] = target == BuildTarget.Android ? (JToken)EditorUserBuildSettings.buildAppBundle : JValue.CreateNull(),
                    ["writability"] = "unknown",
                    ["free_space"] = "unknown",
                };
                partial["options"] = new JObject
                {
                    ["raw"] = (int)flags,
                    ["names"] = new JArray(flags.ToString().Split(',').Select(s => s.Trim())),
                    ["development"] = development,
                };
                partial["requested_backend"] = requestedBackend;
                partial["observed"] = new JObject
                {
                    ["active_target"] = (int)EditorUserBuildSettings.activeBuildTarget,
                    ["active_target_name"] = EditorUserBuildSettings.activeBuildTarget.ToString(),
                    ["active_subtarget"] = (int)EditorUserBuildSettings.standaloneBuildSubtarget,
                    ["active_subtarget_name"] = EditorUserBuildSettings.standaloneBuildSubtarget.ToString(),
                    ["product_name"] = PlayerSettings.productName,
                    ["company_name"] = PlayerSettings.companyName,
                    ["bundle_version"] = PlayerSettings.bundleVersion,
                    ["application_identifier"] = PlayerSettings.GetApplicationIdentifier(namedTarget),
                    ["scripting_backend"] = new JObject { ["name"] = observedBackend.ToString(), ["raw"] = (int)observedBackend },
                    ["architecture"] = PlayerSettings.GetArchitecture(namedTarget),
                    ["defines_exact"] = defines,
                    ["defines_count"] = defines?.Split(';').Count(value => value.Length > 0) ?? 0,
                    ["defines_sha256"] = Hash(defines ?? ""),
                };
                partial["effects"] = new JObject
                {
                    ["scripting_backend_write"] = desiredBackend.HasValue,
                    ["save_before_build"] = true,
                    ["clean_build_cache"] = flags.HasFlag(BuildOptions.CleanBuildCache),
                    ["auto_run"] = flags.HasFlag(BuildOptions.AutoRunPlayer),
                    ["preview_applies_effects"] = false,
                };
                partial["ready_to_schedule"] = true;
                partial["issues"] = new JArray();
                partial["warnings"] = warnings;
                if (Encoding.UTF8.GetByteCount(Canonical(partial)) + 100 > MaxPlanBytes)
                    throw new BuildPlanException("build_plan_limit", "The complete plan exceeds 64 KiB. Reduce the scene/path input.");
                return new BuildPlan(partial, resolvedOptions, desiredBackend, namedTarget);
            }
            catch (BuildPlanException)
            {
                throw;
            }
            catch (ArgumentException error)
            {
                throw new BuildPlanException("build_plan_invalid", error.Message, partial);
            }
            catch (Exception)
            {
                throw new BuildPlanException(
                    "build_plan_observation_unavailable",
                    "Required Editor metadata could not be read. Preview again after checking the selected target and scenes.",
                    partial
                );
            }
        }

        private static bool IsMissing(JToken value) => value == null || value.Type == JTokenType.Null;

        private static JObject ModuleCheck(string status) =>
            new JObject
            {
                ["api"] = "BuildPipeline.IsBuildTargetSupported",
                ["status"] = status,
                ["toolchain_readiness"] = "unknown",
            };

        private static void ValidatePathBound(string value, string parameter)
        {
            if (value?.Length > 4096)
                throw new BuildPlanException("build_plan_limit", $"'{parameter}' paths must not exceed 4096 characters.");
        }

        private static string Canonical(JObject value)
        {
            using (var text = new StringWriter(CultureInfo.InvariantCulture))
            using (var writer = new JsonTextWriter(text) { Formatting = Formatting.None, Culture = CultureInfo.InvariantCulture })
            {
                value.WriteTo(writer);
                writer.Flush();
                return text.ToString();
            }
        }

        private static string Hash(string value)
        {
            using (SHA256 hash = SHA256.Create())
                return BitConverter.ToString(hash.ComputeHash(Encoding.UTF8.GetBytes(value))).Replace("-", "").ToLowerInvariant();
        }
    }

    internal sealed class BuildPlanException : Exception
    {
        internal string Code { get; }
        private readonly JObject partial;

        internal BuildPlanException(string code, string message, JObject observed = null)
            : base(message.Length > 512 ? message.Substring(0, 512) : message)
        {
            Code = code;
            partial = observed == null ? new JObject { ["schema_version"] = BuildPlan.SchemaVersion } : (JObject)observed.DeepClone();
            if (partial["observed"] is JObject settings)
                settings.Remove("defines_exact");
            partial.Remove("plan_sha256");
            partial["ready_to_schedule"] = false;
        }

        internal JObject ToData()
        {
            var result = (JObject)partial.DeepClone();
            result["issues"] = new JArray(new JObject { ["code"] = Code, ["message"] = Message });
            result["next_step"] = "Correct the input or unavailable observation, then request a fresh plan before applying.";
            if (Encoding.UTF8.GetByteCount(result.ToString(Formatting.None)) > 65536)
                return new JObject
                {
                    ["schema_version"] = BuildPlan.SchemaVersion,
                    ["ready_to_schedule"] = false,
                    ["issues"] = result["issues"],
                    ["next_step"] = result["next_step"],
                };
            return result;
        }
    }
}
