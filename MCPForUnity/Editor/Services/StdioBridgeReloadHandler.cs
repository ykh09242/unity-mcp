using System;
using System.Threading;
using System.Threading.Tasks;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Editor.Services.Transport;
using MCPForUnity.Editor.Services.Transport.Transports;
using MCPForUnity.Editor.Windows;
using UnityEditor;

namespace MCPForUnity.Editor.Services
{
    /// <summary>
    /// Ensures the legacy stdio bridge resumes after domain reloads, mirroring the HTTP handler.
    /// </summary>
    [InitializeOnLoad]
    internal static class StdioBridgeReloadHandler
    {
        // Reload intent belongs to one Editor process, and must not survive its exit.
        internal const string ResumeSessionKey = "MCPForUnity.ResumeStdioAfterReload";
        private const string MigratedSessionKey = ResumeSessionKey + ".Migrated";
        private static readonly TimeSpan[] ResumeRetrySchedule =
        {
            TimeSpan.Zero,
            TimeSpan.FromSeconds(1),
            TimeSpan.FromSeconds(3),
            TimeSpan.FromSeconds(5),
            TimeSpan.FromSeconds(10),
            TimeSpan.FromSeconds(30),
        };

        private static CancellationTokenSource _retryCts;
        private static int _resumeGeneration;
        private static EditorApplication.CallbackFunction _resumeCallback;

        internal static bool IsResumePending => SessionState.GetBool(ResumeSessionKey, false);

        static StdioBridgeReloadHandler()
        {
            if (UnityEngine.Application.isBatchMode && string.IsNullOrWhiteSpace(Environment.GetEnvironmentVariable("UNITY_MCP_ALLOW_BATCH")))
            {
                return;
            }

            MigrateLegacyResumeIntent();
            AssemblyReloadEvents.beforeAssemblyReload += OnBeforeAssemblyReload;
            AssemblyReloadEvents.afterAssemblyReload += OnAfterAssemblyReload;
            EditorApplication.quitting += CancelPendingResume;
        }

        internal static void MigrateLegacyResumeIntent()
        {
            // Never import machine-wide intent: it may belong to another Editor or a
            // crashed process. Clean it only once per session, as the HTTP handler does.
            if (SessionState.GetBool(MigratedSessionKey, false))
                return;
            EditorPrefs.DeleteKey(ResumeSessionKey);
            SessionState.SetBool(MigratedSessionKey, true);
        }

        internal static void CancelPendingResume()
        {
            CancelRetries();
            SessionState.EraseBool(ResumeSessionKey);
        }

        private static void CancelRetries()
        {
            _resumeGeneration++;
            if (_resumeCallback != null)
                EditorApplication.delayCall -= _resumeCallback;
            _resumeCallback = null;
            var previous = _retryCts;
            _retryCts = null;
            try
            {
                // The suspended loop owns disposal until its awaited start settles.
                previous?.Cancel();
            }
            catch { }
        }

        private static void OnBeforeAssemblyReload()
        {
            // Cancel any in-flight retry loop before the next reload.
            CancelRetries();

            try
            {
                // A second reload can arrive before the first reload's deferred resume.
                // Preserve that intent until resume succeeds or an explicit stop/switch clears it.
                // Check both TransportManager AND StdioBridgeHost directly, because CI starts via StdioBridgeHost
                // bypassing TransportManager state.
                bool tmRunning = MCPServiceLocator.TransportManager.IsRunning(TransportMode.Stdio);
                bool hostRunning = StdioBridgeHost.IsRunning;
                bool isRunning = tmRunning || hostRunning;
                bool shouldResume = PersistResumeIntent(isRunning);

                if (isRunning)
                {
                    // Stop only stdio before reload. This is centralized here so resume-flag updates
                    // and teardown cannot race each other via separate beforeAssemblyReload handlers.
                    var stopTask = MCPServiceLocator.TransportManager.StopAsync(TransportMode.Stdio);
                    try
                    {
                        stopTask.Wait(500);
                    }
                    catch { }

                    // Legacy safety: stdio may have been started outside TransportManager state.
                    try
                    {
                        StdioBridgeHost.Stop();
                    }
                    catch { }
                }

                if (shouldResume)
                {
                    // Write reloading status so clients don't think we vanished.
                    StdioBridgeHost.WriteHeartbeat(true, "reloading");
                }
            }
            catch (Exception ex)
            {
                McpLog.Warn($"Failed to persist stdio reload flag: {ex.Message}");
            }
        }

        internal static bool PersistResumeIntent(bool isRunning)
        {
            bool shouldResume = !EditorConfigurationCache.Instance.UseHttpTransport && (isRunning || IsResumePending);
            if (shouldResume)
                SessionState.SetBool(ResumeSessionKey, true);
            else
                SessionState.EraseBool(ResumeSessionKey);
            return shouldResume;
        }

        private static void OnAfterAssemblyReload()
        {
            bool resume = false;
            try
            {
                bool resumeFlag = IsResumePending;
                bool useHttp = EditorConfigurationCache.Instance.UseHttpTransport;
                resume = resumeFlag && !useHttp;

                // If we're not going to resume, clear the flag immediately to avoid stuck "Resuming..." state
                if (!resume)
                {
                    CancelPendingResume();
                }
            }
            catch (Exception ex)
            {
                McpLog.Warn($"Failed to read stdio reload flag: {ex.Message}");
            }

            if (!resume)
            {
                return;
            }

            // If the editor is not compiling, attempt an immediate restart without relying on editor focus.
            // Routed through EditorStateCache so a deferred reload (issue #1276) does not block resume.
            bool isCompiling = EditorStateCache.GetActualIsCompiling();

            if (!isCompiling)
            {
                _ = ResumeStdioWithRetriesAsync();
                return;
            }

            // Fallback when compiling: schedule on the editor loop
            ScheduleResumeAfterDelay();
        }

        private static void ScheduleResumeAfterDelay()
        {
            int generation = _resumeGeneration;
            if (_resumeCallback != null)
                EditorApplication.delayCall -= _resumeCallback;
            EditorApplication.CallbackFunction callback = null;
            callback = () =>
            {
                EditorApplication.delayCall -= callback;
                if (ReferenceEquals(_resumeCallback, callback))
                    _resumeCallback = null;
                if (generation == _resumeGeneration && IsResumePending)
                    _ = ResumeStdioWithRetriesAsync();
            };
            _resumeCallback = callback;
            EditorApplication.delayCall += callback;
        }

        internal static async Task ResumeStdioWithRetriesAsync(TimeSpan[] scheduleOverride = null)
        {
            if (!IsResumePending)
                return;
            // Cancel any previous retry loop and create a fresh token.
            CancelRetries();
            var cts = _retryCts = new CancellationTokenSource();
            var token = cts.Token;
            int generation = _resumeGeneration;
            TimeSpan[] schedule = scheduleOverride ?? ResumeRetrySchedule;
            bool OwnsResume() => !token.IsCancellationRequested && generation == _resumeGeneration && IsResumePending;

            Exception lastException = null;

            try
            {
                for (int i = 0; i < schedule.Length; i++)
                {
                    if (!OwnsResume())
                        return;

                    int attempt = i + 1;
                    McpLog.Debug($"[Stdio Reload] Resume attempt {attempt}/{schedule.Length}");

                    TimeSpan delay = schedule[i];
                    if (delay > TimeSpan.Zero)
                    {
                        McpLog.Debug($"[Stdio Reload] Waiting {delay.TotalSeconds:0.#}s before resume attempt {attempt}");
                        try
                        {
                            await Task.Delay(delay, token);
                        }
                        catch (OperationCanceledException)
                        {
                            return;
                        }
                    }

                    if (!OwnsResume())
                        return;
                    // Abort retries if the user switched transports while we were waiting.
                    if (EditorConfigurationCache.Instance.UseHttpTransport)
                    {
                        try
                        {
                            CancelPendingResume();
                        }
                        catch { }
                        return;
                    }

                    try
                    {
                        bool started = await MCPServiceLocator.TransportManager.StartAsync(TransportMode.Stdio);
                        if (!OwnsResume())
                            return;
                        if (started)
                        {
                            McpLog.Debug($"[Stdio Reload] Resume succeeded on attempt {attempt}");
                            try
                            {
                                SessionState.EraseBool(ResumeSessionKey);
                            }
                            catch { }
                            MCPForUnityEditorWindow.RequestHealthVerification();
                            return;
                        }

                        var state = MCPServiceLocator.TransportManager.GetState(TransportMode.Stdio);
                        string reason = string.IsNullOrWhiteSpace(state?.Error) ? "no error detail" : state.Error;
                        McpLog.Debug($"[Stdio Reload] Resume attempt {attempt} failed: {reason}");
                    }
                    catch (Exception ex)
                    {
                        lastException = ex;
                        McpLog.Debug($"[Stdio Reload] Resume attempt {attempt} threw: {ex.Message}");
                    }
                }

                if (!OwnsResume())
                    return;
                try
                {
                    SessionState.EraseBool(ResumeSessionKey);
                }
                catch { }

                // Clear the stale "reloading" heartbeat so clients stop seeing reloading=true.
                // The bridge isn't running, so clients will get connection-refused (recoverable)
                // instead of hanging on a zombie socket or being rejected by the preflight check.
                try
                {
                    StdioBridgeHost.WriteHeartbeat(false, "stopped");
                }
                catch { }

                if (lastException != null)
                {
                    McpLog.Warn($"Failed to resume stdio bridge after domain reload: {lastException.Message}");
                }
                else
                {
                    McpLog.Warn("Failed to resume stdio bridge after domain reload");
                }
            }
            finally
            {
                if (ReferenceEquals(_retryCts, cts))
                    _retryCts = null;
                cts.Dispose();
            }
        }
    }
}
