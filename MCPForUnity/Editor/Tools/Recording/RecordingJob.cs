using System;
using System.IO;
using MCPForUnity.Runtime.Helpers;
using Newtonsoft.Json.Linq;
using UnityEditor.Media;
using UnityEngine;

namespace MCPForUnity.Editor.Tools.Recording
{
    internal interface IRecordingEncoder : IDisposable
    {
        bool AddFrame(Texture2D frame, double elapsed);
    }

    internal sealed class UnityRecordingEncoder : IRecordingEncoder
    {
        private readonly MediaEncoder _encoder;
        private long _lastTimestamp = -1;

        internal UnityRecordingEncoder(string path, RecordingOptions options)
        {
            _encoder = new MediaEncoder(
                path,
                new VideoTrackAttributes
                {
                    width = (uint)options.Width,
                    height = (uint)options.Height,
                    frameRate = new MediaRational(options.Fps),
                    includeAlpha = false,
                }
            );
        }

        public bool AddFrame(Texture2D frame, double elapsed)
        {
            long timestamp = Math.Max(_lastTimestamp + 1, (long)Math.Round(elapsed * 1000000));
            bool added = _encoder.AddFrame(frame, new MediaTime(timestamp, 1000000, 1));
            if (added)
                _lastTimestamp = timestamp;
            return added;
        }

        public void Dispose() => _encoder.Dispose();
    }

    /// <summary>Owns one encoder, staging file and monotonic sampling schedule. All calls run on the Editor thread.</summary>
    internal sealed class RecordingJob
    {
        internal readonly string Id;
        internal readonly RecordingOptions Options;
        internal readonly string OutputPath;
        internal readonly double StartedAt;
        internal string Status { get; private set; } = "recording";
        internal int Frames { get; private set; }
        internal bool IsActive => Status == "recording";
        private readonly string _permittedRoot;
        private readonly string _stagingDirectory;
        private readonly string _stagingFile;
        private IRecordingEncoder _encoder;
        private Action _cleanup;
        private int _lastSlot = -1;
        private long _pixelFrames;
        private double _endedAt;
        private double _lastFrameElapsed;
        private string _error;
        private string _reason;
        private bool _published;

        internal RecordingJob(
            string id,
            RecordingOptions options,
            string projectRoot,
            string outputPath,
            double now,
            Func<string, RecordingOptions, IRecordingEncoder> createEncoder
        )
        {
            Id = id;
            Options = options;
            StartedAt = now;
            _permittedRoot = SafePathUtility.ResolveWithinRoot(projectRoot, RecordingOptions.DefaultFolder);
            OutputPath = SafePathUtility.ResolveWithinRoot(_permittedRoot, outputPath);
            _stagingDirectory = SafePathUtility.ResolveWithinRoot(
                _permittedRoot,
                Path.Combine(Path.GetDirectoryName(OutputPath), ".mcp-recording-" + Guid.NewGuid().ToString("N"))
            );
            _stagingFile = Path.Combine(_stagingDirectory, "video.mp4");
            try
            {
                Directory.CreateDirectory(_stagingDirectory);
                SafePathUtility.ResolveWithinRoot(_permittedRoot, _stagingFile);
                _encoder = createEncoder(_stagingFile, options);
                if (_encoder == null)
                    throw new InvalidOperationException("Video encoder could not be created.");
            }
            catch
            {
                DeleteStaging();
                throw;
            }
        }

        internal void SetCleanup(Action cleanup) => _cleanup = cleanup;

        internal bool IsDue(double now) =>
            IsActive
            && now - StartedAt < Options.Duration
            && Frames < Options.MaxFrames
            && (int)Math.Floor(Math.Max(0, now - StartedAt) * Options.Fps) > _lastSlot;

        internal void Tick(double now)
        {
            if (IsActive && (now - StartedAt >= Options.Duration || Frames >= Options.MaxFrames))
                Finish("completed", "duration_reached", now);
        }

        internal void AddFrame(Texture2D frame, int sourceWidth, int sourceHeight, double now)
        {
            if (!IsDue(now))
                return;
            ValidateSourceBudget(sourceWidth, sourceHeight);
            long cost = (long)sourceWidth * sourceHeight + (long)Options.Width * Options.Height;
            double elapsed = Math.Max(0, now - StartedAt);
            if (!_encoder.AddFrame(frame, elapsed))
                throw new InvalidOperationException("Unity MediaEncoder rejected a video frame.");
            _pixelFrames += cost;
            Frames++;
            _lastSlot = (int)Math.Floor(elapsed * Options.Fps);
            _lastFrameElapsed = elapsed;
        }

        internal void ValidateSourceBudget(int sourceWidth, int sourceHeight)
        {
            ScreenshotUtility.ValidateFrameDimensions(sourceWidth, sourceHeight);
            long cost = (long)sourceWidth * sourceHeight + (long)Options.Width * Options.Height;
            if (cost > RecordingOptions.MaxPixelFrames - _pixelFrames)
                throw new InvalidOperationException(
                    "Recording reached the source plus encoded pixel-frame budget; reduce capture dimensions, fps or duration."
                );
        }

        internal void Finish(string status, string reason, double now, string error = null)
        {
            if (!IsActive)
                return;
            Status = status;
            _reason = reason;
            _endedAt = Math.Max(StartedAt, now);
            _error = error;
            try
            {
                _cleanup?.Invoke();
            }
            catch (Exception ex)
            {
                AppendError("Capture cleanup failed: " + ex.Message);
            }
            finally
            {
                _cleanup = null;
            }
            try
            {
                _encoder?.Dispose();
            }
            catch (Exception ex)
            {
                AppendError("Video finalization failed: " + ex.Message);
            }
            finally
            {
                _encoder = null;
            }

            try
            {
                if (_error == null && (status == "completed" || status == "stopped"))
                {
                    if (Frames == 0)
                        throw new InvalidOperationException("No frames were captured. Keep the requested view visible and Game View focused during Play Mode.");
                    SafePathUtility.ResolveWithinRoot(_permittedRoot, _stagingFile);
                    if (!File.Exists(_stagingFile) || new FileInfo(_stagingFile).Length == 0)
                        throw new IOException("Video encoder produced no output file.");
                    SafePathUtility.ResolveWithinRoot(_permittedRoot, OutputPath);
                    // File.Move on the supported Unity/.NET versions fails when the destination exists.
                    File.Move(_stagingFile, OutputPath);
                    _published = true;
                }
            }
            catch (Exception ex)
            {
                AppendError(ex.Message);
            }
            finally
            {
                try
                {
                    DeleteStaging();
                }
                catch (Exception ex)
                {
                    AppendError("Partial video cleanup failed: " + ex.Message);
                }
            }
            if (_error != null)
                Status = "failed";
        }

        private void AppendError(string message) => _error = string.IsNullOrEmpty(_error) ? message : _error + " " + message;

        private void DeleteStaging()
        {
            SafePathUtility.ResolveWithinRoot(_permittedRoot, _stagingFile);
            if (File.Exists(_stagingFile))
                File.Delete(_stagingFile);
            SafePathUtility.ResolveWithinRoot(_permittedRoot, _stagingDirectory);
            if (Directory.Exists(_stagingDirectory))
                Directory.Delete(_stagingDirectory, false);
        }

        internal JObject Snapshot(double now)
        {
            double elapsed = Math.Max(0, (IsActive ? now : _endedAt) - StartedAt);
            int sampleSlots = Math.Min(Options.MaxFrames, (int)Math.Ceiling(Math.Min(elapsed, Options.Duration) * Options.Fps));
            return new JObject
            {
                ["job_id"] = Id,
                ["status"] = Status,
                ["terminal"] = !IsActive,
                ["capture_source"] = Options.Source,
                ["elapsed_seconds"] = elapsed,
                ["duration_seconds"] = Options.Duration,
                ["frames_recorded"] = Frames,
                ["requested_fps"] = Options.Fps,
                ["average_capture_fps"] = elapsed > 0 ? Frames / elapsed : 0,
                ["skipped_sample_slots"] = Math.Max(0, sampleSlots - Frames),
                ["last_frame_seconds"] = _lastFrameElapsed,
                ["width"] = Options.Width,
                ["height"] = Options.Height,
                ["pixel_frames_processed"] = _pixelFrames,
                ["variable_frame_rate"] = true,
                ["audio_recorded"] = false,
                ["reason"] = _reason,
                ["error"] = _error,
                ["output_path"] = _published ? OutputPath.Replace('\\', '/') : null,
            };
        }
    }
}
