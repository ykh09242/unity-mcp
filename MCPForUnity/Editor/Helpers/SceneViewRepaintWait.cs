using System;
using System.Threading;
using System.Threading.Tasks;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEngine;

namespace MCPForUnity.Editor.Helpers
{
    /// <summary>Owns one main-thread observation through the end of a matching Scene View repaint.</summary>
    internal sealed class SceneViewRepaintWait : IDisposable
    {
        private readonly SceneView _view;
        private readonly CancellationToken _token;
        private readonly Func<double> _clock;
        private readonly double _deadline;
        private readonly TaskCompletionSource<bool> _completion = new(TaskCreationOptions.RunContinuationsAsynchronously);
        private bool _queued;
        private bool _finished;

        internal Task Task => _completion.Task;

        internal static Task WaitAsync(SceneView view, double seconds, CancellationToken token) => new SceneViewRepaintWait(view, seconds, token).Task;

        internal static double ReadTimeout(JToken raw)
        {
            if (raw == null || raw.Type == JTokenType.Null)
                return 300;
            if (raw.Type != JTokenType.Integer)
                throw new ArgumentException("timeout_seconds must be an integer between 1 and 86400.");
            long seconds = raw.Value<long>();
            if (seconds < 1 || seconds > 86400)
                throw new ArgumentOutOfRangeException(nameof(raw), "timeout_seconds must be between 1 and 86400.");
            return seconds;
        }

        internal SceneViewRepaintWait(SceneView view, double seconds, CancellationToken token, Func<double> clock = null)
        {
            if (view == null)
                throw new ArgumentNullException(nameof(view));
            if (double.IsNaN(seconds) || double.IsInfinity(seconds) || seconds <= 0 || seconds > 86400)
                throw new ArgumentOutOfRangeException(nameof(seconds));
            token.ThrowIfCancellationRequested();
            _view = view;
            _token = token;
            _clock = clock ?? (() => EditorApplication.timeSinceStartup);
            _deadline = _clock() + seconds;
            SceneView.duringSceneGui += OnSceneGui;
            EditorApplication.update += Tick;
            EditorApplication.quitting += Dispose;
            AssemblyReloadEvents.beforeAssemblyReload += Dispose;
            try
            {
                view.Repaint();
                EditorApplication.QueuePlayerLoopUpdate();
            }
            catch (Exception error)
            {
                Finish(error);
            }
        }

        private bool CanContinue()
        {
            if (_finished)
                return false;
            if (_token.IsCancellationRequested)
            {
                Dispose();
                return false;
            }
            if (_view == null)
            {
                Finish(new InvalidOperationException("The captured Scene View was closed before its frame completed."));
                return false;
            }
            if (_clock() >= _deadline)
            {
                Finish(new TimeoutException("Scene View did not finish repainting within timeout_seconds. No screenshot was written."));
                return false;
            }
            return true;
        }

        private void Tick()
        {
            // Editor updates cannot run inside the preceding OnGUI call. Some Editor
            // activities defer inspector delay calls even while the view keeps repainting.
            if (_queued)
                AfterRepaint();
            else
                CanContinue();
        }

        private void OnSceneGui(SceneView view) => ObserveEvent(view, Event.current?.type ?? EventType.Ignore);

        private void ObserveEvent(SceneView view, EventType eventType)
        {
            if (_finished || _queued || view != _view || eventType != EventType.Repaint || !CanContinue())
                return;
            // This event runs inside OnGUI; delay readback until the complete repaint, including overlays, has returned.
            _queued = true;
            EditorApplication.delayCall += AfterRepaint;
        }

        private void AfterRepaint()
        {
            if (!_queued || !CanContinue())
                return;
            Finish(null);
        }

        private void Detach()
        {
            _finished = true;
            _queued = false;
            SceneView.duringSceneGui -= OnSceneGui;
            EditorApplication.update -= Tick;
            EditorApplication.delayCall -= AfterRepaint;
            EditorApplication.quitting -= Dispose;
            AssemblyReloadEvents.beforeAssemblyReload -= Dispose;
        }

        private void Finish(Exception error)
        {
            if (_finished)
                return;
            Detach();
            if (error == null)
                _completion.TrySetResult(true);
            else
                _completion.TrySetException(error);
        }

        public void Dispose()
        {
            if (_finished)
                return;
            Detach();
            _completion.TrySetCanceled(_token);
        }
    }
}
