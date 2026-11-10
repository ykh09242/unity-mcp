using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Security.Cryptography;
using System.Text;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Editor.Services.Transport;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using UnityEditor;

namespace MCPForUnity.Editor.Tools.ProjectAuditor
{
    [InitializeOnLoad]
    internal static class ProjectAuditorJobs
    {
        private const string Key = "MCPForUnity.ProjectAuditor.Jobs.v1";
        private const int MaximumJobs = 8;
        internal const int MaximumIssues = 4096;
        private const int MaximumBytes = 4 * 1024 * 1024;
        private const int MaximumArtifactBytes = MaximumBytes + 32 * 1024;
        private const int PageBytes = 256 * 1024;
        private const long RetentionMs = 10 * 60 * 1000;
        private static readonly object Gate = new object();
        private static readonly Dictionary<string, Audit> Jobs = new Dictionary<string, Audit>(StringComparer.Ordinal);
        private static readonly string SessionId = SessionState.GetString(Key + ".session", Guid.NewGuid().ToString("N"));
        internal static Func<IProjectAuditorBackend> BackendFactory = () => new ProjectAuditorApi();
        internal static Func<string, string> SessionReader = key => SessionState.GetString(key, string.Empty);
        internal static Action<string, string> SessionWriter = SessionState.SetString;
        internal static Action<string> SessionEraser = SessionState.EraseString;
        internal static Func<long> Clock = () => DateTimeOffset.UtcNow.ToUnixTimeMilliseconds();
        private static readonly string[] StringFields = { "Category", "Severity", "Areas", "Description", "RelativePath", "DescriptorId", "Recommendation" };
        private static Audit active;
        private static bool dirty;
        internal static bool RecoveryRequired { get; private set; }
        internal static bool HasNativeOwner
        {
            get
            {
                lock (Gate)
                    return active != null;
            }
        }
        internal static NativeRecoveryDetails RecoveryDetails =>
            RecoveryRequired
                ? new NativeRecoveryDetails(
                    "project_auditor",
                    "observer_lost",
                    "audit_completion_observer_lost",
                    "restart_required",
                    "inspect_native_work_then_restart_editor",
                    "Project Auditor analysis crossed a domain reload without its completion observer. Inspect native analysis, then restart the Editor; idle state cannot prove completion."
                )
                : null;

        private sealed class Audit
        {
            internal string Id;
            internal string State = "queued";
            internal string Reason;
            internal string Target;
            internal long Started;
            internal long Finished;
            internal bool NativeInvoked;
            internal bool ResultsAvailable = true;
            internal string ResultsUnavailableReason;
            internal string SnapshotHash;
            internal bool Truncated;
            internal int Rules;
            internal int Diagnostics;
            internal int Modules;
            internal int FailedModules;
            internal int InterruptedModules;
            internal int Bytes;
            internal IProjectAuditorBackend Backend;
            internal readonly List<JObject> Issues = new List<JObject>();
            internal bool Terminal => State != "queued" && State != "running";
        }

        static ProjectAuditorJobs()
        {
            SessionState.SetString(Key + ".session", SessionId);
            Restore(SessionReader(Key) is { Length: > 0 } serialized ? serialized : "[]", SessionState.GetBool(Key + ".recovery", false));
            EditorApplication.update += SaveIfDirty;
            AssemblyReloadEvents.beforeAssemblyReload += BeforeReload;
            EditorApplication.playModeStateChanged += OnPlayMode;
        }

        internal static bool ValidId(string id) =>
            id != null && id.Length == 32 && id.All(character => character >= '0' && character <= '9' || character >= 'a' && character <= 'f');

        private static long Now => Clock();

        private static int FieldMaximum(string name) =>
            name == "Category" || name == "Severity" || name == "DescriptorId" ? 128
            : name == "Areas" ? 256
            : 2048;

        internal static JObject Availability()
        {
            JObject capability = BackendFactory().Availability();
            lock (Gate)
            {
                capability["owned_audit_active"] = active != null;
                capability["recovery_required"] = RecoveryRequired;
                capability["editor_session_id"] = SessionId;
            }
            return capability;
        }

        internal static object Start(string id)
        {
            lock (Gate)
            {
                Prune();
                if (Jobs.TryGetValue(id, out Audit existing))
                    return new SuccessResponse("Audit identity already tracked; no analysis repeated.", Describe(existing));
                if (active != null || RecoveryRequired)
                    return new ErrorResponse(
                        "project_auditor_busy",
                        new
                        {
                            audit_id = id,
                            state = "busy",
                            started = false,
                            recovery_required = RecoveryRequired,
                        }
                    );
                if (EditorApplication.isPlayingOrWillChangePlaymode || EditorApplication.isCompiling || EditorApplication.isUpdating)
                    return new ErrorResponse(
                        "project_auditor_editor_busy",
                        new
                        {
                            audit_id = id,
                            state = "busy",
                            started = false,
                        }
                    );
                if (
                    !BuildPipeline.IsBuildTargetSupported(
                        BuildPipeline.GetBuildTargetGroup(EditorUserBuildSettings.activeBuildTarget),
                        EditorUserBuildSettings.activeBuildTarget
                    )
                )
                    return new ErrorResponse(
                        "project_auditor_target_unsupported",
                        new
                        {
                            audit_id = id,
                            state = "unavailable",
                            started = false,
                        }
                    );
                if (Jobs.Count >= MaximumJobs)
                    return new ErrorResponse(
                        "project_auditor_history_full",
                        new
                        {
                            audit_id = id,
                            state = "busy",
                            started = false,
                            retention_seconds = RetentionMs / 1000,
                        }
                    );
                var backend = BackendFactory();
                JObject capability = backend.Availability();
                var audit = new Audit
                {
                    Id = id,
                    Started = Now,
                    Backend = backend,
                    Target = EditorUserBuildSettings.activeBuildTarget.ToString(),
                };
                Jobs.Add(id, audit);
                if ((string)capability["state"] != "available")
                {
                    Finish(audit, "unavailable", (string)capability["reason"]);
                    Persist();
                    return new ErrorResponse("project_auditor_unavailable", Describe(audit));
                }
                active = audit;
                Persist(); // Record reservation before scheduling any native work.
                EditorApplication.delayCall += () => Launch(audit);
                return new SuccessResponse("Requested one owned Project Auditor analysis.", Describe(audit));
            }
        }

        internal static void LaunchForTests()
        {
            Audit audit;
            lock (Gate)
                audit = active;
            if (audit != null)
                Launch(audit);
        }

        private static void Launch(Audit audit)
        {
            lock (Gate)
            {
                if (!ReferenceEquals(active, audit) || audit.State != "queued")
                    return;
                if (
                    EditorApplication.isPlayingOrWillChangePlaymode
                    || EditorApplication.isCompiling
                    || EditorApplication.isUpdating
                    || audit.Target != EditorUserBuildSettings.activeBuildTarget.ToString()
                )
                {
                    Finish(audit, "interrupted", "editor_state_changed_before_start");
                    active = null;
                    return;
                }
                try
                {
                    audit.Rules = audit.Backend.Initialize();
                    if (audit.Rules == 0)
                    {
                        Finish(audit, "unavailable", "no_registered_rules");
                        active = null;
                        Persist();
                        return;
                    }
                    audit.State = "running";
                    Persist();
                    audit.Backend.Start(
                        row => Incoming(audit, row),
                        result => Module(audit, result),
                        () => Complete(audit),
                        () =>
                        {
                            audit.NativeInvoked = true;
                            Persist(); // Persist before native invocation; idle state cannot prove completion.
                        }
                    );
                }
                catch (Exception error)
                {
                    // Native invocation boundaries can fail after asynchronous work has been scheduled.
                    Finish(audit, "failed", "audit_invocation_failed_" + error.GetType().Name);
                    if (audit.NativeInvoked)
                        RecoveryRequired = true;
                    else
                        active = null;
                    Persist();
                }
            }
        }

        private static void Incoming(Audit audit, JObject row)
        {
            lock (Gate)
            {
                if (!ReferenceEquals(active, audit) || audit.Terminal)
                    return;
                audit.Diagnostics++;
                if (audit.Issues.Count >= MaximumIssues)
                {
                    audit.Truncated = true;
                    return;
                }
                bool shortened = false;
                var captured = new JObject();
                foreach (string name in StringFields)
                {
                    string value = row[name]?.Type == JTokenType.String ? (string)row[name] : null;
                    int maximum = FieldMaximum(name);
                    if (value != null && value.Length > maximum)
                    {
                        int end = char.IsHighSurrogate(value[maximum - 1]) ? maximum - 1 : maximum;
                        value = value.Substring(0, end);
                        shortened = true;
                    }
                    captured[name] = value;
                }
                captured["Line"] = row["Line"]?.Type == JTokenType.Integer ? row["Line"].DeepClone() : JValue.CreateNull();
                captured["fields_truncated"] = shortened;
                int bytes = Encoding.UTF8.GetByteCount(captured.ToString(Formatting.None));
                if (bytes > PageBytes || bytes > MaximumBytes - audit.Bytes)
                    audit.Truncated = true;
                else
                {
                    audit.Bytes += bytes;
                    audit.Issues.Add(captured);
                }
                dirty = true;
            }
        }

        private static void Module(Audit audit, string result)
        {
            lock (Gate)
            {
                if (!ReferenceEquals(active, audit))
                    return;
                audit.Modules++;
                if (result == "Cancelled")
                    audit.InterruptedModules++;
                else if (result != "Success")
                    audit.FailedModules++;
                dirty = true;
            }
        }

        private static void Complete(Audit audit)
        {
            lock (Gate)
            {
                if (!ReferenceEquals(active, audit))
                    return;
                audit.Truncated |= audit.Backend.ObservationTruncated;
                if (!audit.Terminal)
                {
                    if (audit.InterruptedModules != 0)
                        Finish(audit, "interrupted", "native_analysis_cancelled");
                    else if (audit.FailedModules != 0 || audit.Backend.ObservationError != null)
                        Finish(audit, "failed", audit.Backend.ObservationError ?? "native_module_failed");
                    else if (audit.Modules == 0)
                        Finish(audit, "unavailable", "no_modules_completed");
                    else
                        Finish(audit, "completed", "native_modules_completed");
                }
                active = null;
                audit.Backend = null;
                RecoveryRequired = false; // A retained live callback proves this invocation has completed.
                dirty = true;
            }
        }

        private static void Finish(Audit audit, string state, string reason)
        {
            audit.State = state;
            int end = Math.Min(reason?.Length ?? 0, 2048);
            if (end > 0 && char.IsHighSurrogate(reason[end - 1]))
                end--;
            audit.Reason = reason?.Substring(0, end);
            audit.Finished = Now;
            dirty = true;
        }

        internal static object Status(string id)
        {
            lock (Gate)
            {
                Prune();
                return Jobs.TryGetValue(id, out Audit audit)
                    ? new SuccessResponse("Observed owned Project Auditor analysis.", Describe(audit))
                    : new ErrorResponse(
                        "project_auditor_not_found",
                        new { audit_id = id, reason = "Unknown identity, retention expired, or different Editor session." }
                    );
            }
        }

        internal static object Issues(string id, int size, string cursor)
        {
            if (size < 1 || size > 100)
                throw new ArgumentException("page_size must be an integer from 1 to 100.");
            lock (Gate)
            {
                Prune();
                if (!Jobs.TryGetValue(id, out Audit audit))
                    return new ErrorResponse("project_auditor_not_found", new { audit_id = id });
                if (!audit.Terminal || ReferenceEquals(active, audit))
                    return new ErrorResponse("project_auditor_not_ready", Describe(audit));
                if (!audit.ResultsAvailable)
                    return new ErrorResponse("project_auditor_results_unavailable", Describe(audit));
                string prefix = SessionId + ":" + id + ":";
                int offset = 0;
                if (
                    cursor != null
                    && (
                        !cursor.StartsWith(prefix, StringComparison.Ordinal)
                        || !int.TryParse(cursor.Substring(prefix.Length), NumberStyles.None, CultureInfo.InvariantCulture, out offset)
                        || offset < 0
                        || offset > audit.Issues.Count
                    )
                )
                    throw new ArgumentException("Invalid or foreign audit cursor.");
                var items = new JArray();
                int bytes = 0;
                while (offset < audit.Issues.Count && items.Count < size)
                {
                    JObject row = audit.Issues[offset];
                    int rowBytes = Encoding.UTF8.GetByteCount(row.ToString(Formatting.None));
                    if (bytes + rowBytes > PageBytes)
                        break;
                    items.Add(row.DeepClone());
                    bytes += rowBytes;
                    offset++;
                }
                JObject data = Describe(audit);
                data["items"] = items;
                data["has_more"] = offset < audit.Issues.Count;
                data["next_cursor"] = offset < audit.Issues.Count ? prefix + offset.ToString(CultureInfo.InvariantCulture) : null;
                data["page_bytes"] = bytes;
                return new SuccessResponse("Read captured scalar diagnostic page; no analysis executed.", data);
            }
        }

        private static JObject Describe(Audit audit) =>
            new JObject
            {
                ["audit_id"] = audit.Id,
                ["editor_session_id"] = SessionId,
                ["state"] = audit.State,
                ["reason"] = audit.Reason,
                ["build_target"] = audit.Target,
                ["started_unix_ms"] = audit.Started,
                ["finished_unix_ms"] = audit.Finished == 0 ? JValue.CreateNull() : new JValue(audit.Finished),
                ["native_invoked"] = audit.NativeInvoked,
                ["native_owner_retained"] = ReferenceEquals(active, audit),
                ["registered_rule_count"] = audit.Rules,
                ["modules_completed"] = audit.Modules,
                ["modules_failed"] = audit.FailedModules,
                ["modules_interrupted"] = audit.InterruptedModules,
                ["diagnostic_count"] = audit.Diagnostics,
                ["diagnostic_count_exact"] = !audit.Truncated,
                ["retained_issue_count"] = audit.Issues.Count,
                ["results_available"] = audit.ResultsAvailable,
                ["results_unavailable_reason"] = audit.ResultsAvailable ? null : audit.ResultsUnavailableReason,
                ["observation_truncated"] = audit.Truncated,
                ["clean"] = audit.State == "completed" && !audit.Truncated && audit.ResultsAvailable ? new JValue(audit.Diagnostics == 0) : JValue.CreateNull(),
                ["cancel_supported"] = false,
                ["recovery_required"] = RecoveryRequired,
            };

        private static void Prune()
        {
            foreach (
                string id in Jobs
                    .Values.Where(audit => audit.Terminal && !ReferenceEquals(active, audit) && audit.Finished > 0 && Now - audit.Finished > RetentionMs)
                    .Select(audit => audit.Id)
                    .ToArray()
            )
            {
                // Keys are derived only from tracked identities in this Editor session, never from artifact contents.
                // Failed cleanup retains the history slot rather than allowing unbounded orphaned artifacts.
                try
                {
                    SessionEraser(SnapshotKey(id));
                    Jobs.Remove(id);
                    dirty = true;
                }
                catch { /* Retry owned cleanup on the next observation. */ }
            }
        }

        private static void SaveIfDirty()
        {
            lock (Gate)
                if (dirty)
                    Persist();
        }

        private static void Persist()
        {
            Prune();
            SessionWriter(Key, new JArray(Jobs.Values.Select(Receipt)).ToString(Formatting.None));
            SessionState.SetBool(Key + ".recovery", RecoveryRequired);
            dirty = false;
        }

        private static string SnapshotKey(string id) => Key + ".issues." + SessionId + "." + id;

        private static string Hash(string text)
        {
            using var hash = SHA256.Create();
            return string.Concat(hash.ComputeHash(Encoding.UTF8.GetBytes(text)).Select(value => value.ToString("x2", CultureInfo.InvariantCulture)));
        }

        private static JObject SnapshotSummary(JObject summary)
        {
            var result = (JObject)summary.DeepClone();
            foreach (
                string name in new[]
                {
                    "native_owner_retained",
                    "results_available",
                    "results_unavailable_reason",
                    "recovery_required",
                    "diagnostic_snapshot_sha256",
                }
            )
                result.Remove(name);
            return result;
        }

        private static JObject Receipt(Audit audit)
        {
            JObject receipt = Describe(audit);
            if (!audit.Terminal || ReferenceEquals(active, audit) || !audit.ResultsAvailable)
            {
                receipt["results_available"] = false;
                receipt["results_unavailable_reason"] = audit.ResultsUnavailableReason ?? "audit_completion_unobserved";
                return receipt;
            }
            try
            {
                if (audit.SnapshotHash == null)
                {
                    string text = new JObject
                    {
                        ["version"] = 1,
                        ["summary"] = SnapshotSummary(receipt),
                        ["captured_bytes"] = audit.Bytes,
                        ["items"] = new JArray(audit.Issues),
                    }.ToString(Formatting.None);
                    if (Encoding.UTF8.GetByteCount(text) > MaximumArtifactBytes)
                        throw new InvalidDataException("Diagnostic snapshot exceeds its storage bound.");
                    string key = SnapshotKey(audit.Id);
                    SessionWriter(key, text);
                    if (SessionReader(key) != text)
                        throw new InvalidDataException("Diagnostic snapshot write was incomplete.");
                    audit.SnapshotHash = Hash(text);
                }
                receipt["diagnostic_snapshot_sha256"] = audit.SnapshotHash;
            }
            catch
            {
                // A failed durable capture never destroys the still-owned detached rows in memory.
                receipt["results_available"] = false;
                receipt["results_unavailable_reason"] = "captured_rows_persistence_failed";
            }
            return receipt;
        }

        private static JToken Parse(string text)
        {
            using var reader = new JsonTextReader(new StringReader(text)) { DateParseHandling = DateParseHandling.None, MaxDepth = 16 };
            JToken value = JToken.Load(reader, new JsonLoadSettings { DuplicatePropertyNameHandling = DuplicatePropertyNameHandling.Error });
            if (reader.Read())
                throw new InvalidDataException("Unexpected trailing state content.");
            return value;
        }

        private static int Count(JObject row, string name, int maximum = int.MaxValue)
        {
            if (row[name]?.Type != JTokenType.Integer || (long)row[name] < 0 || (long)row[name] > maximum)
                throw new InvalidDataException("Invalid diagnostic count.");
            return (int)row[name];
        }

        private static bool Flag(JObject row, string name)
        {
            if (row[name]?.Type != JTokenType.Boolean)
                throw new InvalidDataException("Invalid diagnostic flag.");
            return (bool)row[name];
        }

        private static void RestoreSnapshot(Audit audit, JObject summary, int retained)
        {
            audit.ResultsAvailable = false;
            audit.ResultsUnavailableReason = (string)summary["results_unavailable_reason"] ?? "captured_rows_lost_on_domain_reload";
            if (!audit.Terminal || Flag(summary, "native_owner_retained"))
            {
                RecoveryRequired |= audit.NativeInvoked;
                audit.ResultsUnavailableReason = "audit_completion_unobserved";
                return;
            }
            if (!Flag(summary, "results_available"))
                return;
            string expectedHash = (string)summary["diagnostic_snapshot_sha256"];
            if (expectedHash == null)
            {
                // Version-one summary-only receipts can prove an empty exact observation, never missing rows.
                audit.ResultsAvailable = audit.Diagnostics == 0 && retained == 0 && !audit.Truncated;
                return;
            }
            audit.ResultsUnavailableReason = "captured_rows_corrupt_on_domain_reload";
            try
            {
                if (expectedHash.Length != 64 || expectedHash.Any(character => !(character >= '0' && character <= '9' || character >= 'a' && character <= 'f')))
                    return;
                string text = SessionReader(SnapshotKey(audit.Id));
                if (string.IsNullOrEmpty(text))
                {
                    audit.ResultsUnavailableReason = "captured_rows_missing_on_domain_reload";
                    return;
                }
                if (text.Length > MaximumArtifactBytes || Encoding.UTF8.GetByteCount(text) > MaximumArtifactBytes || Hash(text) != expectedHash)
                    return;
                if (
                    Parse(text) is not JObject snapshot
                    || snapshot.Count != 4
                    || snapshot["version"]?.Type != JTokenType.Integer
                    || (int?)snapshot["version"] != 1
                    || !JToken.DeepEquals(snapshot["summary"], SnapshotSummary(summary))
                    || snapshot["items"] is not JArray rows
                    || rows.Count != retained
                )
                    return;
                int bytes = 0;
                foreach (JToken token in rows)
                {
                    if (token is not JObject row || row.Count != StringFields.Length + 2 || row["fields_truncated"]?.Type != JTokenType.Boolean)
                        return;
                    foreach (string name in StringFields)
                        if (
                            row[name] == null
                            || row[name].Type != JTokenType.Null && (row[name].Type != JTokenType.String || ((string)row[name]).Length > FieldMaximum(name))
                        )
                            return;
                    if (row["Line"] == null || row["Line"].Type != JTokenType.Null && row["Line"].Type != JTokenType.Integer)
                        return;
                    int rowBytes = Encoding.UTF8.GetByteCount(row.ToString(Formatting.None));
                    if (rowBytes > PageBytes || rowBytes > MaximumBytes - bytes)
                        return;
                    bytes += rowBytes;
                }
                if (Count(snapshot, "captured_bytes", MaximumBytes) != bytes)
                    return;
                // Publish only after the complete artifact, including every page's scalar rows, was validated.
                audit.Issues.AddRange(rows.Cast<JObject>());
                audit.Bytes = bytes;
                audit.SnapshotHash = expectedHash;
                audit.ResultsAvailable = true;
                audit.ResultsUnavailableReason = null;
            }
            catch { /* Corrupt or unavailable snapshots never become fabricated diagnostic pages. */ }
        }

        private static void BeforeReload()
        {
            lock (Gate)
            {
                if (active != null)
                {
                    RecoveryRequired |= active.NativeInvoked;
                    Finish(active, "interrupted", active.NativeInvoked ? "domain_reload_observer_lost" : "domain_reload_before_start");
                }
                Persist();
            }
        }

        private static void OnPlayMode(PlayModeStateChange state)
        {
            lock (Gate)
            {
                if (active == null)
                    return;
                Finish(active, "interrupted", "play_mode_transition");
                if (!active.NativeInvoked)
                    active = null;
                Persist();
            }
        }

        internal static void Restore(string serialized, bool recovery)
        {
            lock (Gate)
            {
                Jobs.Clear();
                active = null;
                RecoveryRequired = recovery;
                dirty = false;
                if (serialized == null || serialized.Length > 128 * 1024 || Encoding.UTF8.GetByteCount(serialized) > 128 * 1024)
                {
                    RecoveryRequired = true;
                    return;
                }
                try
                {
                    JArray rows = Parse(serialized) as JArray;
                    if (rows == null || rows.Count > MaximumJobs || rows.Any(row => !(row is JObject)))
                    {
                        RecoveryRequired = true;
                        return;
                    }
                    foreach (JObject row in rows)
                    {
                        string id = (string)row["audit_id"];
                        if (
                            !ValidId(id)
                            || (string)row["editor_session_id"] != SessionId
                            || Jobs.ContainsKey(id)
                            || !new[] { "queued", "running", "completed", "failed", "unavailable", "interrupted" }.Contains((string)row["state"])
                        )
                        {
                            throw new InvalidDataException("Invalid or foreign audit identity.");
                        }
                        var audit = new Audit
                        {
                            Id = id,
                            State = (string)row["state"],
                            Reason = (string)row["reason"],
                            Target = (string)row["build_target"],
                            Started = (long?)row["started_unix_ms"] ?? 0,
                            Finished = (long?)row["finished_unix_ms"] ?? Now,
                            NativeInvoked = Flag(row, "native_invoked"),
                            Rules = Count(row, "registered_rule_count"),
                            Diagnostics = Count(row, "diagnostic_count"),
                            Modules = Count(row, "modules_completed"),
                            FailedModules = Count(row, "modules_failed"),
                            InterruptedModules = Count(row, "modules_interrupted"),
                            Truncated = Flag(row, "observation_truncated"),
                        };
                        int retained = Count(row, "retained_issue_count", MaximumIssues);
                        if (
                            retained > audit.Diagnostics
                            || Flag(row, "results_available") && !audit.Truncated && retained != audit.Diagnostics
                            || audit.Started < 0
                            || audit.Finished < 0
                            || (audit.Reason?.Length ?? 0) > 2048
                            || (audit.Target?.Length ?? 0) > 128
                            || audit.FailedModules > audit.Modules
                            || audit.InterruptedModules > audit.Modules - audit.FailedModules
                        )
                            throw new InvalidDataException("Invalid audit observation bounds.");
                        RestoreSnapshot(audit, row, retained);
                        if (!audit.Terminal)
                        {
                            RecoveryRequired |= audit.NativeInvoked;
                            Finish(audit, "interrupted", audit.NativeInvoked ? "domain_reload_observer_lost" : "domain_reload_before_start");
                        }
                        Jobs[id] = audit;
                    }
                    Prune();
                }
                catch (Exception error)
                    when (error is JsonException
                        || error is InvalidCastException
                        || error is FormatException
                        || error is OverflowException
                        || error is InvalidDataException
                        || error is ArgumentException
                    )
                {
                    Jobs.Clear();
                    RecoveryRequired = true;
                }
            }
        }
    }
}
