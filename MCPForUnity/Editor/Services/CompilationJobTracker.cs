using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Text;
using System.Text.RegularExpressions;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEditor.Compilation;

namespace MCPForUnity.Editor.Services
{
    // The event-fed ledger has no Editor callbacks or filesystem operations. The
    // cache owns subscriptions; the facade below supplies session persistence.
    internal sealed class CompilationLedger
    {
        private const int MaxHistory = 16;
        private const int MaxActive = 4;
        private const int MaxRecordBytes = 65536;
        private string _session;
        internal string SessionId => _session;
        private readonly string _projectRoot;
        private readonly Action<string> _save;
        private readonly Func<long> _now;
        private readonly List<Request> _requests = new();
        private readonly List<Run> _runs = new();
        private long _generation;
        private bool _persistenceFailed;

        private sealed class Request
        {
            public string Id;
            public long Requested;
            public string Phase;
            public string Reason = "";
            public List<long> Candidates = new();
            public long? ExplicitGeneration;
        }

        private sealed class Run
        {
            [JsonIgnore]
            public object Context;
            public long Generation;
            public long Started;
            public long? Finished;
            public int Errors;
            public int Warnings;
            public int Assemblies;
            public bool Complete = true;
            public int Omitted;
            public List<JObject> Diagnostics = new();

            // Reserve metadata/counter growth independently of the diagnostic array.
            [JsonIgnore]
            public int DiagnosticBytes = 512;
        }

        internal CompilationLedger(string session, string projectRoot, Action<string> save, string restored = null, Func<long> now = null)
        {
            _session = session;
            _projectRoot = Path.GetFullPath(projectRoot).Replace('\\', '/').TrimEnd('/');
            _save = save;
            _now = now ?? (() => DateTimeOffset.UtcNow.ToUnixTimeMilliseconds());
            if (!string.IsNullOrEmpty(restored))
                Restore(restored);
        }

        internal static bool ValidId(string value) => value != null && Regex.IsMatch(value, "\\A[a-f0-9]{32}\\z");

        internal bool Begin(string id)
        {
            if (!ValidId(id) || _requests.Any(r => r.Id == id))
                return false;
            Trim();
            if (_requests.Count >= MaxHistory + 1)
                return false;
            bool pending = _requests.Any(r => r.Phase != "closed");
            if (pending)
                foreach (var prior in _requests.Where(r => r.Phase != "closed"))
                {
                    prior.Reason = "multiple_pending_requests";
                    prior.Phase = "closed";
                }
            _requests.Add(
                new Request
                {
                    Id = id,
                    Requested = _now(),
                    Phase = "refreshing",
                    Reason =
                        pending ? "multiple_pending_requests"
                        : _runs.Any(r => r.Finished == null) ? "preexisting_compile"
                        : "",
                }
            );
            Save();
            return true;
        }

        internal void ExplicitRequest(string id)
        {
            var request = Find(id);
            if (request == null || request.Phase == "closed")
                return;
            request.Phase = "explicit_request";
            if (request.Candidates.Count > 0)
                request.Reason = "refresh_phase_cycle";
            Save();
        }

        internal void Dispatched(string id)
        {
            var request = Find(id);
            if (request == null || request.Phase == "closed")
                return;
            request.Phase = "awaiting_explicit_start";
            CloseFinished(request);
            Save();
        }

        internal void Reject(string id)
        {
            var request = Find(id);
            if (request == null)
                return;
            request.Phase = "closed";
            request.Reason = "dispatch_failed";
            Save();
        }

        internal void Started(object context)
        {
            if (context == null || _runs.Any(r => ReferenceEquals(r.Context, context)))
            {
                Incomplete();
                Save();
                return;
            }
            if (_generation == long.MaxValue || _runs.Count(r => r.Finished == null) >= MaxActive)
            {
                Incomplete();
                Save();
                return;
            }
            var run = new Run
            {
                Context = context,
                Generation = ++_generation,
                Started = _now(),
            };
            if (_runs.Any(r => r.Finished == null))
            {
                Incomplete();
                run.Complete = false;
            }
            _runs.Add(run);
            foreach (var request in _requests.Where(r => r.Phase != "closed"))
            {
                if (request.Candidates.Count < MaxActive)
                    request.Candidates.Add(run.Generation);
                else
                    request.Reason = "multiple_candidate_cycles";
                if ((request.Phase == "explicit_request" || request.Phase == "awaiting_explicit_start") && request.ExplicitGeneration == null)
                    request.ExplicitGeneration = run.Generation;
                if (request.Candidates.Count > 1)
                    request.Reason = "multiple_candidate_cycles";
                else if (request.Phase == "refreshing")
                    request.Reason = "refresh_phase_cycle";
            }
            Save();
        }

        internal void Assembly(string assembly, CompilerMessage[] messages)
        {
            var active = _runs.Where(r => r.Finished == null).ToArray();
            if (active.Length != 1)
            {
                Incomplete();
                Save();
                return;
            }
            var run = active[0];
            if (run.Assemblies == 1000000)
                run.Complete = false;
            else
                run.Assemblies++;
            if (messages == null)
            {
                run.Complete = false;
                Save();
                return;
            }
            string assemblyName = Basename(assembly, 256);
            foreach (var message in messages)
            {
                bool error = message.type == CompilerMessageType.Error;
                if (!error && message.type != CompilerMessageType.Warning)
                {
                    run.Complete = false;
                    continue;
                }
                if (error)
                {
                    if (run.Errors == 1000000)
                        run.Complete = false;
                    else
                        run.Errors++;
                }
                else
                {
                    if (run.Warnings == 1000000)
                        run.Complete = false;
                    else
                        run.Warnings++;
                }
                if (run.Diagnostics.Count == 100)
                {
                    run.Omitted = Math.Min(2000000, run.Omitted + 1);
                    continue;
                }
                string messageText = SanitizeMessage(message.message);
                var diagnostic = new JObject
                {
                    ["severity"] = error ? "error" : "warning",
                    ["assembly"] = assemblyName,
                    ["file"] = RelativeFile(message.file),
                    ["line"] = Math.Max(0, message.line),
                    ["column"] = Math.Max(0, message.column),
                    ["message"] = messageText,
                    ["diagnostic_text_omitted"] = messageText == "<diagnostic text omitted: absolute path>",
                };
                int diagnosticBytes = Encoding.UTF8.GetByteCount(diagnostic.ToString(Formatting.None)) + 1;
                if (run.DiagnosticBytes + diagnosticBytes > MaxRecordBytes - 16384)
                    run.Omitted = Math.Min(2000000, run.Omitted + 1);
                else
                {
                    run.Diagnostics.Add(diagnostic);
                    run.DiagnosticBytes += diagnosticBytes;
                }
            }
            Save();
        }

        internal void Finished(object context)
        {
            var run = _runs.FirstOrDefault(r => ReferenceEquals(r.Context, context) && r.Finished == null);
            if (run == null)
            {
                Incomplete();
                Save();
                return;
            }
            run.Finished = _now();
            run.Context = null;
            foreach (var request in _requests)
                CloseFinished(request);
            Save();
        }

        private void CloseFinished(Request request)
        {
            if (request.Phase != "awaiting_explicit_start" || request.ExplicitGeneration == null || request.Candidates.Count == 0)
                return;
            if (request.Candidates.All(g => _runs.Any(r => r.Generation == g && r.Finished != null)))
                request.Phase = "closed";
        }

        internal JObject Query(string id)
        {
            var request = Find(id);
            var result = Unknown(id, request == null ? "result_unavailable" : "explicit_start_not_observed");
            if (request == null)
                return result;
            result["requested_at_unix_ms"] = request.Requested;
            result["candidate_generations"] = new JArray(request.Candidates);
            result["state"] =
                request.Phase == "closed" ? "finished"
                : request.Candidates.Count > 0 ? "running"
                : "requested";
            var run = request.Candidates.Count == 1 ? _runs.FirstOrDefault(r => r.Generation == request.Candidates[0]) : null;
            if (run != null)
            {
                result["generation"] = run.Generation;
                result["started_at_unix_ms"] = run.Started;
                result["finished_at_unix_ms"] = run.Finished;
                result["error_count"] = run.Errors;
                result["warning_count"] = run.Warnings;
                result["assembly_count"] = run.Assemblies;
                result["collection_complete"] = run.Complete && !_persistenceFailed;
                result["diagnostics_truncated"] = run.Omitted > 0;
                result["omitted_diagnostics"] = run.Omitted;
                result["diagnostics"] = new JArray(
                    run.Diagnostics.OrderBy(d => (string)d["assembly"])
                        .ThenBy(d => (string)d["file"])
                        .ThenBy(d => (int)d["line"])
                        .ThenBy(d => (int)d["column"])
                        .ThenBy(d => (string)d["severity"])
                        .ThenBy(d => (string)d["message"])
                        .Select(d => d.DeepClone())
                );
            }
            string reason = _persistenceFailed ? "persistence_unavailable" : request.Reason;
            if (reason.Length > 0)
            {
                result["reason"] = reason;
                result["attribution"] = "ambiguous";
                return result;
            }
            if (run == null || run.Finished == null || request.Phase != "closed" || request.ExplicitGeneration != run.Generation)
                return result;
            if (!run.Complete || run.Assemblies == 0)
            {
                result["reason"] = "incomplete_events";
                return result;
            }
            result["outcome"] = run.Errors > 0 ? "failed" : "success";
            result["attribution"] = "observed_after_request";
            result["reason"] = "";
            return result;
        }

        internal JObject Summary()
        {
            var latest = _runs.LastOrDefault(r => r.Generation == _generation);
            return new JObject
            {
                ["tracking_schema"] = "unity-mcp/compile_job@1",
                ["session_id"] = _session,
                ["generation"] = _generation == 0 ? (long?)null : _generation,
                ["outcome"] =
                    latest == null || latest.Finished == null || !latest.Complete || latest.Assemblies == 0 || _persistenceFailed ? "unknown"
                    : latest.Errors > 0 ? "failed"
                    : "success",
                ["error_count"] = latest?.Errors ?? 0,
                ["warning_count"] = latest?.Warnings ?? 0,
            };
        }

        private JObject Unknown(string id, string reason) =>
            new JObject
            {
                ["schema_version"] = "unity-mcp/compile_job@1",
                ["compile_id"] = id,
                ["session_id"] = _session,
                ["generation"] = null,
                ["requested_at_unix_ms"] = null,
                ["started_at_unix_ms"] = null,
                ["finished_at_unix_ms"] = null,
                ["state"] = "finished",
                ["outcome"] = "unknown",
                ["attribution"] = "unconfirmed",
                ["reason"] = reason,
                ["candidate_generations"] = new JArray(),
                ["error_count"] = 0,
                ["warning_count"] = 0,
                ["assembly_count"] = 0,
                ["diagnostics"] = new JArray(),
                ["diagnostics_truncated"] = false,
                ["omitted_diagnostics"] = 0,
                ["collection_complete"] = false,
            };

        private Request Find(string id) => _requests.FirstOrDefault(r => r.Id == id);

        private void Incomplete()
        {
            foreach (var run in _runs.Where(r => r.Finished == null))
                run.Complete = false;
            foreach (var request in _requests.Where(r => r.Phase != "closed"))
                request.Reason = "incomplete_events";
        }

        private static string Bound(string value, int max) =>
            string.IsNullOrEmpty(value) ? ""
            : value.Length <= max ? value
            : value.Substring(0, max);

        private static string Basename(string value, int max) => Bound((value ?? "").Replace('\\', '/').Split('/').Last(), max);

        private string RelativeFile(string value)
        {
            string path = (value ?? "").Replace('\\', '/');
            if (path.Split('/').Contains(".."))
                return Basename(path, 512);
            if (path.StartsWith(_projectRoot + "/", StringComparison.OrdinalIgnoreCase))
                return Bound(path.Substring(_projectRoot.Length + 1), 512);
            if (path.StartsWith("Assets/", StringComparison.Ordinal) || path.StartsWith("Packages/", StringComparison.Ordinal))
                return Bound(path, 512);
            return Basename(path, 512);
        }

        internal static string SanitizeMessage(string message)
        {
            // Omit path-bearing text rather than risk partial redaction of quoted,
            // UNC, device or foreign SDK paths. Counts/location remain useful.
            string text = message ?? "";
            if (Regex.IsMatch(text, @"[A-Za-z]:[\\/]|\\\\|(?<![A-Za-z0-9_])/(?:[^\s/]+/)*[^\s]+"))
                return "<diagnostic text omitted: absolute path>";
            return Bound(text.Replace('\r', ' ').Replace('\n', ' '), 1024);
        }

        private void Trim()
        {
            while (_requests.Count(r => r.Phase == "closed") + _runs.Count(r => r.Finished != null) > MaxHistory)
            {
                var request = _requests.FirstOrDefault(r => r.Phase == "closed");
                if (request != null)
                {
                    _requests.Remove(request);
                    continue;
                }
                var run = _runs.FirstOrDefault(r => r.Finished != null);
                if (run == null)
                    break;
                if (_requests.Any(r => r.Candidates.Contains(run.Generation)))
                    break;
                _runs.Remove(run);
            }
            // Remove unreferenced completed runs first to avoid retaining a job
            // whose evidence was evicted while another terminal job was retained.
            _runs.RemoveAll(r => r.Finished != null && !_requests.Any(q => q.Candidates.Contains(r.Generation)) && r != _runs.LastOrDefault());
        }

        private void Save()
        {
            Trim();
            try
            {
                _save(
                    JsonConvert.SerializeObject(
                        new
                        {
                            schema = 1,
                            session = _session,
                            generation = _generation,
                            requests = _requests,
                            runs = _runs,
                        }
                    )
                );
            }
            catch (Exception)
            {
                _persistenceFailed = true;
            }
        }

        private static void Fields(JObject row, params string[] names)
        {
            if (row == null || row.Count != names.Length || row.Properties().Any(p => !names.Contains(p.Name)))
                throw new FormatException();
        }

        private static long Number(JToken token, long max = long.MaxValue)
        {
            if (token?.Type != JTokenType.Integer)
                throw new FormatException();
            long value = token.Value<long>();
            if (value < 0 || value > max)
                throw new FormatException();
            return value;
        }

        private static string Text(JToken token, int max)
        {
            if (token?.Type != JTokenType.String || token.Value<string>().Length > max)
                throw new FormatException();
            return token.Value<string>();
        }

        private static JArray Array(JToken token, int max)
        {
            if (token is not JArray value || value.Count > max)
                throw new FormatException();
            return value;
        }

        private static bool Boolean(JToken token)
        {
            if (token?.Type != JTokenType.Boolean)
                throw new FormatException();
            return token.Value<bool>();
        }

        private void Restore(string encoded)
        {
            try
            {
                if (Encoding.UTF8.GetByteCount(encoded) > 2 * 1024 * 1024)
                    throw new FormatException();
                var root = JObject.Parse(encoded, new JsonLoadSettings { DuplicatePropertyNameHandling = DuplicatePropertyNameHandling.Error });
                Fields(root, "schema", "session", "generation", "requests", "runs");
                if (Number(root["schema"]) != 1 || Text(root["session"], 32) != _session)
                    throw new FormatException();
                _generation = Number(root["generation"]);
                foreach (var token in Array(root["requests"], MaxHistory + 1))
                {
                    var row = token as JObject;
                    Fields(row, "Id", "Requested", "Phase", "Reason", "Candidates", "ExplicitGeneration");
                    string id = Text(row["Id"], 32),
                        phase = Text(row["Phase"], 32),
                        reason = Text(row["Reason"], 80);
                    if (
                        !ValidId(id)
                        || _requests.Any(r => r.Id == id)
                        || !new[] { "refreshing", "explicit_request", "awaiting_explicit_start", "closed" }.Contains(phase)
                        || !Regex.IsMatch(reason, "\\A[a-z_]*\\z")
                    )
                        throw new FormatException();
                    var request = new Request
                    {
                        Id = id,
                        Requested = Number(row["Requested"]),
                        Phase = phase,
                        Reason = reason,
                        ExplicitGeneration = row["ExplicitGeneration"]?.Type == JTokenType.Null ? null : Number(row["ExplicitGeneration"]),
                    };
                    foreach (var candidate in Array(row["Candidates"], MaxActive))
                    {
                        long g = Number(candidate);
                        if (g < 1 || g > _generation || request.Candidates.Contains(g))
                            throw new FormatException();
                        request.Candidates.Add(g);
                    }
                    if (request.ExplicitGeneration != null && !request.Candidates.Contains(request.ExplicitGeneration.Value))
                        throw new FormatException();
                    if (
                        request.Phase == "closed"
                        && request.Reason == ""
                        && (request.Candidates.Count != 1 || request.ExplicitGeneration != request.Candidates[0])
                    )
                        throw new FormatException();
                    _requests.Add(request);
                }
                foreach (var token in Array(root["runs"], MaxHistory + MaxActive))
                {
                    var row = token as JObject;
                    Fields(row, "Generation", "Started", "Finished", "Errors", "Warnings", "Assemblies", "Complete", "Omitted", "Diagnostics");
                    var run = new Run
                    {
                        Generation = Number(row["Generation"]),
                        Started = Number(row["Started"]),
                        Finished = row["Finished"]?.Type == JTokenType.Null ? null : Number(row["Finished"]),
                        Errors = (int)Number(row["Errors"], 1000000),
                        Warnings = (int)Number(row["Warnings"], 1000000),
                        Assemblies = (int)Number(row["Assemblies"], 1000000),
                        Complete = Boolean(row["Complete"]),
                        Omitted = (int)Number(row["Omitted"], 2000000),
                    };
                    if (run.Generation < 1 || run.Generation > _generation || _runs.Any(r => r.Generation == run.Generation) || run.Finished < run.Started)
                        throw new FormatException();
                    foreach (var item in Array(row["Diagnostics"], 100))
                    {
                        var diagnostic = item as JObject;
                        Fields(diagnostic, "severity", "assembly", "file", "line", "column", "message", "diagnostic_text_omitted");
                        if (!new[] { "error", "warning" }.Contains(Text(diagnostic["severity"], 7)))
                            throw new FormatException();
                        string assembly = Text(diagnostic["assembly"], 256),
                            file = Text(diagnostic["file"], 512),
                            message = Text(diagnostic["message"], 1024);
                        Number(diagnostic["line"], int.MaxValue);
                        Number(diagnostic["column"], int.MaxValue);
                        Boolean(diagnostic["diagnostic_text_omitted"]);
                        if (assembly != Basename(assembly, 256) || file != RelativeFile(file) || message != SanitizeMessage(message))
                            throw new FormatException();
                        run.Diagnostics.Add((JObject)diagnostic.DeepClone());
                        run.DiagnosticBytes += Encoding.UTF8.GetByteCount(diagnostic.ToString(Formatting.None)) + 1;
                    }
                    if (
                        Encoding.UTF8.GetByteCount(row.ToString(Formatting.None)) > MaxRecordBytes
                        || run.Diagnostics.Count + run.Omitted != run.Errors + run.Warnings
                        || run.Diagnostics.Count(d => (string)d["severity"] == "error") > run.Errors
                        || run.Diagnostics.Count(d => (string)d["severity"] == "warning") > run.Warnings
                        || run.Assemblies == 0 && run.Errors + run.Warnings > 0
                    )
                        throw new FormatException();
                    if (run.Finished == null)
                        run.Complete = false;
                    _runs.Add(run);
                }
                if (
                    _requests.Count(r => r.Phase != "closed") > 1
                    || _runs.Count(r => r.Finished == null) > MaxActive
                    || _requests.Count(r => r.Phase == "closed") + _runs.Count(r => r.Finished != null) > MaxHistory
                )
                    throw new FormatException();
                foreach (var request in _requests)
                {
                    if (request.Reason != "interrupted" && request.Candidates.Any(g => !_runs.Any(r => r.Generation == g)))
                        throw new FormatException();
                    if (request.Phase != "closed" || request.Candidates.Any(g => _runs.Any(r => r.Generation == g && r.Finished == null)))
                    {
                        request.Reason = "interrupted";
                        request.Phase = "closed";
                    }
                }
                // Runtime context identity cannot survive reload. Keep the
                // interrupted request/candidate identity, but retire orphaned
                // active slots so later requests have independent attribution.
                _runs.RemoveAll(r => r.Finished == null);
            }
            catch (Exception)
            {
                _requests.Clear();
                _runs.Clear();
                // A corrupt ledger cannot prove its old high-water generation.
                // Rotate the tracking incarnation rather than reuse an identity.
                _session = Guid.NewGuid().ToString("N");
                _generation = 0;
                _persistenceFailed = true;
            }
        }
    }

    internal static class CompilationJobTracker
    {
        private const string SessionKey = "MCPForUnity.CompileJobs.Session";
        private const string LedgerKey = "MCPForUnity.CompileJobs.Ledger";
        internal static readonly CompilationLedger Ledger = Create();

        private static CompilationLedger Create()
        {
            string session = SessionState.GetString(SessionKey, "");
            if (!CompilationLedger.ValidId(session))
            {
                session = Guid.NewGuid().ToString("N");
                SessionState.SetString(SessionKey, session);
            }
            var ledger = new CompilationLedger(
                session,
                UnityEngine.Application.dataPath + "/..",
                encoded =>
                {
                    SessionState.SetString(LedgerKey, encoded);
                    if (SessionState.GetString(LedgerKey, "") != encoded)
                        throw new InvalidDataException();
                },
                SessionState.GetString(LedgerKey, "")
            );
            if (ledger.SessionId != session)
            {
                SessionState.SetString(SessionKey, ledger.SessionId);
                SessionState.EraseString(LedgerKey);
            }
            return ledger;
        }
    }
}
