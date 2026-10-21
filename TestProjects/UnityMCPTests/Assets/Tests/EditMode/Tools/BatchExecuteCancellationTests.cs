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
        private MCPForUnityTests.Editor.Services.ScopedConsentTestState scopedConsent;

        [SetUp]
        public void GrantBatchScope() => scopedConsent = new MCPForUnityTests.Editor.Services.ScopedConsentTestState("batch_execute");

        [TearDown]
        public void RestoreBatchScope() => scopedConsent?.Dispose();

        [TestCase(false)]
        [TestCase(true)]
        public async Task SlowSynchronousChildrenYieldWithoutReorderingOrIgnoringCancellation(bool cancel)
        {
            const string tool = "__opt06_slow_child";
            using var handlers = new MCPForUnityTests.Editor.Services.ProgressHandlerScope();
            using var cts = new CancellationTokenSource();
            int calls = 0;
            handlers.Set(
                tool,
                new HandlerInfo(
                    tool,
                    parameters =>
                    {
                        Assert.AreEqual(calls, (int)parameters["sequence"]);
                        calls++;
                        Thread.Sleep(12);
                        return new { done = true };
                    },
                    null
                )
            );
            var commands = new JArray();
            for (int i = 0; i < 4; i++)
                commands.Add(
                    new JObject
                    {
                        ["tool"] = tool,
                        ["params"] = new JObject { ["sequence"] = i },
                    }
                );
            Task<object> batch = BatchExecute.HandleCommand(new JObject { ["commands"] = commands }, cts.Token);
            bool yielded = !batch.IsCompleted;
            int beforeYield = calls;
            if (cancel)
                cts.Cancel();
            try
            {
                await batch;
            }
            catch (OperationCanceledException) when (cancel) { }
            Assert.IsTrue(yielded, "Completed synchronous children must yield at a safe boundary during a long batch.");
            Assert.Less(beforeYield, 4);
            Assert.AreEqual(cancel ? beforeYield : 4, calls);
            Assert.AreEqual(cancel, batch.IsCanceled);
        }

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

        [TestCase(true, false)]
        [TestCase(true, true)]
        [TestCase(false, false)]
        [TestCase(false, true)]
        public async Task ConsentRevokedWhileChildHeld_AppliesOnlyAtNextBoundary(bool revokeParent, bool failFast)
        {
            const string first = "__core12_held";
            const string second = "execute_code";
            const string last = "__core12_remaining";
            using var handlers = new MCPForUnityTests.Editor.Services.ProgressHandlerScope();
            using var progress = new MCPForUnityTests.Editor.Services.NativeProgressProbe();
            Assert.IsTrue(scopedConsent.Service.Grant(second, MCPForUnity.Editor.Services.ToolConsentScope.EditorSession).Succeeded);
            var entered = new TaskCompletionSource<bool>(TaskCreationOptions.RunContinuationsAsynchronously);
            var gate = new TaskCompletionSource<object>(TaskCreationOptions.RunContinuationsAsynchronously);
            int firstCalls = 0,
                secondCalls = 0,
                lastCalls = 0;
            bool firstSettled = false;
            handlers.Set(
                first,
                new HandlerInfo(
                    first,
                    null,
                    async _ =>
                    {
                        firstCalls++;
                        entered.TrySetResult(true);
                        var result = await gate.Task.ConfigureAwait(true);
                        firstSettled = true;
                        return result;
                    }
                )
            );
            handlers.Set(
                second,
                new HandlerInfo(
                    second,
                    _ =>
                    {
                        secondCalls++;
                        return new { success = true };
                    },
                    null
                )
            );
            handlers.Set(
                last,
                new HandlerInfo(
                    last,
                    _ =>
                    {
                        lastCalls++;
                        return new { success = true };
                    },
                    null
                )
            );
            var request = new JObject
            {
                ["failFast"] = failFast,
                ["commands"] = new JArray(new JObject { ["tool"] = first }, new JObject { ["tool"] = second }, new JObject { ["tool"] = last }),
            };
            MCPForUnityTests.Editor.Services.NativeProgressProbe.RequireBatchCapacity(request);
            var preview = JObject.FromObject(BatchExecute.ValidateCommand(request));
            Assert.AreEqual(0, preview.SelectToken("data.attemptedCount").Value<int>());
            Assert.AreEqual(0, firstCalls, "Preview must not enter or reserve a child invocation.");
            Task<object> batch = null;
            try
            {
                batch = BatchExecute.HandleCommand(request, CancellationToken.None, (MCPForUnity.Editor.Services.Transport.ICommandProgress)progress.Instance);
                await MCPForUnityTests.Editor.Services.NativeProgressProbe.AwaitOwned(entered.Task);
                Assert.IsFalse(batch.IsCompleted);
                Assert.IsTrue(scopedConsent.Service.Revoke(revokeParent ? "batch_execute" : second).DeniedInCurrentDomain);
                Assert.IsFalse(firstSettled, "Revocation cannot settle or interrupt entered work.");
                Assert.IsFalse(batch.IsCompleted);
                gate.TrySetResult(new { success = true });
                await MCPForUnityTests.Editor.Services.NativeProgressProbe.AwaitOwned(batch);
                var response = JObject.FromObject(await batch);
                var outcomes = (JArray)response.SelectToken("data.outcomes");
                Assert.IsFalse(response.Value<bool>("success"));
                Assert.IsTrue(firstSettled);
                Assert.AreEqual(1, firstCalls);
                Assert.AreEqual(0, secondCalls, "Consent revocation must be checked after the held child settles.");
                Assert.AreEqual(revokeParent || failFast ? 0 : 1, lastCalls);
                int attempted =
                    revokeParent ? 1
                    : failFast ? 2
                    : 3;
                Assert.AreEqual(attempted, response.SelectToken("data.attemptedCount").Value<int>());
                Assert.AreEqual(3 - attempted, response.SelectToken("data.notRunCount").Value<int>());
                Assert.AreEqual("succeeded", outcomes[0].Value<string>("state"));
                Assert.AreEqual(revokeParent ? "not_run" : "failed", outcomes[1].Value<string>("state"));
                Assert.AreEqual(revokeParent ? "batch_disabled" : "tool_disabled", outcomes[1].Value<string>("reason"));
                Assert.AreEqual(revokeParent || failFast ? "not_run" : "succeeded", outcomes[2].Value<string>("state"));
                Assert.IsTrue(progress.TryTake(out var update));
                Assert.AreEqual(attempted, update.Completed, "Unrun suffix items receive no progress credit.");
                Assert.AreEqual(3, update.Total);
            }
            finally
            {
                gate.TrySetResult(new { success = true });
                if (batch != null)
                    await MCPForUnityTests.Editor.Services.NativeProgressProbe.AwaitOwned(batch);
            }
        }
    }
}
