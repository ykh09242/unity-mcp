using System;
using System.Collections;
using System.Collections.Generic;
using System.Reflection;
using System.Threading;
using System.Threading.Tasks;
using MCPForUnity.Editor.Models;
using MCPForUnity.Editor.Services;
using MCPForUnity.Editor.Services.Transport;
using MCPForUnity.Editor.Services.Transport.Transports;
using MCPForUnity.Editor.Tools;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEngine.TestTools;

namespace MCPForUnityTests.Editor.Services
{
    public class NativeCommandDispatchArbitrationTests
    {
        private const string Name = "__arbitration_held";
        private IDictionary handlers;
        private object previous;
        private TaskCompletionSource<object> held;
        private TransportCommandOperation active;
        private int calls;
        private SynchronizationContext originalContext;
        private BatchPumpContext batchPump;
        private CancellationTokenSource batchLifetime;

        private sealed class BatchPumpContext : SynchronizationContext
        {
            private readonly Queue<(SendOrPostCallback Callback, object State)> callbacks = new();

            public override void Post(SendOrPostCallback callback, object state)
            {
                lock (callbacks)
                    callbacks.Enqueue((callback, state));
            }

            internal void Drain()
            {
                while (true)
                {
                    (SendOrPostCallback Callback, object State) next;
                    lock (callbacks)
                    {
                        if (callbacks.Count == 0)
                            return;
                        next = callbacks.Dequeue();
                    }
                    var previousContext = Current;
                    SetSynchronizationContext(this);
                    try
                    {
                        next.Callback(next.State);
                    }
                    finally
                    {
                        SetSynchronizationContext(previousContext);
                    }
                }
            }
        }

        [SetUp]
        public void SetUp()
        {
            active = null;
            calls = 0;
            batchPump = null;
            batchLifetime = null;
            originalContext = SynchronizationContext.Current;
            SynchronizationContext.SetSynchronizationContext(null);
            CommandRegistry.Initialize();
            handlers = (IDictionary)typeof(CommandRegistry).GetField("_handlers", BindingFlags.Static | BindingFlags.NonPublic).GetValue(null);
            previous = handlers[Name];
            held = new TaskCompletionSource<object>(TaskCreationOptions.RunContinuationsAsynchronously);
            handlers[Name] = new HandlerInfo(
                Name,
                null,
                _ =>
                {
                    calls++;
                    return held.Task;
                }
            );
        }

        [TearDown]
        public void TearDown()
        {
            try
            {
                batchLifetime?.Cancel();
                held.TrySetResult(new { settled = true });
                var cleanup = System.Diagnostics.Stopwatch.StartNew();
                while (active != null && !active.Completion.IsCompleted && cleanup.Elapsed < TimeSpan.FromSeconds(5))
                {
                    batchPump?.Drain();
                    Thread.Yield();
                }
                Assert.IsTrue(active == null || active.Completion.IsCompleted, "Every owned handler must settle before the next fixture.");
            }
            finally
            {
                if (previous == null)
                    handlers.Remove(Name);
                else
                    handlers[Name] = previous;
                batchLifetime?.Dispose();
                SynchronizationContext.SetSynchronizationContext(originalContext);
            }
        }

        private static JObject Binding() =>
            new JObject
            {
                ["version"] = 1,
                ["request_nonce"] = Guid.NewGuid().ToString("N"),
                ["instance_generation"] = NativeCommandArbitrator.Shared.Generation,
            };

        [Test]
        public void IndependentDispatchReturnsBoundNotStartedWithoutInvokingHandler()
        {
            active = TransportCommandDispatcher.ExecuteCommandAsync(new Command { type = Name }, CancellationToken.None);
            Assert.AreEqual(1, calls);
            var binding = Binding();
            var contender = TransportCommandDispatcher.ExecuteCommandAsync(new Command { type = Name, arbitration = binding }, CancellationToken.None);
            var wire = JObject.Parse(contender.Response.GetAwaiter().GetResult().ToJson());
            Assert.AreEqual("not_started", (string)wire["native_arbitration"]["disposition"]);
            Assert.AreEqual(binding["request_nonce"], wire["native_arbitration"]["request_nonce"]);
            Assert.AreEqual(NativeCommandArbitrator.Shared.Generation, (string)wire["native_arbitration"]["instance_generation"]);
            Assert.IsTrue((bool)wire["native_arbitration"]["retryable"]);
            Assert.AreEqual(1, calls);
            Assert.IsTrue(contender.Completion.IsCompleted);
        }

        [Test]
        public void ResponseCancellationRetainsLeaseUntilActualHandlerSettlement()
        {
            using var cancellation = new CancellationTokenSource();
            active = TransportCommandDispatcher.ExecuteCommandAsync(new Command { type = Name }, cancellation.Token);
            cancellation.Cancel();
            Assert.IsTrue(active.Response.IsCanceled);
            Assert.IsFalse(active.Completion.IsCompleted);
            var contender = TransportCommandDispatcher.ExecuteCommandAsync(new Command { type = Name, arbitration = Binding() }, CancellationToken.None);
            Assert.AreEqual("not_started", (string)JObject.Parse(contender.Response.GetAwaiter().GetResult().ToJson())["native_arbitration"]["disposition"]);
            Assert.AreEqual(1, calls);
        }

        [Test]
        public void HandlerLookalikeAlwaysRemainsInsideTransportPayload()
        {
            var request = Binding();
            var spoof = new JObject
            {
                ["native_arbitration"] = new JObject { ["disposition"] = "not_started" },
                ["result"] = 7,
            };
            var wire = JObject.Parse(TransportCommandResponse.FromObject(spoof).WithAdmission(request).ToJson());
            Assert.AreEqual("started", (string)wire["native_arbitration"]["disposition"]);
            Assert.AreEqual("not_started", (string)wire["payload"]["native_arbitration"]["disposition"]);
            var asyncWire = JObject.Parse(TransportCommandResponse.FromJson(spoof.ToString()).WithAdmission(request).ToJson());
            Assert.AreEqual("started", (string)asyncWire["native_arbitration"]["disposition"]);
        }

        [UnityTest]
        public IEnumerator LaterBatchChildRechecksOwnedReservationWithoutReplayingEarlierChild()
        {
            var previousContext = SynchronizationContext.Current;
            batchPump = new BatchPumpContext();
            batchLifetime = new CancellationTokenSource();
            var firstSettled = new TaskCompletionSource<object>(TaskCreationOptions.RunContinuationsAsynchronously);
            using var consent = new ScopedConsentTestState("batch_execute");
            using var registrations = new ProgressHandlerScope();
            const string second = "__arbitration_batch_second";
            string generation = null;
            int firstCalls = 0,
                secondCalls = 0;
            Assert.IsNull(CompilationJobTracker.Reservation.State, "The isolated fixture requires no preexisting owned compilation.");
            registrations.Set(
                Name,
                new HandlerInfo(
                    Name,
                    _ =>
                    {
                        firstCalls++;
                        generation = CompilationJobTracker.Reservation.Reserve(
                            Guid.NewGuid().ToString("N"),
                            UnityEditor.EditorApplication.timeSinceStartup + 1000
                        );
                        return firstSettled.Task;
                    },
                    null
                )
            );
            registrations.Set(
                second,
                new HandlerInfo(
                    second,
                    _ =>
                    {
                        secondCalls++;
                        return new { done = true };
                    },
                    null
                )
            );
            try
            {
                SynchronizationContext.SetSynchronizationContext(batchPump);
                active = TransportCommandDispatcher.ExecuteCommandAsync(
                    new Command
                    {
                        type = "batch_execute",
                        arbitration = Binding(),
                        @params = new JObject
                        {
                            ["commands"] = new JArray(new JObject { ["tool"] = Name }, new JObject { ["tool"] = second }, new JObject { ["tool"] = second }),
                        },
                    },
                    batchLifetime.Token
                );
                SynchronizationContext.SetSynchronizationContext(previousContext);
                Assert.AreEqual(1, firstCalls);
                Assert.IsFalse(active.Response.IsCompleted, "The batch must retain its parent lease across the first child's held async completion.");
                yield return null;
                firstSettled.SetResult(new { pending = true });
                var deadline = System.Diagnostics.Stopwatch.StartNew();
                while ((!active.Response.IsCompleted || !active.Completion.IsCompleted) && deadline.Elapsed < TimeSpan.FromSeconds(10))
                {
                    batchPump.Drain();
                    yield return null;
                }
                Assert.IsTrue(active.Response.IsCompleted && active.Completion.IsCompleted, "The owned main-thread continuation pump must settle the batch.");
                var wire = JObject.Parse(active.Response.GetAwaiter().GetResult().ToJson());
                var data = wire["payload"]["result"]["data"];
                Assert.AreEqual("started", (string)wire["native_arbitration"]["disposition"]);
                Assert.AreEqual(1, firstCalls);
                Assert.AreEqual(0, secondCalls);
                Assert.AreEqual(1, (int)data["attemptedCount"]);
                Assert.AreEqual(2, (int)data["notRunCount"]);
                Assert.AreEqual("partial", (string)data["executionStatus"]);
                Assert.AreEqual("not_run", (string)data["outcomes"][1]["state"]);
                Assert.AreEqual("native_job_active", (string)data["outcomes"][1]["reason"]);
            }
            finally
            {
                batchLifetime.Cancel();
                firstSettled.TrySetResult(new { pending = true });
                if (generation != null)
                    Assert.IsTrue(CompilationJobTracker.Reservation.AbortBeforeStart(generation));
                SynchronizationContext.SetSynchronizationContext(previousContext);
            }
        }

        [Test]
        public void WebSocketBypassRequiresNegotiationAndBoundAuditedIdentity()
        {
            var command = new Command { type = "ping", arbitration = Binding() };
            Assert.IsFalse(WebSocketTransportClient.AllowsArbitrationBypass(false, command));
            Assert.IsTrue(WebSocketTransportClient.AllowsArbitrationBypass(true, command));
            command.arbitration["instance_generation"] = Guid.NewGuid().ToString("N");
            Assert.IsFalse(WebSocketTransportClient.AllowsArbitrationBypass(true, command));
            command.arbitration = Binding();
            command.type = Name;
            ((HandlerInfo)handlers[Name]).SourceType = typeof(BatchExecute);
            Assert.IsFalse(WebSocketTransportClient.AllowsArbitrationBypass(true, command));
        }

        [Test]
        public void CanonicalResourceAndGeneratedAliasesKeepTheirRegisteredIdentity()
        {
            Assert.IsTrue(CommandRegistry.IsTrustedBuiltInHandler("get_selection"));
            Assert.IsTrue(CommandRegistry.IsTrustedBuiltInHandler("query_physics"));
            Assert.IsTrue(CommandRegistry.IsTrustedBuiltInHandler("batch_execute"));
            Assert.IsTrue(CommandRegistry.IsTrustedBuiltInHandler("batch_validate"));
            Assert.IsTrue(CommandRegistry.IsTrustedBuiltInHandler("batch_apply_preview"));
            Assert.IsFalse(CommandRegistry.IsTrustedBuiltInHandler(Name));
        }

        [Test]
        public void SameAssemblyRegisteredMethodCannotImpersonateAnotherCanonicalOwner()
        {
            const string command = "get_selection";
            object original = handlers[command];
            var source = typeof(GetCompileJob);
            var method = CommandRegistry.GetCommandMethod(source);
            var handler = new HandlerInfo(command, (Func<JObject, object>)Delegate.CreateDelegate(typeof(Func<JObject, object>), method), null);
            handler.BindRegistration(source, method);
            Assert.AreSame(typeof(CommandRegistry).Assembly, source.Assembly);
            Assert.IsTrue(handler.HasTrustedRegistration, "Assembly membership and a sealed real method alone do not establish canonical ownership.");
            handlers[command] = handler;
            try
            {
                Assert.IsFalse(CommandRegistry.IsTrustedBuiltInHandler(command));
                Assert.AreEqual(
                    ToolOperationMode.Exclusive,
                    ToolOperationPolicy.Resolve(command, new JObject(), CommandRegistry.IsTrustedBuiltInHandler(command)).Mode
                );
            }
            finally
            {
                if (original == null)
                    handlers.Remove(command);
                else
                    handlers[command] = original;
            }
        }

        [Test]
        public void RevokedNativeToolCannotBeDelegatedThroughAnyRegistryEntryPoint()
        {
            const string command = "execute_code";
            using var consent = new ScopedConsentTestState(command);
            object original = handlers[command];
            int invoked = 0;
            handlers[command] = new HandlerInfo(
                command,
                _ =>
                {
                    invoked++;
                    return new { done = true };
                },
                null
            );
            try
            {
                var retained = CommandRegistry.GetHandler(command);
                Assert.IsTrue(consent.Service.Revoke(command).DeniedInCurrentDomain);
                Assert.IsFalse(MCPServiceLocator.ToolDiscovery.IsToolEnabled(command));
                Assert.That(Assert.Throws<InvalidOperationException>(() => retained(new JObject())).Message, Does.Contain("disabled"));
                Assert.That(
                    Assert
                        .Throws<InvalidOperationException>(() => CommandRegistry.ExecuteCommand(command, new JObject(), new TaskCompletionSource<string>()))
                        .Message,
                    Does.Contain("disabled")
                );
                Assert.That(
                    Assert.Throws<InvalidOperationException>(() => CommandRegistry.InvokeCommandAsync(command, new JObject(), CancellationToken.None)).Message,
                    Does.Contain("disabled")
                );
                Assert.AreEqual(0, invoked, "A custom handler cannot compose a disabled native handler through the registry.");
            }
            finally
            {
                if (original == null)
                    handlers.Remove(command);
                else
                    handlers[command] = original;
            }
        }

        [Test]
        public void RetainedDelegateFailsClosedOnWorkerBeforeDiscoveryCanHideDisabledTool()
        {
            const string command = "execute_code";
            using var consent = new ScopedConsentTestState(command);
            object original = handlers[command];
            int invoked = 0;
            handlers[command] = new HandlerInfo(
                command,
                _ =>
                {
                    Interlocked.Increment(ref invoked);
                    return null;
                },
                null
            );
            try
            {
                var retained = CommandRegistry.GetHandler(command);
                Assert.IsNotNull(MCPServiceLocator.ToolDiscovery.GetToolMetadata(command));
                MCPServiceLocator.ResourceDiscovery.DiscoverAllResources();
                Assert.IsTrue(consent.Service.Revoke(command).DeniedInCurrentDomain);
                var error = Task.Run(() => Assert.Throws<InvalidOperationException>(() => retained(new JObject()))).GetAwaiter().GetResult();
                Assert.That(error.Message, Does.Contain("main thread"));
                Assert.AreEqual(0, invoked);
            }
            finally
            {
                if (original == null)
                    handlers.Remove(command);
                else
                    handlers[command] = original;
            }
        }

        [Test]
        public void StaleGenerationNeverDispatchesAndSourceTypeAloneNeverGrantsReadTrust()
        {
            var binding = Binding();
            binding["instance_generation"] = Guid.NewGuid().ToString("N");
            var rejected = TransportCommandDispatcher.ExecuteCommandAsync(new Command { type = Name, arbitration = binding }, CancellationToken.None);
            Assert.AreEqual(0, calls);
            Assert.IsNull(JObject.Parse(rejected.Response.GetAwaiter().GetResult().ToJson())["native_arbitration"]);
            ((HandlerInfo)handlers[Name]).SourceType = typeof(BatchExecute);
            Assert.IsFalse(CommandRegistry.IsTrustedBuiltInHandler(Name));
            Assert.AreEqual(ToolOperationMode.Exclusive, ToolOperationPolicy.Resolve("get_selection", new JObject(), false).Mode);
        }
    }
}
