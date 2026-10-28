using System;
using UnityEditor.TestTools.TestRunner.Api;

namespace MCPForUnity.Editor.Services
{
    /// <summary>
    /// Thread-safe, minimal shared status for Unity Test Runner execution.
    /// Used by editor readiness snapshots so callers can avoid starting overlapping runs.
    /// </summary>
    internal static class TestRunStatus
    {
        private static readonly object LockObj = new();

        private static bool _isRunning;
        private static TestMode? _mode;
        private static long? _startedUnixMs;
        private static long? _finishedUnixMs;
        private static TestRunSummary _summary;

        public static TestRunSummary Summary
        {
            get
            {
                lock (LockObj)
                    return _summary;
            }
        }

        public static bool IsRunning
        {
            get
            {
                lock (LockObj)
                    return _isRunning;
            }
        }

        public static TestMode? Mode
        {
            get
            {
                lock (LockObj)
                    return _mode;
            }
        }

        public static long? StartedUnixMs
        {
            get
            {
                lock (LockObj)
                    return _startedUnixMs;
            }
        }

        public static long? FinishedUnixMs
        {
            get
            {
                lock (LockObj)
                    return _finishedUnixMs;
            }
        }

        public static void MarkStarted(TestMode mode)
        {
            lock (LockObj)
            {
                _isRunning = true;
                _mode = mode;
                _startedUnixMs = DateTimeOffset.UtcNow.ToUnixTimeMilliseconds();
                _finishedUnixMs = null;
                _summary = null;
            }
        }

        public static void MarkFinished(TestRunSummary summary = null)
        {
            lock (LockObj)
            {
                _isRunning = false;
                // Completion callbacks and finally/cleanup can both arrive for a run.
                // Retain the observed result and time until the next MarkStarted.
                _finishedUnixMs ??= DateTimeOffset.UtcNow.ToUnixTimeMilliseconds();
                _summary ??= summary;
                _mode = null;
            }
        }
    }
}
