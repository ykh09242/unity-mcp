using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Security.Cryptography;
using System.Text;
using System.Text.RegularExpressions;
using MCPForUnity.Runtime.Helpers;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEditor.UIElements;
using UnityEngine.UIElements;

namespace MCPForUnity.Editor.Windows.PlayScenarios
{
    /// <summary>Explicit, value-only imports of Player session summaries and saved evidence.</summary>
    internal sealed class PlayScenarioPlayerSessionEditor
    {
        internal const int JsonLimit = 8 * 1024 * 1024;
        private const int PageSize = 25;
        private static readonly string[] Modes = { "shared-batches", "fresh-process" };
        private static readonly string[] CountFields =
        {
            "planned",
            "executed",
            "passed",
            "failed",
            "timed_out",
            "cancelled",
            "skipped",
            "unknown",
            "pending",
            "running",
        };
        private readonly Action<string, bool> message;
        private readonly TextField path;
        private readonly VisualElement summary;
        private readonly ScrollView rows;
        private readonly ScrollView details;
        private readonly Label pageLabel;
        private readonly Button previous;
        private readonly Button next;
        private JObject session;
        private List<JObject> outcomes = new List<JObject>();
        private string directory;
        private int page;

        internal PlayScenarioPlayerSessionEditor(VisualElement parent, Action<string, bool> message, Func<string> chooseFile)
        {
            this.message = message;
            var section = new Foldout { text = "Imported Player session", name = "playerSession" };
            section.Add(Note("Import a local session.json snapshot. Saved evidence is read on request; retention may remove it."));
            path = new TextField("Session file") { name = "playerSessionPath", maxLength = 4096 };
            section.Add(path);
            section.Add(
                new Button(() =>
                {
                    string selected = chooseFile != null ? chooseFile() : EditorUtility.OpenFilePanel("Open Player session.json", "", "json");
                    if (!string.IsNullOrEmpty(selected))
                    {
                        path.value = selected;
                        Import();
                    }
                })
                {
                    text = "Open session.json",
                    name = "openPlayerSession",
                }
            );
            section.Add(new Button(Import) { text = "Import snapshot", name = "importPlayerSession" });
            summary = new VisualElement { name = "playerSessionSummary" };
            section.Add(summary);
            previous = new Button(() =>
            {
                page--;
                RenderRows();
            })
            {
                text = "Previous outcomes",
                name = "previousPlayerOutcomes",
            };
            next = new Button(() =>
            {
                page++;
                RenderRows();
            })
            {
                text = "Next outcomes",
                name = "nextPlayerOutcomes",
            };
            section.Add(previous);
            pageLabel = new Label { name = "playerOutcomePage" };
            section.Add(pageLabel);
            section.Add(next);
            rows = new ScrollView { name = "playerSessionOutcomes" };
            rows.AddToClassList("scenario-history-comparison");
            section.Add(rows);
            details = new ScrollView { name = "playerSessionDetails" };
            details.AddToClassList("scenario-history-comparison");
            section.Add(details);
            parent.Add(section);
            previous.SetEnabled(false);
            next.SetEnabled(false);
        }

        private void Import()
        {
            try
            {
                string fullPath = Path.GetFullPath(path.value);
                if (!string.Equals(Path.GetFileName(fullPath), "session.json", StringComparison.OrdinalIgnoreCase))
                    throw new InvalidDataException("Select a session.json file.");
                string root = Path.GetDirectoryName(fullPath);
                CheckPath(root, fullPath);
                JObject snapshot = ReadJson(fullPath, 65536);
                Validate(snapshot, root);
                List<JObject> imported = ReadOutcomes(snapshot, root);
                session = snapshot;
                directory = root;
                outcomes = imported;
                page = 0;
                details.Clear();
                RenderSummary();
                RenderRows();
                message("Imported Player session snapshot.", false);
            }
            catch (Exception exception)
            {
                message("Player session import failed: " + exception.Message, true);
            }
        }

        private static string CheckPath(string root, string file)
        {
            string full = Path.GetFullPath(Path.Combine(root, file));
            if (full.StartsWith(@"\\", StringComparison.Ordinal))
                throw new InvalidDataException("Import a local session file.");
            string portable = full.Replace('\\', '/').ToLowerInvariant();
            if (("/" + portable + "/").Contains("/assets/resources/gamedata/"))
                throw new InvalidDataException("Protected GameData paths cannot be imported.");
            if (portable.Split('/').Any(part => part.StartsWith(".env", StringComparison.Ordinal)))
                throw new InvalidDataException("Environment paths cannot be imported.");
            SafePathUtility.ResolveWithinRoot(root, full);
            // Inspect ancestors of the caller-selected root as well as its contained children.
            SafePathUtility.ResolveWithinRoot(Path.GetPathRoot(full), full);
            return full;
        }

        private static byte[] ReadBytes(string file, int maximum)
        {
            CheckPath(Path.GetDirectoryName(file), file);
            using (var stream = new FileStream(file, FileMode.Open, FileAccess.Read, FileShare.ReadWrite))
            {
                if (stream.Length > maximum)
                    throw new InvalidDataException("Saved artifact exceeds its byte limit.");
                byte[] bytes = new byte[(int)stream.Length];
                int offset = 0;
                while (offset < bytes.Length)
                {
                    int read = stream.Read(bytes, offset, bytes.Length - offset);
                    if (read == 0)
                        throw new InvalidDataException("Saved artifact changed while being read.");
                    offset += read;
                }
                if (stream.ReadByte() != -1)
                    throw new InvalidDataException("Saved artifact changed while being read.");
                return bytes;
            }
        }

        internal static JObject ReadJson(string file, int maximum = JsonLimit) => Parse(new UTF8Encoding(false, true).GetString(ReadBytes(file, maximum)));

        private static JObject Parse(string json)
        {
            using (var reader = new JsonTextReader(new StringReader(json)) { DateParseHandling = DateParseHandling.None, MaxDepth = 32 })
            {
                JObject value = JObject.Load(reader, new JsonLoadSettings { DuplicatePropertyNameHandling = DuplicatePropertyNameHandling.Error });
                if (reader.Read())
                    throw new InvalidDataException("Saved JSON contains trailing content.");
                return value;
            }
        }

        internal static void ValidateCounts(JObject counts)
        {
            foreach (string field in CountFields)
                Integer(counts, field, 0, 1000);
            long states = CountFields.Skip(2).Sum(field => (long)counts[field]);
            long executed = new[] { "running", "passed", "failed", "timed_out", "cancelled" }.Sum(field => (long)counts[field]);
            if ((long)counts["planned"] != states || (long)counts["executed"] != executed)
                throw new InvalidDataException("Iteration counters disagree with their states.");
        }

        private static void ValidateSucceeded(JObject counts, long planned)
        {
            if (
                (long)counts["planned"] != planned
                || (long)counts["executed"] != planned
                || (long)counts["passed"] != planned
                || CountFields.Skip(3).Any(field => (long)counts[field] != 0)
            )
                throw new InvalidDataException("Succeeded results require every planned iteration to pass.");
        }

        private static long Integer(JObject value, string field, long minimum, long maximum)
        {
            if (value[field]?.Type != JTokenType.Integer)
                throw new InvalidDataException(field + " must be an integer.");
            long number = (long)value[field];
            if (number < minimum || number > maximum)
                throw new InvalidDataException(field + " is out of bounds.");
            return number;
        }

        private static long? OptionalInteger(JObject value, string field, long minimum, long maximum) =>
            value[field] == null || value[field].Type == JTokenType.Null ? (long?)null : Integer(value, field, minimum, maximum);

        private static void TimestampPair(JObject value, string startKey, string finishKey)
        {
            long? start = OptionalInteger(value, startKey, 0, long.MaxValue);
            long? finish = OptionalInteger(value, finishKey, 0, long.MaxValue);
            if (start != null && finish != null && finish < start)
                throw new InvalidDataException("Saved timestamp interval is reversed.");
        }

        private static string Text(JObject value, string field, int maximum, bool optional = false)
        {
            JToken token = value[field];
            if (optional && (token == null || token.Type == JTokenType.Null))
                return null;
            if (token?.Type != JTokenType.String || ((string)token).Length > maximum || (!optional && string.IsNullOrWhiteSpace((string)token)))
                throw new InvalidDataException(field + " must be a bounded string.");
            return (string)token;
        }

        private static string Identity(JObject value, string field)
        {
            string id = Text(value, field, 32);
            if (!Regex.IsMatch(id, "^[0-9a-f]{32}$"))
                throw new InvalidDataException(field + " must be a lowercase 32-hex identity.");
            return id;
        }

        internal static string ArtifactDirectory(string root, JObject item)
        {
            string artifact = Text(item, "artifact_directory", 4096, true);
            if (artifact == null)
                return null;
            string expected = "runs/" + Identity(item, "job_id");
            if (artifact != expected)
                throw new InvalidDataException("artifact_directory must match runs/job_id.");
            return CheckPath(root, Path.Combine(root, artifact));
        }

        internal static void Validate(JObject value, string root)
        {
            long version = Integer(value, "schema_version", 1, 2);
            Identity(value, "session_id");
            if (value["recovered"] != null && value["recovered"].Type != JTokenType.Boolean)
                throw new InvalidDataException("recovered must be Boolean.");
            if (value["recovery_admissions_complete"] != null && value["recovery_admissions_complete"].Type != JTokenType.Boolean)
                throw new InvalidDataException("recovery_admissions_complete must be Boolean.");
            TimestampPair(value, "started_unix_ms", "finished_unix_ms");
            string mode = Text(value, "mode", 32);
            if (!Modes.Contains(mode) && mode != "compare")
                throw new InvalidDataException("Unknown Player session mode.");
            string status = Text(value, "status", 32);
            if (!new[] { "running", "succeeded", "failed", "cancelled", "timed_out", "interrupted" }.Contains(status))
                throw new InvalidDataException("Unknown Player session status.");
            long requested = Integer(value, "requested_iterations", 1, 1000);
            Integer(value, "outcomes_recorded", 0, 2000);
            string[] arms = mode == "compare" ? Modes : new[] { mode };
            if (version == 2)
            {
                if (
                    !(value["iteration_counts"] is JObject counts)
                    || !arms.OrderBy(x => x).SequenceEqual(counts.Properties().Select(x => x.Name).OrderBy(x => x))
                )
                    throw new InvalidDataException("Iteration count modes do not match the session.");
                foreach (string arm in arms)
                {
                    if (!(counts[arm] is JObject armCounts))
                        throw new InvalidDataException("Iteration mode counts must be objects.");
                    ValidateCounts(armCounts);
                    if ((long)armCounts["planned"] != requested)
                        throw new InvalidDataException("Planned counts differ from requested_iterations.");
                    if (value["completed_iterations"] is JObject completed && Integer(completed, arm, 0, 1000) != (long)armCounts["executed"])
                        throw new InvalidDataException("completed_iterations differs from executed.");
                }
            }
            if (version == 2 && status == "succeeded")
            {
                if ((bool?)value["recovered"] == true)
                    throw new InvalidDataException("Recovered sessions cannot claim success.");
                foreach (string arm in arms)
                    ValidateSucceeded((JObject)value["iteration_counts"][arm], requested);
            }
            if (value["active_child"] is JObject active)
            {
                Identity(active, "job_id");
                string activeMode = Text(active, "mode", 32);
                if (!arms.Contains(activeMode))
                    throw new InvalidDataException("Active child mode differs from the session.");
                Integer(active, "repeat_count", 1, activeMode == "fresh-process" ? 1 : 10);
                ArtifactDirectory(root, active);
                if (status != "running")
                    throw new InvalidDataException("Terminal session cannot retain an active child.");
            }
            else if (value["active_child"] != null && value["active_child"].Type != JTokenType.Null)
                throw new InvalidDataException("active_child must be an object or null.");
            if (!(value["last_outcomes"] is JArray recent) || recent.Count > 32)
                throw new InvalidDataException("last_outcomes must contain at most 32 rows.");
            foreach (JToken token in recent)
            {
                if (!(token is JObject item))
                    throw new InvalidDataException("Every outcome must be an object.");
                ValidateOutcome(value, item, root);
            }
        }

        private static void ValidateOutcome(JObject value, JObject item, string root)
        {
            long version = Integer(item, "schema_version", 1, 2);
            if (Identity(item, "session_id") != (string)value["session_id"])
                throw new InvalidDataException("Outcome belongs to another session.");
            Identity(item, "job_id");
            Integer(item, "sequence", 1, (long)value["outcomes_recorded"]);
            string mode = Text(item, "mode", 32);
            if (!Modes.Contains(mode) || ((string)value["mode"] != "compare" && mode != (string)value["mode"]))
                throw new InvalidDataException("Outcome mode differs from session.");
            long repeats = Integer(item, "repeat_count", 1, mode == "fresh-process" ? 1 : 10);
            string status = Text(item, "status", 64);
            if (!new[] { "running", "succeeded", "failed", "timed_out", "cancelled", "infrastructure_error", "interrupted" }.Contains(status))
                throw new InvalidDataException("Unknown outcome status.");
            string artifact = ArtifactDirectory(root, item);
            if ((bool?)value["recovered"] == true && artifact != null)
                throw new InvalidDataException("Recovered sessions cannot reference source child artifacts.");
            OptionalInteger(item, "exit_code", -(1L << 31), uint.MaxValue);
            OptionalInteger(item, "actual_exit_code", -(1L << 31), uint.MaxValue);
            OptionalInteger(item, "process_id", 1, uint.MaxValue);
            TimestampPair(item, "started_unix_ms", "finished_unix_ms");
            TimestampPair(item, "process_started_unix_ms", "process_finished_unix_ms");
            foreach (string flag in new[] { "native_report_available", "process_ended", "forced_termination" })
                if (item[flag] != null && item[flag].Type != JTokenType.Boolean)
                    throw new InvalidDataException(flag + " must be Boolean.");
            if (item["process_ended"]?.Type == JTokenType.Boolean && (bool)item["process_ended"] != (item["actual_exit_code"]?.Type == JTokenType.Integer))
                throw new InvalidDataException("Process finalization and actual exit observation disagree.");
            if (version == 2)
            {
                if (!(item["iteration_counts"] is JObject counts))
                    throw new InvalidDataException("Version 2 outcomes require iteration_counts.");
                ValidateCounts(counts);
                if ((long)counts["planned"] != repeats)
                    throw new InvalidDataException("Outcome planned count differs from repeat_count.");
                if (status == "succeeded")
                    ValidateSucceeded(counts, repeats);
                string source = Text(item, "iteration_results_source", 32);
                if (!new[] { "native", "legacy_steps", "unavailable" }.Contains(source))
                    throw new InvalidDataException("Unknown iteration_results_source.");
                bool nativeAvailable = (bool?)item["native_report_available"] == true;
                string nativeHash = Text(item, "native_report_sha256", 64, true);
                bool validHash = nativeHash != null && Regex.IsMatch(nativeHash, "^[0-9a-f]{64}$");
                if ((source != "unavailable" && !nativeAvailable) || (nativeAvailable && !validHash))
                    throw new InvalidDataException("Native iteration evidence requires an available report and its SHA256.");
                if (source == "unavailable" || !nativeAvailable)
                {
                    if (
                        (long)counts["unknown"] != repeats
                        || CountFields.Where(field => field != "planned" && field != "unknown").Any(field => (long)counts[field] != 0)
                    )
                        throw new InvalidDataException("Unavailable iteration evidence must keep every admitted repeat unknown.");
                }
                if (value["iteration_counts"] is JObject total && total[mode] is JObject arm)
                    foreach (string field in CountFields)
                        if ((long)counts[field] > (long)arm[field])
                            throw new InvalidDataException("Outcome counters exceed session counters.");
            }
            string reportHash = Text(item, "native_report_sha256", 64, true);
            if (reportHash != null && !Regex.IsMatch(reportHash, "^[0-9a-f]{64}$"))
                throw new InvalidDataException("native_report_sha256 must be a lowercase SHA256.");
            Text(item, "client_error", 4096, true);
            if (item["failure"] is JObject failure)
            {
                Text(failure, "code", 128);
                Text(failure, "message", 4096, true);
                string stage = Text(failure, "stage", 32, true);
                if (stage != null && !new[] { "setup", "main", "cleanup" }.Contains(stage))
                    throw new InvalidDataException("Invalid failure stage.");
                if (failure["iteration"]?.Type != JTokenType.Null && failure["iteration"] != null)
                    Integer(failure, "iteration", 0, repeats);
                if (failure["step_index"]?.Type != JTokenType.Null && failure["step_index"] != null)
                    Integer(failure, "step_index", -1, 63);
            }
            else if (item["failure"] != null && item["failure"].Type != JTokenType.Null)
                throw new InvalidDataException("failure must be an object or null.");
        }

        private static List<JObject> ReadOutcomes(JObject value, string root)
        {
            string file = CheckPath(root, Path.Combine(root, "outcomes.jsonl"));
            var result = new List<JObject>();
            bool fullJournal = File.Exists(file);
            if (!fullJournal)
                result.AddRange(((JArray)value["last_outcomes"]).OfType<JObject>());
            else
            {
                byte[] bytes = ReadBytes(file, JsonLimit);
                int start = 0;
                for (int index = 0; index < bytes.Length; index++)
                {
                    if (bytes[index] != 10)
                        continue;
                    if (index - start + 1 > 4096 || result.Count >= 2000)
                        throw new InvalidDataException("Outcome journal exceeds its row or line bound.");
                    JObject item = Parse(new UTF8Encoding(false, true).GetString(bytes, start, index - start));
                    ValidateOutcome(value, item, root);
                    result.Add(item);
                    start = index + 1;
                }
                if (start != bytes.Length || result.Count != (long)value["outcomes_recorded"])
                    throw new InvalidDataException("Outcome journal is truncated or differs from the summary.");
            }
            if (
                result.Select(item => (string)item["job_id"]).Distinct().Count() != result.Count
                || result.Select(item => (long)item["sequence"]).Distinct().Count() != result.Count
            )
                throw new InvalidDataException("Outcome journal contains duplicate identities or sequences.");
            if (value["iteration_counts"] is JObject totals)
                foreach (string mode in Modes)
                {
                    if (!(totals[mode] is JObject total))
                        continue;
                    var observed = CountFields.ToDictionary(field => field, field => 0L);
                    foreach (JObject item in result.Where(item => (string)item["mode"] == mode))
                    {
                        if (!(item["iteration_counts"] is JObject child))
                        {
                            if (fullJournal)
                                throw new InvalidDataException("Version 2 journal outcomes require iteration counters.");
                            continue;
                        }
                        foreach (string field in CountFields)
                            observed[field] += (long)child[field];
                    }
                    if (fullJournal)
                    {
                        if (value["active_child"] is JObject active && (string)active["mode"] == mode)
                        {
                            long activeRepeats = (long)active["repeat_count"];
                            if (result.Any(item => (string)item["job_id"] == (string)active["job_id"]))
                                throw new InvalidDataException("Active child is already in the finalized journal.");
                            observed["planned"] += activeRepeats;
                            observed["unknown"] += activeRepeats;
                        }
                        long unadmitted = (long)total["planned"] - observed["planned"];
                        if (unadmitted < 0)
                            throw new InvalidDataException("Journal admission count exceeds the session plan.");
                        string remainderState =
                            (bool?)value["recovered"] == true && (bool?)value["recovery_admissions_complete"] != true ? "unknown"
                            : (string)value["status"] == "running" ? "pending"
                            : "skipped";
                        observed[remainderState] += unadmitted;
                        observed["planned"] += unadmitted;
                        foreach (string field in CountFields)
                            if (observed[field] != (long)total[field])
                                throw new InvalidDataException("Complete journal counters differ from the session summary.");
                    }
                    else
                        foreach (string field in CountFields)
                            if (observed[field] > (long)total[field])
                                throw new InvalidDataException("Recent outcome counters exceed the session summary.");
                }
            return result;
        }

        private static string Counts(JObject counts) => string.Join("; ", CountFields.Select(field => field + " " + counts[field]));

        private void RenderSummary()
        {
            summary.Clear();
            summary.Add(Note("Session " + session["session_id"] + ": " + session["status"]));
            if (session["iteration_counts"] is JObject counts)
                foreach (JProperty mode in counts.Properties())
                {
                    summary.Add(Note(mode.Name + ": " + Counts((JObject)mode.Value)));
                    if ((string)session["status"] != "running" && (long)mode.Value["running"] > 0)
                        summary.Add(Note("Running count records unfinished iterations at the last saved checkpoint."));
                }
            else
                summary.Add(
                    Note(
                        "Legacy session: planned/executed/passed/failed/timed_out/cancelled/skipped/unknown/pending/running counts unavailable. Legacy completion counts do not prove passes."
                    )
                );
            if (outcomes.Count != (long)session["outcomes_recorded"])
                summary.Add(Note("Outcome journal unavailable. Showing " + outcomes.Count + " recent saved rows of " + session["outcomes_recorded"] + "."));
            if (session["session_error"]?.Type == JTokenType.String)
                summary.Add(Note("Session error: " + session["session_error"]));
        }

        private void RenderRows()
        {
            rows.Clear();
            int pages = Math.Max(1, (outcomes.Count + PageSize - 1) / PageSize);
            page = Math.Max(0, Math.Min(page, pages - 1));
            pageLabel.text = "Outcome page " + (page + 1) + " of " + pages + " (" + outcomes.Count + " saved outcomes)";
            previous.SetEnabled(page > 0);
            next.SetEnabled(page + 1 < pages);
            foreach (JObject item in outcomes.Skip(page * PageSize).Take(PageSize))
            {
                rows.Add(Note("#" + item["sequence"] + " " + item["mode"] + ": " + item["status"]));
                rows.Add(Note(item["iteration_counts"] is JObject counts ? Counts(counts) : "Iteration state counts unavailable (legacy outcome)."));
                if (item["failure"] is JObject failure)
                    rows.Add(
                        Note(
                            "Failure "
                                + failure["code"]
                                + "; stage "
                                + failure["stage"]
                                + "; iteration "
                                + failure["iteration"]
                                + "; step "
                                + failure["step_index"]
                        )
                    );
                rows.Add(new Button(() => ShowDetails(item)) { text = "View saved steps and logs", name = "playerOutcome_" + item["sequence"] });
            }
        }

        private void ShowDetails(JObject item)
        {
            details.Clear();
            details.Add(Note("Saved outcome: " + item["job_id"] + " • " + item["status"]));
            if (item["failure"] is JObject failure)
                details.Add(Note(failure["message"]?.Type == JTokenType.String ? "Failure: " + failure["message"] : "Failure message unavailable."));
            try
            {
                string artifact = ArtifactDirectory(directory, item);
                if (artifact == null || !Directory.Exists(artifact))
                {
                    details.Add(Note("Unavailable: saved artifacts were not retained or this recovery has no child artifacts."));
                    return;
                }
                string reportFile = CheckPath(directory, Path.Combine(artifact, "run.json"));
                if (!File.Exists(reportFile))
                    details.Add(Note("Unavailable: saved native run.json."));
                else
                {
                    string expectedHash = Text(item, "native_report_sha256", 64, true);
                    if (expectedHash == null || !Regex.IsMatch(expectedHash, "^[0-9a-f]{64}$"))
                        throw new InvalidDataException("Saved native report hash unavailable.");
                    byte[] reportBytes = ReadBytes(reportFile, JsonLimit);
                    using (SHA256 hash = SHA256.Create())
                    {
                        string actual = string.Concat(hash.ComputeHash(reportBytes).Select(value => value.ToString("x2")));
                        if (actual != expectedHash)
                            throw new InvalidDataException("Saved native report SHA256 mismatch.");
                    }
                    JObject report = Parse(new UTF8Encoding(false, true).GetString(reportBytes));
                    if ((string)report["job_id"] != (string)item["job_id"])
                        throw new InvalidDataException("Saved report job identity differs from the outcome.");
                    ValidateReport(report);
                    AddReportNavigation(report);
                }
                string log = CheckPath(directory, Path.Combine(artifact, "player.log"));
                var savedLog = new VisualElement { name = "playerSavedLog" };
                details.Add(savedLog);
                var logButton = new Button(() =>
                {
                    try
                    {
                        string content = new UTF8Encoding(false, true).GetString(ReadBytes(log, JsonLimit));
                        savedLog.Clear();
                        savedLog.Add(Note(content.Length > 32768 ? content.Substring(0, 32768) + "…" : content));
                    }
                    catch (Exception exception)
                    {
                        message("Saved Player log unavailable: " + exception.Message, true);
                    }
                })
                {
                    text = "View saved Player log (first 32768 characters)",
                    name = "viewPlayerLog",
                };
                logButton.SetEnabled(File.Exists(log));
                details.Add(logButton);
                if (!File.Exists(log))
                    details.Add(Note("Unavailable: saved player.log."));
            }
            catch (Exception exception)
            {
                details.Add(Note("Unavailable: " + exception.Message));
            }
        }

        private static void ValidateReport(JObject report)
        {
            if (report["steps"] is JArray steps)
            {
                if (steps.Count > 2048 || steps.Any(item => !(item is JObject)))
                    throw new InvalidDataException("Saved report steps exceed their bound or contain invalid rows.");
                foreach (JObject step in steps)
                {
                    Integer(step, "iteration", 1, 10);
                    Integer(step, "step_index", 0, 63);
                    string stage = Text(step, "stage", 32, true);
                    if (stage != null && !new[] { "setup", "main", "cleanup" }.Contains(stage))
                        throw new InvalidDataException("Saved report contains an invalid stage.");
                    Text(step, "status", 32);
                    Text(step, "name", 128, true);
                    Text(step, "detail", 8192, true);
                }
            }
            else if (report["steps"] != null && report["steps"].Type != JTokenType.Null)
                throw new InvalidDataException("Saved report steps must be an array.");
            if (report["logs"] is JArray logs && (logs.Count > 4096 || logs.Any(item => !(item is JObject))))
                throw new InvalidDataException("Saved report logs exceed their bound or contain invalid rows.");
        }

        private void AddReportNavigation(JObject report)
        {
            var stages = new PopupField<string>("Stage", new List<string> { "all", "unknown", "setup", "main", "cleanup" }, 0) { name = "playerReportStage" };
            var iteration = new IntegerField("Iteration (0 = all)") { name = "playerReportIteration", value = 0 };
            var step = new IntegerField("Step index (-1 = all)") { name = "playerReportStep", value = -1 };
            var logPage = new IntegerField("Log page (0 = first)") { name = "playerReportLogPage", value = 0 };
            var evidence = new VisualElement { name = "playerReportEvidence" };
            Action render = () =>
            {
                evidence.Clear();
                if (report["steps"] is JArray steps)
                {
                    foreach (
                        JObject item in steps
                            .OfType<JObject>()
                            .Where(item =>
                                (stages.value == "all" || ((string)item["stage"] ?? "unknown") == stages.value)
                                && (iteration.value == 0 || (int?)item["iteration"] == iteration.value)
                                && (step.value < 0 || (int?)item["step_index"] == step.value)
                            )
                            .Take(100)
                    )
                        evidence.Add(
                            Note(
                                item["stage"]
                                    + "."
                                    + item["iteration"]
                                    + "."
                                    + item["step_index"]
                                    + " "
                                    + Clip(item["name"], 128)
                                    + ": "
                                    + item["status"]
                                    + "; "
                                    + Clip(item["detail"], 512)
                            )
                        );
                    evidence.Add(Note("At most 100 matching saved steps shown. Narrow stage/iteration/step to navigate."));
                }
                if (report["logs"] is JArray logs)
                {
                    evidence.Add(Note("Saved report logs (100 per page; logs may have no step association):"));
                    foreach (JObject item in logs.OfType<JObject>().Skip(Math.Min(40, Math.Max(0, logPage.value)) * 100).Take(100))
                        evidence.Add(Note(item["type"] + ": " + Clip(item["message"], 512)));
                }
            };
            stages.RegisterValueChangedCallback(_ => render());
            iteration.RegisterValueChangedCallback(_ => render());
            step.RegisterValueChangedCallback(_ => render());
            logPage.RegisterValueChangedCallback(_ => render());
            details.Add(stages);
            details.Add(iteration);
            details.Add(step);
            details.Add(logPage);
            details.Add(evidence);
            render();
        }

        private static string Clip(JToken token, int maximum)
        {
            string text = token?.Type == JTokenType.String ? (string)token : "";
            return text.Length > maximum ? text.Substring(0, maximum) + "…" : text;
        }

        private static Label Note(string text)
        {
            var label = new Label(text);
            label.AddToClassList("scenario-note");
            return label;
        }
    }
}
