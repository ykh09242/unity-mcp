using System;
using System.Collections;
using System.Collections.Generic;
using System.Reflection;
using System.Text.RegularExpressions;
using System.Threading;
using System.Threading.Tasks;
using MCPForUnity.Editor.Services.Transport.Transports;
using MCPForUnity.Editor.Tools;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEngine;
using UnityEngine.TestTools;

namespace MCPForUnityTests.Editor.Services
{
    [TestFixture]
    public class TransportCommandDispatcherTests
    {
        private const BindingFlags StaticPrivate = BindingFlags.Static | BindingFlags.NonPublic;
        private static readonly Type Dispatcher = typeof(WebSocketTransportClient).Assembly.GetType(
            "MCPForUnity.Editor.Services.Transport.TransportCommandDispatcher",
            true
        );
        private static readonly FieldInfo ContextField = Dispatcher.GetField("_mainThreadContext", StaticPrivate);
        private static readonly FieldInfo ThreadIdField = Dispatcher.GetField("_mainThreadId", StaticPrivate);
        private static readonly MethodInfo Execute = Dispatcher.GetMethod("ExecuteCommandJsonAsync", BindingFlags.Static | BindingFlags.Public);
        private static readonly IDictionary Pending = (IDictionary)Dispatcher.GetField("Pending", StaticPrivate).GetValue(null);
        private static readonly object PendingLock = Dispatcher.GetField("PendingLock", StaticPrivate).GetValue(null);
        private readonly DeferredContext _context = new DeferredContext();
        private object _originalContext;
        private object _originalThreadId;

        [SetUp]
        public void SetUp()
        {
            _originalContext = ContextField.GetValue(null);
            _originalThreadId = ThreadIdField.GetValue(null);
            ContextField.SetValue(null, _context);
            ThreadIdField.SetValue(null, Thread.CurrentThread.ManagedThreadId);
        }

        [TearDown]
        public void TearDown()
        {
            try
            {
                _context.Drain();
            }
            finally
            {
                ContextField.SetValue(null, _originalContext);
                ThreadIdField.SetValue(null, _originalThreadId);
            }
        }

        [Test]
        public void ExecuteCommandJsonAsync_AlreadyCanceled_CompletesWithoutEditorPump()
        {
            using var cts = new CancellationTokenSource();
            cts.Cancel();
            Task<string> command = null;
            Assert.IsTrue(
                Task.Run(() =>
                    {
                        command = Dispatch("ping", cts.Token);
                    })
                    .Wait(TimeSpan.FromSeconds(5))
            );

            Assert.IsTrue(command.IsCanceled, "Cancellation must complete even while the main-thread pump is paused.");
            Assert.IsFalse(IsPending(command), "Canceled commands must release the pending entry immediately.");
        }

        [Test]
        public void ExecuteCommandJsonAsync_CanceledBeforeEditorPump_RemovesQueuedCommand()
        {
            using var cts = new CancellationTokenSource();
            Task<string> command = null;
            Assert.IsTrue(
                Task.Run(() =>
                    {
                        command = Dispatch("ping", cts.Token);
                    })
                    .Wait(TimeSpan.FromSeconds(5))
            );
            Assert.IsFalse(command.IsCompleted);
            cts.Cancel();

            Assert.IsTrue(command.IsCanceled);
            Assert.IsFalse(IsPending(command));
        }

        [Test]
        public void ExecuteCommandJsonAsync_CompletedAsyncCommand_CleansUpWithoutEditorFrame()
        {
            const string name = "dispatcher_test_completed_async";
            CommandRegistry.Initialize();
            var handlers = (IDictionary)typeof(CommandRegistry).GetField("_handlers", StaticPrivate).GetValue(null);
            var handlerType = typeof(CommandRegistry).Assembly.GetType("MCPForUnity.Editor.Tools.HandlerInfo", true);
            Func<JObject, Task<object>> handler = _ => Task.FromResult<object>(new { done = true });
            var previous = handlers[name];
            handlers[name] = Activator.CreateInstance(handlerType, new object[] { name, null, handler });
            using var cts = new CancellationTokenSource();
            Task<string> command = null;
            try
            {
                command = Dispatch("{\"type\":\"" + name + "\"}", cts.Token);
                Assert.IsTrue(command.IsCompleted, "The test handler should complete synchronously.");
                Assert.IsTrue(
                    SpinWait.SpinUntil(() => !IsPending(command), TimeSpan.FromSeconds(5)),
                    "Completed async commands must release pending state without waiting for delayCall."
                );
            }
            finally
            {
                cts.Cancel();
                if (previous == null)
                    handlers.Remove(name);
                else
                    handlers[name] = previous;
            }
        }

        [Test]
        public void ExecuteCommandJsonAsync_LargeAsyncResponseWithLoggingDisabled_PreservesResultAndCleansUp()
        {
            const string name = "dispatcher_test_large_async";
            CommandRegistry.Initialize();
            var handlers = (IDictionary)typeof(CommandRegistry).GetField("_handlers", StaticPrivate).GetValue(null);
            var handlerType = typeof(CommandRegistry).Assembly.GetType("MCPForUnity.Editor.Tools.HandlerInfo", true);
            var loggingType = typeof(CommandRegistry).Assembly.GetType("MCPForUnity.Editor.Helpers.McpLogRecord", true);
            var enabledField = loggingType.GetField("_isEnabledCached", StaticPrivate);
            var previousLogging = enabledField.GetValue(null);
            var previousHandler = handlers[name];
            string payload = new string('x', 256 * 1024) + "한글😀";
            Func<JObject, Task<object>> handler = _ => Task.FromResult<object>(new { payload });
            using var cts = new CancellationTokenSource();
            try
            {
                // Use the cached flag to avoid changing EditorPrefs in this fixture.
                enabledField.SetValue(null, false);
                handlers[name] = Activator.CreateInstance(handlerType, new object[] { name, null, handler });
                var command = Dispatch("{\"type\":\"" + name + "\"}", cts.Token);
                Assert.IsTrue(command.Wait(TimeSpan.FromSeconds(5)));
                Assert.AreEqual("{\"status\":\"success\",\"result\":{\"payload\":\"" + payload + "\"}}", command.Result);
                Assert.IsTrue(
                    SpinWait.SpinUntil(() => !IsPending(command), TimeSpan.FromSeconds(5)),
                    "Disabling async response logging must still release pending state without an editor frame."
                );
            }
            finally
            {
                cts.Cancel();
                if (previousHandler == null)
                    handlers.Remove(name);
                else
                    handlers[name] = previousHandler;
                enabledField.SetValue(null, previousLogging);
            }
        }

        private static Task<string> Dispatch(string json, CancellationToken token) => (Task<string>)Execute.Invoke(null, new object[] { json, token });

        private static bool IsPending(Task<string> command)
        {
            lock (PendingLock)
            {
                foreach (var pending in Pending.Values)
                {
                    var completion = (TaskCompletionSource<string>)pending.GetType().GetProperty("JsonResponseSource").GetValue(pending);
                    if (ReferenceEquals(completion.Task, command))
                        return true;
                }
                return false;
            }
        }

        private sealed class DeferredContext : SynchronizationContext
        {
            private readonly Queue<Action> _callbacks = new Queue<Action>();

            public override void Post(SendOrPostCallback callback, object state)
            {
                lock (_callbacks)
                    _callbacks.Enqueue(() => callback(state));
            }

            public void Drain()
            {
                while (true)
                {
                    Action callback;
                    lock (_callbacks)
                    {
                        if (_callbacks.Count == 0)
                            return;
                        callback = _callbacks.Dequeue();
                    }
                    callback();
                }
            }
        }

        [UnityTest]
        public IEnumerator UnknownCommand_IsAnsweredWithoutAnErrorInTheConsole()
        {
            // A server or CLI newer than the package sends commands this package does not have.
            // That reached the console as a red error with a stack trace, as if the Editor broke.
            LogAssert.Expect(LogType.Warning, new Regex("no_such_command.*update the package"));

            var reply = Dispatch("{\"type\":\"no_such_command\",\"params\":{}}", CancellationToken.None);
            for (int frame = 0; frame < 600 && !reply.IsCompleted; frame++)
            {
                yield return null;
            }

            Assert.IsTrue(reply.IsCompleted, "the dispatcher never answered");
            var response = JObject.Parse(reply.Result);
            Assert.AreEqual("error", (string)response["status"]);
            StringAssert.Contains("'no_such_command'", (string)response["error"]);
            Assert.AreEqual("no_such_command", (string)response["command"]);
            Assert.IsNull((string)response["stackTrace"]);
        }
    }
}
