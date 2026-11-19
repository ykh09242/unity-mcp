using System;
using System.Collections.Generic;
using System.Reflection;
using MCPForUnity.Editor.Helpers;
using Newtonsoft.Json.Linq;
using UnityEngine;

namespace MCPForUnity.Editor.Tools.Profiler
{
    internal static class FrameTimingOps
    {
        private static readonly Func<bool> IsFeatureEnabled = FindFeatureCheck();
        private static readonly (string Key, FieldInfo Field)[] ExtendedFields =
        {
            ("cpu_main_thread_frame_time_ms", typeof(FrameTiming).GetField("cpuMainThreadFrameTime")),
            ("cpu_main_thread_present_wait_time_ms", typeof(FrameTiming).GetField("cpuMainThreadPresentWaitTime")),
            ("cpu_render_thread_frame_time_ms", typeof(FrameTiming).GetField("cpuRenderThreadFrameTime")),
            ("frame_start_timestamp", typeof(FrameTiming).GetField("frameStartTimestamp")),
            ("first_submit_timestamp", typeof(FrameTiming).GetField("firstSubmitTimestamp")),
        };

        private static Func<bool> FindFeatureCheck()
        {
            var method = typeof(FrameTimingManager).GetMethod("IsFeatureEnabled", BindingFlags.Public | BindingFlags.Static, null, Type.EmptyTypes, null);
            return method?.ReturnType == typeof(bool) ? (Func<bool>)method.CreateDelegate(typeof(Func<bool>)) : null;
        }

        internal static object GetFrameTiming(JObject @params)
        {
            if (IsFeatureEnabled?.Invoke() == false)
            {
                return new ErrorResponse(
                    "Frame Timing Stats is not enabled. "
                        + "Enable it in Project Settings > Player > Other Settings > 'Frame Timing Stats', "
                        + "or use a Development Build (always enabled)."
                );
            }
            FrameTimingManager.CaptureFrameTimings();
            var timings = new FrameTiming[1];
            uint count = FrameTimingManager.GetLatestTimings(1, timings);

            if (count == 0)
            {
                return new SuccessResponse("No frame timing data available yet (need a few frames).", new { available = false });
            }

            return new SuccessResponse("Frame timing captured.", TimingData(timings[0]));
        }

        internal static Dictionary<string, object> TimingData(FrameTiming timing)
        {
            var data = new Dictionary<string, object>
            {
                ["available"] = true,
                ["cpu_frame_time_ms"] = timing.cpuFrameTime,
                ["gpu_frame_time_ms"] = timing.gpuFrameTime,
                ["cpu_time_present_called"] = timing.cpuTimePresentCalled,
                ["cpu_time_frame_complete"] = timing.cpuTimeFrameComplete,
                ["height_scale"] = timing.heightScale,
                ["width_scale"] = timing.widthScale,
                ["sync_interval"] = timing.syncInterval,
            };
            object boxedTiming = timing;
            foreach (var (key, field) in ExtendedFields)
            {
                if (field != null)
                    data[key] = field.GetValue(boxedTiming);
            }
            return data;
        }
    }
}
