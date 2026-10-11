using System;
using Unity.Profiling;
using UnityEngine;

namespace MCPForUnity.Editor.Services.PlayScenarios
{
    /// <summary>Samples once per real Update, reading the last completed frame's allocation counter.</summary>
    [DefaultExecutionOrder(-32000)]
    internal sealed class PlayScenarioFrameSampler : MonoBehaviour
    {
        private PlayScenarioPerformanceSamples samples;
        private ProfilerRecorder gc;
        private bool gcSelected;
        private int startedFrame;
        private int lastFrame;

        internal static PlayScenarioFrameSampler Begin(PlayScenarioPerformanceSamples samples, bool gcSelected)
        {
            GameObject owner = null;
            PlayScenarioFrameSampler component = null;
            try
            {
                owner = new GameObject("__MCP_ScenarioFrameSampler__");
                owner.hideFlags = HideFlags.HideAndDontSave;
                DontDestroyOnLoad(owner);
                component = owner.AddComponent<PlayScenarioFrameSampler>();
                component.samples = samples;
                component.gcSelected = gcSelected;
                component.startedFrame = Time.frameCount;
                component.lastFrame = Time.frameCount;
                if (gcSelected)
                {
                    component.gc = ProfilerRecorder.StartNew(ProfilerCategory.Memory, "GC Allocated In Frame", 1);
                    if (!component.gc.Valid)
                        samples.Unavailable("GC Allocated In Frame is unsupported on this host.");
                }
                return component;
            }
            catch
            {
                if (component != null)
                    component.Stop();
                else if (owner != null)
                    DestroyImmediate(owner);
                throw;
            }
        }

        private void Update()
        {
            if (samples == null || Time.frameCount <= startedFrame + 1 || lastFrame == Time.frameCount)
                return;
            lastFrame = Time.frameCount;
            try
            {
                samples.AddFrame(Time.unscaledDeltaTime * 1000.0, gcSelected && gc.Valid && gc.Count > 0 ? (long?)gc.LastValue : null);
            }
            catch (Exception error)
            {
                samples.Unavailable("Frame counter read failed: " + error.Message);
            }
        }

        internal void Stop()
        {
            samples = null;
            gc.Dispose();
            if (this != null)
                DestroyImmediate(gameObject);
        }

        private void OnDestroy()
        {
            samples?.Unavailable("The performance frame source was destroyed before step completion.");
            samples = null;
            gc.Dispose();
        }
    }
}
