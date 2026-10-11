using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Security.Cryptography;
using System.Text;
using System.Text.RegularExpressions;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEngine.UIElements;

namespace MCPForUnity.Editor.Windows.PlayScenarios
{
    /// <summary>Manual, bounded imports of stored same-process Player evidence.</summary>
    internal sealed class PlayScenarioSoakEditor
    {
        private const int PageSize = 25;
        private readonly Action<string, bool> message;
        private readonly TextField path;
        private readonly VisualElement summary;
        private readonly ScrollView rows;
        private readonly Label pageLabel;
        private readonly Button previous;
        private readonly Button next;
        private PlayScenarioSoakEvidence evidence;
        private int page;

        internal PlayScenarioSoakEditor(VisualElement parent, Action<string, bool> message, Func<string> chooseFile)
        {
            this.message = message;
            var section = new Foldout
            {
                text = "Imported same-process Player soak",
                name = "playerSoak",
                value = false,
            };
            section.Add(Note("Import soak.json or client-soak.json and its local evidence. Reads happen only on import; paging uses the imported snapshot."));
            path = new TextField("Soak file") { name = "playerSoakPath", maxLength = 4096 };
            section.Add(path);
            section.Add(
                new Button(() =>
                {
                    string selected = chooseFile != null ? chooseFile() : EditorUtility.OpenFilePanel("Open Player soak evidence", "", "json");
                    if (string.IsNullOrEmpty(selected))
                        return;
                    path.value = selected;
                    Import();
                })
                {
                    text = "Open soak.json / client-soak.json",
                    name = "openPlayerSoak",
                }
            );
            section.Add(new Button(Import) { text = "Import soak snapshot", name = "importPlayerSoak" });
            summary = new VisualElement { name = "playerSoakSummary" };
            section.Add(summary);
            previous = new Button(() =>
            {
                page--;
                RenderRows();
            })
            {
                text = "Previous iterations",
                name = "previousSoakIterations",
            };
            next = new Button(() =>
            {
                page++;
                RenderRows();
            })
            {
                text = "Next iterations",
                name = "nextSoakIterations",
            };
            section.Add(previous);
            pageLabel = new Label { name = "playerSoakPage" };
            section.Add(pageLabel);
            section.Add(next);
            rows = new ScrollView { name = "playerSoakRows" };
            rows.AddToClassList("scenario-history-comparison");
            section.Add(rows);
            parent.Add(section);
            RenderRows();
        }

        private void Import()
        {
            evidence = null;
            summary.Clear();
            page = 0;
            try
            {
                evidence = PlayScenarioSoakEvidence.Read(path.value);
                RenderSummary();
                message("Imported Player soak snapshot.", false);
            }
            catch (Exception exception)
            {
                string detail = exception.Message.Length > 1024 ? exception.Message.Substring(0, 1024) + "..." : exception.Message;
                summary.Add(Note("Unavailable: " + detail));
                message("Player soak import failed: " + detail, true);
            }
            RenderRows();
        }

        private void RenderSummary()
        {
            if (evidence.ClientSummary is JObject client)
            {
                summary.Add(Note("Client schema " + client["schema_version"] + "; status " + client["status"] + "; evidence " + client["evidence_state"]));
                summary.Add(
                    Note(
                        "Client planned "
                            + client["requested_iterations"]
                            + "; attempted "
                            + Display(client["attempted_iterations"])
                            + "; passed "
                            + Display(client["passed_iterations"])
                            + "; failed "
                            + Display(client["failed_iterations"])
                            + "; skipped "
                            + Display(client["skipped_iterations"])
                            + "; unknown "
                            + Display(client["unknown_iterations"])
                    )
                );
                summary.Add(
                    Note("Launcher records PID " + Display(client["process_id"]) + "; " + Display(client["client_error"], "no client error recorded") + ".")
                );
            }
            JObject value = evidence.Summary;
            if (value == null)
            {
                summary.Add(Note("Journal: " + evidence.Completeness));
                return;
            }
            summary.Add(Note("Schema " + value["schema_version"] + "; native status " + value["status"] + "; finalization " + value["finalization_state"]));
            summary.Add(Note("Same PID: " + value["process_id"] + " across " + evidence.Rows.Count + " verified saved rows; job " + value["job_id"]));
            summary.Add(
                Note(
                    "Checkpoint planned "
                        + value["requested_iterations"]
                        + "; attempted "
                        + value["attempted_iterations"]
                        + "; passed "
                        + value["passed_iterations"]
                        + "; failed "
                        + value["failed_iterations"]
                        + "; skipped "
                        + value["skipped_iterations"]
                        + "; recorded "
                        + value["recorded_iterations"]
                )
            );
            summary.Add(Note("Journal: " + evidence.Completeness));
            int attempted = evidence.Rows.Count(row => row["started_unix_ms"].Type != JTokenType.Null);
            int passed = evidence.Rows.Count(PlayScenarioSoakEvidence.Passed);
            summary.Add(
                Note(
                    "Imported complete rows: attempted "
                        + attempted
                        + "; passed "
                        + passed
                        + "; failed "
                        + (attempted - passed)
                        + ". Checkpoint counters remain unverified when journal evidence is incomplete."
                )
            );
            summary.Add(
                Note(
                    "First failure: "
                        + Display(value["first_failure_iteration"], "none in the saved rows")
                        + "; selected iteration: "
                        + Display(value["selected_iteration"], "none")
                )
            );
            JObject first = evidence.Rows.FirstOrDefault(row => (long?)row["iteration"] == (long?)value["first_failure_iteration"]);
            if (first != null)
                summary.Add(
                    Note(
                        "First failure detail: "
                            + Display(first["failure_code"], "code unavailable")
                            + "; "
                            + Display(first["error"], "message unavailable")
                            + "; cleanup "
                            + Display(first["cleanup_error"], "no cleanup error recorded")
                    )
                );
            summary.Add(
                Note(
                    "Failure policy "
                        + value["failure_policy"]
                        + "; runner resources released "
                        + value["runner_resources_released"]
                        + "; retained report references "
                        + ((JArray)value["retained_report_files"]).Count
                        + ". Retention references do not prove files still exist."
                )
            );
            if (value["error"]?.Type == JTokenType.String)
                summary.Add(Note("Session diagnostic: " + (string)value["error"]));
            if (!(value["metrics_summary"] is JObject metrics))
                summary.Add(Note("Metrics: disabled or unavailable. Missing measurements are not zero."));
            else
            {
                summary.Add(
                    Note(
                        "Metrics samples "
                            + metrics["sample_count"]
                            + "; usable "
                            + metrics["usable_samples"]
                            + "; warmup "
                            + metrics["warmup_iterations"]
                            + ". "
                            + metrics["summary"]
                    )
                );
                foreach (JToken warning in (JArray)metrics["warnings"])
                    summary.Add(Note("Diagnostic warning: " + (string)warning));
                foreach (JObject sample in ((JArray)metrics["bounded_samples"]).OfType<JObject>())
                    summary.Add(Note(MetricText(sample)));
            }
            summary.Add(Note("Stored native evidence does not establish natural child exit or a memory leak."));
        }

        private void RenderRows()
        {
            rows.Clear();
            int count = evidence?.Rows.Count ?? 0;
            int pages = Math.Max(1, (count + PageSize - 1) / PageSize);
            page = Math.Max(0, Math.Min(page, pages - 1));
            pageLabel.text = "Iteration page " + (page + 1) + " of " + pages + " (" + count + " saved rows)";
            previous.SetEnabled(page > 0);
            next.SetEnabled(page + 1 < pages);
            if (evidence == null)
                return;
            foreach (JObject row in evidence.Rows.Skip(page * PageSize).Take(PageSize))
            {
                var item = new VisualElement { name = "soakIteration_" + row["iteration"] };
                item.AddToClassList("scenario-result-row");
                item.Add(
                    Note(
                        "#"
                            + row["iteration"]
                            + " "
                            + row["status"]
                            + "; "
                            + (row["started_unix_ms"].Type == JTokenType.Null ? "not attempted" : "attempted")
                            + "; PID "
                            + row["process_id"]
                            + "; released "
                            + row["runner_resources_released"]
                    )
                );
                item.Add(
                    Note(
                        "Unexpected logs "
                            + row["unexpected_log_count"]
                            + "; queries "
                            + (
                                row["query_counts"] is JObject queries
                                    ? "searches " + queries["target_searches"] + ", visits " + queries["hierarchy_visits"]
                                    : "unavailable"
                            )
                    )
                );
                if (row["failure_code"].Type != JTokenType.Null || row["error"].Type != JTokenType.Null || row["cleanup_error"].Type != JTokenType.Null)
                    item.Add(
                        Note(
                            "Failure "
                                + Display(row["failure_code"], "unavailable")
                                + "; "
                                + Display(row["error"], "message unavailable")
                                + "; cleanup "
                                + Display(row["cleanup_error"], "no cleanup error recorded")
                        )
                    );
                JArray samples = row["metrics"] as JArray;
                if (samples == null || samples.Count == 0)
                    item.Add(Note("Metrics: disabled or unavailable."));
                else
                    foreach (JObject sample in samples.OfType<JObject>())
                        item.Add(Note(MetricText(sample)));
                rows.Add(item);
            }
        }

        private static string MetricText(JObject sample) =>
            "Iteration "
            + sample["iteration"]
            + " metrics: managed "
            + Display(sample["managed_bytes"])
            + "; allocated "
            + Display(sample["allocated_bytes"])
            + "; objects "
            + Display(sample["object_count"])
            + "; subscriptions "
            + Display(sample["runner_subscription_count"])
            + "; handles "
            + Display(sample["runner_handle_count"])
            + ((bool)sample["ignored_for_trend"] ? " (warmup)" : "")
            + (sample["error"].Type == JTokenType.String ? "; " + (string)sample["error"] : "");

        private static string Display(JToken token, string unavailable = "unavailable") =>
            token == null || token.Type == JTokenType.Null ? unavailable : token.ToString();

        private static Label Note(string text)
        {
            var label = new Label(text);
            label.AddToClassList("scenario-note");
            return label;
        }
    }

    internal sealed class PlayScenarioSoakEvidence
    {
        private const int RowLimit = 16384;
        private const int JournalLimit = RowLimit * 1000;
        private static readonly UTF8Encoding Utf8 = new UTF8Encoding(false, true);
        private static readonly string[] IdentityFields =
        {
            "schema_version",
            "job_id",
            "definition_hash",
            "build_id",
            "build_source_revision",
            "payload_hash",
            "process_id",
        };
        internal JObject Summary;
        internal JObject ClientSummary;
        internal readonly List<JObject> Rows = new List<JObject>();
        internal string Completeness;

        internal static PlayScenarioSoakEvidence Read(string file)
        {
            string full = Path.GetFullPath(file);
            string root = Path.GetDirectoryName(full);
            string name = Path.GetFileName(full);
            if (name != "soak.json" && name != "client-soak.json")
                throw new InvalidDataException("Select soak.json or client-soak.json.");
            PlayScenarioPlayerSessionEditor.CheckPath(root, full);
            JObject source = PlayScenarioPlayerSessionEditor.ReadJson(full, 65536);
            if (name == "client-soak.json")
            {
                ValidateClient(source);
                string native = PlayScenarioPlayerSessionEditor.CheckPath(root, Path.Combine(root, "soak.json"));
                if (!File.Exists(native))
                    return new PlayScenarioSoakEvidence
                    {
                        ClientSummary = source,
                        Completeness = "Unavailable: native soak.json is missing; client claims cannot be verified.",
                    };
                PlayScenarioSoakEvidence imported = Read(native);
                imported.ClientSummary = source;
                if (
                    !JToken.DeepEquals(source["job_id"], imported.Summary["job_id"])
                    || (long)source["requested_iterations"] != (long)imported.Summary["requested_iterations"]
                    || source["process_id"].Type != JTokenType.Null && !JToken.DeepEquals(source["process_id"], imported.Summary["process_id"])
                )
                    throw new InvalidDataException("Client and native soak identities differ.");
                if ((string)source["evidence_state"] == "complete")
                {
                    foreach (
                        string field in new[]
                        {
                            "recorded_iterations",
                            "attempted_iterations",
                            "passed_iterations",
                            "failed_iterations",
                            "skipped_iterations",
                            "journal_bytes",
                            "journal_sha256",
                            "status",
                        }
                    )
                        if (!JToken.DeepEquals(source[field], imported.Summary[field]))
                            throw new InvalidDataException("Complete client and native soak evidence differ: " + field + ".");
                    if (!imported.Completeness.StartsWith("Complete:", StringComparison.Ordinal))
                        imported.Completeness += " Client complete claim is unverified.";
                }
                else
                    imported.Completeness = "Incomplete client evidence; native snapshot: " + imported.Completeness;
                return imported;
            }
            var result = new PlayScenarioSoakEvidence { Summary = source };
            ValidateSummary(source);
            string journal = PlayScenarioPlayerSessionEditor.CheckPath(root, Path.Combine(root, "iterations.jsonl"));
            if (!File.Exists(journal))
            {
                result.Completeness = "Unavailable: iterations.jsonl is missing; success is unverified.";
                return result;
            }
            string hash;
            long bytes;
            bool terminated = result.ReadJournal(journal, out hash, out bytes);
            if (!terminated)
            {
                result.Completeness = "Incomplete: truncated journal tail; only complete rows are shown. Success is unverified.";
                return result;
            }
            result.ValidateRows();
            bool complete = (bool)source["journal_complete"] && (string)source["finalization_state"] == "completed" && (string)source["status"] != "running";
            if (complete && (bytes != (long)source["journal_bytes"] || hash != (string)source["journal_sha256"]))
                throw new InvalidDataException("Journal byte count or SHA256 differs from the completed checkpoint.");
            if (!complete)
            {
                result.Completeness = "Incomplete: unfinished checkpoint; success is unverified.";
                return result;
            }
            string reportFile = PlayScenarioPlayerSessionEditor.CheckPath(root, Path.Combine(root, "run.json"));
            if (!File.Exists(reportFile))
            {
                result.Completeness = "Incomplete: verified journal, but final run.json is missing; success is unverified.";
                return result;
            }
            JObject report = PlayScenarioPlayerSessionEditor.ReadJson(reportFile, 2 * 1024 * 1024);
            if (!JToken.DeepEquals(report["soak"], source) || (string)report["job_id"] != (string)source["job_id"])
                throw new InvalidDataException("Final report soak checkpoint or job identity differs.");
            Integer(report, "player_schema_version", 2, 2);
            Integer(report, "repeat_count", 1, 1);
            Integer(report, "iteration_results_version", 1, 1);
            Choice(report, "execution_environment", "player");
            Choice(report, "finalization_state", "completed");
            Integer(report, "exit_code", 0, 2);
            PlayScenarioPlayerSessionEditor.ValidateReport(report);
            if (!(report["reproduction"] is JObject reproduction))
                throw new InvalidDataException("Final report reproduction identity is unavailable.");
            foreach (string field in new[] { "definition_hash", "build_id", "build_source_revision", "payload_hash" })
                if (!JToken.DeepEquals(reproduction[field], source[field]))
                    throw new InvalidDataException("Final report reproduction identity differs: " + field + ".");
            JObject selected = result.Rows.FirstOrDefault(row => (long)row["iteration"] == (long?)source["selected_iteration"]);
            if (selected != null)
            {
                JObject local = ((JArray)report["iteration_results"]).OfType<JObject>().Single();
                if (
                    !JToken.DeepEquals(local["started_unix_ms"], selected["started_unix_ms"])
                    || !JToken.DeepEquals(local["finished_unix_ms"], selected["finished_unix_ms"])
                    || !JToken.DeepEquals(report["status"], selected["status"])
                    || !JToken.DeepEquals(report["runner_resources_released"], selected["runner_resources_released"])
                )
                    throw new InvalidDataException("Final report selected local iteration differs from the global journal row.");
            }
            if ((string)source["status"] == "succeeded" && ((long?)report["exit_code"] != 0 || (string)report["status"] != "succeeded"))
                throw new InvalidDataException("Final report cannot establish native soak success.");
            result.Completeness = "Complete: ordered rows, same PID, identity, counts and SHA256 verified; final checkpoint matches run.json.";
            return result;
        }

        private bool ReadJournal(string file, out string digest, out long bytes)
        {
            using (var stream = new FileStream(file, FileMode.Open, FileAccess.Read, FileShare.Read))
            using (SHA256 hash = SHA256.Create())
            {
                if (stream.Length > JournalLimit)
                    throw new InvalidDataException("Soak journal exceeds its byte limit.");
                long expectedLength = stream.Length;
                byte[] buffer = new byte[4096];
                byte[] line = new byte[RowLimit];
                int length = 0;
                bytes = 0;
                int read;
                while ((read = stream.Read(buffer, 0, buffer.Length)) != 0)
                {
                    bytes += read;
                    if (bytes > JournalLimit)
                        throw new InvalidDataException("Soak journal exceeds its byte limit.");
                    hash.TransformBlock(buffer, 0, read, buffer, 0);
                    for (int index = 0; index < read; index++)
                    {
                        byte current = buffer[index];
                        if (length >= RowLimit - 1 && current != 10)
                            throw new InvalidDataException("Soak journal row exceeds its byte limit.");
                        if (current != 10)
                        {
                            line[length++] = current;
                            continue;
                        }
                        if (Rows.Count >= 1000)
                            throw new InvalidDataException("Soak journal exceeds 1000 rows.");
                        JObject row = Parse(Utf8.GetString(line, 0, length));
                        ValidateRow(row, Rows.Count + 1, Summary);
                        if (
                            Rows.Count > 0
                            && row["started_unix_ms"].Type != JTokenType.Null
                            && (long?)Rows[Rows.Count - 1]["finished_unix_ms"] > (long)row["started_unix_ms"]
                        )
                            throw new InvalidDataException("Soak journal iteration intervals overlap.");
                        Rows.Add(row);
                        length = 0;
                    }
                }
                if (bytes != expectedLength || stream.Length != expectedLength)
                    throw new InvalidDataException("Soak journal changed during import.");
                hash.TransformFinalBlock(Array.Empty<byte>(), 0, 0);
                digest = string.Concat(hash.Hash.Select(value => value.ToString("x2")));
                return length == 0;
            }
        }

        private static JObject Parse(string text)
        {
            if (text.IndexOf('\r') >= 0 || text.StartsWith("\uFEFF", StringComparison.Ordinal))
                throw new InvalidDataException("Soak journal requires UTF-8 without BOM and LF rows.");
            using (var reader = new JsonTextReader(new StringReader(text)) { DateParseHandling = DateParseHandling.None, MaxDepth = 32 })
            {
                JObject value = JObject.Load(reader, new JsonLoadSettings { DuplicatePropertyNameHandling = DuplicatePropertyNameHandling.Error });
                if (reader.Read())
                    throw new InvalidDataException("Soak row contains trailing content.");
                return value;
            }
        }

        internal static void ValidateSummary(JObject value)
        {
            ValidateIdentity(value);
            Choice(value, "process_scope", "same-process");
            long planned = Integer(value, "requested_iterations", 2, 1000);
            long attempted = Integer(value, "attempted_iterations", 0, planned);
            long passed = Integer(value, "passed_iterations", 0, attempted);
            if (
                Integer(value, "failed_iterations", 0, attempted) != attempted - passed
                || Integer(value, "skipped_iterations", 0, planned) != planned - attempted
            )
                throw new InvalidDataException("Soak attempted/passed/failed/skipped counts disagree.");
            long recorded = Integer(value, "recorded_iterations", attempted, planned);
            if (Integer(value, "journal_rows", 0, planned) != recorded)
                throw new InvalidDataException("Soak recorded count differs from journal_rows.");
            Integer(value, "journal_bytes", 0, JournalLimit);
            long start = Integer(value, "started_unix_ms", 0, long.MaxValue);
            if (start == long.MaxValue)
                throw new InvalidDataException("Soak start timestamp has no valid deadline.");
            Integer(value, "deadline_unix_ms", start + 1, long.MaxValue);
            long? finish = OptionalInteger(value, "finished_unix_ms", start, long.MaxValue);
            Choice(value, "status", "running", "succeeded", "failed", "cancelled", "timed_out");
            Choice(value, "finalization_state", "running", "completed");
            Choice(value, "failure_policy", "stop", "continue");
            int retained = (int)Integer(value, "retain_reports", 1, 64);
            if (Text(value, "journal_file", 32) != "iterations.jsonl")
                throw new InvalidDataException("Soak journal_file must be iterations.jsonl.");
            bool complete = Boolean(value, "journal_complete");
            bool released = Boolean(value, "runner_resources_released");
            string hash = Text(value, "journal_sha256", 64, true);
            if (hash != null && !Regex.IsMatch(hash, "^[0-9a-f]{64}$"))
                throw new InvalidDataException("Invalid soak journal SHA256.");
            OptionalInteger(value, "first_failure_iteration", 1, planned);
            OptionalInteger(value, "selected_iteration", 1, planned);
            Text(value, "error", 2048, true);
            if (
                !(value["retained_report_files"] is JArray files)
                || files.Count > retained
                || files.Any(token => token.Type != JTokenType.String || !Regex.IsMatch((string)token, "^iteration-[0-9]{4}\\.json$"))
                || files.Select(token => (string)token).Distinct().Count() != files.Count
            )
                throw new InvalidDataException("Invalid bounded retained report filenames.");
            if (
                complete != ((string)value["finalization_state"] == "completed")
                || complete && (hash == null || finish == null || (string)value["status"] == "running")
            )
                throw new InvalidDataException("Soak finalization fields disagree.");
            if (
                (string)value["status"] == "succeeded"
                && (!complete || attempted != planned || passed != planned || !released || value["error"].Type != JTokenType.Null)
            )
                throw new InvalidDataException("Succeeded soak requires complete, released evidence and every planned pass.");
            ValidateMetricsSummary(value["metrics_summary"]);
        }

        private static void ValidateClient(JObject value)
        {
            Integer(value, "schema_version", 1, 1);
            Choice(value, "execution_environment", "player");
            Choice(value, "process_scope", "same-process");
            Choice(value, "evidence_state", "complete", "incomplete");
            Choice(value, "status", "succeeded", "failed", "cancelled", "timed_out", "incomplete");
            if (!Regex.IsMatch(Text(value, "job_id", 32), "^[0-9a-f]{32}$"))
                throw new InvalidDataException("Invalid client soak job identity.");
            long planned = Integer(value, "requested_iterations", 2, 1000);
            long? process = OptionalInteger(value, "process_id", 1, int.MaxValue);
            long? recorded = OptionalInteger(value, "recorded_iterations", 0, planned);
            long? attempted = OptionalInteger(value, "attempted_iterations", 0, planned);
            long? passed = OptionalInteger(value, "passed_iterations", 0, planned);
            long? failed = OptionalInteger(value, "failed_iterations", 0, planned);
            long? skipped = OptionalInteger(value, "skipped_iterations", 0, planned);
            long? unknown = OptionalInteger(value, "unknown_iterations", 0, planned);
            long? bytes = OptionalInteger(value, "journal_bytes", 0, JournalLimit);
            string hash = Text(value, "journal_sha256", 64, true);
            string error = Text(value, "client_error", 4096, true);
            if (hash != null && !Regex.IsMatch(hash, "^[0-9a-f]{64}$"))
                throw new InvalidDataException("Invalid client journal SHA256.");
            Choice(value, "native_summary_file", "soak.json");
            Choice(value, "journal_file", "iterations.jsonl");
            Choice(value, "native_report_file", "run.json");
            if (recorded == null)
            {
                if (attempted != null || passed != null || failed != null || skipped != null || unknown != null || bytes != null || hash != null)
                    throw new InvalidDataException("Unavailable client evidence cannot invent counts.");
            }
            else if (
                attempted == null
                || passed == null
                || failed == null
                || skipped == null
                || unknown == null
                || bytes == null
                || hash == null
                || attempted > recorded
                || passed > attempted
                || failed != attempted - passed
                || attempted + skipped + unknown != planned
            )
                throw new InvalidDataException("Client soak counts disagree with evidence availability.");
            bool complete = (string)value["evidence_state"] == "complete";
            if (
                complete && (recorded == null || process == null || unknown != 0 || error != null || (string)value["status"] == "incomplete")
                || !complete && (string)value["status"] != "incomplete"
            )
                throw new InvalidDataException("Client soak finalization state disagrees.");
            if ((string)value["status"] == "succeeded" && (passed != planned || attempted != planned || skipped != 0))
                throw new InvalidDataException("Client soak success requires every planned pass.");
        }

        private void ValidateRows()
        {
            int attempted = Rows.Count(row => row["started_unix_ms"].Type != JTokenType.Null);
            int passed = Rows.Count(Passed);
            JObject first = Rows.FirstOrDefault(row => row["started_unix_ms"].Type != JTokenType.Null && !Passed(row));
            long? firstFailure = (long?)first?["iteration"];
            long? selected = firstFailure ?? (long?)Rows.LastOrDefault(row => row["started_unix_ms"].Type != JTokenType.Null)?["iteration"];
            if (
                Rows.Count != (long)Summary["recorded_iterations"]
                || attempted != (long)Summary["attempted_iterations"]
                || passed != (long)Summary["passed_iterations"]
                || firstFailure != (long?)Summary["first_failure_iteration"]
                || selected != (long?)Summary["selected_iteration"]
            )
                throw new InvalidDataException("Ordered journal counters or first/selected failure differ from checkpoint.");
            long sessionStart = (long)Summary["started_unix_ms"];
            long? sessionFinish = (long?)Summary["finished_unix_ms"];
            foreach (JObject row in Rows)
            {
                long? start = (long?)row["started_unix_ms"];
                long? finish = (long?)row["finished_unix_ms"];
                if (start.HasValue && start.Value < sessionStart || finish.HasValue && sessionFinish.HasValue && finish.Value > sessionFinish.Value)
                    throw new InvalidDataException("Soak journal interval leaves the checkpoint session interval.");
            }
            for (int index = 0; index + 1 < Rows.Count; index++)
                if (
                    Rows[index]["started_unix_ms"].Type == JTokenType.Null
                    || !(bool)Rows[index]["runner_resources_released"]
                    || (string)Rows[index]["status"] == "timed_out"
                    || (string)Rows[index]["status"] == "cancelled"
                    || (string)Summary["failure_policy"] == "stop" && !Passed(Rows[index])
                )
                    throw new InvalidDataException("Soak journal continued after a stopping iteration.");
            if (Summary["metrics_summary"] is JObject metrics)
            {
                List<JObject> samples = Rows.SelectMany(row => (row["metrics"] as JArray ?? new JArray()).OfType<JObject>()).ToList();
                long warmup = (long)metrics["warmup_iterations"];
                if (
                    (long)metrics["sample_count"] != samples.Count
                    || (long)metrics["usable_samples"] != samples.Count(sample => (long)sample["iteration"] > warmup && sample["error"].Type == JTokenType.Null)
                    || !JToken.DeepEquals(metrics["bounded_samples"], new JArray(samples.Skip(Math.Max(0, samples.Count - 10))))
                )
                    throw new InvalidDataException("Soak metrics summary differs from journal samples.");
                foreach (JObject sample in samples)
                    if ((bool)sample["ignored_for_trend"] != ((long)sample["iteration"] <= warmup))
                        throw new InvalidDataException("Soak sample warmup attribution differs.");
            }
        }

        internal static bool Passed(JObject row) =>
            row["started_unix_ms"].Type != JTokenType.Null
            && (string)row["status"] == "succeeded"
            && (bool)row["runner_resources_released"]
            && row["error"].Type == JTokenType.Null
            && row["cleanup_error"].Type == JTokenType.Null
            && row["failure_code"].Type == JTokenType.Null
            && (long)row["unexpected_log_count"] == 0;

        private static void ValidateRow(JObject row, int iteration, JObject summary)
        {
            ValidateIdentity(row);
            foreach (string field in IdentityFields)
                if (!JToken.DeepEquals(row[field], summary[field]))
                    throw new InvalidDataException("Soak journal identity or PID mismatch: " + field + ".");
            if (Integer(row, "iteration", 1, 1000) != iteration || Text(row, "report_file", 32) != "iteration-" + iteration.ToString("D4") + ".json")
                throw new InvalidDataException("Soak journal order or report filename differs.");
            Choice(row, "status", "succeeded", "failed", "timed_out", "cancelled");
            long? start = OptionalInteger(row, "started_unix_ms", 0, long.MaxValue);
            long? finish = OptionalInteger(row, "finished_unix_ms", 0, long.MaxValue);
            if (start != null && (finish == null || finish < start) || start == null && (string)row["status"] == "succeeded")
                throw new InvalidDataException("Soak row interval or actual start differs.");
            Boolean(row, "runner_resources_released");
            foreach (string field in new[] { "failure_code", "error", "cleanup_error" })
                Text(row, field, 512, true);
            Integer(row, "unexpected_log_count", 0, long.MaxValue);
            if (row["query_counts"] is JObject counts)
            {
                Integer(counts, "target_searches", 0, long.MaxValue);
                Integer(counts, "hierarchy_visits", 0, long.MaxValue);
            }
            else if (row["query_counts"]?.Type != JTokenType.Null)
                throw new InvalidDataException("Soak query_counts must be an object or null.");
            ValidateSamples(row["metrics"], 1, iteration);
        }

        private static void ValidateIdentity(JObject value)
        {
            Integer(value, "schema_version", 1, 1);
            foreach (string field in new[] { "job_id", "build_id", "definition_hash", "payload_hash" })
            {
                int length = field.EndsWith("_hash", StringComparison.Ordinal) ? 64 : 32;
                if (!Regex.IsMatch(Text(value, field, length), "^[0-9a-f]{" + length + "}$"))
                    throw new InvalidDataException("Invalid soak identity: " + field + ".");
            }
            Text(value, "build_source_revision", 128, true);
            Integer(value, "process_id", 1, int.MaxValue);
        }

        private static void ValidateMetricsSummary(JToken token)
        {
            if (token?.Type == JTokenType.Null)
                return;
            if (!(token is JObject value))
                throw new InvalidDataException("Soak metrics_summary must be an object or null.");
            long count = Integer(value, "sample_count", 0, 1000);
            Integer(value, "usable_samples", 0, count);
            Integer(value, "warmup_iterations", 0, 9);
            Text(value, "summary", 128);
            if (
                !(value["warnings"] is JArray warnings)
                || warnings.Count > 3
                || warnings.Any(item => item.Type != JTokenType.String || ((string)item).Length > 128)
            )
                throw new InvalidDataException("Soak metric warnings exceed their bounds.");
            ValidateSamples(value["bounded_samples"], 10);
        }

        private static void ValidateSamples(JToken token, int maximum, int? iteration = null)
        {
            if (token?.Type == JTokenType.Null && iteration != null)
                return;
            if (!(token is JArray samples) || samples.Count > maximum)
                throw new InvalidDataException("Soak metric samples exceed their bounds.");
            foreach (JToken sample in samples)
            {
                if (!(sample is JObject value))
                    throw new InvalidDataException("Soak metric sample must be an object.");
                long attributed = Integer(value, "iteration", 1, 1000);
                if (iteration.HasValue && attributed != iteration)
                    throw new InvalidDataException("Soak metric global iteration differs.");
                Integer(value, "timestamp_unix_ms", 0, long.MaxValue);
                foreach (string field in new[] { "managed_bytes", "allocated_bytes", "object_count", "runner_subscription_count", "runner_handle_count" })
                    OptionalInteger(value, field, 0, long.MaxValue);
                Boolean(value, "ignored_for_trend");
                Text(value, "error", 512, true);
            }
        }

        private static long Integer(JObject value, string field, long minimum, long maximum)
        {
            if (value[field]?.Type != JTokenType.Integer)
                throw new InvalidDataException(field + " must be an integer.");
            long result = (long)value[field];
            if (result < minimum || result > maximum)
                throw new InvalidDataException(field + " exceeds its bounds.");
            return result;
        }

        private static long? OptionalInteger(JObject value, string field, long minimum, long maximum)
        {
            if (value[field]?.Type == JTokenType.Null)
                return null;
            return Integer(value, field, minimum, maximum);
        }

        private static bool Boolean(JObject value, string field)
        {
            if (value[field]?.Type != JTokenType.Boolean)
                throw new InvalidDataException(field + " must be Boolean.");
            return (bool)value[field];
        }

        private static string Text(JObject value, string field, int maximum, bool nullable = false)
        {
            JToken token = value[field];
            if (nullable && token?.Type == JTokenType.Null)
                return null;
            if (token?.Type != JTokenType.String || ((string)token).Length > maximum || !nullable && string.IsNullOrWhiteSpace((string)token))
                throw new InvalidDataException(field + " must be a bounded string.");
            return (string)token;
        }

        private static void Choice(JObject value, string field, params string[] choices)
        {
            if (!choices.Contains(Text(value, field, 32)))
                throw new InvalidDataException("Unknown soak " + field + ".");
        }
    }
}
