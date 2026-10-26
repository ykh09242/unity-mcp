// TestRunnerNoThrottle.cs
// Sets Unity Editor to "No Throttling" mode during test runs.
// This helps tests that don't trigger compilation run smoothly in the background.
// Note: Tests that trigger mid-run compilation may still stall due to OS-level throttling.

using System;
using System.IO;
using System.Reflection;
using System.Text;
using MCPForUnity.Editor.Helpers;
using UnityEditor;
using UnityEditor.TestTools.TestRunner.Api;
using UnityEngine;

namespace MCPForUnity.Editor.Services
{
    /// <summary>
    /// Automatically sets the editor to "No Throttling" mode during test runs.
    ///
    /// This helps prevent background stalls for normal tests. However, tests that trigger
    /// script compilation mid-run may still stall because:
    /// - Internal Unity coroutine waits rely on editor ticks
    /// - OS-level throttling affects the main thread when Unity is backgrounded
    /// - No amount of internal nudging can overcome OS thread scheduling
    ///
    /// </summary>
    [InitializeOnLoad]
    public static class TestRunnerNoThrottle
    {
        private const string ApplicationIdleTimeKey = "ApplicationIdleTime";
        private const string InteractionModeKey = "InteractionMode";

        // Narrow test seam: owned fixtures must never read/write the user's global preferences.
        // Production delegates retain the existing EditorPrefs and interaction-refresh behavior.
        internal static Func<string, int, int> ReadPreference = EditorPrefs.GetInt;
        internal static Action<string, int> WritePreference = EditorPrefs.SetInt;
        internal static Action ApplyInteractionPreferences = ForceEditorToApplyInteractionPrefs;
        internal static string LeaseRootForTests;
        private static EditorPreferenceLease lease;
        private static string leaseRoot;
        private static string defaultLeaseRoot;
        private static bool maintaining;
        private static bool warned;
        private static double nextMaintenance;
        private static double? lastAcquireUptime;
        private static bool? lastAcquireSucceeded;
        private static bool? lastAcquireSuppressed;

        internal static object Diagnostics() =>
            new
            {
                test_run_active = IsTestRunActive(),
                settings_captured = AreSettingsCaptured(),
                maintenance_subscribed = maintaining,
                last_acquire_uptime = lastAcquireUptime,
                last_acquire_succeeded = lastAcquireSucceeded,
                last_acquire_suppressed = lastAcquireSuppressed,
            };

        // SessionState keys to persist across domain reload
        private const string SessionKey_TestRunActive = "TestRunnerNoThrottle_TestRunActive";
        private const string SessionKey_PrevIdleTime = "TestRunnerNoThrottle_PrevIdleTime";
        private const string SessionKey_PrevInteractionMode = "TestRunnerNoThrottle_PrevInteractionMode";
        private const string SessionKey_SettingsCaptured = "TestRunnerNoThrottle_SettingsCaptured";

        // Keep reference to avoid GC and set HideFlags to avoid serialization issues
        private static TestRunnerApi _api;
        private static IErrorCallbacks _callbacks;

        static TestRunnerNoThrottle()
        {
            AssemblyReloadEvents.beforeAssemblyReload += ReleaseApi;
            EditorApplication.quitting += OnQuit;
            EditorApplication.delayCall += RecoverAfterStartup;
            try
            {
                _api = ScriptableObject.CreateInstance<TestRunnerApi>();
                _api.hideFlags = HideFlags.HideAndDontSave;
                _callbacks = new TestCallbacks();
                _api.RegisterCallbacks(_callbacks);

                // Check if recovering from domain reload during an active test run
                if (IsTestRunActive())
                {
                    McpLog.Info("[TestRunnerNoThrottle] Recovered from domain reload - reapplying No Throttling.");
                    ApplyNoThrottling();
                }
            }
            catch (Exception e)
            {
                ReleaseApi();
                McpLog.Warn($"[TestRunnerNoThrottle] Failed to register callbacks: {e}");
            }
        }

        private static void ReleaseApi()
        {
            EditorApplication.update -= Maintain;
            EditorApplication.delayCall -= RecoverAfterStartup;
            maintaining = false;
            var api = _api;
            var callbacks = _callbacks;
            _api = null;
            _callbacks = null;
            if (api == null)
                return;
            try
            {
                if (callbacks != null)
                    api.UnregisterCallbacks(callbacks);
            }
            catch (Exception e)
            {
                McpLog.Warn($"[TestRunnerNoThrottle] Failed to unregister callbacks: {e.Message}");
            }
            finally
            {
                // HideAndDontSave objects require explicit destruction before the managed owner reloads.
                // Keep SessionState intact so an active run can reapply and later restore its preferences.
                ScriptableObject.DestroyImmediate(api);
            }
        }

        #region State Persistence

        private static bool IsTestRunActive() => SessionState.GetBool(SessionKey_TestRunActive, false);

        private static void SetTestRunActive(bool active) => SessionState.SetBool(SessionKey_TestRunActive, active);

        private static bool AreSettingsCaptured() => SessionState.GetBool(SessionKey_SettingsCaptured, false);

        private static void SetSettingsCaptured(bool captured) => SessionState.SetBool(SessionKey_SettingsCaptured, captured);

        private static int GetPrevIdleTime() => SessionState.GetInt(SessionKey_PrevIdleTime, 4);

        private static void SetPrevIdleTime(int value) => SessionState.SetInt(SessionKey_PrevIdleTime, value);

        private static int GetPrevInteractionMode() => SessionState.GetInt(SessionKey_PrevInteractionMode, 0);

        private static void SetPrevInteractionMode(int value) => SessionState.SetInt(SessionKey_PrevInteractionMode, value);

        #endregion

        /// <summary>
        /// Apply no-throttling preemptively before tests start.
        /// Call before Execute() in either mode so background progress is available
        /// during startup, before RunStarted fires.
        /// </summary>
        public static void ApplyNoThrottlingPreemptive()
        {
            SetTestRunActive(true);
            ApplyNoThrottling();
        }

        private static EditorPreferenceLease GetLease()
        {
            if (defaultLeaseRoot == null)
            {
                string local = Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData);
                if (string.IsNullOrEmpty(local))
                    local = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.UserProfile), ".local", "share");
                if (!Path.IsPathRooted(local))
                    throw new IOException("No absolute per-user preference directory is available.");
                string machine = AtomicAssetFile.Digest(Encoding.UTF8.GetBytes(Environment.MachineName));
                defaultLeaseRoot = Path.Combine(local, "MCPForUnity", "EditorPreferences", machine);
            }
            string root = LeaseRootForTests ?? defaultLeaseRoot;
            if (lease == null || leaseRoot != root)
            {
                using var process = System.Diagnostics.Process.GetCurrentProcess();
                lease = new EditorPreferenceLease(
                    root,
                    process.Id,
                    process.StartTime.ToUniversalTime().Ticks,
                    OwnerIsAlive,
                    (key, fallback) => ReadPreference(key, fallback),
                    (key, value) => WritePreference(key, value),
                    () => ApplyInteractionPreferences()
                );
                leaseRoot = root;
            }
            return lease;
        }

        private static bool OwnerIsAlive(int pid, long started)
        {
            try
            {
                using var process = System.Diagnostics.Process.GetProcessById(pid);
                return !process.HasExited && process.StartTime.ToUniversalTime().Ticks == started;
            }
            catch (ArgumentException)
            {
                return false;
            }
            catch (InvalidOperationException)
            {
                return false;
            }
            // An inaccessible live process is not proof that its ownership expired.
            catch (System.ComponentModel.Win32Exception)
            {
                return true;
            }
            catch (NotSupportedException)
            {
                return true;
            }
        }

        private static bool TryLease(Action<EditorPreferenceLease> operation)
        {
            try
            {
                operation(GetLease());
                warned = false;
                return true;
            }
            catch (Exception error)
                when (error is IOException
                    || error is UnauthorizedAccessException
                    || error is InvalidOperationException
                    || error is Newtonsoft.Json.JsonException
                    || error is System.ComponentModel.Win32Exception
                    || error is NotSupportedException
                )
            {
                if (!warned)
                    McpLog.Warn(
                        "[TestRunnerNoThrottle] Preference ownership is unavailable; preserving current settings and retrying. " + error.GetType().Name
                    );
                warned = true;
                return false;
            }
        }

        private static void ScheduleMaintenance()
        {
            if (maintaining)
                return;
            maintaining = true;
            nextMaintenance = EditorApplication.timeSinceStartup + 5;
            EditorApplication.update += Maintain;
        }

        private static void RecoverAfterStartup()
        {
            // No directory or lock is created when no recovery record exists.
            if (IsTestRunActive())
                ApplyNoThrottling();
            else if (AreSettingsCaptured())
                RestoreAfterRun();
            else
                TryLease(current =>
                {
                    if (current.Exists)
                        current.Release();
                });
            ScheduleMaintenance();
        }

        private static void Maintain()
        {
            if (EditorApplication.timeSinceStartup < nextMaintenance)
                return;
            nextMaintenance = EditorApplication.timeSinceStartup + 5;
            if (IsTestRunActive())
                ApplyNoThrottling();
            else if (AreSettingsCaptured())
                RestoreAfterRun();
            else if (!TryLease(current => current.Recover()))
                return;
            bool pending = true;
            if (!IsTestRunActive() && !AreSettingsCaptured() && TryLease(current => pending = current.Exists) && !pending)
            {
                EditorApplication.update -= Maintain;
                maintaining = false;
            }
        }

        private static void ApplyNoThrottling()
        {
            ScheduleMaintenance();
            int idle = ReadPreference(ApplicationIdleTimeKey, 4);
            int mode = ReadPreference(InteractionModeKey, 0);
            bool captured = AreSettingsCaptured();
            lastAcquireUptime = EditorApplication.timeSinceStartup;
            lastAcquireSucceeded = TryLease(current =>
            {
                current.Acquire(captured ? GetPrevIdleTime() : (int?)null, captured ? GetPrevInteractionMode() : (int?)null);
                lastAcquireSuppressed = current.LastAcquireSuppressed;
            });
            if (lastAcquireSucceeded != true)
            {
                lastAcquireSuppressed = null;
                return;
            }
            if (!captured)
            {
                SetPrevIdleTime(idle);
                SetPrevInteractionMode(mode);
                SetSettingsCaptured(true);
            }
        }

        internal static void RestoreThrottling()
        {
            SetTestRunActive(false);
            ScheduleMaintenance();
            if (
                TryLease(current =>
                {
                    if (AreSettingsCaptured() && !current.Exists)
                        current.Acquire(GetPrevIdleTime(), GetPrevInteractionMode());
                    current.Release();
                })
            )
                SetSettingsCaptured(false);
        }

        internal static void RestoreAfterRun()
        {
            SetTestRunActive(false);
            // RunFinished precedes UTF's task disposal and final GUID progress event.
            // Keep background ticks available until that resource owner confirms release.
            if (TestJobManager.NativeAdmissionBlocked)
                ScheduleMaintenance();
            else
                RestoreThrottling();
        }

        private static void OnQuit()
        {
            RestoreThrottling();
            ReleaseApi();
        }

        private static void ForceEditorToApplyInteractionPrefs()
        {
            try
            {
                var method = typeof(EditorApplication).GetMethod("UpdateInteractionModeSettings", BindingFlags.Static | BindingFlags.NonPublic);
                method?.Invoke(null, null);
            }
            catch
            {
                // Ignore reflection errors
            }
        }

        private sealed class TestCallbacks : IErrorCallbacks
        {
            public void RunStarted(ITestAdaptor testsToRun)
            {
                SetTestRunActive(true);
                ApplyNoThrottling();
            }

            public void RunFinished(ITestResultAdaptor result)
            {
                RestoreAfterRun();
            }

            public void OnError(string message)
            {
                // Build/prebuild failures may terminate before RunFinished is delivered.
                RestoreAfterRun();
            }

            public void TestStarted(ITestAdaptor test) { }

            public void TestFinished(ITestResultAdaptor result) { }
        }
    }
}
