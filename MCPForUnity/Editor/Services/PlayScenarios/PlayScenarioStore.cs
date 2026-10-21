using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Text;
using System.Text.RegularExpressions;
using MCPForUnity.Runtime.Helpers;
using MCPForUnity.Runtime.PlayScenarios;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;

namespace MCPForUnity.Editor.Services.PlayScenarios
{
    /// <summary>Bounded project-local JSON storage. Never reads or retains Unity objects.</summary>
    public sealed class PlayScenarioStore
    {
        private const string Definitions = "ProjectSettings/MCPForUnity/PlayScenarios";
        private const string Reports = "Library/MCPForUnity/PlayScenarioRuns";
        private const int DefinitionLimit = PlayScenarioDefinition.MaximumDefinitionBytes;
        private const int ReportLimit = PlayScenarioDefinition.MaximumReportBytes;
        private readonly string root;
        private readonly object gate = new object();
        private static readonly UTF8Encoding Utf8 = new UTF8Encoding(false, true);
        private static readonly Dictionary<Type, Dictionary<string, Type>> ReportFields = new Dictionary<Type, Dictionary<string, Type>>
        {
            [typeof(PlayScenarioRun)] = Describe(typeof(PlayScenarioRun)),
            [typeof(PlayScenarioStepResult)] = Describe(typeof(PlayScenarioStepResult)),
            [typeof(PlayScenarioEventEvidence)] = Describe(typeof(PlayScenarioEventEvidence)),
            [typeof(PlayScenarioPerformanceResult)] = Describe(typeof(PlayScenarioPerformanceResult)),
            [typeof(PlayScenarioScreenshotResult)] = Describe(typeof(PlayScenarioScreenshotResult)),
            [typeof(PlayScenarioIterationResult)] = Describe(typeof(PlayScenarioIterationResult)),
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

        public string SaveFailureScreenshot(string jobId, byte[] png) => SaveFailureScreenshot(jobId, png, PlayScenarioScreenshotOptions.MaximumPngBytes);

        internal string SaveFailureScreenshot(string jobId, byte[] png, int maximumBytes)
        {
            if (maximumBytes < 1 || maximumBytes > PlayScenarioScreenshotOptions.MaximumPngBytes)
                throw new ArgumentOutOfRangeException(nameof(maximumBytes));
            ValidateJobId(jobId);
            if (png == null || png.Length == 0 || png.Length > maximumBytes)
                throw new ArgumentException("Failure screenshot must contain 1 byte to 320 MiB.");
            string relative = Reports + "/" + jobId + ".png";
            lock (gate)
                AtomicWrite(Checked(relative), png);
            return relative;
        }

        public string SaveCheckpointScreenshot(string jobId, PlayScenarioStepResult step, byte[] png)
        {
            ValidateJobId(jobId);
            if (step?.Screenshot == null || png == null || png.Length == 0 || png.Length > PlayScenarioScreenshotOptions.MaximumPngBytes)
                throw new ArgumentException("Checkpoint screenshot must contain 1 byte to 320 MiB and an owned step identity.");
            PlayScenarioDefinition.ValidateName(step.Screenshot.Name);
            string filename = jobId + "-" + step.Iteration + "-" + step.Stage + "-" + step.StepIndex + "-" + step.Screenshot.Name + ".png";
            if (step.Stage != "setup" && step.Stage != "main" && step.Stage != "cleanup")
                throw new ArgumentException("Unknown checkpoint stage.");
            lock (gate)
            {
                Directory.CreateDirectory(Checked(Reports));
                long bytes = 0;
                int count = 0;
                foreach (string existing in Directory.EnumerateFiles(Checked(Reports), jobId + "-*.png", SearchOption.TopDirectoryOnly))
                {
                    if (++count > PlayScenarioDefinition.MaximumStepResults)
                        throw new IOException("Checkpoint file count exceeds the bounded run budget.");
                    bytes += new FileInfo(Checked(existing)).Length;
                }
                if (bytes + png.Length > PlayScenarioScreenshotOptions.MaximumRunPngBytes)
                    throw new IOException("Checkpoint screenshots exceed the 8-GiB run artifact budget.");
                AtomicWrite(Checked(Reports + "/" + filename), png);
            }
            return filename;
        }

        private void DeleteReport(string file)
        {
            string jobId = Path.GetFileNameWithoutExtension(file);
            ValidateJobId(jobId);
            string screenshot = Checked(Reports + "/" + jobId + ".png");
            File.Delete(Checked(file));
            if (File.Exists(screenshot))
                File.Delete(Checked(screenshot));
            foreach (string checkpoint in Directory.GetFiles(Checked(Reports), jobId + "-*.png", SearchOption.TopDirectoryOnly))
                File.Delete(Checked(checkpoint));
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
            int repeats = PlayScenarioDefinition.Integer(value, "repeat_count", 1, PlayScenarioDefinition.MaximumRepeats, 0);
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
            ValidateIterationResults(value, repeats);
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
                List<PlayScenarioStep> stageSteps =
                    stage == "setup" ? definition.SetupSteps
                    : stage == "cleanup" ? definition.CleanupSteps
                    : definition.Steps;
                ValidateStepObservations(step, stageSteps[(int)step["step_index"]], (string)value["job_id"], (string)value["execution_environment"]);
            }
            foreach (JObject log in logs)
            {
                Bound(log, "message", 1024);
                Bound(log, "stack_trace", 2048);
                Bound(log, "type", 32);
            }
            var failures = (JArray)value["cleanup_failures"];
            var checks = (JArray)value["resource_checks"];
            if (failures.Count > 16 || checks.Count > PlayScenarioDefinition.MaximumRepeats)
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
            if (metrics.Count > PlayScenarioDefinition.MaximumRepeats || warnings.Count > 16)
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

        private static void ValidateStepObservations(JObject value, PlayScenarioStep definition, string jobId, string environment)
        {
            string status = (string)value["status"];
            if (value["performance"] is JObject performance)
            {
                if (definition.Performance == null)
                    throw new ArgumentException("Performance evidence requires an opted-in definition step.");
                int frames = PlayScenarioDefinition.Integer(performance, "frame_sample_count", 0, definition.Performance.SampleLimit, -1);
                int gc = PlayScenarioDefinition.Integer(performance, "gc_sample_count", 0, frames, -1);
                long observed = (long)performance["observed_frame_count"];
                if (frames < 0 || gc < 0 || observed < frames || (bool)performance["sample_limit_exceeded"] != (observed > definition.Performance.SampleLimit))
                    throw new ArgumentException("Performance sample counts are inconsistent.");
                foreach (string key in new[] { "duration_ms", "frame_time_p95_ms", "gc_allocated_bytes_per_frame_max" })
                    if (performance[key] != null && performance[key].Type != JTokenType.Null && (double)performance[key] < 0)
                        throw new ArgumentException("Performance measurements cannot be negative.");
                if (
                    (gc == 0)
                    != (performance["gc_allocated_bytes_per_frame_max"] == null || performance["gc_allocated_bytes_per_frame_max"].Type == JTokenType.Null)
                )
                    throw new ArgumentException("GC measurement availability does not match sample counts.");
                if ((frames == 0) != (performance["frame_time_p95_ms"] == null || performance["frame_time_p95_ms"].Type == JTokenType.Null))
                    throw new ArgumentException("Frame measurement availability does not match sample counts.");
                Bound(performance, "error", 1024);
                if ((bool)performance["passed"])
                {
                    PlayScenarioPerformanceOptions options = definition.Performance;
                    double? frameTime = performance["frame_time_p95_ms"]?.Type == JTokenType.Null ? null : (double?)performance["frame_time_p95_ms"];
                    long? gcMaximum =
                        performance["gc_allocated_bytes_per_frame_max"]?.Type == JTokenType.Null
                            ? null
                            : (long?)performance["gc_allocated_bytes_per_frame_max"];
                    if (
                        (string)performance["error"] != null
                        || (options.MaxDurationMs.HasValue && (double)performance["duration_ms"] > options.MaxDurationMs)
                        || (options.MaxFrameTimeP95Ms.HasValue && (!frameTime.HasValue || frameTime > options.MaxFrameTimeP95Ms))
                        || (
                            options.MaxGcAllocatedBytesPerFrame.HasValue
                            && (!gcMaximum.HasValue || gc != frames || gcMaximum > options.MaxGcAllocatedBytesPerFrame)
                        )
                        || ((options.MaxFrameTimeP95Ms.HasValue || options.MaxGcAllocatedBytesPerFrame.HasValue) && (bool)performance["sample_limit_exceeded"])
                    )
                        throw new ArgumentException("Passing performance evidence is incomplete or exceeds the selected budget.");
                }
                if (status == "passed" && !(bool)performance["passed"])
                    throw new ArgumentException("A passed step cannot contain a failed performance budget.");
            }
            else if (definition.Performance != null && status == "passed")
                throw new ArgumentException("A passed performance step requires measurement evidence.");
            if (value["screenshot"] is JObject screenshot)
            {
                PlayScenarioScreenshotOptions options = definition.Screenshot;
                if (
                    options == null
                    || (string)screenshot["name"] != options.Name
                    || (int)screenshot["requested_width"] != options.Width
                    || (int)screenshot["requested_height"] != options.Height
                )
                    throw new ArgumentException("Screenshot evidence does not match the opted-in checkpoint.");
                State((string)screenshot["status"], "pending", "captured", "failed", "unavailable");
                Bound(screenshot, "error", 1024);
                string path = (string)screenshot["path"];
                string hash = (string)screenshot["sha256"];
                if ((string)screenshot["status"] == "captured")
                {
                    string suffix = "-" + (int)value["iteration"] + "-" + (string)value["stage"] + "-" + (int)value["step_index"] + "-" + options.Name + ".png";
                    bool owned =
                        environment == "editor"
                            ? path == jobId + suffix
                            : path != null && Regex.IsMatch(path, @"\Acheckpoint-[1-9][0-9]{0,3}" + Regex.Escape(suffix) + @"\z");
                    if (
                        !owned
                        || hash == null
                        || !Regex.IsMatch(hash, @"\A[a-f0-9]{64}\z")
                        || (int?)screenshot["width"] != options.Width
                        || (int?)screenshot["height"] != options.Height
                        || (string)screenshot["error"] != null
                    )
                        throw new ArgumentException("Captured screenshot identity, dimensions or hash are invalid.");
                }
                else if (path != null || hash != null || status == "passed")
                    throw new ArgumentException("An unavailable checkpoint cannot claim captured artifact evidence or a passed step.");
            }
            else if (definition.Screenshot != null && status == "passed")
                throw new ArgumentException("A passed screenshot checkpoint requires captured evidence.");
        }

        internal static void ValidateIterationResults(JObject value, int repeats)
        {
            // Missing both fields identifies a legacy report, whose iteration history
            // cannot be reconstructed safely from step rows.
            if (value.Property("iteration_results_version") == null && value.Property("iteration_results") == null)
                return;
            if (repeats < 1 || repeats > PlayScenarioDefinition.MaximumRepeats)
                throw new ArgumentException("Iteration repeat_count must be 1-100.");
            if (value["iteration_results_version"]?.Type != JTokenType.Integer || (long)value["iteration_results_version"] != 1)
                throw new ArgumentException("Unsupported iteration_results_version.");
            if (!(value["iteration_results"] is JArray results) || results.Count != repeats)
                throw new ArgumentException("Iteration results must contain one ordered row per repetition.");
            State((string)value["status"], "running", "succeeded", "failed", "timed_out", "cancelled");
            long reportStarted = IterationTimestamp(value["started_unix_ms"], false).Value;
            long? reportFinished = IterationTimestamp(value["finished_unix_ms"], true);
            if (reportFinished.HasValue && reportFinished.Value < reportStarted)
                throw new ArgumentException("Iteration report timestamps run backwards.");
            bool terminal = (string)value["status"] != "running";
            long? previousFinished = null;
            for (int index = 0; index < results.Count; index++)
            {
                if (!(results[index] is JObject result))
                    throw new ArgumentException("Iteration results must contain objects.");
                ValidateShape(result, typeof(PlayScenarioIterationResult));
                if ((int)result["iteration"] != index + 1)
                    throw new ArgumentException("Iteration results require ordered unique iteration numbers.");
                string status = (string)result["status"];
                State(status, "pending", "running", "passed", "failed", "timed_out", "cancelled", "skipped");
                long? started = IterationTimestamp(result["started_unix_ms"], true);
                long? finished = IterationTimestamp(result["finished_unix_ms"], true);
                bool attempted = status != "pending" && status != "skipped";
                if (
                    attempted != started.HasValue
                    || (status == "pending" && finished.HasValue)
                    || (status == "running" && finished.HasValue)
                    || (attempted && status != "running" && !finished.HasValue)
                    || (terminal && (status == "pending" || status == "running"))
                    || ((string)value["status"] == "succeeded" && status != "passed")
                )
                    throw new ArgumentException("Iteration result status and timestamps are inconsistent.");
                if (
                    (started.HasValue && (started.Value < reportStarted || (previousFinished.HasValue && started.Value < previousFinished.Value)))
                    || (
                        finished.HasValue && (finished.Value < (started ?? reportStarted) || (reportFinished.HasValue && finished.Value > reportFinished.Value))
                    )
                    || (started.HasValue && reportFinished.HasValue && started.Value > reportFinished.Value)
                )
                    throw new ArgumentException("Iteration result timestamps are outside their execution boundaries.");
                if (attempted)
                    previousFinished = finished;
            }
        }

        private static long? IterationTimestamp(JToken token, bool nullable)
        {
            if (nullable && token?.Type == JTokenType.Null)
                return null;
            if (token?.Type != JTokenType.Integer || !long.TryParse(token.ToString(), out long number) || number < 0)
                throw new ArgumentException("Iteration timestamps must be nonnegative signed 64-bit integers or declared nulls.");
            return number;
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
                int index = PlayScenarioDefinition.Integer(item, "step_index", -1, PlayScenarioDefinition.MaximumSteps - 1, -2);
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
                PlayScenarioDefinition.Integer(failure, "step_index", 0, PlayScenarioDefinition.MaximumSteps - 1, 0);
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

        private static void ValidateEnvironment(JObject value)
        {
            string[] fields = { "schema_version", "operating_system", "platform", "architecture", "scripting_backend", "build_mode", "runtime_version" };
            PlayScenarioDefinition.Fields(value, fields);
            if (value["schema_version"]?.Type != JTokenType.Integer || !long.TryParse(value["schema_version"].ToString(), out long version) || version != 1)
                throw new ArgumentException("Invalid environment schema version.");
            int[] limits = { 256, 64, 32, 6, 11, 64 };
            for (int index = 1; index < fields.Length; index++)
            {
                string key = fields[index];
                JToken token = value[key];
                if (token == null || (token.Type != JTokenType.String && token.Type != JTokenType.Null))
                    throw new ArgumentException("Invalid environment field: " + key);
                if (token.Type == JTokenType.Null)
                    continue;
                string text = (string)token;
                if (string.IsNullOrWhiteSpace(text) || text.Length > limits[index - 1])
                    throw new ArgumentException("Invalid environment text: " + key);
                for (int character = 0; character < text.Length; character++)
                {
                    if (char.IsControl(text[character]) || char.IsLowSurrogate(text[character]))
                        throw new ArgumentException("Invalid environment text: " + key);
                    if (char.IsHighSurrogate(text[character]))
                    {
                        if (character + 1 >= text.Length || !char.IsLowSurrogate(text[character + 1]))
                            throw new ArgumentException("Invalid environment text: " + key);
                        character++;
                    }
                }
            }
            if (value["scripting_backend"].Type != JTokenType.Null)
                State((string)value["scripting_backend"], "mono", "il2cpp");
            if (value["build_mode"].Type != JTokenType.Null)
                State((string)value["build_mode"], "editor", "development", "release");
        }

        private static long EventInteger(JToken value)
        {
            if (
                value == null
                || value.Type != JTokenType.Integer
                || !long.TryParse(
                    value.ToString(Formatting.None),
                    System.Globalization.NumberStyles.Integer,
                    System.Globalization.CultureInfo.InvariantCulture,
                    out long number
                )
            )
                throw new ArgumentException("Event evidence numbers must be signed64-bit integer tokens.");
            return number;
        }

        private static void ValidateEventEvidence(JObject value)
        {
            // Only this optional proof has scalar sequence arrays; do not relax generic report arrays.
            PlayScenarioDefinition.Fields(value, ReportFields[typeof(PlayScenarioEventEvidence)].Keys.ToArray());
            if (EventInteger(value["schema_version"]) != 1 || value["generation"]?.Type != JTokenType.String || value["overflow"]?.Type != JTokenType.Boolean)
                throw new ArgumentException("Invalid event evidence schema, generation or overflow flag.");
            foreach (
                string key in new[] { "since_sequence", "retained_from_sequence", "observed_through_sequence", "consumed_through_sequence", "dropped_total" }
            )
                EventInteger(value[key]);
            long count = EventInteger(value["match_count"]);
            if (count < 0 || count > 128)
                throw new ArgumentException("Event match count must be0..128.");
            foreach (string key in new[] { "first_matched_sequence", "last_matched_sequence" })
                if (value[key] != null && value[key].Type != JTokenType.Null)
                    EventInteger(value[key]);
            JToken positions = value["matched_sequences"];
            if (positions != null && positions.Type != JTokenType.Null)
            {
                if (!(positions is JArray array) || array.Count > 16)
                    throw new ArgumentException("Event proof positions must be an array of at most16 integers.");
                foreach (JToken position in array)
                    EventInteger(position);
            }
            PlayScenarioEventEvidence proof = value.ToObject<PlayScenarioEventEvidence>();
            try
            {
                proof.CheckSize();
            }
            catch (InvalidOperationException error)
            {
                throw new ArgumentException("Invalid bounded event proof.", error);
            }
        }

        private static void ValidateShape(JObject value, Type type)
        {
            Dictionary<string, Type> fields = ReportFields[type];
            PlayScenarioDefinition.Fields(value, fields.Keys.ToArray());
            JObject defaults = null;
            bool legacyIterations =
                type == typeof(PlayScenarioRun) && value.Property("iteration_results_version") == null && value.Property("iteration_results") == null;
            foreach (var pair in fields)
            {
                JToken token = value[pair.Key];
                if (token == null)
                {
                    // Newly optional observation evidence stays absent on legacy reports; required fields remain strict.
                    if (
                        (type == typeof(PlayScenarioStepResult) && (pair.Key == "performance" || pair.Key == "screenshot" || pair.Key == "event_evidence"))
                        || (
                            type == typeof(PlayScenarioPerformanceResult)
                            && (pair.Key == "frame_time_p95_ms" || pair.Key == "gc_allocated_bytes_per_frame_max" || pair.Key == "error")
                        )
                        || (
                            type == typeof(PlayScenarioScreenshotResult)
                            && (pair.Key == "path" || pair.Key == "sha256" || pair.Key == "width" || pair.Key == "height" || pair.Key == "error")
                        )
                    )
                        continue;
                    // Optional property-backed launch evidence has no legacy default and must not be captured on read.
                    if (type == typeof(PlayScenarioReproduction) && pair.Key == "environment")
                        continue;
                    if (legacyIterations && (pair.Key == "iteration_results_version" || pair.Key == "iteration_results"))
                        continue;
                    if (!LegacyOptional(type, pair.Key))
                        throw new ArgumentException("Missing report field: " + pair.Key);
                    defaults = defaults ?? ToJson(Activator.CreateInstance(type));
                    value[pair.Key] = token = defaults[pair.Key].DeepClone();
                }
                Type fieldType = pair.Value;
                if (fieldType == typeof(PlayScenarioDefinition))
                    continue;
                if (fieldType == typeof(PlayScenarioEnvironment))
                {
                    if (token.Type == JTokenType.Null)
                        continue;
                    if (!(token is JObject environment))
                        throw new ArgumentException("Invalid report environment object.");
                    ValidateEnvironment(environment);
                    continue;
                }
                if (fieldType == typeof(PlayScenarioEventEvidence))
                {
                    if (token.Type != JTokenType.Null)
                    {
                        if (!(token is JObject evidence))
                            throw new ArgumentException("Invalid event evidence object.");
                        ValidateEventEvidence(evidence);
                    }
                    continue;
                }
                if (
                    fieldType == typeof(PlayScenarioFailureDiagnostics)
                    || fieldType == typeof(PlayScenarioFailure)
                    || fieldType == typeof(PlayScenarioReproduction)
                    || fieldType == typeof(PlayScenarioQueryCounts)
                    || fieldType == typeof(PlayScenarioPerformanceResult)
                    || fieldType == typeof(PlayScenarioScreenshotResult)
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
                        : itemType == typeof(PlayScenarioStepResult) ? PlayScenarioDefinition.MaximumStepResults
                        : itemType == typeof(PlayScenarioLog) ? 50
                        : itemType == typeof(PlayScenarioResourceCheck)
                        || itemType == typeof(PlayScenarioMetricsSnapshot)
                        || itemType == typeof(PlayScenarioIterationResult)
                            ? PlayScenarioDefinition.MaximumRepeats
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
                    else if (scalarType == typeof(double))
                    {
                        if (
                            (token.Type != JTokenType.Float && token.Type != JTokenType.Integer)
                            || !double.TryParse(
                                token.ToString(),
                                System.Globalization.NumberStyles.Float,
                                System.Globalization.CultureInfo.InvariantCulture,
                                out double finiteNumber
                            )
                            || double.IsNaN(finiteNumber)
                            || double.IsInfinity(finiteNumber)
                        )
                            throw new ArgumentException("Invalid finite report number: " + pair.Key);
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
