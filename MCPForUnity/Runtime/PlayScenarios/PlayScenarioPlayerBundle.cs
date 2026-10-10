using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Text;
using System.Text.RegularExpressions;
using MCPForUnity.Editor.Services.PlayScenarios;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using UnityEngine;

namespace MCPForUnity.Runtime.PlayScenarios
{
    /// <summary>The versioned, frozen definition shipped only by explicit scenario builds.</summary>
    public sealed class PlayScenarioPlayerBundle
    {
        public const int Limit = 128 * 1024;
        public const string ResourceName = "MCPPlayScenarioBundle";
        public const string ManifestName = "scenario-bundle.json";
        public const string ExecutableName = "MCPScenarioPlayer.exe";
        public PlayScenarioDefinition Definition { get; private set; }
        public string DefinitionHash { get; private set; }
        public string[] ScenePaths { get; private set; }
        public string UnityVersion { get; private set; }
        public string PackageVersion { get; private set; }

        public static JObject Create(PlayScenarioDefinition definition, string unityVersion, string packageVersion)
        {
            definition = PlayScenarioDefinition.Parse(JObject.FromObject(definition));
            return new JObject
            {
                ["schema_version"] = 1,
                ["scenario_name"] = definition.Name,
                ["definition_hash"] = PlayScenarioReproduction.Hash(definition),
                ["definition_json"] = DefinitionJson(definition),
                ["executable"] = ExecutableName,
                ["unity_version"] = unityVersion,
                ["package_version"] = packageVersion,
                ["scene_paths"] = new JArray(Scenes(definition)),
                ["definition"] = JObject.FromObject(definition),
            };
        }

        public static PlayScenarioPlayerBundle Parse(string json)
        {
            JObject value = Read(json, Limit);
            Fields(
                value,
                "schema_version",
                "scenario_name",
                "definition_hash",
                "definition_json",
                "executable",
                "scene_paths",
                "definition",
                "unity_version",
                "package_version"
            );
            if (value["schema_version"]?.Type != JTokenType.Integer || (int)value["schema_version"] != 1 || Text(value, "executable", 64) != ExecutableName)
                throw new ArgumentException("Unsupported Player bundle schema or executable.");
            if (!(value["definition"] is JObject definitionJson))
                throw new ArgumentException("Bundle definition must be an object.");
            PlayScenarioDefinition definition = PlayScenarioDefinition.Parse(definitionJson);
            string hash = Text(value, "definition_hash", 64);
            if (
                Text(value, "scenario_name", 64) != definition.Name
                || hash != PlayScenarioReproduction.Hash(definition)
                || Text(value, "definition_json", 65536) != DefinitionJson(definition)
            )
                throw new ArgumentException("Player bundle frozen definition identity does not match.");
            string[] scenes = Scenes(definition);
            if (
                !(value["scene_paths"] is JArray paths)
                || paths.Count > 64
                || !paths.SequenceEqual(scenes.Select(path => (JToken)new JValue(path)), JToken.EqualityComparer)
            )
                throw new ArgumentException("Player bundle scene inventory does not match its definition.");
            return new PlayScenarioPlayerBundle
            {
                Definition = definition,
                DefinitionHash = hash,
                ScenePaths = scenes,
                UnityVersion = Text(value, "unity_version", 128),
                PackageVersion = Text(value, "package_version", 128),
            };
        }

        public static string DefinitionJson(PlayScenarioDefinition definition) => Sort(JObject.FromObject(definition)).ToString(Formatting.None);

        private static JToken Sort(JToken token)
        {
            if (token is JObject obj)
                return new JObject(obj.Properties().OrderBy(p => p.Name, StringComparer.Ordinal).Select(p => new JProperty(p.Name, Sort(p.Value))));
            if (token is JArray array)
                return new JArray(array.Select(Sort));
            return token.DeepClone();
        }

        public static IEnumerable<PlayScenarioStep> Steps(PlayScenarioDefinition definition) =>
            definition.SetupSteps.Concat(definition.Steps).Concat(definition.CleanupSteps);

        public static string[] Scenes(PlayScenarioDefinition definition) =>
            Steps(definition)
                .Where(step => step.Scene != null)
                .Select(step => step.Scene)
                .Distinct(StringComparer.Ordinal)
                .OrderBy(path => path, StringComparer.Ordinal)
                .ToArray();

        public static JObject Read(string json, int limit)
        {
            if (json == null || Encoding.UTF8.GetByteCount(json) > limit)
                throw new ArgumentException("Player JSON exceeds its bounded size.");
            using (var reader = new JsonTextReader(new StringReader(json)) { DateParseHandling = DateParseHandling.None, MaxDepth = 32 })
            {
                JObject value = JObject.Load(reader, new JsonLoadSettings { DuplicatePropertyNameHandling = DuplicatePropertyNameHandling.Error });
                if (reader.Read())
                    throw new ArgumentException("Trailing Player JSON is unsupported.");
                return value;
            }
        }

        internal static void Fields(JObject value, params string[] allowed)
        {
            foreach (JProperty field in value.Properties())
                if (!allowed.Contains(field.Name, StringComparer.Ordinal))
                    throw new ArgumentException("Unknown Player field: " + field.Name);
        }

        internal static string Text(JObject value, string name, int limit)
        {
            if (value[name]?.Type != JTokenType.String)
                throw new ArgumentException(name + " must be a string.");
            string text = (string)value[name];
            if (string.IsNullOrWhiteSpace(text) || text.Length > limit || text.Any(c => char.IsControl(c) && name != "definition_json"))
                throw new ArgumentException(name + " exceeds its text bounds.");
            return text;
        }
    }

    public sealed class PlayScenarioPlayerRequest
    {
        public string JobId { get; private set; }
        public string ScenarioName { get; private set; }
        public string DefinitionHash { get; private set; }
        public int RepeatCount { get; private set; }
        public int TimeoutSeconds { get; private set; }
        public string SourceRevision { get; private set; }

        public static PlayScenarioPlayerRequest Parse(string json)
        {
            JObject value = PlayScenarioPlayerBundle.Read(json, 4096);
            PlayScenarioPlayerBundle.Fields(
                value,
                "schema_version",
                "job_id",
                "scenario_name",
                "definition_hash",
                "repeat_count",
                "timeout_seconds",
                "source_revision"
            );
            if (value["schema_version"]?.Type != JTokenType.Integer || (int)value["schema_version"] != 1)
                throw new ArgumentException("Unsupported Player request schema.");
            string job = PlayScenarioPlayerBundle.Text(value, "job_id", 32);
            string hash = PlayScenarioPlayerBundle.Text(value, "definition_hash", 64);
            string name = PlayScenarioPlayerBundle.Text(value, "scenario_name", 64);
            if (!Regex.IsMatch(job, @"\A[0-9a-f]{32}\z") || !Regex.IsMatch(hash, @"\A[0-9a-f]{64}\z"))
                throw new ArgumentException("Invalid Player request identity.");
            PlayScenarioDefinition.ValidateName(name);
            string revision = null;
            if (value["source_revision"] != null && value["source_revision"].Type != JTokenType.Null)
            {
                revision = PlayScenarioPlayerBundle.Text(value, "source_revision", 128);
                PlayScenarioReproduction.ValidateSourceRevision(revision);
            }
            return new PlayScenarioPlayerRequest
            {
                JobId = job,
                ScenarioName = name,
                DefinitionHash = hash,
                RepeatCount = Integer(value, "repeat_count", 1, 10),
                TimeoutSeconds = Integer(value, "timeout_seconds", 1, 1800),
                SourceRevision = revision,
            };
        }

        private static int Integer(JObject value, string name, int min, int max)
        {
            if (value[name]?.Type != JTokenType.Integer || !int.TryParse(value[name].ToString(), out int result) || result < min || result > max)
                throw new ArgumentException(name + " is outside Player request bounds.");
            return result;
        }
    }

    /// <summary>Applies sticky unexpected log state before step success and final report publication.</summary>
    public static class PlayScenarioPlayerLogDrain
    {
        public static void Apply(PlayScenarioLogBuffer logs, PlayScenarioEngine engine, ref int processed, long now)
        {
            if (logs == null || engine == null)
                return;
            logs.DrainTo(engine.State);
            if (engine.State.UnexpectedLogCount <= processed)
                return;
            processed = engine.State.UnexpectedLogCount;
            engine.ObserveUnexpectedError(engine.State.LastUnexpectedLogError ?? engine.State.UnexpectedLogError, now);
        }

        public static int ExitCode(PlayScenarioRun run) =>
            run != null && run.Status == "succeeded" && run.ReportError == null && run.RunnerResourcesReleased == true ? 0 : 1;
    }

    public static class PlayScenarioPlayerCapabilities
    {
        public static void Validate(PlayScenarioDefinition definition, bool uguiAvailable)
        {
            if (definition.Resources.Enabled)
                throw Unsupported("resources.enabled requires Editor native resource identity resolution.");
            if (definition.Metrics.Enabled)
                throw Unsupported("metrics.enabled is unsupported by the Player host.");
            if (definition.Diagnostics.ScreenshotOnFailure)
                throw Unsupported("diagnostics.screenshot_on_failure is unsupported by the Player host.");
            foreach (PlayScenarioStep step in PlayScenarioPlayerBundle.Steps(definition))
            {
                if (step.Property != null)
                    throw Unsupported("Serialized property conditions are unsupported by the Player host; no reflected getters are invoked.");
                if (step.Action == "click_ui" && !uguiAvailable)
                    throw Unsupported("click_ui requires the optional runtime uGUI backend.");
                if (step.Action == "wait_object" || step.Action == "click_ui")
                {
                    int count = step.Count ?? 1;
                    if (
                        count < 0
                        || count > 10000
                        || (step.TargetId != null && count > 1)
                        || (count == 0 && (step.Active.HasValue || step.Component != null))
                        || (step.Component != null && count != 1)
                    )
                        throw Unsupported("Invalid Player count, active or component condition.");
                    if ((step.Target == null) == (step.TargetId == null))
                        throw Unsupported("Specify exactly one Player target or target_id.");
                    if (step.TargetId != null && !PlayScenarioTarget.IsValidTargetId(step.TargetId))
                        throw Unsupported("Invalid Player target_id.");
                    if (step.Target != null)
                    {
                        string[] parts = step.Target.Split('/');
                        if (parts.Length > 128 || parts.Any(part => part.Length == 0 || part == "." || part == ".."))
                            throw Unsupported("Invalid exact Player target path.");
                    }
                    if (
                        step.Action == "click_ui"
                        && (count != 1 || step.Active == false || (step.ClickMode != null && step.ClickMode != "direct" && step.ClickMode != "raycast"))
                    )
                        throw Unsupported("Player clicks require one active target and direct or raycast mode.");
                }
                if (step.Component != null)
                    ResolveComponent(step.Component);
            }
        }

        public static Type ResolveComponent(string name)
        {
            Type found = null;
            foreach (var assembly in AppDomain.CurrentDomain.GetAssemblies())
            {
                Type candidate = assembly.GetType(name, false, false);
                if (candidate == null)
                    continue;
                if (found != null && found != candidate)
                    throw Unsupported("Component type is ambiguous: " + name);
                found = candidate;
            }
            if (found == null || !typeof(Component).IsAssignableFrom(found) || found.ContainsGenericParameters || found.FullName != name)
                throw Unsupported("Unavailable exact runtime Component type: " + name);
            return found;
        }

        private static PlayScenarioException Unsupported(string message) =>
            new PlayScenarioException(new PlayScenarioFailure { Code = "unsupported_capability", Message = message });
    }
}
