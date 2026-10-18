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
                ? HandlerInfo.Cooperative(
                    first,
                    async (_, token) =>
                    {
                        try
                        {
                            await Task.Delay(Timeout.Infinite, token).ConfigureAwait(true);
                            return null;
                        }
                        finally
                        {
                            settled = true;
                        }
                    }
                )
                : new HandlerInfo(
                    first,
                    null,
                    async _ =>
                    {
                        var value = await legacy.Task.ConfigureAwait(true);
                        settled = true;
                        return value;
                    }
                );
            handlers[next] = new HandlerInfo(
                next,
                _ =>
                {
                    nextCalls++;
                    return new { done = true };
                },
                null
            );
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
                if (previousFirst == null)
                    handlers.Remove(first);
                else
                    handlers[first] = previousFirst;
                if (previousNext == null)
                    handlers.Remove(next);
                else
                    handlers[next] = previousNext;
            }
        }

        [Test]
        public async Task Progress_FirstSettledItemIsVisibleBeforeGatedSecondItem()
        {
            const string first = "__core06_first";
            const string second = "__core06_second";
            using var handlers = new MCPForUnityTests.Editor.Services.ProgressHandlerScope();
            using var progress = new MCPForUnityTests.Editor.Services.NativeProgressProbe();
            var gate = new TaskCompletionSource<object>(TaskCreationOptions.RunContinuationsAsynchronously);
            var entered = new TaskCompletionSource<bool>(TaskCreationOptions.RunContinuationsAsynchronously);
            int firstCalls = 0,
                secondCalls = 0;
            handlers.Set(
                first,
                new HandlerInfo(
                    first,
                    _ =>
                    {
                        firstCalls++;
                        return new { done = true };
                    },
                    null
                )
            );
            handlers.Set(
                second,
                new HandlerInfo(
                    second,
                    null,
                    async _ =>
                    {
                        secondCalls++;
                        entered.TrySetResult(true);
                        return await gate.Task.ConfigureAwait(true);
                    }
                )
            );
            Task<object> batch = null;
            try
            {
                batch = MCPForUnityTests.Editor.Services.NativeProgressProbe.InvokeBatch(
                    new JObject { ["commands"] = new JArray(new JObject { ["tool"] = first }, new JObject { ["tool"] = second }) },
                    CancellationToken.None,
                    progress
                );
                await MCPForUnityTests.Editor.Services.NativeProgressProbe.AwaitOwned(entered.Task);
                Assert.IsFalse(batch.IsCompleted, "The held child must still own the actual batch response.");
                Assert.IsTrue(progress.TryTake(out var firstUpdate), "A settled first item must report before the second item settles.");
                Assert.AreEqual(1, firstUpdate.Completed);
                Assert.AreEqual(2, firstUpdate.Total);
                Assert.AreEqual("BatchItems", firstUpdate.Stage);
                Assert.IsFalse(progress.TryTake(out _));
                gate.SetResult(new { done = true });
                await MCPForUnityTests.Editor.Services.NativeProgressProbe.AwaitOwned(batch);
                var response = JObject.FromObject(await batch);
                Assert.AreEqual(2, response.SelectToken("data.attemptedCount").Value<int>());
                Assert.IsTrue(progress.TryTake(out var finalUpdate));
                Assert.AreEqual(2, finalUpdate.Completed);
                Assert.AreEqual(2, finalUpdate.Total);
                Assert.AreEqual(1, firstCalls);
                Assert.AreEqual(1, secondCalls);
            }
            finally
            {
                gate.TrySetResult(new { done = true });
                if (batch != null)
                    await MCPForUnityTests.Editor.Services.NativeProgressProbe.AwaitOwned(batch);
            }
        }

        [TestCase(false)]
        [TestCase(true)]
        public async Task Progress_CancelledUnsettledItemDoesNotBecomeCompleted(bool cooperative)
        {
            const string first = "__core06_cancel_first";
            const string active = "__core06_cancel_active";
            const string remaining = "__core06_cancel_remaining";
            using var handlers = new MCPForUnityTests.Editor.Services.ProgressHandlerScope();
            using var progress = new MCPForUnityTests.Editor.Services.NativeProgressProbe();
            using var cancellation = new CancellationTokenSource();
            var gate = new TaskCompletionSource<object>(TaskCreationOptions.RunContinuationsAsynchronously);
            var entered = new TaskCompletionSource<bool>(TaskCreationOptions.RunContinuationsAsynchronously);
            bool settled = false;
            int remainingCalls = 0;
            handlers.Set(first, new HandlerInfo(first, _ => new { done = true }, null));
            handlers.Set(
                active,
                cooperative
                    ? HandlerInfo.Cooperative(
                        active,
                        async (_, token) =>
                        {
                            entered.TrySetResult(true);
                            try
                            {
                                await Task.Delay(Timeout.Infinite, token).ConfigureAwait(true);
                                return null;
                            }
                            finally
                            {
                                settled = true;
                            }
                        }
                    )
                    : new HandlerInfo(
                        active,
                        null,
                        async _ =>
                        {
                            entered.TrySetResult(true);
                            var result = await gate.Task.ConfigureAwait(true);
                            settled = true;
                            return result;
                        }
                    )
            );
            handlers.Set(
                remaining,
                new HandlerInfo(
                    remaining,
                    _ =>
                    {
                        remainingCalls++;
                        return new { done = true };
                    },
                    null
                )
            );
            Task<object> batch = null;
            try
            {
                batch = MCPForUnityTests.Editor.Services.NativeProgressProbe.InvokeBatch(
                    new JObject
                    {
                        ["commands"] = new JArray(new JObject { ["tool"] = first }, new JObject { ["tool"] = active }, new JObject { ["tool"] = remaining }),
                    },
                    cancellation.Token,
                    progress
                );
                await MCPForUnityTests.Editor.Services.NativeProgressProbe.AwaitOwned(entered.Task);
                Assert.IsTrue(progress.TryTake(out var update));
                Assert.AreEqual(1, update.Completed);
                Assert.AreEqual(3, update.Total);
                cancellation.Cancel();
                if (!cooperative)
                {
                    Assert.IsFalse(batch.IsCompleted, "An observer cannot detach a legacy active mutation.");
                    gate.TrySetResult(new { done = true });
                }
                Assert.AreSame(batch, await Task.WhenAny(batch, Task.Delay(5000)));
                Assert.IsTrue(batch.IsCanceled);
                Assert.IsTrue(settled);
                Assert.IsFalse(progress.TryTake(out _), "A cancelled child is not a settled attempted result.");
                Assert.AreEqual(0, remainingCalls);
            }
            finally
            {
                cancellation.Cancel();
                gate.TrySetResult(new { done = true });
                if (batch != null)
                {
                    Assert.AreSame(batch, await Task.WhenAny(batch, Task.Delay(5000)));
                    Assert.IsTrue(batch.IsCanceled);
                }
            }
        }

        [Test]
        public void ExistingJObjectOnlyEntryPoint_RemainsCompatible()
        {
            var result = BatchExecute.HandleCommand(new JObject { ["commands"] = new JArray() }).GetAwaiter().GetResult();
            Assert.IsNotNull(result);
            Assert.IsNotNull(
                typeof(BatchExecute).GetMethod("HandleCommand", new[] { typeof(JObject), typeof(CancellationToken) }),
                "The existing cooperative entry point remains directly callable."
            );
        }
    }
}
