using System;
using System.Diagnostics;
using System.Security.Cryptography;
using MCPForUnity.Runtime.Helpers;
using UnityEngine;
using UnityEngine.Rendering;

namespace MCPForUnity.Editor.Services.PlayScenarios
{
    public interface IPlayScenarioStepObserver : IDisposable
    {
        void Begin(PlayScenarioStep step, PlayScenarioStepResult result, long now);
        bool Complete(long now);
        void Abort(string reason, long now);
    }

    /// <summary>Owns one real-frame sampler and one optional composited screenshot checkpoint.</summary>
    public sealed class PlayScenarioStepObserver : IPlayScenarioStepObserver
    {
        private readonly Func<PlayScenarioStepResult, byte[], string> saveScreenshot;
        private readonly Func<bool> captureAllowed;
        private PlayScenarioStep step;
        private PlayScenarioStepResult result;
        private PlayScenarioPerformanceSamples samples;
        private PlayScenarioFrameSampler sampler;
        private RecordingFramePump pump;
        private readonly Stopwatch elapsed = new Stopwatch();
        private long started;
        private long captureDeadline;
        private bool completed;

        public PlayScenarioStepObserver(Func<PlayScenarioStepResult, byte[], string> saveScreenshot = null, Func<bool> captureAllowed = null)
        {
            this.saveScreenshot = saveScreenshot;
            this.captureAllowed = captureAllowed;
        }

        public void Begin(PlayScenarioStep step, PlayScenarioStepResult result, long now)
        {
            Abort("The previous step observation ended before completion.", now);
            this.step = step;
            this.result = result;
            started = now;
            completed = false;
            elapsed.Restart();
            if (step.Performance == null)
                return;
            samples = new PlayScenarioPerformanceSamples(step.Performance);
            if (!step.Performance.MaxFrameTimeP95Ms.HasValue && !step.Performance.MaxGcAllocatedBytesPerFrame.HasValue)
                return;
            try
            {
                if (!Application.isPlaying)
                    throw new InvalidOperationException("Per-frame performance measurement requires a running Unity Player loop.");
                sampler = PlayScenarioFrameSampler.Begin(samples, step.Performance.MaxGcAllocatedBytesPerFrame.HasValue);
            }
            catch (Exception error)
            {
                samples.Unavailable(error.GetType().Name + ": " + error.Message);
            }
        }

        public bool Complete(long now)
        {
            if (result == null)
                return true;
            if (!completed)
            {
                completed = true;
                FinishPerformance(now);
                if (step.Screenshot != null)
                    BeginCapture(now);
            }
            if (pump != null && now >= captureDeadline)
                StopCapture("unavailable", "No rendered frame arrived within the two-second screenshot deadline.");
            return pump == null;
        }

        private void FinishPerformance(long now)
        {
            StopSampler();
            elapsed.Stop();
            if (samples != null)
            {
                result.Performance = samples.Finish(Math.Max(now - started, elapsed.Elapsed.TotalMilliseconds));
                samples = null;
            }
        }

        private void BeginCapture(long now)
        {
            result.Screenshot = new PlayScenarioScreenshotResult
            {
                Name = step.Screenshot.Name,
                RequestedWidth = step.Screenshot.Width,
                RequestedHeight = step.Screenshot.Height,
            };
            if (
                saveScreenshot == null
                || Application.isBatchMode
                || !Application.isPlaying
                || SystemInfo.graphicsDeviceType == GraphicsDeviceType.Null
                || captureAllowed?.Invoke() == false
            )
            {
                StopCapture(
                    "unavailable",
                    "Checkpoint capture requires a running graphical host and an owned screenshot output directory; batch/headless capture is unavailable."
                );
                return;
            }
            captureDeadline = now + 2000;
            try
            {
                pump = RecordingFramePump.Begin(
                    CaptureFrame,
                    () => StopCapture("unavailable", "The screenshot frame source was destroyed before capture completed.")
                );
            }
            catch (Exception error)
            {
                StopCapture("failed", error.GetType().Name + ": " + error.Message);
            }
        }

        private void CaptureFrame()
        {
            Texture2D frame = null;
            try
            {
                if (Screen.width < 1 || Screen.height < 1 || Screen.width > 4096 || Screen.height > 4096 || (long)Screen.width * Screen.height > 8388608)
                    throw new InvalidOperationException("Rendered dimensions exceed the bounded screenshot budget.");
                frame = ScreenCapture.CaptureScreenshotAsTexture();
                if (frame == null)
                    throw new InvalidOperationException("No rendered composited frame was returned.");
                result.Screenshot.Width = frame.width;
                result.Screenshot.Height = frame.height;
                if (frame.width != step.Screenshot.Width || frame.height != step.Screenshot.Height)
                    throw new InvalidOperationException("Rendered screenshot dimensions do not match the requested resolution.");
                byte[] png = frame.EncodeToPNG();
                if (png == null || png.Length == 0 || png.Length > 4 * 1024 * 1024)
                    throw new InvalidOperationException("Checkpoint PNG exceeds the four-MiB screenshot budget.");
                using (SHA256 hash = SHA256.Create())
                    result.Screenshot.Sha256 = BitConverter.ToString(hash.ComputeHash(png)).Replace("-", "").ToLowerInvariant();
                result.Screenshot.Path = saveScreenshot(result, png);
                if (string.IsNullOrEmpty(result.Screenshot.Path))
                    throw new InvalidOperationException("Screenshot persistence returned no artifact path.");
                StopCapture("captured", null);
            }
            catch (Exception error)
            {
                result.Screenshot.Path = null;
                result.Screenshot.Sha256 = null;
                StopCapture("failed", error.GetType().Name + ": " + error.Message);
            }
            finally
            {
                if (frame != null)
                    UnityEngine.Object.DestroyImmediate(frame);
            }
        }

        private void StopCapture(string status, string reason)
        {
            if (result?.Screenshot != null && result.Screenshot.Status == "pending")
            {
                result.Screenshot.Status = status;
                result.Screenshot.Error = PlayScenarioEngine.Bounded(reason, 1024);
            }
            RecordingFramePump active = pump;
            pump = null;
            if (active != null)
                active.Cancel();
        }

        private void StopSampler()
        {
            PlayScenarioFrameSampler active = sampler;
            sampler = null;
            if (active != null)
                active.Stop();
        }

        public void Abort(string reason, long now)
        {
            if (result != null)
            {
                FinishPerformance(now);
                StopCapture("unavailable", reason);
                if (step.Screenshot != null && result.Screenshot == null)
                    result.Screenshot = new PlayScenarioScreenshotResult
                    {
                        Name = step.Screenshot.Name,
                        RequestedWidth = step.Screenshot.Width,
                        RequestedHeight = step.Screenshot.Height,
                        Status = "unavailable",
                        Error = PlayScenarioEngine.Bounded(reason, 1024),
                    };
            }
            result = null;
            step = null;
        }

        public void Dispose() => Abort("Step observation resources were released before capture completed.", started);
    }
}
