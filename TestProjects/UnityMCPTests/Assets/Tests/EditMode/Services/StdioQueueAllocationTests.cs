using System;
using System.Collections.Generic;
using System.Reflection;
using System.Threading;
using System.Threading.Tasks;
using MCPForUnity.Editor.Services.Transport.Transports;
using NUnit.Framework;
using Unity.Profiling;

namespace MCPForUnityTests.Editor.Services
{
    public class StdioQueueAllocationTests
    {
        private const BindingFlags Flags = BindingFlags.Static | BindingFlags.NonPublic;

        private static int AllocationCount(Action action)
        {
            using var recorder = ProfilerRecorder.StartNew(
                ProfilerCategory.Memory,
                "GC.Alloc",
                8192,
                ProfilerRecorderOptions.StartImmediately | ProfilerRecorderOptions.CollectOnlyOnCurrentThread
            );
            if (!recorder.Valid)
                throw new InvalidOperationException("GC.Alloc instrumentation is unavailable.");
            action();
            recorder.Stop();
            return recorder.Count;
        }

        [Test]
        public void BusyPollingAllocatesNothingAndStillCancelsWaitingOwners()
        {
            var type = typeof(StdioBridgeHost);
            var queueField = type.GetField("commandQueue", Flags);
            var runningField = type.GetField("isRunning", Flags);
            var heartbeatField = type.GetField("nextHeartbeatAt", Flags);
            object originalQueue = queueField.GetValue(null);
            object originalRunning = runningField.GetValue(null);
            object originalHeartbeat = heartbeatField.GetValue(null);
            var pump = (Action)Delegate.CreateDelegate(typeof(Action), type.GetMethod("ProcessCommands", Flags));
            var queue = new Dictionary<string, QueuedCommand>();
            var active = new QueuedCommand { IsExecuting = true };
            queue.Add("active", active);
            try
            {
                queueField.SetValue(null, queue);
                runningField.SetValue(null, true);
                heartbeatField.SetValue(null, double.MaxValue);
                for (int i = 0; i < 10; i++)
                    pump();
                Assert.Greater(AllocationCount(() => GC.KeepAlive(new byte[32])), 0, "Calibrate the allocation marker first.");
                int count = AllocationCount(() =>
                {
                    for (int i = 0; i < 100; i++)
                        pump();
                });
                Assert.AreEqual(0, count, "An executing handler must not allocate cancellation queries each editor update.");
                using var cancelled = new CancellationTokenSource();
                cancelled.Cancel();
                var waiting = new TaskCompletionSource<string>();
                queue.Add("cancelled", new QueuedCommand { OwnerCancellation = cancelled.Token, Tcs = waiting });
                pump();
                Assert.IsTrue(waiting.Task.IsCanceled);
                Assert.AreEqual(1, queue.Count);
                Assert.AreSame(active, queue["active"]);
            }
            finally
            {
                queueField.SetValue(null, originalQueue);
                runningField.SetValue(null, originalRunning);
                heartbeatField.SetValue(null, originalHeartbeat);
            }
        }
    }
}
