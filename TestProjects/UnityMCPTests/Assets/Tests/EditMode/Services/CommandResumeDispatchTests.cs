using System;
using System.Collections;
using System.Reflection;
using System.Threading;
using System.Threading.Tasks;
using MCPForUnity.Editor.Models;
using MCPForUnity.Editor.Services.Transport;
using MCPForUnity.Editor.Tools;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEngine.TestTools;

namespace MCPForUnityTests.Editor.Services
{
    public class CommandResumeDispatchTests
    {
        private const string Name = "__command_resume_dispatch";
        private IDictionary handlers;
        private object previous;
        private string stored;
        private int calls;
        private bool failWrite;
        private CommandResumeJournal journal;

        [SetUp]
        public void Setup()
        {
            CommandRegistry.Initialize();
            handlers = (IDictionary)typeof(CommandRegistry).GetField("_handlers", BindingFlags.Static | BindingFlags.NonPublic).GetValue(null);
            previous = handlers[Name];
            stored = null;
            calls = 0;
            failWrite = false;
            journal = Open();
        }

        [TearDown]
        public void Teardown()
        {
            if (previous == null)
                handlers.Remove(Name);
            else
                handlers[Name] = previous;
        }

        private CommandResumeJournal Open() =>
            new CommandResumeJournal(
                () => stored,
                value =>
                {
                    if (failWrite)
                        throw new InvalidOperationException("injected write failure");
                    stored = value;
                },
                () => 1000
            );

        private JObject Request(string action = "new") =>
            new JObject
            {
                ["version"] = 1,
                ["invocation_id"] = "11111111111111111111111111111111",
                ["editor_session_id"] = journal.SessionId,
                ["deadline_unix_ms"] = 10000,
                ["action"] = action,
            };

        private static JObject Binding() =>
            new JObject
            {
                ["version"] = 1,
                ["request_nonce"] = Guid.NewGuid().ToString("N"),
                ["instance_generation"] = NativeCommandArbitrator.Shared.Generation,
            };

        [Test]
        public void PublicOriginRefusesActualCustomAndArbitraryHandlerDependencies()
        {
            var method = typeof(CommandResumeState).GetMethod("CanContinue", BindingFlags.NonPublic | BindingFlags.Static);
            var policy = (Func<string, JObject, bool>)Delegate.CreateDelegate(typeof(Func<string, JObject, bool>), method);
            journal = new CommandResumeJournal(() => stored, value => stored = value, () => 1000, policy);
            handlers[Name] = new HandlerInfo(
                Name,
                _ =>
                {
                    calls++;
                    return new { done = true };
                },
                null
            );
            foreach (string name in new[] { Name, "execute_code", "execute_menu_item" })
            {
                var request = Request();
                request["invocation_id"] = Guid.NewGuid().ToString("N");
                request["origin_generation"] = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";
                Assert.IsNull(journal.Admit(request, name, new JObject()).Ticket, name);
            }
            Assert.AreEqual(0, calls);
        }

        [Test]
        public void DispatcherPersistsCompletionBeforePublishingAndCachedReplyRebindsCurrentProof()
        {
            handlers[Name] = new HandlerInfo(
                Name,
                _ =>
                {
                    calls++;
                    return new { value = 42 };
                },
                null
            );
            var request = Request();
            var admission = journal.Admit(request, Name, new JObject());
            var originalBinding = Binding();
            var operation = TransportCommandDispatcher.ExecuteCommandAsync(
                new Command
                {
                    type = Name,
                    @params = new JObject(),
                    arbitration = originalBinding,
                    resume = request,
                    ResumeTicket = admission.Ticket,
                },
                CancellationToken.None
            );
            Assert.IsTrue(operation.Response.IsCompleted && operation.Completion.IsCompleted);
            var first = JObject.Parse(operation.Response.Result.ToJson());
            Assert.AreEqual("completed", (string)first["command_resume"]["state"]);
            journal.Release(admission.Ticket);
            journal = Open();
            var current = Binding();
            var cached = JObject.Parse(journal.Admit(Request("resume"), Name, new JObject()).Response(current).ToJson());
            Assert.AreEqual(1, calls);
            Assert.AreEqual(42, (int)cached["payload"]["payload"]["result"]["value"]);
            Assert.AreEqual(current["request_nonce"], cached["payload"]["native_arbitration"]["request_nonce"]);
            Assert.AreNotEqual(originalBinding["request_nonce"], cached["payload"]["native_arbitration"]["request_nonce"]);
        }

        [Test]
        public void LargeOriginalResultIsDeliveredAfterTerminalFenceAndCannotBeReplayed()
        {
            string large = new string('x', CommandResumeJournal.MaxResultBytes + 1);
            handlers[Name] = new HandlerInfo(
                Name,
                _ =>
                {
                    calls++;
                    return new { content = large };
                },
                null
            );
            var admission = journal.Admit(Request(), Name, new JObject());
            var operation = TransportCommandDispatcher.ExecuteCommandAsync(
                new Command
                {
                    type = Name,
                    @params = new JObject(),
                    ResumeTicket = admission.Ticket,
                },
                CancellationToken.None
            );
            var response = JObject.Parse(operation.Response.Result.ToJson());
            Assert.AreEqual("completed", (string)response["command_resume"]["state"]);
            Assert.AreEqual(large, (string)response["payload"]["result"]["content"]);
            journal.Release(admission.Ticket);
            journal = Open();
            var resumed = journal.Admit(Request("resume"), Name, new JObject());
            Assert.AreEqual("unavailable", resumed.State);
            Assert.AreEqual("completed_result_not_retained", (string)resumed.Payload["code"]);
            Assert.AreEqual(1, calls);
        }

        [Test]
        public void NonretryablePreexecutionRefusalBecomesTerminalCompletedReceipt()
        {
            var admission = journal.Admit(Request(), Name, new JObject());
            var command = new Command
            {
                type = Name,
                @params = new JObject(),
                ResumeTicket = admission.Ticket,
            };
            var pendingType = typeof(TransportCommandDispatcher).GetNestedType("PendingCommand", BindingFlags.NonPublic);
            var pending = Activator.CreateInstance(
                pendingType,
                BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Instance,
                null,
                new object[]
                {
                    null,
                    command,
                    CancellationToken.None,
                    default(CancellationTokenRegistration),
                    null,
                    null,
                    false,
                    null,
                    CancellationToken.None,
                    CancellationToken.None,
                },
                null
            );
            pendingType.GetProperty("Arbitration").SetValue(pending, Binding());
            pendingType
                .GetMethod("TrySetResult")
                .Invoke(
                    pending,
                    new object[]
                    {
                        new JObject
                        {
                            ["status"] = "error",
                            ["code"] = "native_job_active",
                            ["error"] = "Editor restart required",
                        },
                        true,
                        new NativeAdmissionBlocker("native_job_active", false),
                    }
                );
            var source = (TaskCompletionSource<TransportCommandResponse>)pendingType.GetProperty("ResponseSource").GetValue(pending);
            var response = JObject.Parse(source.Task.Result.ToJson());
            Assert.AreEqual("completed", (string)response["command_resume"]["state"]);
            Assert.AreEqual("not_started", (string)response["payload"]["native_arbitration"]["disposition"]);
            Assert.IsFalse((bool)response["payload"]["native_arbitration"]["retryable"]);
            Assert.AreEqual("Editor restart required", (string)response["payload"]["payload"]["error"]);
            journal.Release(admission.Ticket);
            journal = Open();
            Assert.AreEqual("completed", journal.Admit(Request("resume"), Name, new JObject()).State);
            var replay = JObject.Parse(journal.Admit(Request("resume"), Name, new JObject()).Response(Binding()).ToJson());
            Assert.AreEqual("not_started", (string)replay["payload"]["native_arbitration"]["disposition"]);
            Assert.AreEqual("native_job_active", (string)replay["payload"]["native_arbitration"]["reason"]);
        }

        [Test]
        public void FailedStartedCheckpointPreventsActualHandlerInvocation()
        {
            handlers[Name] = new HandlerInfo(
                Name,
                _ =>
                {
                    calls++;
                    return new { done = true };
                },
                null
            );
            var admission = journal.Admit(Request(), Name, new JObject());
            failWrite = true;
            var operation = TransportCommandDispatcher.ExecuteCommandAsync(
                new Command
                {
                    type = Name,
                    @params = new JObject(),
                    ResumeTicket = admission.Ticket,
                },
                CancellationToken.None
            );
            Assert.AreEqual(0, calls);
            Assert.AreEqual("unavailable", (string)JObject.Parse(operation.Response.Result.ToJson())["command_resume"]["state"]);
            Assert.IsTrue(operation.Completion.IsCompleted);
            journal.Release(admission.Ticket);
        }

        [UnityTest]
        public IEnumerator ResponseCancellationKeepsActualLeaseUntilMainThreadCompletionCheckpoint()
        {
            var held = new TaskCompletionSource<object>(TaskCreationOptions.RunContinuationsAsynchronously);
            handlers[Name] = new HandlerInfo(
                Name,
                null,
                _ =>
                {
                    calls++;
                    return held.Task;
                }
            );
            var admission = journal.Admit(Request(), Name, new JObject());
            using var cancellation = new CancellationTokenSource();
            var operation = TransportCommandDispatcher.ExecuteCommandAsync(
                new Command
                {
                    type = Name,
                    @params = new JObject(),
                    ResumeTicket = admission.Ticket,
                },
                cancellation.Token
            );
            try
            {
                Assert.AreEqual(1, calls);
                cancellation.Cancel();
                Assert.IsTrue(operation.Response.IsCanceled);
                Assert.IsFalse(operation.Completion.IsCompleted);
                var other = TransportCommandDispatcher.ExecuteCommandAsync(new Command { type = Name, arbitration = Binding() }, CancellationToken.None);
                Assert.AreEqual("not_started", (string)JObject.Parse(other.Response.Result.ToJson())["native_arbitration"]["disposition"]);
                held.TrySetResult(new { value = 7 });
                var wait = System.Diagnostics.Stopwatch.StartNew();
                while (!operation.Completion.IsCompleted && wait.Elapsed < TimeSpan.FromSeconds(10))
                    yield return null;
                Assert.IsTrue(operation.Completion.IsCompleted);
                Assert.AreEqual("completed", journal.Inspect(admission.Ticket).State);
                Assert.AreEqual(1, calls);
            }
            finally
            {
                held.TrySetResult(new { value = 7 });
                journal.Release(admission.Ticket);
            }
        }
    }
}
