using System;
using System.IO;
using System.Linq;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Runtime.PlayScenarios;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using UnityEngine;

namespace MCPForUnity.Editor.Services.PlayScenarios
{
    /// <summary>Resolve one saved suite once, freeze every concrete child and build their scene union once.</summary>
    public static class PlayScenarioPlayerSuiteBuild
    {
        public static object HandleCommand(JObject request)
        {
            string[] allowed = { "action", "name", "output_directory", "source_revision", "failure_policy", "isolation", "build_target", "scripting_backend" };
            if (request == null || request.Properties().Any(property => !allowed.Contains(property.Name)))
                throw new ArgumentException("Unknown Player suite build field.");
            string name = Text(request, "name", 64);
            PlayScenarioDefinition.ValidateName(name);
            string isolation = Text(request, "isolation", 32, "fresh-process");
            if (isolation != "fresh-process")
                throw new ArgumentException("Player suite builds support fresh-process isolation only.");
            string project = Path.GetDirectoryName(Application.dataPath);
            PlayScenarioSuiteDefinition suite = new PlayScenarioSuiteStore(project).Get(name);
            var children = new JArray();
            foreach (PlayScenarioDefinition definition in suite.Resolve(new PlayScenarioStore(project)))
            {
                PlayScenarioDefinition concrete = PlayScenarioParameters.Materialize(definition);
                PlayScenarioPlayerCapabilities.Validate(concrete, MCPForUnity.Editor.Tools.Input.ManageInput.UguiBackend != null);
                children.Add(new JObject { ["definition"] = JObject.FromObject(concrete) });
            }
            string revision = request.Property("source_revision") == null ? null : Text(request, "source_revision", 128);
            string package = UnityEditor.PackageManager.PackageInfo.FindForAssembly(typeof(PlayScenarioPlayerBuild).Assembly)?.version ?? "unknown";
            JObject manifest = PlayScenarioPlayerSuite.Create(
                new JObject
                {
                    ["name"] = suite.Name,
                    ["children"] = children,
                    ["unity_version"] = Application.unityVersion,
                    ["package_version"] = package,
                    ["build_source_revision"] = revision,
                    ["failure_policy"] = Text(request, "failure_policy", 8, suite.FailurePolicy),
                    ["isolation"] = isolation,
                }
            );
            PlayScenarioPlayerBundle bundle = PlayScenarioPlayerBundle.Parse(manifest.ToString(Formatting.None));
            PlayScenarioPlayerBuildResult result = PlayScenarioPlayerBuild.BuildFrozen(
                manifest,
                bundle.ScenePaths,
                Text(request, "output_directory", 4096),
                Text(request, "build_target", 32, "windows64"),
                Text(request, "scripting_backend", 16, "mono")
            );
            return new SuccessResponse(
                "Built one frozen Player suite payload; each child requires a fresh process.",
                new
                {
                    bundle_path = result.BundlePath,
                    executable_path = result.ExecutablePath,
                    suite_name = bundle.SuiteName,
                    suite_hash = bundle.SuiteHash,
                    child_count = bundle.Children.Count,
                    scene_paths = bundle.ScenePaths,
                    isolation,
                }
            );
        }

        private static string Text(JObject request, string key, int limit, string fallback = null)
        {
            if (request.Property(key) == null && fallback != null)
                return fallback;
            if (
                request[key]?.Type != JTokenType.String
                || string.IsNullOrWhiteSpace((string)request[key])
                || ((string)request[key]).Length > limit
                || ((string)request[key]).Any(char.IsControl)
            )
                throw new ArgumentException(key + " must be a bounded nonempty string.");
            return (string)request[key];
        }
    }
}
