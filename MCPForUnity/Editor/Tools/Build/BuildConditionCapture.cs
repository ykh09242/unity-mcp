using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Text.RegularExpressions;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEditor.Build;
using UnityEditor.Build.Reporting;
using UnityEditor.Compilation;

namespace MCPForUnity.Editor.Tools.Build
{
    // Unity instantiates the callback. Observe only the synchronous MCP invocation that owns the options.
    internal sealed class BuildConditionCapture : BuildPlayerProcessor, IFilterBuildAssemblies
    {
        private static BuildJob owner;
        private static JObject observation;
        private static string[] editorAssemblies;
        private static readonly Regex Identifier = new Regex(@"\A[A-Za-z0-9_][A-Za-z0-9_.-]{0,95}\z", RegexOptions.CultureInvariant);
        private static readonly string[] Lists =
        {
            "editor_active_defines",
            "player_settings_defines",
            "player_extra_defines",
            "player_filter_assemblies",
            "editor_compilation_only_assemblies",
            "player_filter_only_assemblies",
        };

        public override int callbackOrder => int.MaxValue;

        internal static BuildReport Invoke(BuildJob job, Func<BuildReport> build, out JObject result)
        {
            result = null;
            owner = job;
            try
            {
                BuildReport report = build();
                if (report != null && report.summary.platform == job.Target)
                    result = observation;
                return report;
            }
            finally
            {
                owner = null;
                observation = null;
                editorAssemblies = null;
            }
        }

        public override void PrepareForBuild(BuildPlayerContext context)
        {
            if (owner == null)
                return;
            try
            {
                var options = context.BuildPlayerOptions;
                if (
                    options.target != owner.Target
                    || (options.options & BuildOptions.DetailedBuildReport) == 0
                    || Path.GetFullPath(options.locationPathName) != Path.GetFullPath(owner.OutputPath)
                )
                    return;
                var named = BuildTargetMapping.GetNamedBuildTarget(options.target, (StandaloneBuildSubtarget)options.subtarget);
                editorAssemblies = CompilationPipeline.GetAssemblies(AssembliesType.Editor).Select(assembly => assembly.name).ToArray();
                observation = new JObject
                {
                    ["editor_active_target"] = EditorUserBuildSettings.activeBuildTarget.ToString(),
                    ["player_target"] = options.target.ToString(),
                    ["editor_active_defines"] = Names(EditorUserBuildSettings.activeScriptCompilationDefines),
                    ["player_settings_defines"] = Names(
                        PlayerSettings.GetScriptingDefineSymbols(named).Split(new[] { ';' }, StringSplitOptions.RemoveEmptyEntries)
                    ),
                    ["player_extra_defines"] = Names(options.extraScriptingDefines ?? Array.Empty<string>()),
                };
            }
            catch (Exception)
            {
                observation = null;
            }
        }

        public string[] OnFilterAssemblies(BuildOptions buildOptions, string[] assemblies)
        {
            if (observation != null && assemblies != null)
            {
                try
                {
                    string[] player = assemblies
                        .Select(Path.GetFileName)
                        .Select(name => name != null && name.EndsWith(".dll", StringComparison.OrdinalIgnoreCase) ? name.Substring(0, name.Length - 4) : name)
                        .ToArray();
                    observation["player_filter_assemblies"] = Names(player);
                    observation["editor_compilation_only_assemblies"] = Names(editorAssemblies.Except(player, StringComparer.Ordinal));
                    observation["player_filter_only_assemblies"] = Names(player.Except(editorAssemblies, StringComparer.Ordinal));
                }
                catch (Exception)
                {
                    // Preserve the earlier input observations when the assembly filter evidence is unavailable.
                }
            }
            return assemblies;
        }

        private static JObject Names(IEnumerable<string> values)
        {
            string[] unique = values.Distinct(StringComparer.Ordinal).OrderBy(value => value, StringComparer.Ordinal).ToArray();
            var names = new JArray(unique.Where(value => value != null && Identifier.IsMatch(value)).Take(256));
            return new JObject { ["names"] = names, ["complete"] = names.Count == unique.Length };
        }

        internal static bool Valid(JToken token)
        {
            if (
                !(token is JObject data) || data.Properties().Any(p => !Lists.Contains(p.Name) && p.Name != "editor_active_target" && p.Name != "player_target")
            )
                return false;
            foreach (string name in new[] { "editor_active_target", "player_target" })
                if (data[name]?.Type != JTokenType.String || !Identifier.IsMatch((string)data[name]))
                    return false;
            foreach (string name in Lists)
            {
                if (data[name] == null && Array.IndexOf(Lists, name) >= 3)
                    continue;
                if (
                    !(data[name] is JObject list)
                    || list.Count != 2
                    || list["complete"]?.Type != JTokenType.Boolean
                    || !(list["names"] is JArray names)
                    || names.Count > 256
                    || names.Any(value => value.Type != JTokenType.String || !Identifier.IsMatch((string)value))
                )
                    return false;
            }
            return true;
        }
    }
}
