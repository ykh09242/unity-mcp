using System;
using System.Collections;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Reflection;
using System.Text;
using MCPForUnity.Editor.Services;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEditor;
using UnityEngine;
using UnityEngine.TestTools;
using NativeApi = UnityEditor.TestTools.TestRunner.Api.TestRunnerApi;

namespace MCPForUnityTests.Editor.Services
{
    /// <summary>QA-only outside-runner coordinator. Never combine its executeMethod with -runTests.</summary>
    [InitializeOnLoad]
    public static class TestJobCancellationScenario
    {
        private const string Key = "MCPForUnity.Play03.ActualScenarioV1";
        private const string Prefix = "MCPForUnityTests.Editor.Services.TestJobCancellationScenarioFixture.";
        private const string PlayPrefix = "MCPForUnityTests.PlayMode.TestJobCancellationProbeFixture.";
        private const BindingFlags Hidden = BindingFlags.Static | BindingFlags.NonPublic;
        private static readonly string ManagedNonce = Guid.NewGuid().ToString("N");
        private static readonly string[] Cases =
        {
            "edit_cancel",
            "play_cancel",
            "edit_deadline",
            "play_deadline",
            "edit_reload_cancel",
            "edit_reload_deadline",
        };
        private static JObject state;
        private static NativeTestRunControl witness;
        private static string initializationError;
        private static int turn;
        private static int witnessedTurn = -1;
        private static bool projectAuthorized;
        private static bool backendVerified;
        private static bool journalOwned;

        static TestJobCancellationScenario()
        {
            if (string.IsNullOrWhiteSpace(Environment.GetEnvironmentVariable("UNITY_MCP_PLAY03_SCENARIO")))
                return;
            try
            {
                Guard();
                string saved = SessionState.GetString(Key, "");
                if (saved.Length == 0)
                    return;
                if (Encoding.UTF8.GetByteCount(saved) > 65536)
                    throw new InvalidOperationException("Scenario journal exceeds its byte bound.");
                using var reader = new JsonTextReader(new StringReader(saved)) { MaxDepth = 16, DateParseHandling = DateParseHandling.None };
                state = JObject.Load(reader, new JsonLoadSettings { DuplicatePropertyNameHandling = DuplicatePropertyNameHandling.Error });
                if (
                    reader.Read()
                    || (string)state["nonce"] != Nonce()
                    || (string)state["project"] != Project()
                    || (string)state["case"] != Case()
                    || (bool?)state["before_reload"] != true
                )
                    throw new InvalidOperationException("Scenario reload ownership is not established.");
                ValidateJournal();
                journalOwned = true;
                if ((string)state["managed_before_reload"] == ManagedNonce)
                    throw new InvalidOperationException("Scenario requires a changed managed nonce after reload.");
                var job = Job((string)state["job_id"]);
                if (
                    job?.Control?.NativeGuid != (string)state["native_guid"]
                    || !JToken.DeepEquals(job.NativeControl["deadline_uptime"], state["original_deadline"])
                )
                    throw new InvalidOperationException("Native GUID/deadline continuity was not retained.");
                state["before_reload"] = false;
                state["actual_reload_observed"] = true;
                Record("fresh_managed_domain_same_native_guid_and_deadline");
                BindWitness(job.Control.NativeGuid);
                Subscribe();
                Persist();
            }
            catch (Exception error)
            {
                initializationError = error.GetType().Name;
                Abort(error);
            }
        }

        public static void Run()
        {
            try
            {
                Guard();
                if (initializationError != null)
                    throw new InvalidOperationException("Scenario initialization failed.");
                if (Array.Exists(Environment.GetCommandLineArgs(), value => string.Equals(value, "-runTests", StringComparison.OrdinalIgnoreCase)))
                    throw new InvalidOperationException("Scenario cannot be nested in a command-line NUnit run.");
                if (state != null)
                {
                    Record("execute_method_reentry_did_not_dispatch_again");
                    Persist();
                    return;
                }
                if (
                    !NativeIdle()
                    || TestJobManager.NativeAdmissionBlocked
                    || TestJobManager.HasRunningJob
                    || PlayModeOptionsGuard.IsPending
                    || SessionState.GetBool("TestRunnerNoThrottle_TestRunActive", false)
                    || SessionState.GetBool("TestRunnerNoThrottle_SettingsCaptured", false)
                )
                    throw new InvalidOperationException("Scenario requires a fresh inactive owned project/session.");
                if (!NativeTestRunControl.Available || NativeTestRunControl.DisableForTests || NativeTestRunControl.FactoryOverride != null)
                    throw new InvalidOperationException("Scenario requires actual exact-version native control without test overrides.");
                Require(
                    ((Dictionary<string, TestJob>)typeof(TestJobManager).GetField("Jobs", Hidden).GetValue(null)).Count == 0,
                    "Scenario requires an empty fresh managed job table."
                );
                Require(
                    string.IsNullOrEmpty(SessionState.GetString("MCPForUnity.CurrentTestJobIdV1", ""))
                        && string.IsNullOrEmpty(SessionState.GetString("MCPForUnity.TestRunner.RetiredRunJobId", "")),
                    "Scenario requires no durable current/retired owner."
                );
                TestJobCancellationProbe.VerifyOwnedBackendPositiveControl();
                string journal = SessionState.GetString("MCPForUnity.TestJobsV1", "");
                if (Encoding.UTF8.GetByteCount(journal) > 49152)
                    throw new InvalidOperationException("Existing job history exceeds its supported bound.");
                state = new JObject
                {
                    ["version"] = 1,
                    ["nonce"] = Nonce(),
                    ["project"] = Project(),
                    ["case"] = Case(),
                    ["started_uptime"] = Uptime(),
                    ["watchdog_uptime"] = Uptime() + 180,
                    ["events"] = new JArray(),
                    ["dispatches"] = 0,
                    ["public_polls_before_terminal"] = 0,
                    ["user_cleanup_guaranteed"] = false,
                    ["user_cleanup_instrumented"] = false,
                    ["options_enabled"] = EditorSettings.enterPlayModeOptionsEnabled,
                    ["options"] = (int)EditorSettings.enterPlayModeOptions,
                    ["service_was_absent"] = typeof(MCPServiceLocator).GetField("_testRunnerService", Hidden).GetValue(null) == null,
                    ["backups"] = CaptureStrings(),
                    ["guard_pending"] = SessionState.GetBool("MCPForUnity.PlayModeOptions.PendingRestore", false),
                    ["guard_enabled"] = SessionState.GetBool("MCPForUnity.PlayModeOptions.OriginalEnabled", false),
                    ["guard_options"] = SessionState.GetInt("MCPForUnity.PlayModeOptions.OriginalOptions", 0),
                };
                string evidence = OwnedPath("scenario-evidence.json");
                if (File.Exists(evidence))
                    throw new InvalidOperationException("Scenario evidence must start in a fresh owned nonce directory.");
                journalOwned = true;
                CaptureMarker();
                Record("pre_dispatch_owned_backend_and_native_idle_verified");
                Persist();
                Start(false);
                Subscribe();
            }
            catch (Exception error)
            {
                Abort(error);
            }
        }

        private static string Case()
        {
            string value = Environment.GetEnvironmentVariable("UNITY_MCP_PLAY03_SCENARIO");
            if (!Array.Exists(Cases, item => item == value))
                throw new InvalidOperationException("Scenario case is not supported by this bounded coordinator.");
            return value;
        }

        private static string Nonce() => Environment.GetEnvironmentVariable("UNITY_MCP_PLAY03_QA_NONCE");

        private static string Project() => Path.GetFullPath(Path.Combine(Application.dataPath, "..")).TrimEnd(Path.DirectorySeparatorChar);

        private static double Uptime() => EditorApplication.timeSinceStartup;

        private static bool Deadline => Case().EndsWith("deadline", StringComparison.Ordinal);
        private static bool Reload => Case().Contains("_reload_");
        private static bool Play => Case().StartsWith("play_", StringComparison.Ordinal);

        private static void Guard()
        {
            string requested = Environment.GetEnvironmentVariable("UNITY_MCP_PLAY03_QA_PROJECT");
            if (
                !Guid.TryParseExact(Nonce(), "N", out _)
                || string.IsNullOrWhiteSpace(requested)
                || !string.Equals(Project(), Path.GetFullPath(requested).TrimEnd(Path.DirectorySeparatorChar), StringComparison.OrdinalIgnoreCase)
            )
                throw new InvalidOperationException("Scenario requires its exact owned project and nonce.");
            Case();
            projectAuthorized = true;
            backendVerified = false;
            // Read only this already-reviewed QA bootstrap. Never alter native success witnesses.
            var type = typeof(TestJobCancellationProbe);
            if (
                (bool)type.GetField("installed", Hidden).GetValue(null) != true
                || !ReferenceEquals(TestRunnerNoThrottle.ReadPreference, type.GetField("ownedRead", Hidden).GetValue(null))
                || !ReferenceEquals(TestRunnerNoThrottle.WritePreference, type.GetField("ownedWrite", Hidden).GetValue(null))
                || !ReferenceEquals(TestRunnerNoThrottle.ApplyInteractionPreferences, type.GetField("ownedApply", Hidden).GetValue(null))
                || typeof(TestRunnerNoThrottle).GetField("_api", Hidden).GetValue(null) != null
            )
                throw new InvalidOperationException("Reviewed owned backend must precede every native dispatch.");
            var backend = (JObject)type.GetField("state", Hidden).GetValue(null);
            if ((string)backend["nonce"] != Nonce() || (string)backend["project"] != Project())
                throw new InvalidOperationException("Backend ownership marker differs from scenario.");
            backendVerified = true;
        }

        internal static string OwnedPath(string name)
        {
            string root = Project();
            string directory = Path.Combine(root, "Library", "Play03Qa", Nonce());
            if (!Directory.Exists(directory))
                throw new InvalidOperationException("Reviewed bootstrap must create the owned evidence directory.");
            string path = Path.GetFullPath(Path.Combine(directory, name));
            if (!path.StartsWith(directory + Path.DirectorySeparatorChar, StringComparison.OrdinalIgnoreCase))
                throw new InvalidOperationException("Scenario evidence escapes its nonce directory.");
            CheckLinks(root, path);
            return path;
        }

        private static void CheckLinks(string root, string path)
        {
            if (!Path.GetFullPath(path).StartsWith(root + Path.DirectorySeparatorChar, StringComparison.OrdinalIgnoreCase))
                throw new InvalidOperationException("Scenario path escapes its owned project.");
            for (string current = path; !string.Equals(current, root, StringComparison.OrdinalIgnoreCase); current = Path.GetDirectoryName(current))
                if ((File.Exists(current) || Directory.Exists(current)) && (File.GetAttributes(current) & FileAttributes.ReparsePoint) != 0)
                    throw new InvalidOperationException("Scenario refuses reparse paths.");
            if ((File.GetAttributes(root) & FileAttributes.ReparsePoint) != 0)
                throw new InvalidOperationException("Scenario refuses a linked project root.");
        }

        private static TestJob Job(string id)
        {
            var jobs = (Dictionary<string, TestJob>)typeof(TestJobManager).GetField("Jobs", Hidden).GetValue(null);
            return id != null && jobs.TryGetValue(id, out var job) ? job : null;
        }

        private static bool NativeIdle()
        {
            if ((bool)typeof(NativeApi).GetMethod("IsRunActive", Hidden).Invoke(null, null))
                return false;
            object holder = typeof(NativeApi).GetProperty("m_testJobDataHolder", Hidden).GetValue(null);
            if (((Array)holder.GetType().GetMethod("GetAllRunners").Invoke(holder, null)).Length != 0)
                return false;
            int count = 0;
            foreach (object row in (IEnumerable)holder.GetType().GetField("TestRuns").GetValue(holder))
                if (++count > 32 || (bool)row.GetType().GetField("isRunning").GetValue(row))
                    return false;
            return true;
        }

        private static void Start(bool replacement)
        {
            Guard();
            string prefix = Play ? PlayPrefix : Prefix;
            var names = replacement
                ? new JArray(prefix + "ReplacementCompletes")
                : new JArray(prefix + "CooperatesUntilCancelled", prefix + "SentinelMustRemainNotRun");
            var request = new JObject
            {
                ["mode"] = Play ? "PlayMode" : "EditMode",
                ["testNames"] = names,
                ["initTimeout"] = 60000,
            };
            if (!replacement && Deadline)
                request["runTimeoutMs"] =
                    Play ? 30000
                    : Reload ? 10000
                    : 10000;
            state["dispatches"] = (int)state["dispatches"] + 1;
            Record(replacement ? "replacement_public_run_dispatch" : "original_public_run_dispatch");
            Persist();
            var response = JObject.FromObject(MCPForUnity.Editor.Tools.RunTests.HandleCommand(request).GetAwaiter().GetResult());
            Require((bool?)response["success"] == true, "Public run handler did not accept the owned fixture.");
            string id = (string)response["data"]["job_id"];
            state[replacement ? "replacement_job_id" : "job_id"] = id;
            state["phase"] = replacement ? "replacement" : "running";
            witnessedTurn = -1;
            var job = Job(id);
            if (job?.Control?.NativeGuid != null)
                Attach(job, replacement);
            Persist();
        }

        private static void Attach(TestJob job, bool replacement)
        {
            Require(job.Control.NativeGuid != null, "A real native GUID must be captured.");
            state[replacement ? "replacement_native_guid" : "native_guid"] = job.Control.NativeGuid;
            if (!replacement)
                state["original_deadline"] = job.NativeControl["deadline_uptime"].DeepClone();
            BindWitness(job.Control.NativeGuid);
            Record(replacement ? "replacement_native_guid_captured" : "original_native_guid_captured");
        }

        private static void BindWitness(string guid)
        {
            witness?.Dispose();
            witness = NativeTestRunControl.Create(Guid.NewGuid().ToString("N"), 0);
            Require(witness != null, "Passive native final-progress binding is unavailable.");
            witness.BindGuid(guid);
        }

        private static void Subscribe()
        {
            EditorApplication.update -= Tick;
            EditorApplication.update += Tick;
            AssemblyReloadEvents.beforeAssemblyReload -= BeforeReload;
            AssemblyReloadEvents.beforeAssemblyReload += BeforeReload;
        }

        private static void Tick()
        {
            try
            {
                Guard();
                turn++;
                Require(Uptime() < (double)state["watchdog_uptime"], "Owned QA watchdog expired; this is containment, not native cancellation success.");
                bool replacement = (string)state["phase"] == "replacement";
                var job = Job((string)state[replacement ? "replacement_job_id" : "job_id"]);
                Require(job?.Control != null, "Native managed owner is unavailable.");
                if (witness == null && job.Control.NativeGuid != null)
                    Attach(job, replacement);
                if (
                    !replacement
                    && File.Exists(OwnedPath("started.txt"))
                    && File.Exists(OwnedPath("inside-try.txt"))
                    && File.Exists(OwnedPath("yield-ready.txt"))
                )
                {
                    if ((bool?)state["fixture_started"] != true)
                    {
                        state["fixture_started"] = true;
                        state["user_cleanup_instrumented"] = true;
                        Record("actual_fixture_start_observed");
                        Record("owned_in_try_and_yield_ready_observed");
                        Persist();
                    }
                    if (Reload && (bool?)state["reload_requested"] != true)
                    {
                        state["reload_requested"] = true;
                        File.WriteAllText(OwnedPath("reload-requested.txt"), "requested", new UTF8Encoding(false));
                        Record("public_script_reload_requested_outside_runner");
                        Persist();
                        EditorUtility.RequestScriptReload();
                        return;
                    }
                    if (
                        !Deadline
                        && (bool?)state["cancel_requested"] != true
                        && (!Reload || (bool?)state["actual_reload_observed"] == true && File.Exists(OwnedPath("resumed.txt")))
                    )
                    {
                        state["cancel_requested"] = true;
                        var response = JObject.FromObject(MCPForUnity.Editor.Tools.CancelTestJob.HandleCommand(new JObject { ["job_id"] = job.JobId }));
                        Require(
                            (bool?)response["success"] == true && (string)response["data"]["native_control"]["request_state"] == "accepted",
                            "Actual public native cancel was not accepted."
                        );
                        state["cancel_response"] = response;
                        Record("public_cancel_accepted_for_captured_guid");
                        RejectReplacement(job);
                        Persist();
                    }
                    if (Deadline && job.Control.Requested && (bool?)state["deadline_observed"] != true)
                    {
                        Require(
                            (string)job.NativeControl["reason"] == "deadline" && job.Control.CancelAccepted,
                            "Deadline did not obtain an accepted native stop intent."
                        );
                        Require((int)state["public_polls_before_terminal"] == 0, "Deadline must not depend on public polling.");
                        state["deadline_observed"] = true;
                        Record("poll_free_deadline_stop_observed");
                        RejectReplacement(job);
                        Persist();
                    }
                }
                if ((bool?)witness?.State["final_witness"] != true)
                    return;
                if (witnessedTurn < 0)
                {
                    witnessedTurn = turn;
                    Record("matching_post_dispose_native_progress_observed");
                    Persist();
                    return;
                }
                if (
                    turn <= witnessedTurn
                    || !NativeIdle()
                    || !job.Control.Released
                    || EditorApplication.isPlayingOrWillChangePlaymode
                    || EditorApplication.isPlaying
                )
                    return;
                if (
                    EditorSettings.enterPlayModeOptionsEnabled != (bool)state["options_enabled"]
                    || EditorSettings.enterPlayModeOptions != (EnterPlayModeOptions)(int)state["options"]
                )
                    return;
                Record("later_main_thread_coherent_idle_and_project_options_restored");
                var status = JObject.FromObject(MCPForUnity.Editor.Tools.GetTestJob.HandleCommand(new JObject { ["job_id"] = job.JobId }));
                Require((bool?)status["success"] == true, "Public terminal poll was unavailable.");
                if (replacement)
                {
                    Require(job.Status == TestJobStatus.Succeeded && File.Exists(OwnedPath("replacement.txt")), "Replacement did not genuinely pass.");
                    state["replacement_public_status"] = status;
                    Require((int)state["dispatches"] == 2, "Exactly one original and one replacement dispatch are required.");
                    state["execution_contract_passed"] = true;
                    state["user_cleanup"] = ObserveUserCleanup();
                    state["teardown_observed"] = File.Exists(OwnedPath("teardown.txt"));
                    Record("native_execution_contract_complete");
                    Finish();
                }
                else
                {
                    Require(
                        (bool?)state["fixture_started"] == true && (bool?)state["replacement_rejected"] == true,
                        "Running fixture/rejection evidence is missing."
                    );
                    Require(!File.Exists(OwnedPath("sentinel.txt")), "The remaining sentinel executed despite native cancellation.");
                    Require(
                        Deadline ? job.Status == TestJobStatus.Failed && job.CompletionKind == "deadline_exceeded" : job.Status == TestJobStatus.Cancelled,
                        "Terminal status does not truthfully match the native stop."
                    );
                    Require(!Reload || (bool?)state["actual_reload_observed"] == true, "A real managed reload was not observed.");
                    state["original_public_status"] = status;
                    state["teardown_observed"] = File.Exists(OwnedPath("teardown.txt"));
                    state["user_cleanup_at_original_idle"] = ObserveUserCleanup();
                    witness.Dispose();
                    witness = null;
                    Start(true);
                }
            }
            catch (Exception error)
            {
                Abort(error);
            }
        }

        private static void RejectReplacement(TestJob owned)
        {
            Require(!owned.Control.Released, "Stop must not be treated as finished at request receipt.");
            string prefix = Play ? PlayPrefix : Prefix;
            var rejected = JObject.FromObject(
                MCPForUnity
                    .Editor.Tools.RunTests.HandleCommand(
                        new JObject { ["mode"] = Play ? "PlayMode" : "EditMode", ["testNames"] = new JArray(prefix + "ReplacementCompletes") }
                    )
                    .GetAwaiter()
                    .GetResult()
            );
            Require(
                (bool?)rejected["success"] == false && (string)rejected["error"] == "tests_running",
                "Replacement must be rejected while native cleanup is unconfirmed."
            );
            state["replacement_rejected"] = true;
            Record("replacement_rejected_before_confirmed_termination");
        }

        private static void BeforeReload()
        {
            state["before_reload"] = true;
            state["managed_before_reload"] = ManagedNonce;
            state["before_reload_count"] = ((int?)state["before_reload_count"] ?? 0) + 1;
            Record("before_assembly_reload_witness");
            Persist();
            witness?.Dispose();
        }

        private static JObject CaptureStrings()
        {
            var result = new JObject();
            foreach (
                string key in new[]
                {
                    "MCPForUnity.TestJobsV1",
                    "MCPForUnity.CurrentTestJobIdV1",
                    "MCPForUnity.TestRunner.RetiredRunJobId",
                    "MCPForUnity.NativeTestControlSessionV1",
                    "MCPForUnity.TestResultSessionV1",
                }
            )
            {
                string missing = "missing-" + Nonce();
                string value = SessionState.GetString(key, missing);
                result[key] = new JObject { ["had"] = value != missing, ["value"] = value == missing ? "" : value };
            }
            return result;
        }

        private static void CaptureMarker()
        {
            string path = Path.Combine(Project(), "Library", "MCPPlayModeOptionsBackup.txt");
            CheckLinks(Project(), path);
            state["marker_had"] = File.Exists(path);
            if (File.Exists(path))
            {
                Require(new FileInfo(path).Length <= 256, "Owned options marker exceeds its supported bound.");
                state["marker_bytes"] = Convert.ToBase64String(File.ReadAllBytes(path));
            }
        }

        private static void Finish()
        {
            Require(NativeIdle() && !TestJobManager.NativeAdmissionBlocked, "Success cleanup requires native idle.");
            EditorApplication.update -= Tick;
            AssemblyReloadEvents.beforeAssemblyReload -= BeforeReload;
            witness?.Dispose();
            witness = null;
            if ((bool)state["service_was_absent"] && typeof(MCPServiceLocator).GetField("_testRunnerService", Hidden).GetValue(null) is IDisposable service)
            {
                service.Dispose();
                typeof(MCPServiceLocator).GetField("_testRunnerService", Hidden).SetValue(null, null);
            }
            var jobs = (Dictionary<string, TestJob>)typeof(TestJobManager).GetField("Jobs", Hidden).GetValue(null);
            foreach (var job in jobs.Values)
                job.Control?.Dispose();
            jobs.Clear();
            typeof(TestJobManager).GetField("_currentJobId", Hidden).SetValue(null, null);
            foreach (var entry in ((JObject)state["backups"]).Properties())
                if ((bool)entry.Value["had"])
                    SessionState.SetString(entry.Name, (string)entry.Value["value"]);
                else
                    SessionState.EraseString(entry.Name);
            typeof(TestJobManager).GetMethod("TryRestoreFromSessionState", Hidden).Invoke(null, null);
            SessionState.SetBool("MCPForUnity.PlayModeOptions.PendingRestore", (bool)state["guard_pending"]);
            SessionState.SetBool("MCPForUnity.PlayModeOptions.OriginalEnabled", (bool)state["guard_enabled"]);
            SessionState.SetInt("MCPForUnity.PlayModeOptions.OriginalOptions", (int)state["guard_options"]);
            EditorSettings.enterPlayModeOptionsEnabled = (bool)state["options_enabled"];
            EditorSettings.enterPlayModeOptions = (EnterPlayModeOptions)(int)state["options"];
            string marker = Path.Combine(Project(), "Library", "MCPPlayModeOptionsBackup.txt");
            CheckLinks(Project(), marker);
            if ((bool)state["marker_had"])
                File.WriteAllBytes(marker, Convert.FromBase64String((string)state["marker_bytes"]));
            else if (File.Exists(marker))
                File.Delete(marker);
            Record("owned_project_state_restored_before_backend_defaults");
            Persist();
            SessionState.EraseString(Key);
            TestJobCancellationProbe.Cleanup();
            EditorApplication.Exit(0);
        }

        private static void Abort(Exception error)
        {
            Debug.LogError("PLAY03 actual scenario refused/failed: " + error.GetType().Name);
            // An accidental invocation in an unrelated Editor must not mutate or close it.
            if (!projectAuthorized)
                return;
            EditorApplication.update -= Tick;
            AssemblyReloadEvents.beforeAssemblyReload -= BeforeReload;
            try
            {
                witness?.Dispose();
            }
            catch { }
            // Keep fake delegates installed while native work may still be unwinding.
            EditorApplication.quitting -= TestJobCancellationProbe.Cleanup;
            if (journalOwned)
            {
                try
                {
                    state["execution_contract_passed"] = false;
                    state["user_cleanup"] = ObserveUserCleanup();
                    state["error_type"] = error.GetType().Name;
                    Record("failed_containment_kept_owned_backend_installed");
                    Persist();
                }
                catch { }
            }
            if (!backendVerified)
            {
                // Only the exact opted-in disposable QA process reaches here. A delayed exit
                // could let a resumed framework run invoke an unverified preference backend.
                Environment.Exit(2);
                return;
            }
            EditorApplication.Exit(2);
        }

        private static void ValidateJournal()
        {
            Require(state["version"]?.Type == JTokenType.Integer && (int)state["version"] == 1, "Scenario journal version is unsupported.");
            Require(state["events"] is JArray events && events.Count <= 80, "Scenario journal event bound is invalid.");
            Require(state["started_uptime"]?.Type == JTokenType.Float || state["started_uptime"]?.Type == JTokenType.Integer, "Scenario start is invalid.");
            Require(
                state["watchdog_uptime"]?.Type == JTokenType.Float || state["watchdog_uptime"]?.Type == JTokenType.Integer,
                "Scenario watchdog is invalid."
            );
            double start = (double)state["started_uptime"],
                end = (double)state["watchdog_uptime"];
            Require(
                !double.IsNaN(start)
                    && !double.IsInfinity(start)
                    && !double.IsNaN(end)
                    && !double.IsInfinity(end)
                    && start >= 0
                    && end >= start
                    && end - start <= 180.1
                    && Uptime() < end,
                "Scenario watchdog is not a finite same-session bound."
            );
            Require(
                state["backups"] is JObject
                    && state["guard_pending"]?.Type == JTokenType.Boolean
                    && state["guard_enabled"]?.Type == JTokenType.Boolean
                    && state["guard_options"]?.Type == JTokenType.Integer,
                "Scenario restoration metadata is invalid."
            );
        }

        private static JObject ObserveUserCleanup()
        {
            bool instrumented = (bool?)state["user_cleanup_instrumented"] == true;
            string Observation(string name) =>
                !instrumented ? "not_instrumented"
                : File.Exists(OwnedPath(name)) ? "observed"
                : "not_observed";
            return new JObject
            {
                ["iterator_finally"] = Observation("teardown.txt"),
                ["nunit_teardown"] = Observation("nunit-teardown.txt"),
                ["unity_teardown"] = Observation("unity-teardown.txt"),
            };
        }

        private static void Record(string name)
        {
            var events = (JArray)state["events"];
            Require(events.Count < 80, "Scenario event limit exceeded.");
            events.Add(
                new JObject
                {
                    ["sequence"] = events.Count + 1,
                    ["event"] = name,
                    ["uptime"] = Uptime(),
                    ["managed_nonce"] = ManagedNonce,
                }
            );
        }

        private static void Persist()
        {
            string text = state.ToString(Formatting.None);
            Require(Encoding.UTF8.GetByteCount(text) <= 65536, "Scenario journal byte limit exceeded.");
            SessionState.SetString(Key, text);
            File.WriteAllText(OwnedPath("scenario-evidence.json"), text, new UTF8Encoding(false));
        }

        private static void Require(bool condition, string message)
        {
            if (!condition)
                throw new InvalidOperationException(message);
        }

        internal static void Mark(string name)
        {
            string path = OwnedPath(name);
            Require(!File.Exists(path), "Owned fixture lifecycle witness must be emitted once.");
            File.WriteAllText(path, "observed", new UTF8Encoding(false));
        }
    }

    public sealed class TestJobCancellationScenarioFixture
    {
        private static double deadline = double.NaN;
        private static int frames;

        [UnityTest, Explicit("Only the separately approved outside-runner PLAY03 coordinator may select this fixture."), Order(0)]
        public IEnumerator CooperatesUntilCancelled()
        {
            Start();
            try
            {
                TestJobCancellationScenario.Mark("inside-try.txt");
                while (Continue())
                {
                    if (
                        File.Exists(TestJobCancellationScenario.OwnedPath("reload-requested.txt"))
                        && !File.Exists(TestJobCancellationScenario.OwnedPath("resumed.txt"))
                    )
                    {
                        yield return new WaitForDomainReload();
                        TestJobCancellationScenario.Mark("resumed.txt");
                    }
                    if (!File.Exists(TestJobCancellationScenario.OwnedPath("yield-ready.txt")))
                        TestJobCancellationScenario.Mark("yield-ready.txt");
                    yield return null;
                }
                Assert.Fail("Cooperative fixture reached its finite bound without native cancellation.");
            }
            finally
            {
                TestJobCancellationScenario.Mark("teardown.txt");
            }
        }

        private static bool IsCooperatorContext() =>
            string.Equals(
                NUnit.Framework.TestContext.CurrentContext.Test.FullName,
                typeof(TestJobCancellationScenarioFixture).FullName + "." + nameof(CooperatesUntilCancelled),
                StringComparison.Ordinal
            );

        [TearDown]
        public void ObserveNUnitTearDown()
        {
            if (IsCooperatorContext())
                TestJobCancellationScenario.Mark("nunit-teardown.txt");
        }

        [UnityTearDown]
        public IEnumerator ObserveUnityTearDown()
        {
            if (IsCooperatorContext())
                TestJobCancellationScenario.Mark("unity-teardown.txt");
            yield break;
        }

        [Test, Explicit("Cancellation must leave the remaining selected sentinel NotRun."), Order(1)]
        public void SentinelMustRemainNotRun()
        {
            TestJobCancellationScenario.Mark("sentinel.txt");
            Assert.Fail("Native cancellation did not stop the sentinel.");
        }

        [Test, Explicit("Only confirmed native idle may admit the replacement.")]
        public void ReplacementCompletes() => TestJobCancellationScenario.Mark("replacement.txt");

        private static void Start()
        {
            string path = TestJobCancellationScenario.OwnedPath("started.txt");
            Assert.IsFalse(File.Exists(path), "Reload must not restart Execute/the fixture.");
            deadline = EditorApplication.timeSinceStartup + 120;
            File.WriteAllText(path, deadline.ToString("R", CultureInfo.InvariantCulture), new UTF8Encoding(false));
            frames = 0;
        }

        private static bool Continue()
        {
            if (double.IsNaN(deadline))
            {
                using var input = new FileStream(TestJobCancellationScenario.OwnedPath("started.txt"), FileMode.Open, FileAccess.Read, FileShare.Read);
                byte[] bytes = new byte[65];
                int count = 0;
                while (count < bytes.Length)
                {
                    int read = input.Read(bytes, count, bytes.Length - count);
                    if (read == 0)
                        break;
                    count += read;
                }
                Assert.LessOrEqual(count, 64);
                Assert.IsTrue(
                    double.TryParse(new UTF8Encoding(false, true).GetString(bytes, 0, count), NumberStyles.Float, CultureInfo.InvariantCulture, out deadline)
                );
                Assert.IsFalse(double.IsNaN(deadline) || double.IsInfinity(deadline));
            }
            return ++frames <= 60000 && EditorApplication.timeSinceStartup < deadline;
        }
    }
}
