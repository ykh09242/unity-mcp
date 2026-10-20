using System;
using System.Collections;
using System.Diagnostics;
using System.IO;
using System.Threading;
using MCPForUnity.Editor.Helpers;
using NUnit.Framework;
using UnityEngine.TestTools;

namespace MCPForUnityTests.Editor.Helpers
{
    public class UvxOfflineProbeTests
    {
        [UnityTest]
        public IEnumerator ProbeReturnsPromptlySharesWorkAndIsolatesCanceledWaiters()
        {
            using var gate = new ManualResetEventSlim(false);
            using var cancel = new CancellationTokenSource();
            int calls = 0,
                thread = 0;
            int editorThread = Thread.CurrentThread.ManagedThreadId;
            var probe = new UvxOfflineProbe(
                (_, _) =>
                {
                    Interlocked.Increment(ref calls);
                    thread = Thread.CurrentThread.ManagedThreadId;
                    return gate.Wait(5000);
                }
            );
            var timer = Stopwatch.StartNew();
            Assert.IsFalse(probe.GetCachedOrSchedule("synthetic-uvx", "source-one"));
            var first = probe.ProbeAsync("synthetic-uvx", "source-one", cancel.Token);
            var second = probe.ProbeAsync("synthetic-uvx", "source-one", CancellationToken.None);
            Assert.Less(timer.ElapsedMilliseconds, 500);
            cancel.Cancel();
            gate.Set();
            for (int i = 0; i < 300 && (!first.IsCompleted || !second.IsCompleted); i++)
                yield return null;
            Assert.IsTrue(first.IsCanceled);
            Assert.IsTrue(second.GetAwaiter().GetResult());
            Assert.AreEqual(1, calls);
            Assert.AreNotEqual(editorThread, thread);
        }

        [UnityTest]
        public IEnumerator SourceExecutableIdentityAndExpiryInvalidateCache()
        {
            string root = Path.Combine(Path.GetTempPath(), "uvx-probe-" + Guid.NewGuid().ToString("N"));
            Directory.CreateDirectory(root);
            string exe = Path.Combine(root, "synthetic.exe");
            File.WriteAllText(exe, "first");
            DateTime now = DateTime.UtcNow;
            int calls = 0;
            var probe = new UvxOfflineProbe(
                (_, _) =>
                {
                    Interlocked.Increment(ref calls);
                    return true;
                },
                () => now
            );
            try
            {
                for (int phase = 0; phase < 5; phase++)
                {
                    if (phase == 2)
                        File.WriteAllText(exe, "new identity");
                    if (phase == 3)
                        now = now.AddSeconds(31);
                    var task = probe.ProbeAsync(exe, phase == 4 ? "different" : "source", CancellationToken.None);
                    while (!task.IsCompleted)
                        yield return null;
                    Assert.IsTrue(task.GetAwaiter().GetResult());
                }
                Assert.AreEqual(4, calls);
            }
            finally
            {
                Directory.Delete(root, true);
            }
        }
    }
}
