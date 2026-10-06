using System;
using System.Collections;
using System.Reflection;
using System.Threading;
using System.Threading.Tasks;
using MCPForUnity.Editor.Tools;
using Newtonsoft.Json.Linq;
using NUnit.Framework;

namespace MCPForUnityTests.Editor.Tools
{
    [TestFixture]
    public class BatchExecuteCancellationTests
    {
        [TestCase(false)]
        [TestCase(true)]
        public async Task Cancellation_StopsRemainingCommandsAfterActiveChildSettles(bool cooperative)
        {
            const string first = "phase7_batch_active";
            const string next = "phase7_batch_remaining";
            CommandRegistry.Initialize();
            var handlers = (IDictionary)typeof(CommandRegistry).GetField("_handlers", BindingFlags.NonPublic | BindingFlags.Static).GetValue(null);
            var previousFirst = handlers[first];
            var previousNext = handlers[next];
            var legacy = new TaskCompletionSource<object>(TaskCreationOptions.RunContinuationsAsynchronously);
            bool settled = false;
            int nextCalls = 0;
            handlers[first] = cooperative
                ? HandlerInfo.Cooperative(first, async (_, token) =>
                {
                    try { await Task.Delay(Timeout.Infinite, token).ConfigureAwait(true); return null; }
                    finally { settled = true; }
                })
                : new HandlerInfo(first, null, async _ => { var value = await legacy.Task.ConfigureAwait(true); settled = true; return value; });
            handlers[next] = new HandlerInfo(next, _ => { nextCalls++; return new { done = true }; }, null);
            using var cancellation = new CancellationTokenSource();
            try
            {
                var parameters = new JObject { ["commands"] = new JArray(new JObject { ["tool"] = first }, new JObject { ["tool"] = next }) };
                var batch = BatchExecute.HandleCommand(parameters, cancellation.Token);
                cancellation.Cancel();
                if (!cooperative)
                {
                    Assert.IsFalse(batch.IsCompleted, "A legacy active child must settle before the canceled batch finishes.");
                    legacy.SetResult(new { done = true });
                }
                Assert.AreSame(batch, await Task.WhenAny(batch, Task.Delay(5000)));
                Assert.IsTrue(batch.IsCanceled);
                Assert.IsTrue(settled);
                Assert.AreEqual(0, nextCalls, "Cancellation cannot run or replay remaining batch mutations.");
            }
            finally
            {
                legacy.TrySetResult(new { done = true });
                if (previousFirst == null) handlers.Remove(first); else handlers[first] = previousFirst;
                if (previousNext == null) handlers.Remove(next); else handlers[next] = previousNext;
            }
        }

        [Test]
        public void ExistingJObjectOnlyEntryPoint_RemainsCompatible()
        {
            var result = BatchExecute.HandleCommand(new JObject { ["commands"] = new JArray() }).GetAwaiter().GetResult();
            Assert.IsNotNull(result);
            Assert.AreEqual(2, CommandRegistry.GetCommandMethod(typeof(BatchExecute)).GetParameters().Length);
        }
    }
}
