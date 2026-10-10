using System;
using System.IO;
using MCPForUnity.Editor.Helpers;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEngine;

namespace MCPForUnity.Editor.Services.PlayScenarios
{
    /// <summary>Owns editor subscriptions and the single current job across Play-entry reloads.</summary>
    [InitializeOnLoad]
    public static class PlayScenarioService
    {
        private const string SessionKey = "MCPForUnity.PlayScenario.V1";
        private static PlayScenarioEngine _engine;
        private static PlayScenarioLogBuffer _logs;
        private static Application.LogCallback _logCallback;
        private static bool _attached;
        private static bool _finalized;
        private static bool _suppressCapture;
        private static int _processedUnexpectedLogCount;
        private static PlayScenarioFailureCapture _failureCapture;
        internal static int ActiveSubscriptionCount => _attached ? 5 : 0;
        private static int _savedRevision;
        private static long _checkpointAt;
        private static PlayScenarioStore Store => new PlayScenarioStore(Path.GetDirectoryName(Application.dataPath));
        private static long Now => DateTimeOffset.UtcNow.ToUnixTimeMilliseconds();

        static PlayScenarioService() => Restore();

        private static void Restore()
        {
            string saved = SessionState.GetString(SessionKey, "");
            if (string.IsNullOrEmpty(saved))
                return;
            try
            {
                PlayScenarioRun state;
                using (var reader = new JsonTextReader(new StringReader(saved)) { DateParseHandling = DateParseHandling.None, MaxDepth = 32 })
                    state = JsonSerializer.Create().Deserialize<PlayScenarioRun>(reader);
                if (state == null)
                    return;
                _engine = CreateEngine(state);
                _finalized = !_engine.Running;
                if (_engine.Running)
                {
                    Attach();
                    if (state.Phase != "starting")
                        _engine.Interrupt("Assembly reload interrupted scenario execution; effects were not replayed.", Now);
                    Synchronize(true);
                    if (_engine.Running && !state.PlayRequested)
                        EditorApplication.delayCall += RequestPlay;
                }
            }
            catch (Exception exception)
            {
                Detach();
                _failureCapture?.Dispose();
                _failureCapture = null;
                _logs = null;
                _engine?.Release();
                _engine = null;
                SessionState.EraseString(SessionKey);
                Debug.LogWarning("MCP play scenario restore failed: " + exception.Message);
            }
        }

        public static object Start(string name, int repeatCount, int timeoutSeconds, string jobId)
        {
            if (jobId != null)
            {
                PlayScenarioRun previous = Find(jobId);
                if (previous != null)
                {
                    if (
                        previous.Scenario.Name != name
                        || previous.RepeatCount != repeatCount
                        || previous.DeadlineUnixMs - previous.StartedUnixMs != timeoutSeconds * 1000L
                    )
                        return new ErrorResponse("job_id already belongs to a different run request.");
                    return new SuccessResponse("Existing scenario job.", Snapshot(previous));
                }
            }
            if ((_engine != null && (!_finalized || _engine.Running)) || _failureCapture?.Pending == true)
                return new ErrorResponse("play_scenario_busy", new { job_id = _engine.State.JobId });
            if (EditorApplication.isCompiling || EditorApplication.isUpdating || EditorApplication.isPlaying != EditorApplication.isPlayingOrWillChangePlaymode)
                return new ErrorResponse("Wait for the editor to finish compiling, importing or changing Play Mode.");
            PlayScenarioDefinition definition = Store.Get(name);
            var state = PlayScenarioEngine.Create(definition, jobId ?? Guid.NewGuid().ToString("N"), repeatCount, timeoutSeconds, Now);
            _engine = CreateEngine(state);
            _finalized = false;
            _suppressCapture = false;
            Attach();
            if (EditorApplication.isPlaying)
                _engine.EnteredPlayMode();
            Synchronize(true);
            // Return the job before a possible domain reload. No transport retry is needed.
            if (!EditorApplication.isPlaying)
                EditorApplication.delayCall += RequestPlay;
            return new SuccessResponse("Scenario job started.", Snapshot(state));
        }

        private static PlayScenarioEngine CreateEngine(PlayScenarioRun state)
        {
            _failureCapture?.Dispose();
            _failureCapture = null;
            _processedUnexpectedLogCount = 0;
            return new PlayScenarioEngine(state, new UnityPlayScenarioHost(state.Scenario.LogPolicy), CaptureFailure, DrainAndApplyErrors);
        }

        private static void CaptureFailure(PlayScenarioRun state, PlayScenarioStep step, long now)
        {
            if (state.FailureDiagnostics != null)
                return;
            // Consume the primary operation's logs before cleanup starts, so they are not
            // subsequently mislabeled as fresh cleanup failures.
            _logs?.DrainTo(state);
            _processedUnexpectedLogCount = state.UnexpectedLogCount;
            state.FailureDiagnostics = UnityPlayScenarioDiagnostics.DescribeFailure(state, step, now);
            _failureCapture = new PlayScenarioFailureCapture(state, Store, now, _suppressCapture);
        }

        private static void DrainAndApplyErrors()
        {
            if (_engine == null)
                return;
            _logs?.DrainTo(_engine.State);
            if (_engine.State.UnexpectedLogCount <= _processedUnexpectedLogCount)
                return;
            _processedUnexpectedLogCount = _engine.State.UnexpectedLogCount;
            _engine.ObserveUnexpectedError(_engine.State.LastUnexpectedLogError ?? _engine.State.UnexpectedLogError, Now);
        }

        public static object Status(string jobId)
        {
            PlayScenarioRun state = Find(jobId);
            return state == null ? new ErrorResponse("play_scenario_job_not_found") : new SuccessResponse("Scenario job status.", Snapshot(state));
        }

        public static object Cancel(string jobId)
        {
            if (_engine?.State.JobId == jobId)
            {
                DrainAndApplyErrors();
                _engine.Cancel(Now);
                Synchronize(true);
                return new SuccessResponse("Scenario job cancellation processed.", Snapshot(_engine.State));
            }
            return Status(jobId);
        }

        private static PlayScenarioRun Find(string jobId)
        {
            if (_engine?.State.JobId == jobId)
                return _engine.State;
            try
            {
                return Store.GetReport(jobId);
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

        private static JObject Snapshot(PlayScenarioRun state)
        {
            if (ReferenceEquals(state, _engine?.State))
                _logs?.DrainTo(state);
            var snapshot = JObject.FromObject(state, JsonSerializer.Create());
            if (ReferenceEquals(state, _engine?.State) && !_engine.Running && !_finalized)
            {
                // Publish a terminal outcome only after optional evidence and report saving finish.
                // Otherwise the Editor/CLI can stop observing before the final report exists.
                snapshot["pending_status"] = state.Status;
                snapshot["status"] = "running";
                snapshot["phase"] = "finalizing";
                snapshot["runner_resources_released"] = false;
            }
            return snapshot;
        }

        private static void RequestPlay()
        {
            if (_engine?.Running != true || _engine.State.Phase != "starting")
                return;
            if (Now >= _engine.State.DeadlineUnixMs)
            {
                Tick();
                return;
            }
            if (_engine.State.PlayRequested)
                return;
            _engine.State.PlayRequested = true;
            Synchronize(true);
            try
            {
                EditorApplication.isPlaying = true;
            }
            catch (Exception exception)
            {
                _engine.Interrupt("Could not enter Play Mode: " + exception.Message, Now);
                Synchronize(true);
            }
        }

        private static void Tick()
        {
            if (_engine == null)
                return;
            _failureCapture?.Tick(Now);
            DrainAndApplyErrors();
            if (_failureCapture?.Pending == true)
            {
                Synchronize(false);
                return;
            }
            if (_engine.Running)
            {
                try
                {
                    _engine.Tick(
                        Now,
                        EditorApplication.isPlaying && !EditorApplication.isPaused && !EditorApplication.isCompiling && !EditorApplication.isUpdating
                    );
                }
                catch (Exception exception)
                {
                    _engine.Interrupt("Scenario runner failed: " + exception.Message, Now);
                }
            }
            DrainAndApplyErrors();
            Synchronize(false);
        }

        private static void PlayModeChanged(PlayModeStateChange state)
        {
            if (_engine?.Running != true)
                return;
            if (state == PlayModeStateChange.EnteredPlayMode)
                _engine.EnteredPlayMode();
            else if (state == PlayModeStateChange.ExitingPlayMode || state == PlayModeStateChange.EnteredEditMode)
            {
                _suppressCapture = true;
                _failureCapture?.Stop("Play Mode ended before a screenshot became available.");
                _engine.Interrupt("Play Mode stopped before the scenario finished.", Now);
            }
            Synchronize(true);
        }

        private static void BeforeReload()
        {
            _suppressCapture = true;
            _failureCapture?.Stop("Assembly reload interrupted screenshot capture.");
            if (_engine?.Running == true && _engine.State.Phase != "starting")
                _engine.Interrupt("Assembly reload interrupted scenario execution; effects were not replayed.", Now);
            Synchronize(true);
            Detach();
            _engine?.Release();
        }

        private static void Quit()
        {
            _suppressCapture = true;
            _failureCapture?.Stop("Editor shutdown interrupted screenshot capture.");
            _engine?.Interrupt("Editor is quitting.", Now);
            Synchronize(true);
            Detach();
        }

        private static void Attach()
        {
            if (_attached)
                return;
            _attached = true;
            _logs = new PlayScenarioLogBuffer(_engine.State.Scenario.LogPolicy);
            PlayScenarioLogBuffer capture = _logs;
            _logCallback = (message, stack, type) => capture.Add(Now, type.ToString(), message, stack);
            Application.logMessageReceivedThreaded += _logCallback;
            EditorApplication.update += Tick;
            EditorApplication.playModeStateChanged += PlayModeChanged;
            AssemblyReloadEvents.beforeAssemblyReload += BeforeReload;
            EditorApplication.quitting += Quit;
        }

        private static void Detach()
        {
            if (!_attached)
                return;
            _attached = false;
            Application.logMessageReceivedThreaded -= _logCallback;
            _logCallback = null;
            _logs?.Close();
            EditorApplication.update -= Tick;
            EditorApplication.delayCall -= RequestPlay;
            EditorApplication.playModeStateChanged -= PlayModeChanged;
            AssemblyReloadEvents.beforeAssemblyReload -= BeforeReload;
            EditorApplication.quitting -= Quit;
        }

        private static void Synchronize(bool force)
        {
            if (_engine == null)
                return;
            DrainAndApplyErrors();
            long now = Now;
            if (!_engine.Running && !_finalized)
            {
                _logs?.Close();
                DrainAndApplyErrors();
                if (_failureCapture?.Pending == true)
                    return;
                Detach();
                _failureCapture?.Dispose();
                _failureCapture = null;
                _logs = null;
                _engine.Release();
                _engine.State.RunnerResourcesReleased = _engine.State.RunnerResourcesReleased != false && !_attached && _logCallback == null;
                _finalized = true;
                try
                {
                    _engine.State.ReportPath = Store.SaveReport(_engine.State);
                }
                catch (Exception exception)
                {
                    _engine.State.ReportError = PlayScenarioEngine.Bounded(exception.Message, 2048);
                }
                force = true;
            }
            if (!force && _savedRevision == _engine.Revision && now < _checkpointAt)
                return;
            _logs?.DrainTo(_engine.State);
            SessionState.SetString(SessionKey, JObject.FromObject(_engine.State, JsonSerializer.Create()).ToString(Formatting.None));
            _savedRevision = _engine.Revision;
            _checkpointAt = now + 1000;
        }
    }
}
