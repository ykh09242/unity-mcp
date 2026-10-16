using System;
using System.Collections.Generic;
using System.IO;
using System.Reflection;
using System.Text;
using MCPForUnity.Editor.Services;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEditor.TestTools.TestRunner.Api;
using UnityEngine;

namespace MCPForUnityTests.Editor.Services
{
    /// <summary>
    /// Explicit, disposable-project-only preference isolation. This is not a nested test runner.
    /// QA must supply a fresh owned project/session and opt in before starting its outer runner.
    /// </summary>
    [InitializeOnLoad]
    public static class TestJobCancellationProbe
    {
        private const string StateKey = "MCPForUnity.Play03.QaBackendV1";
        private const string ActiveKey = "TestRunnerNoThrottle_TestRunActive";
        private const string CapturedKey = "TestRunnerNoThrottle_SettingsCaptured";
        private const string IdleKey = "TestRunnerNoThrottle_PrevIdleTime";
        private const string ModeKey = "TestRunnerNoThrottle_PrevInteractionMode";
        private const BindingFlags Hidden = BindingFlags.Static | BindingFlags.NonPublic;
        private static readonly string ManagedNonce = System.Guid.NewGuid().ToString("N");
        private static readonly Dictionary<string, int> Preferences = new Dictionary<string, int>();
        private static Func<string, int, int> originalRead;
        private static Action<string, int> originalWrite;
        private static Action originalApply;
        private static Func<string, int, int> ownedRead;
        private static Action<string, int> ownedWrite;
        private static Action ownedApply;
        private static JObject state;
        private static string evidence;
        private static TestRunnerApi api;
        private static Observer observer;
        private static bool installed;
        private static string initializationFailure;
        private static NativeTestRunControl outerWitness;
        private static int turn;
        private static int finalTurn = -1;
        private static double outerDeadline;
        private const string NativeSessionKey = "MCPForUnity.NativeTestControlSessionV1";

        static TestJobCancellationProbe()
        {
            try
            {
                InitializeOwnedBackend();
            }
            catch (Exception error)
            {
                // Keep executeMethod callable so it can refuse execution and exit nonzero.
                // A throwing static constructor alone does not stop Unity's command-line runner.
                initializationFailure = error.GetType().Name;
                Debug.LogError("PLAY03 QA backend initialization refused: " + initializationFailure);
            }
        }

        private static void InitializeOwnedBackend()
        {
            string requested = Environment.GetEnvironmentVariable("UNITY_MCP_PLAY03_QA_PROJECT");
            string nonce = Environment.GetEnvironmentVariable("UNITY_MCP_PLAY03_QA_NONCE");
            if (string.IsNullOrWhiteSpace(requested) || !System.Guid.TryParseExact(nonce, "N", out _))
                return;
            string project = Path.GetFullPath(Path.Combine(Application.dataPath, "..")).TrimEnd(Path.DirectorySeparatorChar);
            if (!string.Equals(project, Path.GetFullPath(requested).TrimEnd(Path.DirectorySeparatorChar), StringComparison.OrdinalIgnoreCase))
                return;
            // Initial launch must use a fresh/verified inactive session. Before reload our own hook
            // clears these flags so TestRunnerNoThrottle's new static constructor cannot touch prefs.
            if (SessionState.GetBool(ActiveKey, false) || SessionState.GetBool(CapturedKey, false))
                throw new InvalidOperationException("PLAY03 QA requires inactive no-throttle flags before initialization.");
            string saved = SessionState.GetString(StateKey, "");
            if (Encoding.UTF8.GetByteCount(saved) > 32768)
                throw new InvalidOperationException("PLAY03 QA state is oversized.");
            state = string.IsNullOrEmpty(saved) ? null : JObject.Parse(saved);
            bool resumed = state != null;
            if (resumed && ((string)state["nonce"] != nonce || (string)state["project"] != project || (bool?)state["before_reload"] != true))
                throw new InvalidOperationException("PLAY03 QA ownership/resume marker does not match.");
            if (!resumed)
                state = new JObject
                {
                    ["version"] = 1,
                    ["nonce"] = nonce,
                    ["project"] = project,
                    ["initial_idle"] = SessionState.GetInt(IdleKey, 4),
                    ["initial_mode"] = SessionState.GetInt(ModeKey, 0),
                    ["native_session"] = SessionState.GetString(NativeSessionKey, ""),
                    ["events"] = new JArray(),
                    ["reads"] = 0,
                    ["writes"] = 0,
                    ["refreshes"] = 0,
                };
            string directory = Path.Combine(project, "Library", "Play03Qa", nonce);
            CheckNoLinks(project, directory);
            Directory.CreateDirectory(directory);
            evidence = Path.Combine(directory, "backend-evidence.json");
            CheckNoLinks(project, evidence);
            if (resumed && state["preferences"] is JObject values)
                foreach (var entry in values.Properties())
                    Preferences.Add(entry.Name, (int)entry.Value);
            originalRead = TestRunnerNoThrottle.ReadPreference;
            originalWrite = TestRunnerNoThrottle.WritePreference;
            originalApply = TestRunnerNoThrottle.ApplyInteractionPreferences;
            // Unregister the automatic observer before outer Execute. It must never get an early
            // RunStarted callback that precedes a fixture's in-memory preference injection.
            typeof(TestRunnerNoThrottle).GetMethod("ReleaseApi", Hidden).Invoke(null, null);
            Record("automatic_callback_released");
            ownedRead = (key, fallback) =>
            {
                Count("reads");
                return Preferences.TryGetValue(key, out int value) ? value : fallback;
            };
            ownedWrite = (key, value) =>
            {
                Count("writes");
                Preferences[key] = value;
            };
            ownedApply = () => Count("refreshes");
            TestRunnerNoThrottle.ReadPreference = ownedRead;
            TestRunnerNoThrottle.WritePreference = ownedWrite;
            TestRunnerNoThrottle.ApplyInteractionPreferences = ownedApply;
            installed = true;
            Record("owned_backend_installed");
            if (resumed)
            {
                if ((string)state["managed_nonce"] == ManagedNonce)
                    throw new InvalidOperationException("PLAY03 QA resume requires a real managed reload.");
                SessionState.SetBool(ActiveKey, (bool?)state["resume_active"] == true);
                SessionState.SetBool(CapturedKey, (bool?)state["resume_captured"] == true);
                SessionState.SetInt(IdleKey, (int?)state["resume_idle"] ?? 4);
                SessionState.SetInt(ModeKey, (int?)state["resume_mode"] ?? 0);
                Record("reload_backend_bound_before_resume");
            }
            state["managed_nonce"] = ManagedNonce;
            state["before_reload"] = false;
            api = ScriptableObject.CreateInstance<TestRunnerApi>();
            api.hideFlags = HideFlags.HideAndDontSave;
            observer = new Observer();
            api.RegisterCallbacks(observer, int.MinValue);
            AssemblyReloadEvents.beforeAssemblyReload += BeforeReload;
            EditorApplication.quitting += Cleanup;
            Persist();
        }

        /// <summary>QA-only outer runner. Never combine this executeMethod with -runTests.</summary>
        public static void RunOrdinary()
        {
            try
            {
                string[] args = Environment.GetCommandLineArgs();
                if (Array.Exists(args, arg => string.Equals(arg, "-runTests", StringComparison.OrdinalIgnoreCase)))
                    throw new InvalidOperationException("PLAY03 isolated launcher must not use -runTests.");
                if (
                    initializationFailure != null
                    || !installed
                    || state == null
                    || !System.Guid.TryParseExact((string)state["nonce"], "N", out _)
                    || TestRunnerNoThrottle.ReadPreference != ownedRead
                    || TestRunnerNoThrottle.WritePreference != ownedWrite
                    || TestRunnerNoThrottle.ApplyInteractionPreferences != ownedApply
                    || typeof(TestRunnerNoThrottle).GetField("_api", Hidden).GetValue(null) != null
                )
                    throw new InvalidOperationException("PLAY03 isolated launcher was not initialized safely.");
                string filter = Environment.GetEnvironmentVariable("UNITY_MCP_PLAY03_QA_TEST_FILTER");
                if (string.IsNullOrWhiteSpace(filter) || filter.Length > 8192)
                    throw new InvalidOperationException("PLAY03 isolated launcher requires bounded explicit test selection.");
                string[] names = filter.Split(';');
                if (names.Length > 128 || Array.Exists(names, name => !AllowedOrdinaryName(name)))
                    throw new InvalidOperationException("PLAY03 isolated launcher refuses an unrelated or ambiguous filter.");
                var active = typeof(TestRunnerApi).GetMethod("IsRunActive", Hidden, null, Type.EmptyTypes, null);
                if (active == null || (bool)active.Invoke(null, null) || !NativeTestRunControl.Available)
                    throw new InvalidOperationException("PLAY03 isolated launcher requires exact supported and idle native framework.");
                VerifyOwnedBackendPositiveControl();
                state["ordinary_filter"] = filter;
                state["ordinary_pending"] = true;
                Record("pre_execute_owned_backend_verified");
                Persist();
                outerDeadline = EditorApplication.timeSinceStartup + 300;
                // This is the sole outer Execute. Individual ordinary tests inject fake scheduling;
                // none starts an actual nested TestRunnerApi job.
                string guid = api.Execute(new ExecutionSettings(new Filter { testMode = TestMode.EditMode, testNames = names }));
                state["outer_guid"] = guid;
                outerWitness = NativeTestRunControl.Create(System.Guid.NewGuid().ToString("N"), 0);
                if (outerWitness == null)
                    throw new InvalidOperationException("PLAY03 isolated launcher lost final-progress observation.");
                outerWitness.BindGuid(guid);
                Record("outer_execute_returned_guid");
                Persist();
                EditorApplication.update += FinishOrdinary;
            }
            catch (Exception error)
            {
                Debug.LogError("PLAY03 isolated launcher refused execution: " + error.Message);
                if (installed)
                {
                    state["launcher_error"] = error.GetType().Name;
                    Record("launcher_refused_without_success");
                    Cleanup();
                }
                EditorApplication.Exit(2);
            }
        }

        // QA-only positive control: exercise the real helper exclusively through the already
        // verified owned delegates before any outer Execute can deliver callbacks.
        internal static void VerifyOwnedBackendPositiveControl()
        {
            if (
                !installed
                || TestRunnerNoThrottle.ReadPreference != ownedRead
                || TestRunnerNoThrottle.WritePreference != ownedWrite
                || TestRunnerNoThrottle.ApplyInteractionPreferences != ownedApply
                || typeof(TestRunnerNoThrottle).GetField("_api", Hidden).GetValue(null) != null
                || SessionState.GetBool(ActiveKey, false)
                || SessionState.GetBool(CapturedKey, false)
            )
                throw new InvalidOperationException("PLAY03 positive control requires an inactive verified owned backend.");
            var savedPreferences = new Dictionary<string, int>(Preferences);
            int savedIdle = SessionState.GetInt(IdleKey, 4);
            int savedMode = SessionState.GetInt(ModeKey, 0);
            int reads = (int)state["reads"];
            int writes = (int)state["writes"];
            int refreshes = (int)state["refreshes"];
            try
            {
                Preferences["ApplicationIdleTime"] = 37;
                Preferences["InteractionMode"] = 2;
                Record("owned_backend_positive_control_started");
                TestRunnerNoThrottle.ApplyNoThrottlingPreemptive();
                if (
                    (int)state["reads"] != reads + 2
                    || (int)state["writes"] != writes + 2
                    || (int)state["refreshes"] != refreshes + 1
                    || Preferences["ApplicationIdleTime"] != 0
                    || Preferences["InteractionMode"] != 1
                    || !SessionState.GetBool(ActiveKey, false)
                    || !SessionState.GetBool(CapturedKey, false)
                )
                    throw new InvalidOperationException("PLAY03 owned Apply positive control failed.");
                Record("owned_backend_positive_control_applied");
                TestRunnerNoThrottle.RestoreThrottling();
                if (
                    (int)state["reads"] != reads + 2
                    || (int)state["writes"] != writes + 4
                    || (int)state["refreshes"] != refreshes + 2
                    || Preferences["ApplicationIdleTime"] != 37
                    || Preferences["InteractionMode"] != 2
                    || SessionState.GetBool(ActiveKey, false)
                    || SessionState.GetBool(CapturedKey, false)
                )
                    throw new InvalidOperationException("PLAY03 owned Restore positive control failed.");
                state["positive_control"] = new JObject
                {
                    ["reads"] = 2,
                    ["writes"] = 4,
                    ["refreshes"] = 2,
                    ["restored_idle"] = 37,
                    ["restored_mode"] = 2,
                };
                Record("owned_backend_positive_control_restored");
            }
            finally
            {
                // All values/flags belong to this fresh QA session. Never call default delegates.
                SessionState.SetBool(ActiveKey, false);
                SessionState.SetBool(CapturedKey, false);
                SessionState.SetInt(IdleKey, savedIdle);
                SessionState.SetInt(ModeKey, savedMode);
                Preferences.Clear();
                foreach (var entry in savedPreferences)
                    Preferences.Add(entry.Key, entry.Value);
            }
            Persist();
        }

        private static bool AllowedOrdinaryName(string name)
        {
            const string prefix = "MCPForUnityTests.Editor.Services.";
            foreach (string fixture in new[] { "TestJobCancellationTests", "TestRunnerServiceLifecycleTests", "TestJobManagerReloadTests" })
                if (name == prefix + fixture || name.StartsWith(prefix + fixture + ".", StringComparison.Ordinal))
                    return true;
            return false;
        }

        private static void FinishOrdinary()
        {
            try
            {
                turn++;
                if (EditorApplication.timeSinceStartup > outerDeadline)
                    throw new TimeoutException("PLAY03 outer runner did not finish within its owned QA bound.");
                if ((bool?)state["ordinary_finished"] != true || (bool?)outerWitness?.State["final_witness"] != true)
                    return;
                if (finalTurn < 0)
                {
                    finalTurn = turn;
                    Record("outer_matching_post_dispose_progress_observed");
                    Persist();
                    return;
                }
                if (turn <= finalTurn || (bool)typeof(TestRunnerApi).GetMethod("IsRunActive", Hidden).Invoke(null, null))
                    return;
                Record("outer_later_main_thread_idle_observed");
                int exit = (int?)state["ordinary_failures"] > 0 ? 2 : 0;
                EditorApplication.update -= FinishOrdinary;
                outerWitness.Dispose();
                outerWitness = null;
                Cleanup();
                EditorApplication.Exit(exit);
            }
            catch (Exception error)
            {
                Debug.LogError("PLAY03 isolated launcher did not prove completion: " + error.Message);
                state["launcher_error"] = error.GetType().Name;
                EditorApplication.update -= FinishOrdinary;
                outerWitness?.Dispose();
                Cleanup();
                EditorApplication.Exit(2);
            }
        }

        private static void CheckNoLinks(string root, string path)
        {
            string full = Path.GetFullPath(path);
            if (!full.StartsWith(root + Path.DirectorySeparatorChar, StringComparison.OrdinalIgnoreCase))
                throw new InvalidOperationException("PLAY03 QA evidence must remain in its owned project.");
            for (string current = full; !string.Equals(current, root, StringComparison.OrdinalIgnoreCase); current = Path.GetDirectoryName(current))
                if ((File.Exists(current) || Directory.Exists(current)) && (File.GetAttributes(current) & FileAttributes.ReparsePoint) != 0)
                    throw new InvalidOperationException("PLAY03 QA refuses linked evidence paths.");
            if ((File.GetAttributes(root) & FileAttributes.ReparsePoint) != 0)
                throw new InvalidOperationException("PLAY03 QA refuses a linked project root.");
        }

        private static void Count(string key) => state[key] = (int)state[key] + 1;

        private static void Record(string name)
        {
            var events = (JArray)state["events"];
            if (events.Count >= 128)
                throw new InvalidOperationException("PLAY03 QA witness bound exceeded.");
            events.Add(
                new JObject
                {
                    ["sequence"] = events.Count + 1,
                    ["event"] = name,
                    ["managed_nonce"] = ManagedNonce,
                }
            );
        }

        private static void Persist()
        {
            state["preferences"] = JObject.FromObject(Preferences);
            string json = state.ToString(Formatting.None);
            if (Encoding.UTF8.GetByteCount(json) > 32768)
                throw new InvalidOperationException("PLAY03 QA witness byte limit exceeded.");
            CheckNoLinks((string)state["project"], evidence);
            SessionState.SetString(StateKey, json);
            File.WriteAllText(evidence, json, new UTF8Encoding(false));
        }

        private static void BeforeReload()
        {
            state["resume_active"] = SessionState.GetBool(ActiveKey, false);
            state["resume_captured"] = SessionState.GetBool(CapturedKey, false);
            state["resume_idle"] = SessionState.GetInt(IdleKey, 4);
            state["resume_mode"] = SessionState.GetInt(ModeKey, 0);
            state["before_reload"] = true;
            Record("before_reload_fake_state_saved");
            SessionState.SetBool(ActiveKey, false);
            SessionState.SetBool(CapturedKey, false);
            Record("before_reload_active_flags_cleared");
            Persist();
            ReleaseObserver();
        }

        public static void Cleanup()
        {
            if (!installed)
                return;
            // Clear only task-owned flags before restoring default delegates. No real preferences
            // are read or applied, and the initial admission policy required both flags false.
            SessionState.SetBool(ActiveKey, false);
            SessionState.SetBool(CapturedKey, false);
            SessionState.SetInt(IdleKey, (int)state["initial_idle"]);
            SessionState.SetInt(ModeKey, (int)state["initial_mode"]);
            Record("cleanup_active_flags_cleared");
            ReleaseObserver();
            AssemblyReloadEvents.beforeAssemblyReload -= BeforeReload;
            EditorApplication.quitting -= Cleanup;
            TestRunnerNoThrottle.ReadPreference = originalRead;
            TestRunnerNoThrottle.WritePreference = originalWrite;
            TestRunnerNoThrottle.ApplyInteractionPreferences = originalApply;
            installed = false;
            Record("cleanup_default_delegates_restored");
            Persist();
            SessionState.SetString(NativeSessionKey, (string)state["native_session"] ?? "");
            SessionState.EraseString(StateKey);
        }

        private static void ReleaseObserver()
        {
            if (api == null)
                return;
            api.UnregisterCallbacks(observer);
            ScriptableObject.DestroyImmediate(api);
            api = null;
            observer = null;
        }

        private sealed class Observer : ICallbacks
        {
            public void RunStarted(ITestAdaptor tests)
            {
                if (
                    !installed
                    || TestRunnerNoThrottle.ReadPreference != ownedRead
                    || TestRunnerNoThrottle.WritePreference != ownedWrite
                    || TestRunnerNoThrottle.ApplyInteractionPreferences != ownedApply
                    || typeof(TestRunnerNoThrottle).GetField("_api", Hidden).GetValue(null) != null
                )
                    throw new InvalidOperationException("PLAY03 QA backend must be installed before outer Execute callbacks.");
                Record("outer_run_started_with_owned_backend");
                Persist();
            }

            public void RunFinished(ITestResultAdaptor result)
            {
                Record("outer_run_finished_with_owned_backend");
                if ((bool?)state["ordinary_pending"] == true)
                {
                    string resultPath = Path.Combine(Path.GetDirectoryName(evidence), "results.xml");
                    CheckNoLinks((string)state["project"], resultPath);
                    TestRunnerApi.SaveResultToFile(result, resultPath);
                    state["ordinary_failures"] = result.FailCount;
                    state["ordinary_passed"] = result.PassCount;
                    state["ordinary_skipped"] = result.SkipCount;
                    state["ordinary_finished"] = true;
                }
                Persist();
            }

            public void TestStarted(ITestAdaptor test) { }

            public void TestFinished(ITestResultAdaptor result) { }
        }
    }
}
