using System;
using System.Collections.Generic;
using System.Linq;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Editor.Tools.Build;
using MCPForUnity.Editor.Tools.Cameras;
using MCPForUnity.Editor.Tools.Netcode;
using MCPForUnity.Runtime.Helpers;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEditor.PackageManager;
using UnityEngine;
using PackageInfo = UnityEditor.PackageManager.PackageInfo;

namespace MCPForUnity.Editor.Services
{
    // Only explicit diagnostics call Capture. No scene traversal, package requests or updates.
    internal static class FeatureReadiness
    {
        internal sealed class PackageVersions
        {
            private readonly Func<string, string> read;
            private readonly Dictionary<string, string> versions = new();

            internal PackageVersions(Func<string, string> reader) => read = reader;

            internal void Invalidate() => versions.Clear();

            internal bool TryRead(string name, out string version)
            {
                try
                {
                    if (!versions.TryGetValue(name, out version))
                        versions[name] = version = read(name);
                    return true;
                }
                catch (Exception)
                {
                    version = null;
                    return false; // Failed observations are never cached as missing packages.
                }
            }
        }

        private static readonly PackageVersions Packages = new(name => PackageInfo.FindForAssetPath("Packages/" + name)?.version);

        [InitializeOnLoadMethod]
        private static void Subscribe()
        {
            Events.registeringPackages -= Invalidate;
            Events.registeringPackages += Invalidate;
            Events.registeredPackages -= Invalidate;
            Events.registeredPackages += Invalidate;
        }

        private static void Invalidate(PackageRegistrationEventArgs _) => Packages.Invalidate();

        internal static string FeatureForTool(string tool) =>
            tool switch
            {
                "manage_build" => "build",
                "manage_physics"
                or "manage_physics_settings"
                or "manage_physics_materials"
                or "manage_physics_joints"
                or "query_physics"
                or "apply_physics_force"
                or "manage_rigidbody"
                or "diagnose_physics"
                or "simulate_physics" => "physics",
                "manage_render_pipeline" => "render_pipeline",
                "manage_cinemachine" => "cinemachine",
                "manage_probuilder" => "probuilder",
                "manage_netcode" => "netcode",
                _ => null,
            };

        internal static JObject Capture()
        {
            bool busy = EditorApplication.isCompiling || EditorApplication.isUpdating || BuildPipeline.isBuildingPlayer;
            var result = new JObject();
            result["build"] = Build(busy);
            result["physics"] = Describe(
                OptionalUnityModules.PhysicsTools,
                OptionalUnityModules.PhysicsTools ? "preflight_required" : "missing_engine_module",
                true,
                busy,
                OptionalUnityModules.PhysicsTools ? "inspect_physics_settings" : "inspect_module_requirements"
            );
            result["screen_capture"] = Describe(
                OptionalUnityModules.ScreenCapture,
                OptionalUnityModules.ScreenCapture ? "preflight_required" : "missing_engine_module",
                true,
                busy,
                OptionalUnityModules.ScreenCapture ? "inspect_capture_source" : "use_camera_or_scene_view_capture"
            );
            result["physics"]["observed"] = new JObject
            {
                ["physics3d_module"] = OptionalUnityModules.Physics3D,
                ["physics2d_module"] = OptionalUnityModules.Physics2D,
            };
            result["screen_capture"]["observed"] = new JObject { ["screen_capture_module"] = OptionalUnityModules.ScreenCapture };
            result["physics"]["required_version"] = "com.unity.modules.physics and com.unity.modules.physics2d";
            result["screen_capture"]["required_version"] = "com.unity.modules.screencapture";
            // Shared module evidence has no single tool toggle as its admission authority.
            result["physics"]["tool_enabled"] = JValue.CreateNull();
            result["screen_capture"]["tool_enabled"] = JValue.CreateNull();
            var pipeline = RenderPipelineUtility.GetActivePipeline();
            var render = Describe(
                pipeline == RenderPipelineUtility.PipelineKind.Custom ? (bool?)null : true,
                pipeline == RenderPipelineUtility.PipelineKind.Custom ? "pipeline_support_unknown" : "preflight_required",
                Enabled("manage_render_pipeline"),
                busy,
                "inspect_selected_pipeline_settings"
            );
            render["observed"] = new JObject { ["pipeline"] = pipeline.ToString() };
            result["render_pipeline"] = render;
            result["cinemachine"] = Package(
                "com.unity.cinemachine",
                "manage_cinemachine",
                CameraHelpers.HasCinemachine,
                "CinemachineCamera API (3.x or compatible)",
                "inspect_camera_targets",
                busy,
                true
            );
            result["probuilder"] = Package(
                "com.unity.probuilder",
                "manage_probuilder",
                Type.GetType("UnityEngine.ProBuilder.ProBuilderMesh, Unity.ProBuilder") != null,
                "ProBuilderMesh API; operation compatibility checked per action",
                "inspect_mesh_and_requested_operation",
                busy
            );
            result["netcode"] = Package(
                NetcodeAccess.PackageName,
                "manage_netcode",
                NetcodeAccess.Type("NetworkManager") != null,
                "NetworkManager API; preflight verified with NGO 2.7.0",
                "run_explicit_scene_netcode_preflight",
                busy
            );
            return result;
        }

        private static bool Enabled(string tool) => MCPServiceLocator.ToolDiscovery.IsToolEnabled(tool);

        internal static JObject ForTool(JObject feature, bool enabled)
        {
            var result = (JObject)feature.DeepClone();
            bool busy = feature["blocking_reasons"].Values<string>().Contains("editor_busy");
            var readiness = Describe(
                feature["supported"].Value<bool?>(),
                feature["reason"].Value<string>(),
                enabled,
                busy,
                feature["next_action"].Value<string>()
            );
            foreach (JProperty property in readiness.Properties())
                result[property.Name] = property.Value.DeepClone();
            return result;
        }

        private static JObject Package(string name, string tool, bool api, string required, string next, bool busy, bool cm = false)
        {
            bool observed = Packages.TryRead(name, out string version);
            bool incompatible = cm && version != null && int.TryParse(version.Split('.')[0], out int major) && major < 3;
            var result = DescribePackage(name, version, observed, api, incompatible, Enabled(tool), busy, required, next);
            return result;
        }

        internal static JObject DescribePackage(
            string name,
            string version,
            bool observed,
            bool api,
            bool incompatible,
            bool enabled,
            bool busy,
            string required,
            string next
        )
        {
            string reason =
                !observed ? "package_metadata_unknown"
                : version == null ? "missing_package"
                : incompatible ? "unsupported_package_version"
                : !api ? "package_api_unavailable"
                : "preflight_required";
            bool? supported =
                !observed ? (bool?)null
                : version == null || incompatible ? false
                : api ? true
                : (bool?)null;
            var result = Describe(
                supported,
                reason,
                enabled,
                busy,
                reason == "missing_package" ? "inspect_package_requirement"
                    : incompatible ? "inspect_package_compatibility"
                    : next
            );
            result["package_name"] = name;
            result["package_version"] = version;
            result["required_version"] = required;
            return result;
        }

        internal static JObject Describe(bool? supported, string reason, bool enabled, bool busy, string next)
        {
            var blockers = new JArray();
            if (supported == false)
                blockers.Add(reason);
            if (!enabled)
                blockers.Add("tool_disabled");
            if (busy)
                blockers.Add("editor_busy");
            return new JObject
            {
                ["supported"] = supported.HasValue ? new JValue(supported.Value) : JValue.CreateNull(),
                ["ready"] = blockers.Count > 0 ? new JValue(false) : JValue.CreateNull(),
                ["status"] =
                    supported == false ? "unsupported"
                    : blockers.Count > 0 ? "blocked"
                    : "unknown",
                ["reason"] = reason,
                ["tool_enabled"] = enabled,
                ["blocking_reasons"] = blockers,
                ["next_action"] =
                    !enabled ? "review_tool_enablement"
                    : busy ? "wait_for_editor_idle"
                    : next,
            };
        }

        private static JObject Build(bool busy)
        {
            bool? module = null;
            string backend = null;
            BuildTarget target = EditorUserBuildSettings.activeBuildTarget;
            try
            {
                module = BuildPipeline.IsBuildTargetSupported(BuildTargetMapping.GetTargetGroup(target), target);
                backend = PlayerSettings.GetScriptingBackend(BuildTargetMapping.GetNamedBuildTarget(target)).ToString();
            }
            catch (Exception) { /* Module/backend evidence remains unknown independently. */ }
            var result = Describe(
                module,
                module == false ? "missing_platform_module" : "preflight_required",
                Enabled("manage_build"),
                busy,
                module == false ? "inspect_build_modules" : "preview_selected_build_and_verify_toolchain"
            );
            result["observed"] = new JObject
            {
                ["target"] = target.ToString(),
                ["backend"] = backend,
                ["module_supported"] = module.HasValue ? new JValue(module.Value) : JValue.CreateNull(),
                ["backend_toolchain_ready"] = JValue.CreateNull(),
                ["subtarget"] = "Player",
            };
            return result;
        }
    }
}
