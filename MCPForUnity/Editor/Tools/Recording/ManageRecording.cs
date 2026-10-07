using System;
using System.Collections.Generic;
using System.IO;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Editor.Tools.Recording;
using MCPForUnity.Runtime.Helpers;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEngine;

namespace MCPForUnity.Editor.Tools
{
    [InitializeOnLoad]
    [McpForUnityTool(
        "manage_recording",
        AutoRegister = false,
        Description = "Start, poll and stop bounded silent MP4 recordings of Game View or Scene View. Poll status explicitly using job_id."
    )]
    public static class ManageRecording
    {
        private const string HistoryKey = "MCPForUnity.Recording.History";
        private const int HistoryLimit = 8;
        private static readonly List<JObject> History = new List<JObject>();
        private static RecordingJob _active;
        private static SceneView _sceneView;
        private static RecordingFramePump _pump;
        private static RenderTexture _scaledRenderTexture;
        private static Texture2D _scaledTexture;
        private static bool _sceneCaptureQueued;
        private static double _lastFrameAt;

        static ManageRecording()
        {
            try
            {
                foreach (var entry in JArray.Parse(SessionState.GetString(HistoryKey, "[]")))
                    if (entry is JObject summary && History.Count < HistoryLimit)
                        History.Add(summary);
            }
            catch (JsonException ex)
            {
                McpLog.Debug("Recording history unavailable: " + ex.Message);
            }
        }

        public static object HandleCommand(JObject parameters)
        {
            try
            {
                var p = new ToolParams(parameters ?? new JObject());
                switch (p.Get("action"))
                {
                    case "capabilities":
                        return new SuccessResponse("Recording capabilities.", Capabilities());
                    case "start":
                        return Start(p);
                    case "status":
                        return Status(p.Get("job_id"), false);
                    case "stop":
                        return Status(p.Get("job_id"), true);
                    default:
                        return new ErrorResponse("action must be 'capabilities', 'start', 'status' or 'stop'.");
                }
            }
            catch (Exception ex)
            {
                return new ErrorResponse("Recording request failed: " + ex.Message);
            }
        }

        private static JObject Capabilities()
        {
            bool platformSupported = Application.platform == RuntimePlatform.WindowsEditor || Application.platform == RuntimePlatform.OSXEditor;
            return new JObject
            {
                ["supported"] = platformSupported && !Application.isBatchMode,
                ["platform"] = Application.platform.ToString(),
                ["reason"] =
                    !platformSupported ? "Unity MediaEncoder MP4 recording is supported here only on Windows and macOS Editors; Linux recording is unsupported."
                    : Application.isBatchMode ? "Recording requires an interactive Editor with a rendered view."
                    : null,
                ["format"] = "mp4",
                ["encoder"] = "UnityEditor.Media.MediaEncoder",
                ["codec_support"] = "Encoder creation is checked at start; native codec availability depends on the Editor and operating system.",
                ["capture_sources"] = new JArray("game_view", "scene_view"),
                ["game_view_requires_play_mode"] = true,
                ["game_view_requires_visible_focused_view"] = true,
                ["scene_view_requires_visible_view"] = true,
                ["audio_recorded"] = false,
                ["max_duration_seconds"] = RecordingOptions.MaxDurationSeconds,
                ["max_fps"] = RecordingOptions.MaxFps,
                ["max_dimension"] = RecordingOptions.MaxDimension,
                ["max_pixel_frames"] = RecordingOptions.MaxPixelFrames,
                ["output_root"] = RecordingOptions.DefaultFolder,
                ["resolution_policy"] = "Frames are scaled to the requested even width/height; differing aspect ratios are stretched.",
            };
        }

        private static object Start(ToolParams p)
        {
            if (_active != null && _active.IsActive)
                return new ErrorResponse(
                    "A recording is already active. Stop it by job_id before starting another.",
                    _active.Snapshot(EditorApplication.timeSinceStartup)
                );
            var options = new RecordingOptions(
                p.Get("capture_source", "game_view"),
                p.GetInt("width", 1280).Value,
                p.GetInt("height", 720).Value,
                p.GetInt("fps", 15).Value,
                ReadDuration(p)
            );
            var capabilities = Capabilities();
            if (!capabilities.Value<bool>("supported"))
                return new ErrorResponse(capabilities.Value<string>("reason"), capabilities);
            if (EditorApplication.isCompiling || EditorApplication.isUpdating || EditorApplication.isPlayingOrWillChangePlaymode != EditorApplication.isPlaying)
                return new ErrorResponse("Wait until the Editor finishes compiling, importing or changing Play Mode before recording.");
            if (options.Source == "game_view" && (!EditorApplication.isPlaying || EditorApplication.isPaused))
                return new ErrorResponse("Game View recording requires running, unpaused Play Mode and a visible, focused Game View.");
            var sceneView = options.Source == "scene_view" ? SceneView.lastActiveSceneView : null;
            if (options.Source == "scene_view" && (sceneView == null || sceneView.camera == null))
                return new ErrorResponse("Open a visible Scene View before starting scene_view recording.");
            string id = Guid.NewGuid().ToString("N");
            string projectRoot = Path.GetFullPath(Path.Combine(Application.dataPath, ".."));
            string outputPath = RecordingOptions.ResolveOutputPath(projectRoot, p.Get("output_folder"), p.Get("file_name"), id);
            double now = EditorApplication.timeSinceStartup;
            var job = new RecordingJob(id, options, projectRoot, outputPath, now, (path, config) => new UnityRecordingEncoder(path, config));
            _active = job;
            _sceneView = sceneView;
            _lastFrameAt = now;
            job.SetCleanup(Cleanup);
            try
            {
                EditorApplication.update += Tick;
                EditorApplication.playModeStateChanged += OnPlayModeChanged;
                AssemblyReloadEvents.beforeAssemblyReload += BeforeReload;
                EditorApplication.quitting += BeforeQuit;
                if (options.Source == "game_view")
                {
                    _pump = RecordingFramePump.Begin(CaptureGameFrame, PumpDestroyed);
                    EditorApplication.QueuePlayerLoopUpdate();
                }
                else
                {
                    SceneView.duringSceneGui += OnSceneGui;
                    sceneView.Repaint();
                }
            }
            catch (Exception ex)
            {
                job.Finish("failed", "capture_setup_failed", EditorApplication.timeSinceStartup, ex.Message);
                Remember(job);
                return new ErrorResponse("Recording could not start: " + ex.Message, job.Snapshot(EditorApplication.timeSinceStartup));
            }
            return new SuccessResponse("Recording started. Poll status with job_id or stop to finalize the video.", job.Snapshot(now));
        }

        private static double ReadDuration(ToolParams p)
        {
            var token = p.GetRaw("duration_seconds");
            if (token == null)
                return 10;
            if (token.Type != JTokenType.Integer && token.Type != JTokenType.Float)
                throw new ArgumentException("duration_seconds must be a finite JSON number.");
            return token.Value<double>();
        }

        private static object Status(string id, bool stop)
        {
            if (string.IsNullOrWhiteSpace(id))
                return new ErrorResponse("job_id is required for recording status/stop.");
            if (_active != null && _active.Id == id)
            {
                if (stop)
                    _active.Finish("stopped", "stop_requested", EditorApplication.timeSinceStartup);
                else
                    _active.Tick(EditorApplication.timeSinceStartup);
                Remember(_active);
                return new SuccessResponse("Recording " + _active.Status + ".", _active.Snapshot(EditorApplication.timeSinceStartup));
            }
            var summary = History.Find(entry => entry.Value<string>("job_id") == id);
            return summary == null
                ? (object)new ErrorResponse("Unknown or expired recording job_id (the latest eight terminal jobs are retained in this Editor session).")
                : new SuccessResponse("Recording " + summary.Value<string>("status") + ".", summary.DeepClone());
        }

        private static void Tick()
        {
            var job = _active;
            if (job == null || !job.IsActive)
                return;
            double now = EditorApplication.timeSinceStartup;
            job.Tick(now);
            if (job.IsActive && job.Options.Source == "game_view" && (!EditorApplication.isPlaying || EditorApplication.isPaused))
                job.Finish("interrupted", "play_mode_paused_or_stopped", now);
            if (job.IsActive && now - _lastFrameAt > 5)
                job.Finish(
                    "failed",
                    "frame_timeout",
                    now,
                    "No rendered frame arrived for five seconds. Keep the capture view visible and Game View focused and unpaused."
                );
            if (_sceneView != null && !_sceneCaptureQueued && job.IsDue(now))
                _sceneView.Repaint();
            Remember(job);
        }

        private static void CaptureGameFrame() => CaptureFrame(false);

        private static void CaptureSceneFrame()
        {
            _sceneCaptureQueued = false;
            CaptureFrame(true);
        }

        private static void OnSceneGui(SceneView sceneView)
        {
            if (
                _active == null
                || !_active.IsActive
                || sceneView != _sceneView
                || Event.current.type != EventType.Repaint
                || _sceneCaptureQueued
                || !_active.IsDue(EditorApplication.timeSinceStartup)
            )
                return;
            // GrabPixels after the whole Scene View repaint has completed, including overlays.
            _sceneCaptureQueued = true;
            EditorApplication.delayCall += CaptureSceneFrame;
        }

        private static void CaptureFrame(bool scene)
        {
            var job = _active;
            if (job == null || !job.IsDue(EditorApplication.timeSinceStartup))
                return;
            Texture2D frame = null;
            try
            {
                if (scene)
                {
                    if (_sceneView == null)
                        throw new InvalidOperationException("The recorded Scene View was closed.");
                    frame = EditorWindowScreenshotUtility.CaptureSceneViewViewport(_sceneView, job.ValidateSourceBudget);
                }
                else
                {
                    job.ValidateSourceBudget(Screen.width, Screen.height);
                    frame = ScreenCapture.CaptureScreenshotAsTexture();
                }
                if (frame == null)
                    throw new InvalidOperationException("Capture returned no rendered frame.");
                var encoded = ScaleFrame(frame, job.Options);
                job.AddFrame(encoded, frame.width, frame.height, EditorApplication.timeSinceStartup);
                _lastFrameAt = EditorApplication.timeSinceStartup;
            }
            catch (Exception ex)
            {
                job.Finish("failed", "capture_failed", EditorApplication.timeSinceStartup, ex.Message);
            }
            finally
            {
                if (frame != null)
                    UnityEngine.Object.DestroyImmediate(frame);
                Remember(job);
            }
        }

        private static Texture2D ScaleFrame(Texture2D frame, RecordingOptions options)
        {
            if (frame.width == options.Width && frame.height == options.Height && frame.format == TextureFormat.RGBA32)
                return frame;
            if (_scaledRenderTexture == null)
            {
                _scaledRenderTexture = new RenderTexture(options.Width, options.Height, 0, RenderTextureFormat.ARGB32)
                {
                    hideFlags = HideFlags.HideAndDontSave,
                };
                if (!_scaledRenderTexture.Create())
                    throw new InvalidOperationException("Could not create recording resize render target.");
                _scaledTexture = new Texture2D(options.Width, options.Height, TextureFormat.RGBA32, false) { hideFlags = HideFlags.HideAndDontSave };
            }
            var previous = RenderTexture.active;
            try
            {
                UnityEngine.Graphics.Blit(frame, _scaledRenderTexture);
                RenderTexture.active = _scaledRenderTexture;
                _scaledTexture.ReadPixels(new Rect(0, 0, options.Width, options.Height), 0, 0);
                _scaledTexture.Apply(false, false);
                return _scaledTexture;
            }
            finally
            {
                RenderTexture.active = previous;
            }
        }

        private static void Cleanup()
        {
            EditorApplication.update -= Tick;
            EditorApplication.delayCall -= CaptureSceneFrame;
            EditorApplication.playModeStateChanged -= OnPlayModeChanged;
            AssemblyReloadEvents.beforeAssemblyReload -= BeforeReload;
            EditorApplication.quitting -= BeforeQuit;
            SceneView.duringSceneGui -= OnSceneGui;
            _sceneCaptureQueued = false;
            _sceneView = null;
            try
            {
                if (_pump != null)
                    _pump.Cancel();
            }
            finally
            {
                _pump = null;
                if (_scaledTexture != null)
                    UnityEngine.Object.DestroyImmediate(_scaledTexture);
                _scaledTexture = null;
                if (_scaledRenderTexture != null)
                {
                    _scaledRenderTexture.Release();
                    UnityEngine.Object.DestroyImmediate(_scaledRenderTexture);
                }
                _scaledRenderTexture = null;
            }
        }

        private static void Interrupt(string reason)
        {
            if (_active == null || !_active.IsActive)
                return;
            _active.Finish("interrupted", reason, EditorApplication.timeSinceStartup);
            Remember(_active);
        }

        private static void BeforeReload() => Interrupt("assembly_reload");

        private static void BeforeQuit() => Interrupt("editor_quit");

        private static void PumpDestroyed()
        {
            _pump = null; // OnDestroy is already in progress; cleanup must not destroy the helper twice.
            Interrupt("capture_helper_destroyed");
        }

        private static void OnPlayModeChanged(PlayModeStateChange state)
        {
            if (state == PlayModeStateChange.ExitingPlayMode || state == PlayModeStateChange.ExitingEditMode)
                Interrupt("play_mode_changed");
        }

        private static void Remember(RecordingJob job)
        {
            if (job.IsActive)
                return;
            History.RemoveAll(entry => entry.Value<string>("job_id") == job.Id);
            History.Add(job.Snapshot(EditorApplication.timeSinceStartup));
            if (History.Count > HistoryLimit)
                History.RemoveAt(0);
            SessionState.SetString(HistoryKey, new JArray(History).ToString(Formatting.None));
        }
    }
}
