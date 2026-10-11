using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Security.Cryptography;
using System.Text;
using MCPForUnity.Editor.Services.PlayScenarios;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;

namespace MCPForUnity.Runtime.PlayScenarios
{
    /// <summary>Durable bounded evidence for sequential one-repeat engines in one native process.</summary>
    public sealed class PlayScenarioPlayerSoakJournal : IDisposable
    {
        public const int RowLimit = 16384;
        public const int JournalLimit = RowLimit * 1000;
        public const int SummaryLimit = 65536;
        public const int ReportLimit = 2 * 1024 * 1024;
        private static readonly UTF8Encoding Utf8 = new UTF8Encoding(false, true);
        private readonly string directory;
        private readonly string journalPath;
        private readonly string summaryPath;
        private readonly JObject identity;
        private readonly PlayScenarioPlayerSoakOptions options;
        private readonly PlayScenarioPlayerSoakMetrics metrics;
        private readonly List<string> retained = new List<string>();
        private long bytes;
        private int passed;
        private bool released = true;
        private bool completed;
        private string journalHash;
        private readonly SHA256 accumulatedHash;
        private bool disposed;
        private int recorded;
        private int? lastStarted;
        public PlayScenarioPlayerPersistence Persistence { get; }
        public int Attempted { get; private set; }
        public int? FirstFailure { get; private set; }
        public long StartedUnixMs { get; }
        public long DeadlineUnixMs { get; }
        public string SelectedReportFile =>
            FirstFailure.HasValue ? Filename(FirstFailure.Value)
            : lastStarted.HasValue ? Filename(lastStarted.Value)
            : null;
        public int CurrentIteration => recorded + 1;

        public PlayScenarioPlayerSoakJournal(
            string directory,
            PlayScenarioPlayerRequest request,
            PlayScenarioPlayerBundle bundle,
            int processId,
            long now,
            PlayScenarioPlayerPersistence persistence = null
        )
        {
            Persistence = persistence ?? new PlayScenarioPlayerPersistence();
            if (request?.Soak == null || bundle == null || processId <= 0)
                throw new ArgumentException("A soak journal requires a validated request, bundle and native PID.");
            this.directory = PlayScenarioPlayerFiles.CheckedAbsolute(directory);
            journalPath = PlayScenarioPlayerFiles.CheckedAbsolute(Path.Combine(directory, "iterations.jsonl"));
            summaryPath = PlayScenarioPlayerFiles.CheckedAbsolute(Path.Combine(directory, "soak.json"));
            options = request.Soak;
            metrics = new PlayScenarioPlayerSoakMetrics(bundle.Definition.Metrics);
            StartedUnixMs = now;
            DeadlineUnixMs = checked(now + options.MaxRuntimeSeconds * 1000L);
            identity = new JObject
            {
                ["schema_version"] = 1,
                ["job_id"] = request.JobId,
                ["definition_hash"] = bundle.DefinitionHash,
                ["build_id"] = bundle.BuildId,
                ["build_source_revision"] = bundle.BuildSourceRevision == null ? JValue.CreateNull() : new JValue(bundle.BuildSourceRevision),
                ["payload_hash"] = request.PayloadHash,
                ["process_id"] = processId,
            };
            Persistence.Execute(
                "checkpoint_replace",
                null,
                () =>
                {
                    if (File.Exists(summaryPath) || File.Exists(summaryPath + ".tmp") || Directory.EnumerateFiles(directory, "iteration-*.json*").Any())
                        throw new IOException("Player soak evidence already exists; use a fresh directory.");
                }
            );
            accumulatedHash = SHA256.Create();
            try
            {
                Persistence.Execute(
                    "journal_append",
                    null,
                    () =>
                    {
                        using (var stream = new FileStream(journalPath, FileMode.CreateNew, FileAccess.Write, FileShare.Read))
                            stream.Flush(true);
                    }
                );
                Persistence.Execute(
                    "checkpoint_replace",
                    null,
                    () => PlayScenarioPlayerFiles.WriteNew(summaryPath, Snapshot("running", null, null, false).ToString(Formatting.None), SummaryLimit)
                );
            }
            catch
            {
                Dispose();
                throw;
            }
        }

        public bool DeadlineReached(long now) => now >= DeadlineUnixMs;

        public bool Append(PlayScenarioRun run, JObject report)
        {
            if (Persistence.Failed)
                throw new IOException("Player soak cannot append after a persistence failure.");
            bool result = false;
            try
            {
                Persistence.Execute("journal_append", CurrentIteration, () => result = AppendCompleted(run, report));
            }
            catch
            {
                Dispose();
                throw;
            }
            return result;
        }

        private bool AppendCompleted(PlayScenarioRun run, JObject report)
        {
            if (completed || disposed || recorded >= options.Iterations || run == null || run.Status == "running" || !run.FinishedUnixMs.HasValue)
                throw new InvalidOperationException("Only one completed native iteration can be appended in order.");
            int iteration = CurrentIteration;
            PlayScenarioIterationResult ledger = run.IterationResults?.SingleOrDefault();
            bool started = ledger?.StartedUnixMs != null;
            bool success =
                started
                && PlayScenarioPlayerLogDrain.ExitCode(run) == 0
                && run.Error == null
                && run.CleanupError == null
                && run.Failure == null
                && run.UnexpectedLogCount == 0;
            string filename = Filename(iteration);
            JObject row = (JObject)identity.DeepClone();
            row["iteration"] = iteration;
            row["status"] = run.Status;
            row["started_unix_ms"] = ledger?.StartedUnixMs;
            row["finished_unix_ms"] = ledger?.FinishedUnixMs;
            row["runner_resources_released"] = run.RunnerResourcesReleased == true;
            row["failure_code"] = run.Failure?.Code;
            row["error"] = PlayScenarioEngine.Bounded(run.Error ?? run.ReportError, 512);
            row["cleanup_error"] = PlayScenarioEngine.Bounded(run.CleanupError, 512);
            row["unexpected_log_count"] = run.UnexpectedLogCount;
            row["query_counts"] = JObject.FromObject(run.QueryCounts);
            JArray boundaryMetrics = metrics.Boundary(run, iteration);
            row["metrics"] = boundaryMetrics;
            row["report_file"] = filename;
            byte[] encoded = Utf8.GetBytes(row.ToString(Formatting.None) + "\n");
            if (encoded.Length > RowLimit || bytes + encoded.Length > JournalLimit)
                throw new IOException("Player soak journal exceeded its fixed evidence bounds.");
            Persistence.Execute(
                "iteration_report",
                iteration,
                () => PlayScenarioPlayerFiles.WriteNew(Path.Combine(directory, filename), report.ToString(Formatting.None), ReportLimit)
            );
            Persistence.Execute(
                "journal_append",
                iteration,
                () =>
                {
                    using (var stream = new FileStream(PlayScenarioPlayerFiles.CheckedAbsolute(journalPath), FileMode.Open, FileAccess.Write, FileShare.Read))
                    {
                        if (stream.Length != bytes)
                            throw new IOException("Player soak journal changed outside its owner.");
                        stream.Position = bytes;
                        stream.Write(encoded, 0, encoded.Length);
                        stream.Flush(true);
                    }
                }
            );
            bytes += encoded.Length;
            accumulatedHash.TransformBlock(encoded, 0, encoded.Length, encoded, 0);
            metrics.Observe(boundaryMetrics, iteration);
            recorded++;
            if (started)
            {
                Attempted++;
                lastStarted = iteration;
            }
            if (success)
                passed++;
            else if (started && !FirstFailure.HasValue)
                FirstFailure = iteration;
            released &= run.RunnerResourcesReleased == true;
            retained.Add(filename);
            while (retained.Count > options.RetainReports)
            {
                string remove = retained.First(name => name != SelectedReportFile);
                Persistence.Execute("report_retention", iteration, () => File.Delete(PlayScenarioPlayerFiles.CheckedAbsolute(Path.Combine(directory, remove))));
                retained.Remove(remove);
            }
            Persistence.Execute(
                "checkpoint_replace",
                iteration,
                () => PlayScenarioPlayerFiles.ReplaceExisting(summaryPath, Snapshot("running", null, null, false).ToString(Formatting.None), SummaryLimit)
            );
            return recorded < options.Iterations
                && started
                && (success || (options.FailurePolicy == "continue" && run.Status == "failed"))
                && released
                && run.ReportError == null;
        }

        public JObject Finish(string stopStatus, string error, bool runnerReleased, long now)
        {
            if (Persistence.Failed)
                throw new IOException("Player soak cannot finalize complete evidence after a persistence failure.");
            if (completed || disposed)
                throw new InvalidOperationException("Player soak evidence was already finalized.");
            try
            {
                Persistence.Execute(
                    "journal_verify",
                    null,
                    () =>
                    {
                        accumulatedHash.TransformFinalBlock(Array.Empty<byte>(), 0, 0);
                        journalHash = string.Concat(accumulatedHash.Hash.Select(value => value.ToString("x2")));
                        VerifyJournal();
                    }
                );
                released &= runnerReleased;
                string status = Persistence.Failed
                    ? "failed"
                    : stopStatus ?? (Attempted == options.Iterations && passed == Attempted && released ? "succeeded" : "failed");
                if (status == "succeeded" && (Attempted != options.Iterations || passed != Attempted || !released))
                    status = "failed";
                JObject summary = Snapshot(status, error ?? Persistence.Message, now, true);
                Persistence.Execute(
                    "checkpoint_replace",
                    null,
                    () => PlayScenarioPlayerFiles.ReplaceExisting(summaryPath, summary.ToString(Formatting.None), SummaryLimit)
                );
                completed = true;
                return summary;
            }
            finally
            {
                Dispose();
            }
        }

        public JObject SelectedReport()
        {
            JObject report = null;
            Persistence.Execute(
                "final_report",
                FirstFailure ?? lastStarted,
                () => report = JObject.Parse(PlayScenarioPlayerFiles.Read(Path.Combine(directory, SelectedReportFile), ReportLimit))
            );
            return report;
        }

        private JObject Snapshot(string status, string error, long? finished, bool complete)
        {
            JObject result = (JObject)identity.DeepClone();
            result["process_scope"] = "same-process";
            result["requested_iterations"] = options.Iterations;
            result["attempted_iterations"] = Attempted;
            result["passed_iterations"] = passed;
            result["failed_iterations"] = Attempted - passed;
            result["recorded_iterations"] = recorded;
            result["skipped_iterations"] = options.Iterations - Attempted;
            result["first_failure_iteration"] = FirstFailure;
            result["started_unix_ms"] = StartedUnixMs;
            result["deadline_unix_ms"] = DeadlineUnixMs;
            result["finished_unix_ms"] = finished;
            result["status"] = status;
            result["finalization_state"] = complete ? "completed" : "running";
            result["failure_policy"] = options.FailurePolicy;
            result["retain_reports"] = options.RetainReports;
            result["journal_file"] = "iterations.jsonl";
            result["journal_rows"] = recorded;
            result["journal_bytes"] = bytes;
            result["journal_sha256"] = journalHash;
            result["journal_complete"] = complete;
            result["runner_resources_released"] = released;
            result["selected_iteration"] = FirstFailure ?? lastStarted;
            result["retained_report_files"] = new JArray(retained);
            result["error"] = PlayScenarioEngine.Bounded(error, 2048);
            result["metrics_summary"] = metrics.Snapshot();
            return result;
        }

        private void VerifyJournal()
        {
            if (HashJournal() != journalHash)
                throw new IOException("Player soak journal integrity changed.");
            int rows = 0,
                successes = 0,
                attempts = 0;
            long previousFinished = StartedUnixMs;
            using (var stream = new FileStream(PlayScenarioPlayerFiles.CheckedAbsolute(journalPath), FileMode.Open, FileAccess.Read, FileShare.Read))
            {
                var buffer = new byte[RowLimit];
                int length = 0,
                    next;
                while ((next = stream.ReadByte()) >= 0)
                {
                    if (length >= RowLimit)
                        throw new IOException("Player soak journal row exceeds its bound.");
                    buffer[length++] = (byte)next;
                    if (next != 10)
                        continue;
                    JObject row = PlayScenarioPlayerBundle.Read(Utf8.GetString(buffer, 0, length - 1), RowLimit);
                    if (
                        identity.Properties().Any(field => !JToken.DeepEquals(field.Value, row[field.Name]))
                        || (int?)row["iteration"] != ++rows
                        || rows > options.Iterations
                        || (long?)row["started_unix_ms"] < previousFinished
                        || (long?)row["finished_unix_ms"] < (long?)row["started_unix_ms"]
                    )
                        throw new IOException("Player soak journal attribution or ordering is invalid.");
                    bool started = row["started_unix_ms"]?.Type == JTokenType.Integer;
                    if (started)
                    {
                        attempts++;
                        previousFinished = (long)row["finished_unix_ms"];
                    }
                    if (
                        started
                        && (string)row["status"] == "succeeded"
                        && (bool?)row["runner_resources_released"] == true
                        && row["error"]?.Type == JTokenType.Null
                        && row["cleanup_error"]?.Type == JTokenType.Null
                        && row["failure_code"]?.Type == JTokenType.Null
                        && (int?)row["unexpected_log_count"] == 0
                    )
                        successes++;
                    length = 0;
                }
                if (length != 0 || rows != recorded || attempts != Attempted || successes != passed)
                    throw new IOException("Player soak journal is incomplete or its counts disagree.");
            }
        }

        private string HashJournal()
        {
            using (var stream = new FileStream(PlayScenarioPlayerFiles.CheckedAbsolute(journalPath), FileMode.Open, FileAccess.Read, FileShare.Read))
            using (SHA256 hash = SHA256.Create())
            {
                if (stream.Length != bytes || stream.Length > JournalLimit)
                    throw new IOException("Player soak journal length changed.");
                return string.Concat(hash.ComputeHash(stream).Select(value => value.ToString("x2")));
            }
        }

        private static string Filename(int iteration) => "iteration-" + iteration.ToString("D4") + ".json";

        public void Dispose()
        {
            if (disposed)
                return;
            disposed = true;
            accumulatedHash?.Dispose();
        }
    }
}
