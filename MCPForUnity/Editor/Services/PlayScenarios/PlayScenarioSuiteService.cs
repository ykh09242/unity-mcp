using System;
using System.IO;
using System.Linq;
using System.Text;
using MCPForUnity.Editor.Helpers;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEngine;

namespace MCPForUnity.Editor.Services.PlayScenarios
{
    [InitializeOnLoad]
    public static class PlayScenarioSuiteService
    {
        private const string SessionKey = "MCPForUnity.PlayScenarioSuite.V1";
        private static PlayScenarioSuiteRunner _runner;
        private static bool _attached;
        private static bool _finalized;
        private static long _nextPoll;
        internal static int ActiveSubscriptionCount => _attached ? 3 : 0;
        private static long Now => DateTimeOffset.UtcNow.ToUnixTimeMilliseconds();
        private static PlayScenarioSuiteStore Store => new PlayScenarioSuiteStore(Path.GetDirectoryName(Application.dataPath));

        static PlayScenarioSuiteService() => Restore();

        internal static bool CanStartChild(string jobId) =>
            _runner?.Running != true || (_runner.Current?.Status == "running" && _runner.Current.JobId == jobId);

        public static object Handle(JObject parameters)
        {
            try
            {
                string action = PlayScenarioDefinition.Text(parameters, "action", 32);
                switch (action)
                {
                    case "suite_save":
                        Allow(parameters, "action", "suite");
                        PlayScenarioSuiteDefinition definition = PlayScenarioSuiteDefinition.Parse(parameters["suite"] as JObject);
                        Store.Save(definition);
                        return new SuccessResponse("Suite saved.", definition);
                    case "suite_get":
                        Allow(parameters, "action", "name");
                        return new SuccessResponse("Suite definition.", Store.Get(Name(parameters)));
                    case "suite_list":
                        Allow(parameters, "action");
                        return new SuccessResponse("Saved suite names.", new { suites = Store.List() });
                    case "suite_delete":
                        Allow(parameters, "action", "name");
                        return new SuccessResponse("Suite deletion processed.", new { deleted = Store.Delete(Name(parameters)) });
                    case "suite_reports":
                        Allow(parameters, "action", "name");
                        return new SuccessResponse(
                            "Saved suite reports.",
                            new { reports = Store.ListReports(parameters.Property("name") == null ? null : Name(parameters)) }
                        );
                    case "suite_run":
                        Allow(parameters, "action", "name", "suite_id", "repeat_count", "timeout_seconds", "source_revision");
                        return Start(
                            Name(parameters),
                            PlayScenarioDefinition.Integer(parameters, "repeat_count", 1, 10, 1),
                            PlayScenarioDefinition.Integer(parameters, "timeout_seconds", 1, 1800, 300),
                            Revision(parameters),
                            parameters.Property("suite_id") == null ? null : Id(parameters)
                        );
                    case "suite_status":
                        Allow(parameters, "action", "suite_id");
                        return Status(Id(parameters));
                    case "suite_cancel":
                        Allow(parameters, "action", "suite_id");
                        string id = Id(parameters);
                        if (_runner?.Running == true && _runner.State.Report.SuiteId == id)
                        {
                            _runner.RequestStop("cancelled", "Suite cancelled by request.", Now);
                            Tick();
                        }
                        return Status(id);
                    default:
                        throw new ArgumentException("Unsupported suite action.");
                }
            }
            catch (Exception exception)
                when (exception is ArgumentException
                    || exception is IOException
                    || exception is UnauthorizedAccessException
                    || exception is InvalidOperationException
                    || exception is JsonException
                )
            {
                return new ErrorResponse(exception.Message);
            }
        }

        private static object Start(string name, int repeats, int timeout, string revision, string suiteId)
        {
            PlayScenarioService.ValidateSourceRevision(revision);
            if (suiteId != null)
            {
                PlayScenarioSuiteReport previous = _runner?.State.Report.SuiteId == suiteId ? _runner.State.Report : FindReport(suiteId);
                if (previous != null)
                {
                    if (
                        previous.Suite.Name != name
                        || previous.RepeatCount != repeats
                        || previous.TimeoutSeconds != timeout
                        || previous.SourceRevision != revision
                    )
                        return new ErrorResponse("suite_id already belongs to a different run request.");
                    return new SuccessResponse("Existing suite.", Snapshot(previous));
                }
            }
            if (_runner?.Running == true || PlayScenarioService.IsBusy)
                return new ErrorResponse("play_scenario_busy", new { suite_id = _runner?.State.Report.SuiteId });
            if (!EditorReady)
                return new ErrorResponse("Wait for the editor to finish compiling, importing or changing Play Mode.");
            PlayScenarioSuiteDefinition suite = Store.Get(name);
            PlayScenarioSuiteState state = PlayScenarioSuiteRunner.Create(
                suite,
                suite.Resolve(new PlayScenarioStore(Path.GetDirectoryName(Application.dataPath))),
                repeats,
                timeout,
                revision,
                Now,
                suiteId
            );
            _runner = CreateRunner(state);
            _finalized = false;
            _nextPoll = 0;
            try
            {
                Persist();
                Attach();
            }
            catch
            {
                _runner = null;
                _finalized = true;
                Detach();
                throw;
            }
            // The persisted queue owns admission before any child can request Play Mode.
            return new SuccessResponse("Suite started.", Snapshot(state.Report));
        }

        private static PlayScenarioSuiteReport FindReport(string suiteId)
        {
            try
            {
                return Store.GetReport(suiteId);
            }
            catch (FileNotFoundException)
            {
                return null;
            }
            catch (DirectoryNotFoundException)
            {
                return null;
            }
        }

        private static PlayScenarioSuiteRunner CreateRunner(PlayScenarioSuiteState state) =>
            new PlayScenarioSuiteRunner(
                state,
                (definition, repeats, timeout, id, revision) => Data(PlayScenarioService.Start(definition, repeats, timeout, id, revision)),
                id => Data(PlayScenarioService.Status(id)),
                id => Data(PlayScenarioService.Cancel(id)),
                Persist,
                () => PlayScenarioService.IsBusy
            );

        private static bool EditorReady =>
            !EditorApplication.isCompiling && !EditorApplication.isUpdating && EditorApplication.isPlaying == EditorApplication.isPlayingOrWillChangePlaymode;

        private static JObject Data(object response)
        {
            if (response is SuccessResponse success)
                return success.Data as JObject ?? JObject.FromObject(success.Data, PlayScenarioSuiteStore.Serializer());
            if (response is ErrorResponse error)
                throw new InvalidOperationException(error.Error);
            throw new InvalidOperationException("Unexpected child service response.");
        }

        private static object Status(string id)
        {
            if (_runner?.State.Report.SuiteId == id)
                return new SuccessResponse("Suite status.", Snapshot(_runner.State.Report));
            try
            {
                return new SuccessResponse("Suite status.", Snapshot(Store.GetReport(id)));
            }
            catch (FileNotFoundException)
            {
                return new ErrorResponse("play_scenario_suite_not_found");
            }
            catch (DirectoryNotFoundException)
            {
                return new ErrorResponse("play_scenario_suite_not_found");
            }
        }

        private static JObject Snapshot(PlayScenarioSuiteReport report)
        {
            JObject snapshot = JObject.FromObject(report, PlayScenarioSuiteStore.Serializer());
            if (ReferenceEquals(report, _runner?.State.Report) && !_finalized && !_runner.Running)
                snapshot["status"] = "running";
            return snapshot;
        }

        private static void Tick()
        {
            if (_runner == null || _finalized || Now < _nextPoll)
                return;
            _nextPoll = Now + 100;
            try
            {
                _runner.Tick(Now, EditorReady && !PlayScenarioService.IsBusy);
                if (!_runner.Running)
                    FinalizeReport();
            }
            catch (Exception exception)
            {
                _runner.State.Report.ReportError = Bounded("Suite coordination failed: " + exception.Message);
                _runner.RequestStop("failed", _runner.State.Report.ReportError, Now);
                // Keep observing the reserved child; publishing a terminal parent before its cleanup is unsafe.
                if (_runner.Current?.Status != "running")
                {
                    _runner.Tick(Now, false);
                    FinalizeReport();
                }
            }
        }

        private static void FinalizeReport()
        {
            PlayScenarioSuiteReport report = _runner.State.Report;
            try
            {
                report.ReportPath = Store.SaveReport(report);
            }
            catch (Exception exception)
            {
                report.ReportPath = null;
                report.ReportError = Bounded("Suite report persistence failed: " + exception.Message);
                report.Error = report.Error ?? report.ReportError;
                if (report.Status == "succeeded")
                    report.Status = "failed";
            }
            _finalized = true;
            Detach();
            Persist();
        }

        private static void Persist()
        {
            if (_runner == null)
                return;
            string json = JObject.FromObject(_runner.State, PlayScenarioSuiteStore.Serializer()).ToString(Formatting.None);
            if (Encoding.UTF8.GetByteCount(json) > 3 * 1024 * 1024)
                throw new InvalidOperationException("Suite session snapshot exceeds its 3 MiB limit.");
            SessionState.SetString(SessionKey, json);
        }

        private static void Restore()
        {
            string saved = SessionState.GetString(SessionKey, "");
            if (string.IsNullOrEmpty(saved))
                return;
            try
            {
                if (Encoding.UTF8.GetByteCount(saved) > 3 * 1024 * 1024)
                    throw new InvalidDataException("Suite session snapshot exceeds its limit.");
                JObject value;
                using (var reader = new JsonTextReader(new StringReader(saved)) { DateParseHandling = DateParseHandling.None, MaxDepth = 48 })
                {
                    value = JObject.Load(reader, new JsonLoadSettings { DuplicatePropertyNameHandling = DuplicatePropertyNameHandling.Error });
                    if (reader.Read())
                        throw new InvalidDataException("Suite snapshot has trailing JSON content.");
                }
                PlayScenarioDefinition.Fields(
                    value,
                    "report",
                    "definitions",
                    "repeat_count",
                    "deadline_unix_ms",
                    "cursor",
                    "requested_status",
                    "first_failure_status",
                    "cancel_sent",
                    "finalization_deadline_unix_ms"
                );
                PlayScenarioSuiteStore.ValidateReport(value["report"] as JObject);
                if (!(value["definitions"] is JArray definitions) || definitions.Count < 1 || definitions.Count > 16)
                    throw new InvalidDataException("Invalid frozen suite definitions.");
                var normalized = definitions.Select(token => PlayScenarioDefinition.Parse(token as JObject)).ToList();
                PlayScenarioDefinition.Integer(value, "repeat_count", 1, 10, 0);
                PlayScenarioDefinition.Integer(value, "cursor", 0, definitions.Count, -1);
                if (
                    value["cancel_sent"]?.Type != JTokenType.Boolean
                    || value["deadline_unix_ms"]?.Type != JTokenType.Integer
                    || !long.TryParse(value["deadline_unix_ms"].ToString(), out _)
                )
                    throw new InvalidDataException("Invalid suite snapshot scalar fields.");
                foreach (string key in new[] { "requested_status", "first_failure_status" })
                    if (value[key]?.Type != JTokenType.Null && value[key]?.Type != JTokenType.String)
                        throw new InvalidDataException("Invalid suite snapshot outcome field.");
                JToken cleanupDeadline = value["finalization_deadline_unix_ms"];
                if (
                    cleanupDeadline?.Type != JTokenType.Null
                    && (cleanupDeadline?.Type != JTokenType.Integer || !long.TryParse(cleanupDeadline.ToString(), out _))
                )
                    throw new InvalidDataException("Invalid suite finalization deadline.");
                PlayScenarioSuiteState state = value.ToObject<PlayScenarioSuiteState>(PlayScenarioSuiteStore.Serializer());
                state.Definitions = normalized;
                if (
                    state.Definitions == null
                    || state.Definitions.Count != state.Report.Scenarios.Count
                    || state.Cursor < 0
                    || state.Cursor > state.Definitions.Count
                    || state.RepeatCount < 1
                    || state.RepeatCount > 10
                    || state.RepeatCount != state.Report.RepeatCount
                    || state.DeadlineUnixMs - state.Report.StartedUnixMs != state.Report.TimeoutSeconds * 1000L
                    || state.DeadlineUnixMs < state.Report.StartedUnixMs
                    || state.DeadlineUnixMs - state.Report.StartedUnixMs > 1800000
                )
                    throw new InvalidDataException("Invalid restored suite queue.");
                for (int index = 0; index < state.Definitions.Count; index++)
                {
                    state.Definitions[index] = PlayScenarioDefinition.Parse(JObject.FromObject(state.Definitions[index]));
                    if (state.Definitions[index].Name != state.Report.Scenarios[index].Name)
                        throw new InvalidDataException("Restored suite queue does not match the frozen definitions.");
                    string status = state.Report.Scenarios[index].Status;
                    if (
                        state.Report.Status == "running"
                        && ((index < state.Cursor && (status == "pending" || status == "running")) || (index > state.Cursor && status != "pending"))
                    )
                        throw new InvalidDataException("Restored suite cursor does not match child statuses.");
                }
                if (
                    (state.RequestedStatus != null && !new[] { "failed", "timed_out", "cancelled" }.Contains(state.RequestedStatus, StringComparer.Ordinal))
                    || (
                        state.FirstFailureStatus != null
                        && !new[] { "failed", "timed_out", "cancelled" }.Contains(state.FirstFailureStatus, StringComparer.Ordinal)
                    )
                    || (
                        state.RequestedStatus != null
                        && (
                            !state.FinalizationDeadlineUnixMs.HasValue
                            || state.FinalizationDeadlineUnixMs.Value < state.Report.StartedUnixMs
                            || state.FinalizationDeadlineUnixMs.Value > Now + 360000
                        )
                    )
                )
                    throw new InvalidDataException("Invalid restored suite stop status.");
                _runner = CreateRunner(state);
                _finalized = !_runner.Running && state.Report.ReportPath != null;
                if (!_finalized)
                    Attach();
            }
            catch (Exception exception)
            {
                Detach();
                _runner = null;
                SessionState.EraseString(SessionKey);
                Debug.LogWarning("MCP play scenario suite restore failed: " + exception.Message);
            }
        }

        private static void BeforeReload()
        {
            Persist();
            Detach();
        }

        private static void Quit()
        {
            _runner?.RequestStop("cancelled", "Editor shutdown interrupted suite execution.", Now);
            Persist();
            Detach();
        }

        private static void Attach()
        {
            if (_attached)
                return;
            _attached = true;
            EditorApplication.update += Tick;
            AssemblyReloadEvents.beforeAssemblyReload += BeforeReload;
            EditorApplication.quitting += Quit;
        }

        private static void Detach()
        {
            if (!_attached)
                return;
            _attached = false;
            EditorApplication.update -= Tick;
            AssemblyReloadEvents.beforeAssemblyReload -= BeforeReload;
            EditorApplication.quitting -= Quit;
        }

        private static void Allow(JObject value, params string[] fields) => PlayScenarioDefinition.Fields(value, fields);

        private static string Name(JObject value)
        {
            string name = PlayScenarioDefinition.Text(value, "name", 64);
            PlayScenarioDefinition.ValidateName(name);
            return name;
        }

        private static string Id(JObject value)
        {
            string id = PlayScenarioDefinition.Text(value, "suite_id", 32);
            PlayScenarioSuiteStore.ValidateId(id);
            return id;
        }

        private static string Revision(JObject value)
        {
            if (value.Property("source_revision") == null)
                return null;
            if (value["source_revision"]?.Type != JTokenType.String)
                throw new ArgumentException("source_revision must be a string.");
            string revision = (string)value["source_revision"];
            PlayScenarioService.ValidateSourceRevision(revision);
            return revision;
        }

        private static string Bounded(string value) => value?.Length > 4096 ? value.Substring(0, 4096) : value;
    }
}
