using System;
using System.Collections.Generic;
using System.Linq;
using System.Threading;
using System.Threading.Tasks;
using MCPForUnity.Editor.Helpers;
using Newtonsoft.Json.Linq;
using Unity.Profiling;
using Unity.Profiling.LowLevel.Unsafe;
using UnityEditor;
using UnityEngine;
using UnityEngine.Profiling;
using UProfiler = UnityEngine.Profiling.Profiler;

namespace MCPForUnity.Editor.Tools.Graphics
{
    [InitializeOnLoad]
    internal static class RenderingStatsOps
    {
        private static readonly int MainThreadId = Thread.CurrentThread.ManagedThreadId;
        internal static int ActiveCaptureCount { get; private set; }

        private static readonly (string counterName, string jsonKey)[] COUNTER_MAP = new[]
        {
            ("Draw Calls Count", "draw_calls"),
            ("Batches Count", "batches"),
            ("SetPass Calls Count", "set_pass_calls"),
            ("Triangles Count", "triangles"),
            ("Vertices Count", "vertices"),
            ("Dynamic Batches Count", "dynamic_batches"),
            ("Dynamic Batched Draw Calls Count", "dynamic_batched_draw_calls"),
            ("Static Batches Count", "static_batches"),
            ("Static Batched Draw Calls Count", "static_batched_draw_calls"),
            ("Instanced Batches Count", "instanced_batches"),
            ("Instanced Batched Draw Calls Count", "instanced_batched_draw_calls"),
            ("Shadow Casters Count", "shadow_casters"),
            ("Render Textures Count", "render_textures"),
            ("Render Textures Bytes", "render_textures_bytes"),
            ("Used Textures Count", "used_textures"),
            ("Used Textures Bytes", "used_textures_bytes"),
            ("Render Textures Changes Count", "render_target_changes"),
            ("Visible Skinned Meshes Count", "visible_skinned_meshes"),
        };

        // === stats_get ===
        internal static object GetStats(JObject @params) => GetStatsAsync(@params, CancellationToken.None);

        internal static Task<object> GetStatsAsync(JObject @params, CancellationToken cancellationToken) =>
            CaptureCountersAsync(ProfilerCategory.Render, COUNTER_MAP, cancellationToken);

        internal static Task<object> CaptureCountersAsync(
            ProfilerCategory category,
            (string counterName, string jsonKey)[] counters,
            CancellationToken cancellationToken
        )
        {
            if (Thread.CurrentThread.ManagedThreadId != MainThreadId)
                return Task.FromException<object>(new InvalidOperationException("Rendering counters must be captured on the Editor main thread."));
            if (cancellationToken.IsCancellationRequested)
                return Task.FromCanceled<object>(cancellationToken);

            var completion = new TaskCompletionSource<object>(TaskCreationOptions.RunContinuationsAsynchronously);
            var recorders = new List<ProfilerRecorder>();
            var errors = new List<string>();
            double deadline = EditorApplication.timeSinceStartup + 2;
            int ticks = 0;
            bool cleaned = false;

            void Cleanup()
            {
                if (cleaned)
                    return;
                cleaned = true;
                EditorApplication.update -= Tick;
                AssemblyReloadEvents.beforeAssemblyReload -= Abort;
                EditorApplication.quitting -= Abort;
                foreach (var recorder in recorders)
                    recorder.Dispose();
                recorders.Clear();
                ActiveCaptureCount--;
            }

            void Abort()
            {
                Cleanup();
                completion.TrySetCanceled();
            }

            void Tick()
            {
                // Tokens may be canceled on worker threads. Native access and disposal
                // remain on this Editor update, before the task settles.
                if (cancellationToken.IsCancellationRequested)
                {
                    Cleanup();
                    completion.TrySetCanceled(cancellationToken);
                    return;
                }

                try
                {
                    ticks++;
                    bool awaitingSample = recorders.Any(recorder => recorder.Valid && recorder.Count == 0);
                    bool boundedWait = ticks >= 30 || EditorApplication.timeSinceStartup >= deadline;
                    if (ticks < 2 || (awaitingSample && !boundedWait))
                    {
                        EditorApplication.QueuePlayerLoopUpdate();
                        return;
                    }

                    var stats = new Dictionary<string, object>();
                    var evidence = new Dictionary<string, object>();
                    int completedCounters = 0;
                    for (int i = 0; i < recorders.Count; i++)
                    {
                        var recorder = recorders[i];
                        bool supported = recorder.Valid;
                        int count = supported ? recorder.Count : 0;
                        long value = count > 0 ? recorder.GetSample(count - 1).Value : 0;
                        var counter = DescribeCounter(supported, count, value, supported ? recorder.UnitType.ToString() : "Unknown", errors[i]);
                        stats[counters[i].jsonKey] = counter["value"];
                        evidence[counters[i].jsonKey] = counter;
                        if ((bool)counter["valid"])
                            completedCounters++;
                    }
                    stats["counter_status"] = evidence;
                    bool partial = completedCounters < counters.Length;
                    var result = new
                    {
                        success = true,
                        message = partial ? "Rendering stats captured with unavailable counters. See counter_status." : "Rendering stats captured.",
                        partial,
                        status = completedCounters == 0 ? "unavailable"
                        : partial ? "partial"
                        : "complete",
                        sampling = new
                        {
                            source = "completed_frame",
                            editor_updates = ticks,
                            completed_counters = completedCounters,
                            requested_counters = counters.Length,
                            bounded_wait = boundedWait && awaitingSample,
                        },
                        data = stats,
                    };
                    Cleanup();
                    completion.TrySetResult(result);
                }
                catch (Exception ex)
                {
                    Cleanup();
                    completion.TrySetException(ex);
                }
            }

            ActiveCaptureCount++;
            try
            {
                foreach (var (counterName, _) in counters)
                {
                    try
                    {
                        // Capacity one retains only the most recent completed frame.
                        recorders.Add(ProfilerRecorder.StartNew(category, counterName, 1));
                        errors.Add(null);
                    }
                    catch (Exception ex)
                    {
                        recorders.Add(default);
                        errors.Add(ex.Message);
                    }
                }
                AssemblyReloadEvents.beforeAssemblyReload += Abort;
                EditorApplication.quitting += Abort;
                EditorApplication.update += Tick;
                EditorApplication.QueuePlayerLoopUpdate();
            }
            catch (Exception ex)
            {
                Cleanup();
                completion.TrySetException(ex);
            }
            return completion.Task;
        }

        internal static Dictionary<string, object> DescribeCounter(bool supported, int sampleCount, long value, string unit, string error)
        {
            bool valid = supported && sampleCount > 0 && error == null;
            return new Dictionary<string, object>
            {
                ["value"] = valid ? (object)value : null,
                ["supported"] = supported,
                ["valid"] = valid,
                ["unit"] = unit,
                ["sample_count"] = sampleCount,
                ["status"] =
                    error != null ? "invalid"
                    : !supported ? "unsupported"
                    : sampleCount == 0 ? "no_completed_sample"
                    : "completed",
                ["error"] = error,
            };
        }

        // === stats_list_counters ===
        internal static object ListCounters(JObject @params)
        {
            var p = new ToolParams(@params);
            string categoryName = p.Get("category");

            // Default to "Render" category to avoid massive payloads (all categories = 300K+ chars)
            ProfilerCategory category = ProfilerCategory.Render;
            if (!string.IsNullOrEmpty(categoryName))
            {
                category = TryResolveCategory(categoryName);
            }

            string resolvedCategoryName = category.Name;
            var allHandles = new List<ProfilerRecorderHandle>();
            ProfilerRecorderHandle.GetAvailable(allHandles);
            var counters = allHandles
                .Select(h =>
                {
                    var description = ProfilerRecorderHandle.GetDescription(h);
                    return (description, categoryName: description.Category.Name);
                })
                .Where(item => string.Equals(item.categoryName, resolvedCategoryName, StringComparison.OrdinalIgnoreCase))
                .Select(item => new
                {
                    name = item.description.Name,
                    category = item.categoryName,
                    unit = item.description.UnitType.ToString(),
                })
                .OrderBy(c => c.name)
                .ToList();

            return new
            {
                success = true,
                message = $"Found {counters.Count} counters in category '{resolvedCategoryName}'.",
                data = new { counters },
            };
        }

        // === stats_set_scene_debug_mode ===
        internal static object SetSceneDebugMode(JObject @params)
        {
            var p = new ToolParams(@params);
            string modeName = p.Get("mode");
            if (string.IsNullOrEmpty(modeName))
            {
                var validModes = string.Join(", ", Enum.GetNames(typeof(DrawCameraMode)).Take(20));
                return new ErrorResponse($"'mode' parameter required. Options: {validModes}");
            }

            if (!Enum.TryParse<DrawCameraMode>(modeName, true, out var drawMode) || !Enum.IsDefined(typeof(DrawCameraMode), drawMode))
            {
                var validModes = string.Join(", ", Enum.GetNames(typeof(DrawCameraMode)).Take(20));
                return new ErrorResponse($"Unknown mode '{modeName}'. Valid: {validModes}");
            }

            SceneView.CameraMode cameraMode;
            try
            {
                cameraMode = SceneView.GetBuiltinCameraMode(drawMode);
            }
            catch (InvalidOperationException)
            {
                return new ErrorResponse($"Mode '{modeName}' is not an available built-in Scene View mode.");
            }

            var sceneView = SceneView.lastActiveSceneView;
            if (sceneView == null)
                return new ErrorResponse("No active Scene View found.");

            sceneView.cameraMode = cameraMode;

            sceneView.Repaint();

            return new { success = true, message = $"Scene debug mode set to '{drawMode}'." };
        }

        // === stats_get_memory ===
        internal static object GetMemory(JObject @params)
        {
            var data = new Dictionary<string, object>
            {
                ["totalAllocatedMB"] = Math.Round(UProfiler.GetTotalAllocatedMemoryLong() / (1024.0 * 1024.0), 2),
                ["totalReservedMB"] = Math.Round(UProfiler.GetTotalReservedMemoryLong() / (1024.0 * 1024.0), 2),
                ["totalUnusedReservedMB"] = Math.Round(UProfiler.GetTotalUnusedReservedMemoryLong() / (1024.0 * 1024.0), 2),
                ["monoUsedMB"] = Math.Round(UProfiler.GetMonoUsedSizeLong() / (1024.0 * 1024.0), 2),
                ["monoHeapMB"] = Math.Round(UProfiler.GetMonoHeapSizeLong() / (1024.0 * 1024.0), 2),
                ["graphicsDriverMB"] = Math.Round(UProfiler.GetAllocatedMemoryForGraphicsDriver() / (1024.0 * 1024.0), 2),
            };

            return new
            {
                success = true,
                message = "Memory stats captured.",
                data,
            };
        }

        // --- Helper: Try to resolve a ProfilerCategory by name ---
        private static ProfilerCategory TryResolveCategory(string name)
        {
            // ProfilerCategory has static properties for well-known categories
            switch (name.ToLowerInvariant())
            {
                case "render":
                    return ProfilerCategory.Render;
                case "scripts":
                    return ProfilerCategory.Scripts;
                case "memory":
                    return ProfilerCategory.Memory;
                case "physics":
                    return ProfilerCategory.Physics;
                case "animation":
                    return ProfilerCategory.Animation;
                case "audio":
                    return ProfilerCategory.Audio;
                case "lighting":
                    return ProfilerCategory.Lighting;
                case "network":
                    return ProfilerCategory.Network;
                case "gui":
                    return ProfilerCategory.Gui;
                case "ai":
                    return ProfilerCategory.Ai;
                case "video":
                    return ProfilerCategory.Video;
                case "loading":
                    return ProfilerCategory.Loading;
                case "input":
                    return ProfilerCategory.Input;
                case "vr":
                    return ProfilerCategory.Vr;
                case "internal":
                    return ProfilerCategory.Internal;
                default:
                    return ProfilerCategory.Render;
            }
        }
    }
}
