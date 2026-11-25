using System;
using System.Collections;
using System.Collections.Generic;
using System.Reflection;
using System.Threading;
using System.Threading.Tasks;
using MCPForUnity.Editor.Constants;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Editor.Models;
using MCPForUnity.Editor.Services;
using MCPForUnity.Editor.Services.Transport;
using MCPForUnity.Editor.Services.Transport.Transports;
using MCPForUnity.Editor.Tools;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEditor;
using UnityEngine;
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
            handlers[Name] = new HandlerInfo(Name, null, _ =>
            {
                calls++;
                return held.Task;
            });
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

        [UnityTest]
        public IEnumerator SharedSceneDebugAndAssetWarningReachConsoleOnlyUnderTheirOwnWriter()
        {
            using var consent = new ScopedConsentTestState("scene_inspect", "asset_bundles");
            bool hadPref = EditorPrefs.HasKey(EditorPrefKeys.DebugLogs);
            bool debug = EditorPrefs.GetBool(EditorPrefKeys.DebugLogs, false);
            bool assetEnabled = MCPServiceLocator.ToolDiscovery.IsToolEnabled("asset_bundles");
            bool pollEnabled = MCPServiceLocator.ToolDiscovery.IsToolEnabled("get_test_job");
            var receipts = new List<JObject>();
            bool activeScene = false,
                hierarchy = false,
                warning = false,
                controlError = false;
            void Observe(string message, string stack, LogType type)
            {
                bool sceneActive = message.Contains("[ManageScene] get_active:");
                bool sceneHierarchy = message.Contains("[ManageScene] get_hierarchy:");
                bool badProperties = type == LogType.Warning && message.Contains("[ManageAsset] Could not parse 'properties'");
                bool badPoll = type == LogType.Error && message.Contains("Error processing command:") && message.Contains("GetTestJob");
                if (!sceneActive && !sceneHierarchy && !badProperties && !badPoll)
                    return;
                activeScene |= sceneActive;
                hierarchy |= sceneHierarchy;
                warning |= badProperties;
                controlError |= badPoll;
                receipts.Add(JObject.FromObject(NativeCommandArbitrator.Shared.CaptureStatus()));
            }

            Application.logMessageReceived += Observe;
            try
            {
                MCPServiceLocator.ToolDiscovery.SetToolEnabled("asset_bundles", true);
                MCPServiceLocator.ToolDiscovery.SetToolEnabled("get_test_job", true);
                McpLog.SetDebugLoggingEnabled(true);
                Assert.IsTrue(NativeCommandArbitrator.Shared.TryAcquire(ToolOperationMode.SharedRead, "get_selection", null, null, out var sibling, out _));
                using (sibling)
                {
                    foreach (string action in new[] { "get_active", "get_hierarchy" })
                    {
                        var command = new Command
                        {
                            type = "scene_inspect",
                            @params = new JObject { ["action"] = action },
                            arbitration = Binding(),
                        };
                        active = TransportCommandDispatcher.ExecuteCommandAsync(command, CancellationToken.None);
                        var reply = JObject.Parse(active.Response.GetAwaiter().GetResult().ToJson());
                        Assert.AreEqual("started", (string)reply.SelectToken("native_arbitration.disposition"), reply.ToString());
                        Assert.AreEqual(true, (bool?)reply.SelectToken("payload.result.success"), reply.ToString());
                    }
                    McpLog.SetDebugLoggingEnabled(false);
                    var bundle = CommandRegistry.InvokeCommandAsync("asset_bundles", new JObject { ["action"] = "list_asset_bundles", ["properties"] = "{" });
                    Assert.IsTrue(bundle.IsCompleted);
                    bundle.GetAwaiter().GetResult();
                    McpLog.FlushDeferred();
                    Assert.IsEmpty(receipts, "Neither debug output nor unconditional warnings may execute a project subscriber during shared reads.");
                }
                Assert.IsTrue(NativeCommandArbitrator.Shared.TryAcquire(ToolOperationMode.Exclusive, null, null, null, out var siblingWriter, out _));
                using (siblingWriter)
                {
                    // Validation throws inside the real control handler before the job lookup.
                    // The dispatcher must return the error while the owner's writer remains held.
                    LogAssert.Expect(LogType.Error, new System.Text.RegularExpressions.Regex("Error processing command:"));
                    active = TransportCommandDispatcher.ExecuteCommandAsync(
                        new Command
                        {
                            type = "get_test_job",
                            @params = new JObject { ["job_id"] = "missing", ["include_details"] = new JObject() },
                            arbitration = Binding(),
                        },
                        CancellationToken.None
                    );
                    var poll = JObject.Parse(active.Response.GetAwaiter().GetResult().ToJson());
                    Assert.AreEqual("started", (string)poll.SelectToken("native_arbitration.disposition"), poll.ToString());
                    Assert.AreEqual("error", (string)poll.SelectToken("payload.status"), poll.ToString());
                    yield return null;
                    McpLog.FlushDeferred();
                    Assert.IsEmpty(receipts);
                }
                var deadline = System.Diagnostics.Stopwatch.StartNew();
                while (!(activeScene && hierarchy && warning && controlError) && deadline.Elapsed < TimeSpan.FromSeconds(5))
                    yield return null;
                Assert.IsTrue(activeScene && hierarchy && warning && controlError, "Reads and controls must preserve their original console messages.");
                foreach (var receipt in receipts)
                {
                    Assert.AreEqual(0, (int)receipt["active_reads"]);
                    Assert.AreEqual(1, (int)receipt["active_writers"]);
                    Assert.AreEqual("mcp_console", (string)receipt.SelectToken("owners[0].tool"));
                }
            }
            finally
            {
                Application.logMessageReceived -= Observe;
                McpLog.SetDebugLoggingEnabled(debug);
                if (!hadPref)
                    EditorPrefs.DeleteKey(EditorPrefKeys.DebugLogs);
                MCPServiceLocator.ToolDiscovery.SetToolEnabled("asset_bundles", assetEnabled);
                MCPServiceLocator.ToolDiscovery.SetToolEnabled("get_test_job", pollEnabled);
            }
        }

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
                            ["failFast"] = false,
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
                Assert.AreEqual("succeeded", (string)data["outcomes"][0]["state"]);
                Assert.AreEqual("not_run", (string)data["outcomes"][2]["state"]);
                Assert.AreEqual("native_job_active", (string)data["outcomes"][2]["reason"]);
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
        public void ReplacedGeneratedAssetStatusRunsItsOwnHandlerWithoutTheBatchWriter()
        {
            const string command = "generate_image";
            string key = EditorPrefKeys.ToolEnabledPrefix + command;
            bool hadPreference = EditorPrefs.HasKey(key);
            bool enabled = EditorPrefs.GetBool(key, false);
            object original = handlers[command];
            ExecutionContext continuation = null;
            void AssertNoParent()
            {
                Assert.IsNull(NativeCommandArbitrator.TakeCompositionLease());
                bool admitted = NativeCommandArbitrator.Shared.TryAcquire(ToolOperationMode.Exclusive, null, null, null, out var lease, out var blocker);
                lease?.Dispose();
                Assert.IsFalse(admitted);
                Assert.AreEqual("operation_busy", blocker.Reason);
            }

            try
            {
                MCPServiceLocator.ToolDiscovery.SetToolEnabled(command, true);
                Assert.IsTrue(CommandRegistry.IsTrustedBuiltInHandler(command));
                handlers[command] = new HandlerInfo(
                    command,
                    _ =>
                    {
                        AssertNoParent();
                        continuation = ExecutionContext.Capture();
                        return "replacement";
                    },
                    null
                );
                Assert.IsFalse(CommandRegistry.IsTrustedBuiltInHandler(command));
                Assert.IsTrue(NativeCommandArbitrator.Shared.TryAcquire(ToolOperationMode.Exclusive, null, null, null, out var parent, out _));
                using (parent)
                {
                    var result = CommandRegistry.InvokeCommandAsync(command, new JObject { ["action"] = "status" }, CancellationToken.None, parent);
                    Assert.AreEqual("replacement", result.GetAwaiter().GetResult());
                    ExecutionContext.Run(continuation, _ => AssertNoParent(), null);
                }
            }
            finally
            {
                handlers[command] = original;
                if (hadPreference)
                    EditorPrefs.SetBool(key, enabled);
                else
                    EditorPrefs.DeleteKey(key);
            }
        }

        [Test]
        public void SameAssemblyRegisteredMethodCannotImpersonateAnotherCanonicalOwner()
        {
            const string command = "get_selection";
            object original = handlers[command];
            var source = typeof(GetCompileJob);
            Func<JObject, CancellationToken, Task<object>> compileJob = GetCompileJob.HandleCommand;
            Assert.AreEqual(CommandRegistry.GetCommandMethod(source), compileJob.Method);
            Assert.IsTrue(CommandRegistry.IsTrustedBuiltInHandler("get_compile_job"));
            var handler = HandlerInfo.Cooperative(command, compileJob);
            handler.BindRegistration(source, compileJob.Method);
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
