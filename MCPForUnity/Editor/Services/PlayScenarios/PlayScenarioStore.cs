using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Text;
using System.Text.RegularExpressions;
using MCPForUnity.Runtime.Helpers;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;

namespace MCPForUnity.Editor.Services.PlayScenarios
{
    /// <summary>Bounded project-local JSON storage. Never reads or retains Unity objects.</summary>
    public sealed class PlayScenarioStore
    {
        private const string Definitions = "ProjectSettings/MCPForUnity/PlayScenarios";
        private const string Reports = "Library/MCPForUnity/PlayScenarioRuns";
        private const int DefinitionLimit = 64 * 1024;
        private const int ReportLimit = 2 * 1024 * 1024;
        private readonly string root;
        private readonly object gate = new object();
        private static readonly UTF8Encoding Utf8 = new UTF8Encoding(false, true);
        private static readonly Dictionary<Type, Dictionary<string, Type>> ReportFields = new Dictionary<Type, Dictionary<string, Type>>
        {
            [typeof(PlayScenarioRun)] = Describe(typeof(PlayScenarioRun)),
            [typeof(PlayScenarioStepResult)] = Describe(typeof(PlayScenarioStepResult)),
            [typeof(PlayScenarioLog)] = Describe(typeof(PlayScenarioLog)),
            [typeof(PlayScenarioMetricsSnapshot)] = Describe(typeof(PlayScenarioMetricsSnapshot)),
            [typeof(PlayScenarioFailureDiagnostics)] = Describe(typeof(PlayScenarioFailureDiagnostics)),
            [typeof(PlayScenarioFailure)] = Describe(typeof(PlayScenarioFailure)),
            [typeof(PlayScenarioReproduction)] = Describe(typeof(PlayScenarioReproduction)),
            [typeof(PlayScenarioResourceCheck)] = Describe(typeof(PlayScenarioResourceCheck)),
            [typeof(PlayScenarioQueryCounts)] = Describe(typeof(PlayScenarioQueryCounts)),
            [typeof(PlayScenarioTimelineEvent)] = Describe(typeof(PlayScenarioTimelineEvent)),
            [typeof(PlayScenarioRetainedResource)] = Describe(typeof(PlayScenarioRetainedResource)),
        };

        public PlayScenarioStore(string projectRoot)
        {
            if (string.IsNullOrWhiteSpace(projectRoot))
                throw new ArgumentException("Project root is required.");
            root = Path.GetFullPath(projectRoot);
            SafePathUtility.ResolveWithinRoot(Path.GetPathRoot(root), root);
        }

        public void Save(PlayScenarioDefinition definition)
        {
            if (definition == null)
                throw new ArgumentNullException(nameof(definition));
            PlayScenarioDefinition validated = PlayScenarioDefinition.Parse(ToJson(definition));
            byte[] bytes = Encode(ToJson(validated), DefinitionLimit);
            lock (gate)
            {
                string path = DefinitionPath(validated.Name);
                string[] files = Files(Definitions, 100);
                if (!File.Exists(path) && files.Length >= 100)
                    throw new InvalidOperationException("At most 100 scenarios may be saved.");
                AtomicWrite(path, bytes);
            }
        }

        public PlayScenarioDefinition Get(string name)
        {
            lock (gate)
            {
                PlayScenarioDefinition value = PlayScenarioDefinition.Parse(Read(DefinitionPath(name), DefinitionLimit));
                if (value.Name != name)
                    throw new InvalidDataException("Stored scenario name does not match its filename.");
                return value;
            }
        }

        public IReadOnlyList<string> List()
        {
            lock (gate)
            {
                var names = new List<string>();
                foreach (string file in Files(Definitions, 100))
                {
                    string name = Path.GetFileNameWithoutExtension(file);
                    PlayScenarioDefinition.ValidateName(name);
                    names.Add(name);
                }
                names.Sort(StringComparer.Ordinal);
                return names.AsReadOnly();
            }
        }

        public bool Delete(string name)
        {
            lock (gate)
            {
                string path = DefinitionPath(name);
                if (!File.Exists(path))
                    return false;
                File.Delete(Checked(path));
                return true;
            }
        }

        public string SaveReport(PlayScenarioRun run)
        {
            if (run == null)
                throw new ArgumentNullException(nameof(run));
            ValidateJobId(run.JobId);
            string relative = Reports + "/" + run.JobId + ".json";
            run.ReportPath = relative;
            JObject value = ToJson(run);
            ValidateReport(value);
            byte[] bytes = Encode(value, ReportLimit);
            lock (gate)
            {
                AtomicWrite(Checked(relative), bytes);
                // The current report is always retained, including a replacement with an older timestamp.
                string[] files = Files(Reports, 21);
                foreach (
                    string file in files
                        .Where(file => Path.GetFileNameWithoutExtension(file) != run.JobId)
                        .OrderByDescending(file => File.GetLastWriteTimeUtc(file))
                        .ThenBy(file => file, StringComparer.Ordinal)
                        .Skip(19)
                )
                    DeleteReport(file);
            }
            return relative;
        }

        public PlayScenarioRun GetReport(string jobId)
        {
            ValidateJobId(jobId);
            lock (gate)
            {
                JObject value = Read(Checked(Reports + "/" + jobId + ".json"), ReportLimit);
                var legacyResourceChecks =
                    (value["resource_checks"] as JArray)?.OfType<JObject>().Where(check => check.Property("retained_resources") == null).ToArray()
                    ?? Array.Empty<JObject>();
                ValidateShape(value, typeof(PlayScenarioRun));
                foreach (JObject check in legacyResourceChecks)
                    check["omitted_resource_count"] = new[] { "new_scriptable_objects", "new_subscriptions", "new_handles" }.Sum(key =>
                        check[key].Type == JTokenType.Null ? 0 : (int)check[key]
                    );
                ValidateJobId((string)value["job_id"]);
                if ((string)value["job_id"] != jobId)
                    throw new InvalidDataException("Stored report job_id does not match its filename.");
                ValidateReport(value);
                return value.ToObject<PlayScenarioRun>(new JsonSerializer { TypeNameHandling = TypeNameHandling.None });
            }
        }

        public IReadOnlyList<PlayScenarioRun> ListReports(string name = null, int limit = 20)
        {
            if (name != null)
                PlayScenarioDefinition.ValidateName(name);
            if (limit < 1 || limit > 20)
                throw new ArgumentException("Report limit must be between 1 and 20.");
            lock (gate)
            {
                var reports = new List<PlayScenarioRun>();
                foreach (string file in Files(Reports, 20))
                {
                    PlayScenarioRun run = GetReport(Path.GetFileNameWithoutExtension(file));
                    if (run.Status != "running" && (name == null || run.Scenario.Name == name))
                        reports.Add(run);
                }
                return reports
                    .OrderByDescending(run => run.FinishedUnixMs ?? run.StartedUnixMs)
                    .ThenBy(run => run.JobId, StringComparer.Ordinal)
                    .Take(limit)
                    .ToList()
                    .AsReadOnly();
            }
        }

        public string SaveFailureScreenshot(string jobId, byte[] png)
        {
            ValidateJobId(jobId);
            if (png == null || png.Length == 0 || png.Length > 4 * 1024 * 1024)
                throw new ArgumentException("Failure screenshot must contain 1 byte to 4 MiB.");
            string relative = Reports + "/" + jobId + ".png";
            lock (gate)
                AtomicWrite(Checked(relative), png);
            return relative;
        }

        private void DeleteReport(string file)
        {
            string jobId = Path.GetFileNameWithoutExtension(file);
            ValidateJobId(jobId);
            string screenshot = Checked(Reports + "/" + jobId + ".png");
            File.Delete(Checked(file));
            if (File.Exists(screenshot))
                File.Delete(Checked(screenshot));
        }

        private string DefinitionPath(string name)
        {
            PlayScenarioDefinition.ValidateName(name);
            return Checked(Definitions + "/" + name + ".json");
        }

        private string Checked(string path) => SafePathUtility.ResolveWithinRoot(root, path);

        private string[] Files(string directory, int maximum)
        {
            string path = Checked(directory);
            if (!Directory.Exists(path))
                return Array.Empty<string>();
            var files = new List<string>();
            foreach (string file in Directory.EnumerateFiles(path, "*.json", SearchOption.TopDirectoryOnly))
            {
                files.Add(Checked(file));
                if (files.Count > maximum)
                    throw new InvalidDataException("Stored file count exceeds the limit.");
            }
            return files.ToArray();
        }

        private void AtomicWrite(string path, byte[] bytes)
        {
            string directory = Checked(Path.GetDirectoryName(path));
            Directory.CreateDirectory(directory);
            Checked(directory);
            string temporary = Checked(Path.Combine(directory, "." + Guid.NewGuid().ToString("N") + ".tmp"));
            try
            {
                using (var stream = new FileStream(temporary, FileMode.CreateNew, FileAccess.Write, FileShare.None))
                {
                    stream.Write(bytes, 0, bytes.Length);
                    stream.Flush();
                }
                Checked(path);
                Checked(temporary);
                if (File.Exists(path))
                    File.Replace(temporary, path, null);
                else
                    File.Move(temporary, path);
            }
            finally
            {
                if (File.Exists(temporary))
                    File.Delete(Checked(temporary));
            }
        }

        private JObject Read(string path, int maximum)
        {
            using (var stream = new FileStream(Checked(path), FileMode.Open, FileAccess.Read, FileShare.Read))
            using (var output = new MemoryStream())
            {
                if (stream.Length > maximum)
                    throw new InvalidDataException("Stored JSON exceeds its byte limit.");
                var buffer = new byte[8192];
                int count;
                while ((count = stream.Read(buffer, 0, Math.Min(buffer.Length, maximum - (int)output.Length + 1))) != 0)
                {
                    if (output.Length + count > maximum)
                        throw new InvalidDataException("Stored JSON exceeds its byte limit.");
                    output.Write(buffer, 0, count);
                }
                using (var reader = new JsonTextReader(new StringReader(Utf8.GetString(output.GetBuffer(), 0, (int)output.Length))))
                {
                    reader.DateParseHandling = DateParseHandling.None;
                    reader.MaxDepth = 32;
                    var value = JObject.Load(reader, new JsonLoadSettings { DuplicatePropertyNameHandling = DuplicatePropertyNameHandling.Error });
                    if (reader.Read())
                        throw new InvalidDataException("Stored JSON has trailing content.");
                    return value;
                }
            }
        }

        private static byte[] Encode(JObject value, int maximum)
        {
            string json = value.ToString(Formatting.None);
            if (Utf8.GetByteCount(json) > maximum)
                throw new ArgumentException("JSON exceeds its byte limit.");
            return Utf8.GetBytes(json);
        }

        private static void ValidateJobId(string jobId)
        {
            if (jobId == null || !Regex.IsMatch(jobId, @"\A[a-f0-9]{32}\z"))
                throw new ArgumentException("job_id must be 32 lowercase hexadecimal characters.");
        }

        internal static void ValidateReport(JObject value)
        {
            var legacyResourceChecks =
                (value["resource_checks"] as JArray)?.OfType<JObject>().Where(check => check.Property("retained_resources") == null).ToArray()
                ?? Array.Empty<JObject>();
            ValidateShape(value, typeof(PlayScenarioRun));
            foreach (JObject check in legacyResourceChecks)
                check["omitted_resource_count"] = new[] { "new_scriptable_objects", "new_subscriptions", "new_handles" }.Sum(key =>
                    check[key].Type == JTokenType.Null ? 0 : (int)check[key]
                );
            ValidateJobId((string)value["job_id"]);
            if (!(value["scenario"] is JObject scenario))
                throw new ArgumentException("Report scenario is required.");
            PlayScenarioDefinition definition = PlayScenarioDefinition.Parse(scenario);
            int repeats = PlayScenarioDefinition.Integer(value, "repeat_count", 1, 10, 0);
            if (repeats == 0)
                throw new ArgumentException("Report repeat_count is required.");
            var steps = (JArray)value["steps"];
            var logs = (JArray)value["logs"];
            if (steps.Count > (definition.Steps.Count + definition.SetupSteps.Count + definition.CleanupSteps.Count) * repeats || logs.Count > 50)
                throw new ArgumentException("Report arrays exceed their bounds.");
            State((string)value["execution_environment"], "editor", "player");
            ValidateQueryCounts((JObject)value["query_counts"]);
            ValidateTimeline(value, repeats);
            State((string)value["status"], "running", "succeeded", "failed", "timed_out", "cancelled");
            State((string)value["phase"], "starting", "executing", "settling", "cleaning", "finished");
            if (value["pending_status"].Type != JTokenType.Null)
                State((string)value["pending_status"], "failed", "timed_out", "cancelled");
            Bound(value, "error", 4096);
            Bound(value, "report_error", 4096);
            Bound(value, "pending_error", 4096);
            Bound(value, "cleanup_error", 4096);
            Bound(value, "unexpected_log_error", 4096);
            Bound(value, "last_unexpected_log_error", 4096);
            Bound(value, "metrics_summary", 2048);
            string reportPath = (string)value["report_path"];
            if (reportPath != null && reportPath != Reports + "/" + (string)value["job_id"] + ".json")
                throw new ArgumentException("Report path does not belong to its job.");
            if ((int)value["unexpected_log_count"] < 0)
                throw new ArgumentException("Unexpected log count cannot be negative.");
            foreach (JObject step in steps)
            {
                PlayScenarioDefinition.Integer(step, "iteration", 1, repeats, 0);
                string stage = (string)step["stage"];
                State(stage, "setup", "main", "cleanup");
                int count =
                    stage == "setup" ? definition.SetupSteps.Count
                    : stage == "cleanup" ? definition.CleanupSteps.Count
                    : definition.Steps.Count;
                if (count == 0)
                    throw new ArgumentException("Report step stage has no definition steps.");
                PlayScenarioDefinition.Integer(step, "step_index", 0, count - 1, -1);
                State((string)step["status"], "pending", "running", "passed", "failed", "timed_out", "cancelled", "skipped");
                // Older engines copied the bounded 4096-character primary error into a step detail.
                Bound(step, "detail", 4096);
                Bound(step, "name", 128);
                Bound(step, "action", 32);
                ValidateQueryCounts((JObject)step["query_counts"]);
            }
            foreach (JObject log in logs)
            {
                Bound(log, "message", 1024);
                Bound(log, "stack_trace", 2048);
                Bound(log, "type", 32);
            }
            var failures = (JArray)value["cleanup_failures"];
            var checks = (JArray)value["resource_checks"];
            if (failures.Count > 16 || checks.Count > 10)
                throw new ArgumentException("Operation result arrays exceed their bounds.");
            if (value["failure"] is JObject primary)
                ValidateFailure(primary, repeats);
            foreach (JObject secondary in failures)
                ValidateFailure(secondary, repeats);
            if (value["reproduction"] is JObject reproduction)
            {
                string hash = (string)reproduction["definition_hash"];
                if (
                    hash == null
                    || !Regex.IsMatch(hash, @"\A[a-f0-9]{64}\z")
                    || (hash != PlayScenarioReproduction.Hash(definition) && !LegacyDefinitionHash(scenario, definition, hash))
                )
                    throw new ArgumentException("Invalid definition hash.");
                Bound(reproduction, "unity_version", 128);
                Bound(reproduction, "package_version", 128);
                PlayScenarioReproduction.ValidateSourceRevision((string)reproduction["source_revision"]);
            }
            var checkedIterations = new HashSet<int>();
            foreach (JObject check in checks)
            {
                int iteration = PlayScenarioDefinition.Integer(check, "iteration", 1, repeats, 0);
                if (iteration == 0 || !checkedIterations.Add(iteration))
                    throw new ArgumentException("Resource checks require unique valid iterations.");
                Bound(check, "error", 2048);
                ValidateRetainedResources(check);
                foreach (string key in new[] { "new_scriptable_objects", "new_subscriptions", "new_handles" })
                    if (check[key].Type != JTokenType.Null)
                        PlayScenarioDefinition.Integer(check, key, 0, 4096, 0);
            }
            var metrics = (JArray)value["metrics"];
            var warnings = (JArray)value["metric_warnings"];
            if (metrics.Count > 10 || warnings.Count > 16)
                throw new ArgumentException("Metric arrays exceed their bounds.");
            foreach (JObject snapshot in metrics)
            {
                PlayScenarioDefinition.Integer(snapshot, "iteration", 1, repeats, 0);
                Bound(snapshot, "error", 2048);
                foreach (string key in new[] { "managed_bytes", "allocated_bytes", "object_count", "runner_subscription_count", "runner_handle_count" })
                    if (snapshot[key].Type != JTokenType.Null && (long)snapshot[key] < 0)
                        throw new ArgumentException("Metrics cannot be negative.");
            }
            foreach (JToken warning in warnings)
                if (((string)warning).Length > 2048)
                    throw new ArgumentException("Metric warning exceeds its bound.");
            if (value["failure_diagnostics"] is JObject diagnostics)
            {
                Bound(diagnostics, "active_scene", 4096);
                Bound(diagnostics, "target", 4096);
                Bound(diagnostics, "observation", 2048);
                Bound(diagnostics, "target_detail", 2048);
                Bound(diagnostics, "screenshot_error", 2048);
                string screenshot = (string)diagnostics["screenshot_path"];
                if (screenshot != null && screenshot != Reports + "/" + (string)value["job_id"] + ".png")
                    throw new ArgumentException("Failure screenshot path does not belong to its report.");
            }
        }

        private static void ValidateQueryCounts(JObject counts)
        {
            if ((long)counts["target_searches"] < 0 || (long)counts["hierarchy_visits"] < 0)
                throw new ArgumentException("Query counts cannot be negative.");
        }

        private static void ValidateTimeline(JObject value, int repeats)
        {
            PlayScenarioDefinition.Integer(value, "dropped_timeline_count", 0, int.MaxValue, 0);
            var timeline = (JArray)value["timeline"];
            if (timeline.Count > 128)
                throw new ArgumentException("Timeline exceeds its event bound.");
            long previous = 0;
            foreach (JObject item in timeline)
            {
                long sequence = (long)item["sequence"];
                if (sequence <= previous)
                    throw new ArgumentException("Timeline requires positive increasing event sequences.");
                previous = sequence;
                string stage = (string)item["stage"];
                State(stage, "run", "setup", "main", "cleanup");
                int iteration = PlayScenarioDefinition.Integer(item, "iteration", 0, repeats, -1);
                int index = PlayScenarioDefinition.Integer(item, "step_index", -1, 31, -2);
                if ((stage == "run" && (iteration != 0 || index != -1)) || (stage != "run" && (iteration == 0 || index < 0)))
                    throw new ArgumentException("Timeline attribution is invalid.");
                Bound(item, "event", 64);
                if (string.IsNullOrWhiteSpace((string)item["event"]))
                    throw new ArgumentException("Timeline event is required.");
                Bound(item, "detail", 512);
            }
        }

        private static void ValidateRetainedResources(JObject check)
        {
            var resources = (JArray)check["retained_resources"];
            if (resources.Count > 32)
                throw new ArgumentException("Retained resource descriptions exceed their bound.");
            int omitted = PlayScenarioDefinition.Integer(check, "omitted_resource_count", 0, 4096, -1);
            var unique = new HashSet<long>();
            foreach (JObject resource in resources)
            {
                long id = (long)resource["id"];
                if (id <= 0 || !unique.Add(id))
                    throw new ArgumentException("Retained resources require unique positive identities.");
                State((string)resource["kind"], "scriptable_object", "subscription", "handle");
                Bound(resource, "owner", 128);
                Bound(resource, "type_name", 256);
                Bound(resource, "resource_name", 128);
                Bound(resource, "source_file", 128);
                Bound(resource, "source_member", 128);
                string file = (string)resource["source_file"];
                if (file != null && (file.IndexOf('/') >= 0 || file.IndexOf('\\') >= 0))
                    throw new ArgumentException("Resource call-site file must be a basename.");
                PlayScenarioDefinition.Integer(resource, "source_line", 0, int.MaxValue, -1);
            }
            int total = new[] { "new_scriptable_objects", "new_subscriptions", "new_handles" }.Sum(key =>
                check[key].Type == JTokenType.Null ? 0 : (int)check[key]
            );
            if (resources.Count + omitted > total)
                throw new ArgumentException("Retained resource details do not match the measured identity count.");
        }

        private static bool LegacyDefinitionHash(JObject stored, PlayScenarioDefinition definition, string hash)
        {
            if (stored.Property("query_budget") != null || (stored["diagnostics"] as JObject)?.Property("record_timeline") != null)
                return false;
            JObject legacy = ToJson(definition);
            legacy.Remove("query_budget");
            ((JObject)legacy["diagnostics"]).Remove("record_timeline");
            return PlayScenarioReproduction.HashSerializedDefinition(legacy) == hash;
        }

        private static void ValidateFailure(JObject failure, int repeats)
        {
            State(
                (string)failure["code"],
                "target_missing",
                "target_ambiguous",
                "condition_unmet",
                "property_mismatch",
                "input_blocked",
                "capability_unavailable",
                "action_exception",
                "step_timeout",
                "run_timeout",
                "unexpected_log",
                "cancelled",
                "interrupted",
                "cleanup_timeout",
                "resource_assertion_failed",
                "resource_measurement_failed",
                "query_budget_exceeded",
                "reset_participant_unavailable",
                "state_provider_unavailable",
                "state_provider_error"
            );
            if (failure["stage"].Type != JTokenType.Null)
                State((string)failure["stage"], "setup", "main", "cleanup");
            if (failure["iteration"].Type != JTokenType.Null)
                PlayScenarioDefinition.Integer(failure, "iteration", 1, repeats, 0);
            if (failure["step_index"].Type != JTokenType.Null)
                PlayScenarioDefinition.Integer(failure, "step_index", 0, 31, 0);
            Bound(failure, "target", 4096);
            Bound(failure, "component", 256);
            Bound(failure, "property_path", 256);
            Bound(failure, "expected", 2048);
            Bound(failure, "actual", 2048);
            Bound(failure, "message", 4096);
        }

        private static void Bound(JObject value, string key, int maximum)
        {
            if (((string)value[key])?.Length > maximum)
                throw new ArgumentException(key + " exceeds its report bound.");
        }

        private static void State(string value, params string[] allowed)
        {
            if (!allowed.Contains(value, StringComparer.Ordinal))
                throw new ArgumentException("Invalid report state.");
        }

        private static JObject ToJson(object value) =>
            JObject.FromObject(value, new JsonSerializer { NullValueHandling = NullValueHandling.Include, TypeNameHandling = TypeNameHandling.None });

        private static Dictionary<string, Type> Describe(Type type) =>
            type.GetFields()
                .ToDictionary(
                    field => ((JsonPropertyAttribute)Attribute.GetCustomAttribute(field, typeof(JsonPropertyAttribute))).PropertyName,
                    field => field.FieldType,
                    StringComparer.Ordinal
                );

        private static bool LegacyOptional(Type type, string key)
        {
            if (type == typeof(PlayScenarioStepResult))
                return key == "stage" || key == "stable_since_unix_ms" || key == "query_counts";
            if (type == typeof(PlayScenarioResourceCheck))
                return key == "retained_resources" || key == "omitted_resource_count";
            if (type != typeof(PlayScenarioRun))
                return false;
            return new[]
            {
                "pending_status",
                "pending_error",
                "cleanup_deadline_unix_ms",
                "settle_deadline_unix_ms",
                "cleanup_error",
                "unexpected_log_count",
                "unexpected_log_error",
                "last_unexpected_log_error",
                "metrics",
                "metric_warnings",
                "metrics_summary",
                "failure_diagnostics",
                "runner_resources_released",
                "failure",
                "cleanup_failures",
                "reproduction",
                "resource_checks",
                "execution_environment",
                "query_counts",
                "timeline",
                "dropped_timeline_count",
            }.Contains(key, StringComparer.Ordinal);
        }

        private static void ValidateShape(JObject value, Type type)
        {
            Dictionary<string, Type> fields = ReportFields[type];
            PlayScenarioDefinition.Fields(value, fields.Keys.ToArray());
            JObject defaults = null;
            foreach (var pair in fields)
            {
                JToken token = value[pair.Key];
                if (token == null)
                {
                    if (!LegacyOptional(type, pair.Key))
                        throw new ArgumentException("Missing report field: " + pair.Key);
                    defaults = defaults ?? ToJson(Activator.CreateInstance(type));
                    value[pair.Key] = token = defaults[pair.Key].DeepClone();
                }
                Type fieldType = pair.Value;
                if (fieldType == typeof(PlayScenarioDefinition))
                    continue;
                if (
                    fieldType == typeof(PlayScenarioFailureDiagnostics)
                    || fieldType == typeof(PlayScenarioFailure)
                    || fieldType == typeof(PlayScenarioReproduction)
                    || fieldType == typeof(PlayScenarioQueryCounts)
                )
                {
                    if (token.Type == JTokenType.Null && fieldType != typeof(PlayScenarioQueryCounts))
                        continue;
                    if (!(token is JObject nested))
                        throw new ArgumentException("Invalid failure diagnostics.");
                    ValidateShape(nested, fieldType);
                }
                else if (fieldType.IsGenericType && fieldType.GetGenericTypeDefinition() == typeof(List<>))
                {
                    if (!(token is JArray array))
                        throw new ArgumentException("Invalid report array: " + pair.Key);
                    Type itemType = fieldType.GetGenericArguments()[0];
                    int maximum =
                        itemType == typeof(PlayScenarioTimelineEvent) ? 128
                        : itemType == typeof(PlayScenarioRetainedResource) ? 32
                        : itemType == typeof(PlayScenarioStepResult) ? 640
                        : itemType == typeof(PlayScenarioLog) ? 50
                        : itemType == typeof(PlayScenarioResourceCheck) || itemType == typeof(PlayScenarioMetricsSnapshot) ? 10
                        : 16;
                    if (array.Count > maximum)
                        throw new ArgumentException("Report array exceeds its bound: " + pair.Key);
                    foreach (JToken child in array)
                    {
                        if (itemType == typeof(string))
                        {
                            if (child.Type != JTokenType.String)
                                throw new ArgumentException("Invalid report string array entry.");
                        }
                        else
                        {
                            if (!(child is JObject item))
                                throw new ArgumentException("Invalid report array entry.");
                            ValidateShape(item, itemType);
                        }
                    }
                }
                else if (fieldType == typeof(string))
                {
                    if (token.Type != JTokenType.String && token.Type != JTokenType.Null)
                        throw new ArgumentException("Invalid report string: " + pair.Key);
                }
                else
                {
                    bool nullable = Nullable.GetUnderlyingType(fieldType) != null;
                    Type scalarType = Nullable.GetUnderlyingType(fieldType) ?? fieldType;
                    if (nullable && token.Type == JTokenType.Null)
                        continue;
                    if (scalarType == typeof(bool))
                    {
                        if (token.Type != JTokenType.Boolean)
                            throw new ArgumentException("Invalid report boolean: " + pair.Key);
                    }
                    else if (
                        token.Type != JTokenType.Integer
                        || !long.TryParse(token.ToString(), out long number)
                        || (scalarType == typeof(int) && (number < int.MinValue || number > int.MaxValue))
                    )
                        throw new ArgumentException("Invalid report integer: " + pair.Key);
                }
            }
        }
    }
}
