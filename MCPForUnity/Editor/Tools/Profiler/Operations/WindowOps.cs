using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Linq;
using MCPForUnity.Editor.Helpers;
using Newtonsoft.Json.Linq;
using Unity.Profiling;
using Unity.Profiling.LowLevel;
using Unity.Profiling.LowLevel.Unsafe;
using UnityEditor;
using UnityEngine;

namespace MCPForUnity.Editor.Tools.Profiler
{
    // Independent owned recorders; never changes the global Profiler session or scenario observations.
    internal static class WindowOps
    {
        private const int MaximumActive = 4;
        private const int MaximumStored = 16;
        private const double RetentionSeconds = 120;
        private static readonly Dictionary<string, Window> windows = new Dictionary<string, Window>(StringComparer.Ordinal);
        private static bool subscribed;
        private static readonly List<Window> pruning = new(MaximumStored);
        private static readonly List<Window> sampling = new(MaximumActive);

        private sealed class Marker
        {
            internal string Name;
            internal string Unit;
            internal string UnavailableReason;
            internal ProfilerRecorder Recorder;
            internal bool Owned;
            internal string CleanupError;
            internal JObject Result;
        }

        private sealed class Window
        {
            internal string Id;
            internal string Category;
            internal int Frames;
            internal int Warmup;
            internal bool IncludeSamples;
            internal double Started;
            internal double Deadline;
            internal double Finished;
            internal string State = "warming";
            internal string Reason;
            internal bool Armed;
            internal bool Measuring;
            internal int LastFrame;
            internal int WarmupObserved;
            internal int FramesObserved;
            internal int UnobservedFrames;
            internal long CollectorTicks;
            internal List<Marker> Markers = new List<Marker>();
            internal bool Running => State == "warming" || State == "measuring";
        }

        internal static object Start(JObject parameters)
        {
            string categoryName = Text(parameters, "category", 64);
            var category = CounterOps.ResolveCategory(categoryName, out string categoryError);
            if (category == null)
                throw new ArgumentException(categoryError);
            if (!(parameters["markers"] is JArray names) || names.Count < 1 || names.Count > 8)
                throw new ArgumentException("markers must be an array of 1–8 distinct marker names.");
            var selected = new List<string>();
            foreach (JToken name in names)
            {
                if (name.Type != JTokenType.String || string.IsNullOrWhiteSpace(name.Value<string>()) || name.Value<string>().Length > 256)
                    throw new ArgumentException("Each marker must be a nonempty string of at most 256 characters.");
                if (selected.Contains(name.Value<string>()))
                    throw new ArgumentException("Duplicate marker names are not allowed.");
                selected.Add(name.Value<string>());
            }
            int frames = Integer(parameters, "frame_count", 1, 600, 60);
            int warmup = Integer(parameters, "warmup_frames", 0, 120, 0);
            int timeout = Integer(parameters, "timeout_seconds", 1, 120, 30);
            bool include = Boolean(parameters, "include_samples", false);
            Prune();
            if (windows.Values.Count(item => item.Running) >= MaximumActive || windows.Count >= MaximumStored)
                return new ErrorResponse("window_capacity_reached", new { maximum_active = MaximumActive, maximum_retained = MaximumStored });

            // Resolve before acquiring any recorder. Missing names remain explicit unavailable rows.
            var handles = new List<ProfilerRecorderHandle>();
            ProfilerRecorderHandle.GetAvailable(handles);
            if (handles.Count > 16384)
                return new ErrorResponse("window_marker_inventory_limit", new { maximum_handles = 16384 });
            var descriptions = handles
                .Select(handle => (handle, description: ProfilerRecorderHandle.GetDescription(handle)))
                .Where(item => string.Equals(item.description.Category.Name, category.Value.Name, StringComparison.OrdinalIgnoreCase))
                .ToArray();
            double now = EditorApplication.timeSinceStartup;
            var window = new Window
            {
                Id = Guid.NewGuid().ToString("N"),
                Category = category.Value.Name,
                Frames = frames,
                Warmup = warmup,
                IncludeSamples = include,
                Started = now,
                Deadline = now + timeout,
                LastFrame = Time.frameCount,
            };
            try
            {
                foreach (string name in selected)
                {
                    var marker = new Marker { Name = name };
                    window.Markers.Add(marker);
                    var found = descriptions.Where(item => item.description.Name == name).Take(2).ToArray();
                    if (found.Length != 1)
                    {
                        marker.UnavailableReason = found.Length == 0 ? "marker_not_registered_in_category" : "marker_identity_ambiguous";
                        continue;
                    }
                    marker.Unit = found[0].description.UnitType.ToString();
                    if (found[0].description.DataType != ProfilerMarkerDataType.Int64)
                    {
                        marker.UnavailableReason = "unsupported_data_type_" + found[0].description.DataType;
                        continue;
                    }
                    marker.Recorder = new ProfilerRecorder(
                        found[0].handle,
                        frames + 2,
                        ProfilerRecorderOptions.StartImmediately | ProfilerRecorderOptions.SumAllSamplesInFrame
                    );
                    marker.Owned = true;
                    if (!marker.Recorder.Valid)
                    {
                        marker.UnavailableReason = "native_recorder_unavailable";
                        Dispose(marker);
                    }
                }
                windows.Add(window.Id, window);
                Subscribe();
                EditorApplication.QueuePlayerLoopUpdate();
                return new SuccessResponse("Started a bounded independent profiling window.", Describe(window));
            }
            catch (Exception error)
            {
                Finish(window, "failed", "initialization_failed_" + error.GetType().Name);
                windows[window.Id] = window;
                Subscribe();
                return new ErrorResponse("window_start_failed", Describe(window));
            }
        }

        internal static object Query(JObject parameters, string action)
        {
            string id = Text(parameters, "window_id", 64);
            Prune();
            if (!windows.TryGetValue(id, out Window window))
                return new ErrorResponse(
                    "window_not_found",
                    new { window_id = id, reason = "Unknown, expired retention, or lost on domain reload/lifecycle transition." }
                );
            if (action == "window_cancel" && window.Running)
                Finish(window, "cancelled", "cancel_requested");
            if (action == "window_result" && window.Running)
                return new ErrorResponse("window_not_ready", Describe(window));
            return new SuccessResponse("Read independent profiling window.", Describe(window));
        }

        private static void Subscribe()
        {
            if (subscribed)
                return;
            EditorApplication.update += Tick;
            EditorApplication.playModeStateChanged += OnPlayMode;
            AssemblyReloadEvents.beforeAssemblyReload += Clear;
            EditorApplication.quitting += Clear;
            subscribed = true;
        }

        private static void UnsubscribeIfEmpty()
        {
            if (!subscribed || windows.Count != 0)
                return;
            EditorApplication.update -= Tick;
            EditorApplication.playModeStateChanged -= OnPlayMode;
            AssemblyReloadEvents.beforeAssemblyReload -= Clear;
            EditorApplication.quitting -= Clear;
            subscribed = false;
        }

        private static void OnPlayMode(PlayModeStateChange state) => Clear();

        private static void Clear()
        {
            foreach (Window window in windows.Values)
            foreach (Marker marker in window.Markers)
                Dispose(marker);
            windows.Clear();
            UnsubscribeIfEmpty();
        }

        private static void Prune()
        {
            double now = EditorApplication.timeSinceStartup;
            foreach (Window window in windows.Values)
                pruning.Add(window);
            try
            {
                foreach (Window window in pruning)
                {
                    bool owned = false;
                    foreach (Marker marker in window.Markers)
                    {
                        if (!window.Running && marker.Owned)
                            Dispose(marker);
                        owned |= marker.Owned;
                    }
                    if (window.Running && now >= window.Deadline)
                        Finish(window, "expired", "deadline_reached");
                    else if (!window.Running && now - window.Finished >= RetentionSeconds && !owned)
                        windows.Remove(window.Id);
                }
            }
            finally
            {
                pruning.Clear();
            }
            UnsubscribeIfEmpty();
        }

        private static void Tick()
        {
            Prune();
            foreach (Window window in windows.Values)
                if (window.Running)
                    sampling.Add(window);
            try
            {
                foreach (Window window in sampling)
                {
                    long before = Stopwatch.GetTimestamp();
                    try
                    {
                        int frame = Time.frameCount;
                        if (frame == window.LastFrame)
                        {
                            EditorApplication.QueuePlayerLoopUpdate();
                            continue;
                        }
                        window.UnobservedFrames += Math.Max(0, frame - window.LastFrame - 1);
                        window.LastFrame = frame;
                        if (!window.Armed)
                        {
                            window.Armed = true;
                            Reset(window);
                            if (window.Warmup == 0)
                            {
                                window.Measuring = true;
                                window.State = "measuring";
                            }
                        }
                        else if (!window.Measuring)
                        {
                            if (++window.WarmupObserved >= window.Warmup)
                            {
                                Reset(window);
                                window.Measuring = true;
                                window.State = "measuring";
                            }
                        }
                        else if (++window.FramesObserved >= window.Frames)
                            Finish(window, "completed", null);
                        EditorApplication.QueuePlayerLoopUpdate();
                    }
                    catch (Exception error)
                    {
                        Finish(window, "failed", error.GetType().Name + ": " + error.Message);
                    }
                    finally
                    {
                        window.CollectorTicks += Stopwatch.GetTimestamp() - before;
                    }
                }
            }
            finally
            {
                sampling.Clear();
            }
        }

        private static void Reset(Window window)
        {
            foreach (Marker marker in window.Markers)
                if (marker.Owned)
                {
                    // Reset also stops native collection; rearm after discarding the partial/warmup frame.
                    marker.Recorder.Reset();
                    marker.Recorder.Start();
                }
        }

        private static void Finish(Window window, string state, string reason)
        {
            window.State = state;
            window.Reason = reason;
            window.Finished = EditorApplication.timeSinceStartup;
            foreach (Marker marker in window.Markers)
            {
                try
                {
                    var samples = new List<ProfilerRecorderSample>();
                    if (marker.Owned)
                    {
                        marker.Recorder.Stop();
                        if (window.Measuring)
                            marker.Recorder.CopyTo(samples);
                    }
                    marker.Result = Summarize(marker, samples, window.Frames, window.IncludeSamples);
                }
                catch (Exception error)
                {
                    marker.UnavailableReason = "sample_read_failed_" + error.GetType().Name;
                    marker.Result = Summarize(marker, new List<ProfilerRecorderSample>(), window.Frames, window.IncludeSamples);
                }
                finally
                {
                    Dispose(marker);
                }
            }
            if (window.Markers.Any(marker => marker.Owned))
            {
                window.State = "failed";
                window.Reason = "recorder_cleanup_failed";
            }
        }

        private static void Dispose(Marker marker)
        {
            if (!marker.Owned)
                return;
            try
            {
                marker.Recorder.Dispose();
                marker.Owned = false;
                marker.CleanupError = null;
            }
            catch (Exception error)
            {
                marker.CleanupError = error.GetType().Name + ": " + error.Message;
            }
        }

        private static JObject Summarize(Marker marker, List<ProfilerRecorderSample> records, int limit, bool include)
        {
            var selected = records.Take(limit).Where(sample => sample.Count > 0).ToArray();
            long[] values = selected.Select(sample => sample.Value).OrderBy(value => value).ToArray();
            var result = new JObject
            {
                ["name"] = marker.Name,
                ["unit"] = marker.Unit == null ? JValue.CreateNull() : new JValue(marker.Unit),
                ["status"] =
                    marker.UnavailableReason != null ? "unavailable"
                    : values.Length == 0 ? "missing"
                    : "available",
                ["reason"] = marker.UnavailableReason,
                ["sample_count"] = values.Length,
                ["native_record_count"] = records.Count,
                ["empty_record_count"] = records.Take(limit).Count(sample => sample.Count == 0),
                ["invocation_count"] = selected.Sum(sample => (double)sample.Count),
                ["sample_limit_exceeded"] = records.Count > limit,
                ["mean"] = values.Length == 0 ? JValue.CreateNull() : new JValue(values.Average(value => (double)value)),
                ["median"] = values.Length == 0 ? JValue.CreateNull() : new JValue(values[(values.Length - 1) / 2] / 2.0 + values[values.Length / 2] / 2.0),
                ["p95"] = values.Length == 0 ? JValue.CreateNull() : new JValue(values[(int)Math.Ceiling(values.Length * 0.95) - 1]),
                ["max"] = values.Length == 0 ? JValue.CreateNull() : new JValue(values[values.Length - 1]),
            };
            if (include)
                result["samples"] = new JArray(selected.Select(sample => new JObject { ["value"] = sample.Value, ["count"] = sample.Count }));
            return result;
        }

        private static JObject Describe(Window window)
        {
            var rows = new JArray(
                window.Markers.Select(marker =>
                    marker.Result
                    ?? new JObject
                    {
                        ["name"] = marker.Name,
                        ["status"] = marker.UnavailableReason == null ? "pending" : "unavailable",
                        ["reason"] = marker.UnavailableReason,
                    }
                )
            );
            return new JObject
            {
                ["window_id"] = window.Id,
                ["category"] = window.Category,
                ["state"] = window.State,
                ["reason"] = window.Reason,
                ["collection_finished"] = window.State == "completed",
                ["complete"] =
                    window.State == "completed"
                    && window.UnobservedFrames == 0
                    && rows.All(row =>
                        row.Value<string>("status") == "available"
                        && row.Value<int>("sample_count") == window.Frames
                        && !row.Value<bool>("sample_limit_exceeded")
                    ),
                ["active_recorders"] = window.Markers.Count(marker => marker.Owned),
                ["recorder_cleanup"] =
                    window.Running ? "active"
                    : window.Markers.Any(marker => marker.Owned) ? "failed"
                    : "disposed",
                ["cleanup_errors"] = new JArray(
                    window
                        .Markers.Where(marker => marker.CleanupError != null)
                        .Select(marker => new JObject { ["name"] = marker.Name, ["error"] = marker.CleanupError })
                ),
                ["requested_frame_count"] = window.Frames,
                ["observed_frame_count"] = window.FramesObserved,
                ["warmup_frames"] = window.Warmup,
                ["warmup_frames_observed"] = window.WarmupObserved,
                ["unobserved_player_loop_frames"] = window.UnobservedFrames,
                ["elapsed_seconds"] = (window.Running ? EditorApplication.timeSinceStartup : window.Finished) - window.Started,
                ["result_retention_seconds"] = RetentionSeconds,
                ["markers"] = rows,
                ["ranking_by_max"] = new JArray(
                    rows.Where(row => row.Value<string>("unit") == "TimeNanoseconds" && row.Value<string>("status") == "available")
                        .OrderByDescending(row => row.Value<long>("max"))
                        .Select(row => row.Value<string>("name"))
                ),
                ["percentile_definition"] = "nearest_rank_ceil_p_times_n",
                ["median_definition"] = "midpoint_of_middle_sorted_values",
                ["sample_basis"] = "completed_frame_sums_all_threads; Count>0 required; first_partial_frame_and_warmup_excluded",
                ["measurement_overhead"] = new JObject
                {
                    ["collector_elapsed_milliseconds"] = window.CollectorTicks * 1000.0 / Stopwatch.Frequency,
                    ["profiler_overhead_status"] = "not_measured",
                    ["collector_measurement_scope"] = "update_callback_body_only; start/query/cancel/deadline_work_excluded",
                    ["includes_transport_or_unrelated_editor_work"] = false,
                },
                ["limitations"] = new JArray(
                    "Recorder frame sums are not individual marker-call latency, exclusive time, or a full CPU breakdown.",
                    "Frame boundaries are observed via Time.frameCount on Editor updates; skipped boundaries and sample truncation are reported.",
                    "Missing samples are excluded, never padded with zeros; a Count>0 sample with Value=0 is valid.",
                    "Editor/player-loop and recorder overhead can affect the measured workload; no performance improvement is claimed.",
                    "Recorders/results are session-scoped and cleared on reload, play-mode transitions and shutdown."
                ),
            };
        }

        private static string Text(JObject parameters, string key, int maximum)
        {
            JToken token = parameters[key];
            if (token?.Type != JTokenType.String || string.IsNullOrWhiteSpace(token.Value<string>()) || token.Value<string>().Length > maximum)
                throw new ArgumentException(key + " must be a nonempty bounded string.");
            return token.Value<string>();
        }

        private static int Integer(JObject parameters, string key, int minimum, int maximum, int fallback)
        {
            JToken token = parameters[key];
            if (token == null || token.Type == JTokenType.Null)
                return fallback;
            if (token.Type != JTokenType.Integer || !long.TryParse(token.ToString(), out long value) || value < minimum || value > maximum)
                throw new ArgumentException(key + $" must be an integer between {minimum} and {maximum}.");
            return (int)value;
        }

        private static bool Boolean(JObject parameters, string key, bool fallback)
        {
            JToken token = parameters[key];
            if (token == null || token.Type == JTokenType.Null)
                return fallback;
            if (token.Type != JTokenType.Boolean)
                throw new ArgumentException(key + " must be a boolean.");
            return token.Value<bool>();
        }
    }
}
