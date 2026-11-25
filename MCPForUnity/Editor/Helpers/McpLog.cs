using System;
using System.Collections.Generic;
using System.Threading;
using MCPForUnity.Editor.Constants;
using MCPForUnity.Editor.Services;
using MCPForUnity.Editor.Services.Transport;
using UnityEditor;
using UnityEngine;

namespace MCPForUnity.Editor.Helpers
{
    internal static class McpLog
    {
        private const string InfoPrefix = "<b><color=#2EA3FF>" + ProductInfo.ProductName + "</color></b>:";
        private const string DebugPrefix = "<b><color=#6AA84F>" + ProductInfo.ProductName + "</color></b>:";
        private const string WarnPrefix = "<b><color=#cc7a00>" + ProductInfo.ProductName + "</color></b>:";
        private const string ErrorPrefix = "<b><color=#cc3333>" + ProductInfo.ProductName + "</color></b>:";

        private static volatile bool _debugEnabled = ReadDebugPreference();
        private const int MaxDeferred = 128;
        private const int MaxMessageChars = 4096;
        private const string OmittedOnReload = "MCPForUnity.DeferredReadLogCount";
        private static readonly AsyncLocal<WeakReference<NativeCommandArbitrator.Lease>> NonExclusiveLease = new();
        private static readonly Queue<(LogType type, string message)> Deferred = new();
        private static volatile bool hasDeferred;
        private static bool stopped;
        private static int omitted;
        private static double nextAttempt;

        [InitializeOnLoadMethod]
        private static void InitializeDeferredConsole()
        {
            lock (Deferred)
            {
                omitted = (int)Math.Min(int.MaxValue, (long)omitted + SessionState.GetInt(OmittedOnReload, 0));
                hasDeferred = omitted > 0 || Deferred.Count > 0;
            }
            SessionState.EraseInt(OmittedOnReload);
            EditorApplication.update += FlushDeferred;
            AssemblyReloadEvents.beforeAssemblyReload += StopDeferred;
            EditorApplication.quitting += StopDeferred;
        }

        private static void StopDeferred()
        {
            EditorApplication.update -= FlushDeferred;
            lock (Deferred)
            {
                if (stopped)
                    return;
                stopped = true;
                int count = (int)Math.Min(int.MaxValue, (long)omitted + Deferred.Count);
                Deferred.Clear();
                hasDeferred = false;
                if (count > 0)
                    SessionState.SetInt(OmittedOnReload, count);
            }
        }

        // The caller restores its context immediately. Awaited work inherits only
        // the original lease; a detached continuation cannot retain a settled handler.
        internal static IDisposable EnterHandler(NativeCommandArbitrator.Lease lease)
        {
            var previous = NonExclusiveLease.Value;
            bool defer = lease != null && lease.Mode != ToolOperationMode.Exclusive;
            if (!defer && previous == null)
                return null;
            NonExclusiveLease.Value = defer ? new WeakReference<NativeCommandArbitrator.Lease>(lease) : null;
            return new HandlerLogScope(previous);
        }

        private sealed class HandlerLogScope : IDisposable
        {
            private readonly WeakReference<NativeCommandArbitrator.Lease> previous;

            internal HandlerLogScope(WeakReference<NativeCommandArbitrator.Lease> previous) => this.previous = previous;

            public void Dispose() => NonExclusiveLease.Value = previous;
        }

        private static void Write(LogType type, string message)
        {
            var scope = NonExclusiveLease.Value;
            if (scope == null || !scope.TryGetTarget(out var lease) || !lease.IsActive)
            {
                Publish(type, message);
                return;
            }
            lock (Deferred)
            {
                if (stopped)
                    return;
                if (Deferred.Count < MaxDeferred)
                    Deferred.Enqueue((type, message.Length <= MaxMessageChars ? message : message.Substring(0, MaxMessageChars - 12) + " [truncated]"));
                else if (omitted < int.MaxValue)
                    omitted++;
                hasDeferred = true;
            }
        }

        // Unity's console invokes project log subscribers synchronously. An earlier
        // subscriber can start native work, so admit each queued delivery independently.
        internal static void FlushDeferred()
        {
            if (!hasDeferred || EditorApplication.timeSinceStartup < nextAttempt)
                return;
            try
            {
                FlushAdmitted();
            }
            catch (Exception)
            {
                // Failure to inspect native ownership cannot authorize a console callback.
                // A delivery already removed from the queue must not run its subscribers twice.
                nextAttempt = EditorApplication.timeSinceStartup + 0.25;
            }
        }

        private static void FlushAdmitted()
        {
            for (int i = 0; i < 8 && hasDeferred; i++)
            {
                if (!NativeCommandArbitrator.Shared.TryAcquire(ToolOperationMode.Exclusive, null, null, null, out var lease, out _))
                {
                    nextAttempt = EditorApplication.timeSinceStartup + 0.25;
                    return;
                }
                using (lease)
                {
                    (LogType type, string message) entry;
                    lock (Deferred)
                    {
                        if (omitted != 0)
                        {
                            entry = (LogType.Warning, $"{WarnPrefix} {omitted} deferred console messages were omitted due to capacity or Editor reload.");
                            omitted = 0;
                        }
                        else if (Deferred.Count != 0)
                            entry = Deferred.Dequeue();
                        else
                            return;
                        hasDeferred = omitted != 0 || Deferred.Count != 0;
                    }
                    lease.Describe("mcp_console", true, CancellationToken.None);
                    Publish(entry.type, entry.message);
                }
            }
        }

        private static void Publish(LogType type, string message)
        {
            if (type == LogType.Warning)
                UnityEngine.Debug.LogWarning(message);
            else if (type == LogType.Error)
                UnityEngine.Debug.LogError(message);
            else
                UnityEngine.Debug.Log(message);
        }

        private static bool IsDebugEnabled() => _debugEnabled;

        private static bool ReadDebugPreference()
        {
            try
            {
                return EditorPrefs.GetBool(EditorPrefKeys.DebugLogs, false);
            }
            catch
            {
                return false;
            }
        }

        public static void SetDebugLoggingEnabled(bool enabled)
        {
            _debugEnabled = enabled;
            try
            {
                EditorPrefs.SetBool(EditorPrefKeys.DebugLogs, enabled);
            }
            catch { }
        }

        public static void Debug(string message)
        {
            if (!IsDebugEnabled())
                return;
            Write(LogType.Log, $"{DebugPrefix} {HttpEndpointUtility.RedactUrls(message)}");
        }

        public static void Info(string message, bool always = true)
        {
            if (!always && !IsDebugEnabled())
                return;
            Write(LogType.Log, $"{InfoPrefix} {HttpEndpointUtility.RedactUrls(message)}");
        }

        public static void Warn(string message)
        {
            Write(LogType.Warning, $"{WarnPrefix} {HttpEndpointUtility.RedactUrls(message)}");
        }

        public static void Error(string message)
        {
            Write(LogType.Error, $"{ErrorPrefix} {HttpEndpointUtility.RedactUrls(message)}");
        }
    }
}
