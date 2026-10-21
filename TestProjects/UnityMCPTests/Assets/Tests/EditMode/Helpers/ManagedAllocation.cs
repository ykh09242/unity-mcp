using System;
using Unity.Profiling;

namespace MCPForUnityTests
{
    internal static class ManagedAllocation
    {
        // Unity Mono may report zero through GC.GetAllocatedBytesForCurrentThread.
        internal static int Count(Action action)
        {
            using var recorder = ProfilerRecorder.StartNew(
                ProfilerCategory.Memory,
                "GC.Alloc",
                32768,
                ProfilerRecorderOptions.StartImmediately | ProfilerRecorderOptions.CollectOnlyOnCurrentThread
            );
            if (!recorder.Valid)
                throw new InvalidOperationException("GC.Alloc instrumentation is unavailable.");
            action();
            recorder.Stop();
            if (recorder.Count >= recorder.Capacity)
                throw new InvalidOperationException("Allocation sample capacity exceeded.");
            return recorder.Count;
        }
    }
}
