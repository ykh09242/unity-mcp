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
        private OwnedTestTasks owned;

        [SetUp]
        public void SetUp() => owned = new OwnedTestTasks();

        [UnityTearDown]
        public IEnumerator TearDown() => owned.Cleanup();

        [UnityTest]
        public IEnumerator ProbeReturnsPromptlySharesWorkAndIsolatesCanceledWaiters()
        {
            var gate = owned.Own(new ManualResetEventSlim(false));
            var cancel = owned.Own(new CancellationTokenSource());
            owned.OnCleanup(gate.Set);
            owned.OnCleanup(cancel.Cancel);
            int calls = 0,
                thread = 0;
            int editorThread = Thread.CurrentThread.ManagedThreadId;
            var probe = new UvxOfflineProbe(
                (_, _) =>
                {
                    Interlocked.Increment(ref calls);
                    thread = Thread.CurrentThread.ManagedThreadId;
                    gate.Wait();
                    return true;
                }
            );
            var timer = Stopwatch.StartNew();
            Assert.IsFalse(probe.GetCachedOrSchedule("synthetic-uvx", "source-one"));
            var first = owned.Track(probe.ProbeAsync("synthetic-uvx", "source-one", cancel.Token));
            var second = owned.Track(probe.ProbeAsync("synthetic-uvx", "source-one", CancellationToken.None));
            try
            {
                Assert.Less(timer.ElapsedMilliseconds, 500);
                Assert.IsFalse(second.IsCompleted, "Scheduling must return while the owned worker gate is closed.");
                cancel.Cancel();
                gate.Set();
                yield return TestUtilities.WaitUntil(() => first.IsCompleted && second.IsCompleted, "both probe observations to complete");
                Assert.IsTrue(first.IsCompleted && second.IsCompleted, "Never block the Editor on an unfinished observation.");
                Assert.IsTrue(first.IsCanceled);
                Assert.IsTrue(second.GetAwaiter().GetResult());
                Assert.AreEqual(1, calls);
                Assert.AreNotEqual(editorThread, thread);
            }
            finally
            {
                gate.Set();
                cancel.Cancel();
            }
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
                    var task = owned.Track(probe.ProbeAsync(exe, phase == 4 ? "different" : "source", CancellationToken.None));
                    yield return TestUtilities.WaitUntil(() => task.IsCompleted, "probe cache observation to complete");
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
