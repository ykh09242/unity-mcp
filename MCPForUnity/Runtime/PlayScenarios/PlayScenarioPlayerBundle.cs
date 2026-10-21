using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Text;
using System.Text.RegularExpressions;
using MCPForUnity.Editor.Services.PlayScenarios;
using MCPForUnity.Runtime.PlayScenarios.UIToolkit;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using UnityEngine;

namespace MCPForUnity.Runtime.PlayScenarios
{
    /// <summary>The versioned, frozen definition shipped only by explicit scenario builds.</summary>
    public sealed class PlayScenarioPlayerBundle
    {
        // Includes both the inventory object and its canonical JSON identity, plus definitions.
        public const int Limit = 96 * 1024 * 1024;
        public const string ResourceName = "MCPPlayScenarioBundle";
        public const string ManifestName = "scenario-bundle.json";
        public const string ExecutableName = "MCPScenarioPlayer.exe";
        public int SchemaVersion { get; private set; }
        public string BuildId { get; private set; }
        public string BuildSourceRevision { get; private set; }
        public PlayScenarioDefinition Definition { get; private set; }
        public string DefinitionHash { get; private set; }
        public string[] ScenePaths { get; private set; }
        public string UnityVersion { get; private set; }
        public string PackageVersion { get; private set; }
        public string BuildTarget { get; private set; } = "windows64";
        public string ScriptingBackend { get; private set; } = "mono";
        public string ExecutablePath { get; private set; } = ExecutableName;
        public string SuiteName { get; private set; }
        public string SuiteHash { get; private set; }
        public string FailurePolicy { get; private set; }
        public int SuiteChildIndex { get; private set; }
        public IReadOnlyList<PlayScenarioPlayerBundle> Children { get; private set; }

        public static JObject Create(
            PlayScenarioDefinition definition,
            string unityVersion,
            string packageVersion,
            string buildId = null,
            string buildSourceRevision = null
        )
        {
            definition = PlayScenarioParameters.Materialize(definition);
            buildId = buildId ?? Guid.NewGuid().ToString("N");
            ValidateHex(buildId, 32, "build_id");
            PlayScenarioReproduction.ValidateSourceRevision(buildSourceRevision);
            return new JObject
            {
                ["schema_version"] = 2,
                ["build_id"] = buildId,
                ["build_source_revision"] = buildSourceRevision,
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
            int schema = Schema(value);
            if (schema == 3)
                return PlayScenarioPlayerSuite.Parse(value);
            string[] fields =
            {
                "schema_version",
                "scenario_name",
                "definition_hash",
                "definition_json",
                "executable",
                "scene_paths",
                "definition",
                "unity_version",
                "package_version",
            };
            Fields(
                value,
                schema == 1
                    ? fields
                    : fields
                        .Concat(
                            new[]
                            {
                                "build_id",
                                "build_source_revision",
                                "payload_inventory",
                                "payload_inventory_json",
                                "payload_hash",
                                "build_target",
                                "scripting_backend",
                            }
                        )
                        .ToArray()
            );
            string target = value.Property("build_target") == null ? "windows64" : Text(value, "build_target", 32);
            string backend = value.Property("scripting_backend") == null ? "mono" : Text(value, "scripting_backend", 16);
            PlayScenarioPlayerPlatform.Validate(target, backend);
            string executable = Text(value, "executable", 128);
            if (executable != PlayScenarioPlayerPlatform.Executable(target))
                throw new ArgumentException("Unsupported Player executable.");
            string buildId = null,
                buildRevision = null;
            if (schema == 2)
            {
                buildId = Text(value, "build_id", 32);
                ValidateHex(buildId, 32, "build_id");
                buildRevision = OptionalRevision(value, "build_source_revision", true);
                PlayScenarioPlayerPayload.ValidateManifest(value);
            }
            if (!(value["definition"] is JObject definitionJson))
                throw new ArgumentException("Bundle definition must be an object.");
            PlayScenarioDefinition definition = PlayScenarioDefinition.Parse(definitionJson);
            if (definition.Parameters != null || definition.ParameterBindings != null || definition.Datasets != null)
                throw new ArgumentException("Frozen Player definitions must be materialized before execution.");
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
                SchemaVersion = schema,
                BuildId = buildId,
                BuildSourceRevision = buildRevision,
                Definition = definition,
                DefinitionHash = hash,
                ScenePaths = scenes,
                UnityVersion = Text(value, "unity_version", 128),
                PackageVersion = Text(value, "package_version", 128),
                BuildTarget = target,
                ScriptingBackend = backend,
                ExecutablePath = executable,
            };
        }

        internal static PlayScenarioPlayerBundle FromSuite(JObject value, IReadOnlyList<PlayScenarioPlayerBundle> children, string[] scenes) =>
            new PlayScenarioPlayerBundle
            {
                SchemaVersion = 3,
                BuildId = children[0].BuildId,
                BuildSourceRevision = children[0].BuildSourceRevision,
                Definition = children[0].Definition,
                DefinitionHash = children[0].DefinitionHash,
                ScenePaths = scenes,
                UnityVersion = children[0].UnityVersion,
                PackageVersion = children[0].PackageVersion,
                BuildTarget = children[0].BuildTarget,
                ScriptingBackend = children[0].ScriptingBackend,
                ExecutablePath = children[0].ExecutablePath,
                SuiteName = Text(value, "suite_name", 64),
                SuiteHash = Text(value, "suite_hash", 64),
                FailurePolicy = Text(value, "failure_policy", 8),
                Children = children,
            };

        internal void SelectChild(string name, string hash)
        {
            if (Children == null)
                return;
            int index = -1;
            for (int candidate = 0; candidate < Children.Count; candidate++)
                if (Children[candidate].Definition.Name == name)
                {
                    index = candidate;
                    break;
                }
            if (index < 0 || Children[index].DefinitionHash != hash)
                throw new ArgumentException("Player request does not identify an exact frozen suite child.");
            Definition = Children[index].Definition;
            DefinitionHash = Children[index].DefinitionHash;
            SuiteChildIndex = index;
        }

        public static string DefinitionJson(PlayScenarioDefinition definition) => CanonicalJson(JObject.FromObject(definition));

        public static string CanonicalJson(JToken value) => Sort(value).ToString(Formatting.None);

        internal static int Schema(JObject value)
        {
            if (
                value["schema_version"]?.Type != JTokenType.Integer
                || !int.TryParse(value["schema_version"].ToString(), out int schema)
                || (schema != 1 && schema != 2 && schema != 3)
            )
                throw new ArgumentException("Unsupported Player schema.");
            return schema;
        }

        internal static void ValidateHex(string value, int length, string field)
        {
            if (value == null || value.Length != length || !Regex.IsMatch(value, @"\A[0-9a-f]+\z"))
                throw new ArgumentException("Invalid Player " + field + ".");
        }

        internal static string OptionalRevision(JObject value, string field, bool required = false)
        {
            if (value.Property(field) == null)
            {
                if (required)
                    throw new ArgumentException("Player " + field + " must be present, even when null.");
                return null;
            }
            if (value[field].Type == JTokenType.Null)
                return null;
            string revision = Text(value, field, 128);
            PlayScenarioReproduction.ValidateSourceRevision(revision);
            return revision;
        }

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
        public int SchemaVersion { get; private set; }
        public string BuildId { get; private set; }
        public string BuildSourceRevision { get; private set; }
        public string PayloadHash { get; private set; }
        public string JobId { get; private set; }
        public string ScenarioName { get; private set; }
        public string DefinitionHash { get; private set; }
        public int RepeatCount { get; private set; }
        public int TimeoutSeconds { get; private set; }
        public string SourceRevision { get; private set; }
        public PlayScenarioPlayerSoakOptions Soak { get; private set; }

        public static PlayScenarioPlayerRequest Parse(string json)
        {
            JObject value = PlayScenarioPlayerBundle.Read(json, 4096);
            int schema = PlayScenarioPlayerBundle.Schema(value);
            if (schema == 3)
                throw new ArgumentException("Suite children use the version-2 request protocol in fresh processes.");
            string[] fields = { "schema_version", "job_id", "scenario_name", "definition_hash", "repeat_count", "timeout_seconds", "source_revision" };
            PlayScenarioPlayerBundle.Fields(
                value,
                schema == 1 ? fields : fields.Concat(new[] { "build_id", "build_source_revision", "payload_hash", "soak" }).ToArray()
            );
            string buildId = null,
                buildRevision = null,
                payloadHash = null;
            if (schema == 2)
            {
                buildId = PlayScenarioPlayerBundle.Text(value, "build_id", 32);
                payloadHash = PlayScenarioPlayerBundle.Text(value, "payload_hash", 64);
                PlayScenarioPlayerBundle.ValidateHex(buildId, 32, "build_id");
                PlayScenarioPlayerBundle.ValidateHex(payloadHash, 64, "payload_hash");
                buildRevision = PlayScenarioPlayerBundle.OptionalRevision(value, "build_source_revision", true);
            }
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
            int repeatCount = Integer(value, "repeat_count", 1, 10);
            PlayScenarioPlayerSoakOptions soak = null;
            if (value.Property("soak") != null)
            {
                if (schema != 2 || repeatCount != 1 || !(value["soak"] is JObject options))
                    throw new ArgumentException("Player soak requires schema 2, repeat_count 1 and an options object.");
                soak = PlayScenarioPlayerSoakOptions.Parse(options);
            }
            return new PlayScenarioPlayerRequest
            {
                SchemaVersion = schema,
                BuildId = buildId,
                BuildSourceRevision = buildRevision,
                PayloadHash = payloadHash,
                JobId = job,
                ScenarioName = name,
                DefinitionHash = hash,
                RepeatCount = repeatCount,
                TimeoutSeconds = Integer(value, "timeout_seconds", 1, 1800),
                SourceRevision = revision,
                Soak = soak,
            };
        }

        public void ValidateBundle(PlayScenarioPlayerBundle bundle)
        {
            bundle.SelectChild(ScenarioName, DefinitionHash);
            if (
                (SchemaVersion != bundle.SchemaVersion && !(SchemaVersion == 2 && bundle.SchemaVersion == 3))
                || ScenarioName != bundle.Definition.Name
                || DefinitionHash != bundle.DefinitionHash
                || (SchemaVersion == 2 && (BuildId != bundle.BuildId || BuildSourceRevision != bundle.BuildSourceRevision))
            )
                throw new ArgumentException("Player request does not match the frozen scenario and build identity.");
        }

        public void AddReproduction(JObject report, PlayScenarioPlayerBundle bundle)
        {
            if (!(report["reproduction"] is JObject reproduction))
            {
                reproduction = new JObject();
                report["reproduction"] = reproduction;
            }
            string buildId = SchemaVersion == 2 ? bundle?.BuildId ?? BuildId : null;
            string buildRevision = SchemaVersion == 2 ? (bundle != null ? bundle.BuildSourceRevision : BuildSourceRevision) : null;
            string payloadHash = SchemaVersion == 2 ? PayloadHash : null;
            reproduction["build_id"] = buildId == null ? JValue.CreateNull() : new JValue(buildId);
            reproduction["build_source_revision"] = buildRevision == null ? JValue.CreateNull() : new JValue(buildRevision);
            reproduction["payload_hash"] = payloadHash == null ? JValue.CreateNull() : new JValue(payloadHash);
            reproduction["payload_verification"] = SchemaVersion == 2 ? "launcher_admission" : "unverified_legacy";
            report["player_schema_version"] = SchemaVersion;
            if (bundle?.SuiteName != null)
                report["player_suite"] = new JObject
                {
                    ["name"] = bundle.SuiteName,
                    ["suite_hash"] = bundle.SuiteHash,
                    ["child_index"] = bundle.SuiteChildIndex,
                    ["isolation"] = "fresh-process",
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
            if (definition.Draft == true)
                throw Unsupported("Recorded drafts require review and draft=false before Player execution.");
            if (definition.Diagnostics.ScreenshotOnFailure)
                throw Unsupported("diagnostics.screenshot_on_failure is unsupported by the Player host.");
            foreach (PlayScenarioStep step in PlayScenarioPlayerBundle.Steps(definition))
            {
                if (step.Action == "input" && step.Input?["action"]?.Value<string>() != "release_all")
                {
                    var backend = PlayScenarioPlayerInput.CreateInputBackend?.Invoke();
                    try
                    {
                        if (backend == null || !backend.Available)
                            throw Unsupported(backend?.UnavailableReason ?? "input requires the optional runtime Input System backend.");
                    }
                    finally
                    {
                        backend?.ReleaseAll();
                    }
                }
                if (step.Property != null)
                    throw Unsupported("Serialized property conditions are unsupported by the Player host; no reflected getters are invoked.");
                bool toolkit = false;
                if (step.Action == "click_ui")
                {
                    try
                    {
                        toolkit = UIToolkitScenarioInputAdapter.ValidateRequest(step.UiBackend, step.ElementName, step.ElementPath, step.ClickMode);
                    }
                    catch (Exception error)
                    {
                        throw Unsupported(error.Message);
                    }
                }
                if (step.Action == "click_ui" && !toolkit && !uguiAvailable)
                    throw Unsupported("uGUI click_ui requires the optional runtime uGUI backend.");
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
