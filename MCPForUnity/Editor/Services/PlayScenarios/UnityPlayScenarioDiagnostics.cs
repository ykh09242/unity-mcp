using System;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Runtime.Helpers;
using UnityEditor;
using UnityEngine;
using UnityEngine.Profiling;
using UnityEngine.Rendering;
using UnityEngine.SceneManagement;

namespace MCPForUnity.Editor.Services.PlayScenarios
{
    /// <summary>Samples only at iteration boundaries; returned values never retain Unity objects.</summary>
    internal static class UnityPlayScenarioDiagnostics
    {
        internal static PlayScenarioMetricsSnapshot CaptureMetrics(int iteration, long now, int runnerHandleCount)
        {
            var snapshot = new PlayScenarioMetricsSnapshot
            {
                Iteration = iteration,
                TimestampUnixMs = now,
                RunnerSubscriptionCount = PlayScenarioService.ActiveSubscriptionCount,
                RunnerHandleCount = runnerHandleCount,
            };
            try
            {
                snapshot.ManagedBytes = GC.GetTotalMemory(false);
                long allocated = Profiler.GetTotalAllocatedMemoryLong();
                if (allocated <= 0)
                    throw new InvalidOperationException("Unity allocated-memory statistics are unavailable.");
                snapshot.AllocatedBytes = allocated;
                GameObject[] objects = UnityEngine.Resources.FindObjectsOfTypeAll<GameObject>();
                if (objects.Length > 100000)
                    throw new InvalidOperationException("Object sampling exceeded the 100000-entry inspection limit.");
                int count = 0;
                foreach (GameObject item in objects)
                    if (
                        item != null
                        && item.scene.IsValid()
                        && item.scene.isLoaded
                        && !EditorUtility.IsPersistent(item)
                        && (item.hideFlags & HideFlags.HideAndDontSave) == 0
                    )
                        count++;
                snapshot.ObjectCount = count;
            }
            catch (Exception exception)
            {
                snapshot.Error = PlayScenarioEngine.Bounded(exception.GetType().Name + ": " + exception.Message, 1024);
            }
            return snapshot;
        }

        internal static PlayScenarioFailureDiagnostics DescribeFailure(PlayScenarioRun state, PlayScenarioStep step, long now)
        {
            var detail = new PlayScenarioFailureDiagnostics
            {
                CapturedUnixMs = now,
                ActiveScene = PlayScenarioEngine.Bounded(SceneManager.GetActiveScene().path, 4096),
                Target = step?.Target,
                Observation = PlayScenarioEngine.Bounded(state.Error ?? state.PendingError, 2048),
            };
            if (state.Cursor >= 0 && state.Cursor < state.Steps.Count)
                detail.Observation = PlayScenarioEngine.Bounded(state.Steps[state.Cursor].Detail ?? detail.Observation, 2048);
            try
            {
                detail.TargetDetail = step?.Target == null ? "The failed operation has no object target." : UnityPlayScenarioHost.DescribeTarget(step);
            }
            catch (Exception exception)
            {
                detail.TargetDetail = PlayScenarioEngine.Bounded("Target inspection unavailable: " + exception.Message, 2048);
            }
            return detail;
        }
    }

    /// <summary>Owns at most one end-of-frame capture and releases its callback, object and textures.</summary>
    internal sealed class PlayScenarioFailureCapture : IDisposable
    {
        private readonly PlayScenarioFailureDiagnostics _diagnostics;
        private readonly PlayScenarioStore _store;
        private readonly string _jobId;
        private readonly long _deadline;
        private RecordingFramePump _pump;
        internal bool Pending { get; private set; }

        internal PlayScenarioFailureCapture(PlayScenarioRun state, PlayScenarioStore store, long now, bool suppressCapture)
        {
            _diagnostics = state.FailureDiagnostics;
            _store = store;
            _jobId = state.JobId;
            _deadline = now + 2000;
            if (!state.Scenario.Diagnostics.ScreenshotOnFailure)
                return;
            if (
                suppressCapture
                || Application.isBatchMode
                || !EditorApplication.isPlaying
                || EditorApplication.isPaused
                || SystemInfo.graphicsDeviceType == GraphicsDeviceType.Null
            )
            {
                _diagnostics.ScreenshotError = "Game View capture requires an interactive, running, unpaused Editor with graphics; capture was unavailable.";
                return;
            }
            try
            {
                Pending = true;
                _pump = RecordingFramePump.Begin(CaptureFrame, PumpDestroyed);
                EditorApplication.QueuePlayerLoopUpdate();
            }
            catch (Exception exception)
            {
                Stop("Screenshot setup failed: " + exception.Message);
            }
        }

        internal void Tick(long now)
        {
            if (Pending && now >= _deadline)
                Stop("No rendered Game View frame arrived within the two-second capture deadline.");
        }

        private void CaptureFrame()
        {
            if (!Pending)
                return;
            Texture2D frame = null;
            Texture2D scaled = null;
            try
            {
                if (Screen.width <= 0 || Screen.height <= 0 || Screen.width > 4096 || Screen.height > 4096 || (long)Screen.width * Screen.height > 8388608)
                    throw new InvalidOperationException("Game View dimensions exceed the bounded screenshot budget.");
                frame = ScreenCapture.CaptureScreenshotAsTexture();
                if (frame == null)
                    throw new InvalidOperationException("No rendered frame was returned.");
                scaled = frame.width > 1280 || frame.height > 1280 ? ScreenshotUtility.DownscaleTexture(frame, 1280) : frame;
                _diagnostics.ScreenshotPath = _store.SaveFailureScreenshot(_jobId, scaled.EncodeToPNG());
            }
            catch (Exception exception)
            {
                _diagnostics.ScreenshotError = PlayScenarioEngine.Bounded(exception.GetType().Name + ": " + exception.Message, 1024);
            }
            finally
            {
                if (scaled != null && scaled != frame)
                    UnityEngine.Object.DestroyImmediate(scaled);
                if (frame != null)
                    UnityEngine.Object.DestroyImmediate(frame);
                Stop(null);
            }
        }

        private void PumpDestroyed() => Stop("The screenshot frame source was destroyed before capture completed.");

        internal void Stop(string reason)
        {
            if (Pending && _diagnostics.ScreenshotPath == null && _diagnostics.ScreenshotError == null && reason != null)
                _diagnostics.ScreenshotError = PlayScenarioEngine.Bounded(reason, 1024);
            Pending = false;
            var pump = _pump;
            _pump = null;
            if (pump != null)
                pump.Cancel();
        }

        public void Dispose() => Stop("Screenshot capture ended before a frame became available.");
    }
}
