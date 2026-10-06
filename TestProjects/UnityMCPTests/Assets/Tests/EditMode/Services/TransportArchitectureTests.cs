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

namespace MCPForUnityTests.Editor.Services
{
    [TestFixture]
    public class TransportArchitectureTests
    {
        [Test]
        public void MainThreadCallback_WithMissingContext_WaitsForEditorPump()
        {
            var context = typeof(TransportCommandDispatcher).GetField("_mainThreadContext", BindingFlags.Static | BindingFlags.NonPublic);
            var original = context.GetValue(null);
            int mainThread = Thread.CurrentThread.ManagedThreadId;
            Task<int> queued = null;
            try
            {
                context.SetValue(null, null);
                Task.Run(() => { queued = TransportCommandDispatcher.RunOnMainThreadAsync(() => Thread.CurrentThread.ManagedThreadId, CancellationToken.None); }).GetAwaiter().GetResult();
                Assert.IsFalse(queued.IsCompleted, "Missing Unity synchronization context must never execute Unity work on a receiver thread.");
                typeof(TransportCommandDispatcher).GetMethod("ProcessQueue", BindingFlags.Static | BindingFlags.NonPublic).Invoke(null, null);
                Assert.AreEqual(mainThread, queued.GetAwaiter().GetResult());
            }
            finally { context.SetValue(null, original); }
        }

        [Test]
        public void OwnedWork_BoundsIdsAndRetainsFifoUntilExecutionCompletes()
        {
            using var lifetime = new CancellationTokenSource();
            var owner = new ConnectionCommandWork(lifetime.Token, 2);
            var active = new TaskCompletionSource<bool>(TaskCreationOptions.RunContinuationsAsynchronously);
            bool secondStarted = false;
            Assert.IsNull(owner.TryStart("first", _ => active.Task));
            Assert.AreEqual("Duplicate command id", owner.TryStart("first", _ => Task.CompletedTask));
            Assert.IsNull(owner.TryStart("second", async previous =>
            {
                await previous.ConfigureAwait(false);
                secondStarted = true;
            }));
            Assert.AreEqual("Command queue is full", owner.TryStart("third", _ => Task.CompletedTask));
            Assert.IsFalse(secondStarted);
            active.SetResult(true);
            Assert.IsTrue(owner.DrainAsync().Wait(TimeSpan.FromSeconds(5)));
            Assert.IsTrue(secondStarted);
        }

        [Test]
        public void TypedAndLegacySyncResponses_PreserveExactEnvelope()
        {
            CommandRegistry.Initialize();
            const string name = "architecture_sync_parity";
            var handlers = (IDictionary)typeof(CommandRegistry).GetField("_handlers", BindingFlags.Static | BindingFlags.NonPublic).GetValue(null);
            var previous = handlers[name];
            handlers[name] = new HandlerInfo(name, _ => new { text = "한글😀", nested = new { count = 3 } }, null);
            try
            {
                var command = new Command { type = name, @params = new JObject() };
                var typed = TransportCommandDispatcher.ExecuteCommandAsync(command, CancellationToken.None);
                var legacy = TransportCommandDispatcher.ExecuteCommandJsonAsync("{\"type\":\"" + name + "\"}", CancellationToken.None);
                Assert.AreEqual(legacy.GetAwaiter().GetResult(), typed.Response.GetAwaiter().GetResult().ToJson());
                Assert.IsTrue(typed.Completion.IsCompleted);
                Assert.IsInstanceOf<JToken>(typed.Response.Result.Payload, "Unity-facing values are frozen without a JSON text round trip.");
            }
            finally
            {
                if (previous == null) handlers.Remove(name);
                else handlers[name] = previous;
            }
        }

        [Test]
        public void TypedAndLegacyAsyncResponses_PreserveExactEnvelope()
        {
            CommandRegistry.Initialize();
            const string name = "architecture_async_parity";
            var handlers = (IDictionary)typeof(CommandRegistry).GetField("_handlers", BindingFlags.Static | BindingFlags.NonPublic).GetValue(null);
            var previous = handlers[name];
            handlers[name] = new HandlerInfo(name, null, _ => Task.FromResult<object>(new { text = "한글😀", values = new[] { 1, 2, 3 } }));
            try
            {
                var typed = TransportCommandDispatcher.ExecuteCommandAsync(new Command { type = name }, CancellationToken.None);
                var legacy = TransportCommandDispatcher.ExecuteCommandJsonAsync("{\"type\":\"" + name + "\"}", CancellationToken.None);
                Assert.AreEqual(legacy.GetAwaiter().GetResult(), typed.Response.GetAwaiter().GetResult().ToJson());
                Assert.IsTrue(typed.Completion.IsCompleted);
            }
            finally
            {
                if (previous == null) handlers.Remove(name);
                else handlers[name] = previous;
            }
        }

        [Test]
        public async Task ActiveDeadline_CancelsResponseButTracksActualAsyncSettlement()
        {
            CommandRegistry.Initialize();
            const string name = "architecture_async_deadline";
            var handlers = (IDictionary)typeof(CommandRegistry).GetField("_handlers", BindingFlags.Static | BindingFlags.NonPublic).GetValue(null);
            var previous = handlers[name];
            var handlerCompletion = new TaskCompletionSource<object>(TaskCreationOptions.RunContinuationsAsynchronously);
            handlers[name] = new HandlerInfo(name, null, _ => handlerCompletion.Task);
            using var deadline = new CancellationTokenSource();
            try
            {
                var operation = TransportCommandDispatcher.ExecuteCommandAsync(new Command { type = name }, deadline.Token);
                deadline.Cancel();
                Assert.IsTrue(operation.Response.IsCanceled);
                Assert.IsFalse(operation.Completion.IsCompleted, "Response cancellation must not release the active mutation.");
                handlerCompletion.SetResult(new { done = true });
                Assert.AreSame(operation.Completion, await Task.WhenAny(operation.Completion, Task.Delay(TimeSpan.FromSeconds(5))));
            }
            finally
            {
                handlerCompletion.TrySetResult(new { done = true });
                if (previous == null) handlers.Remove(name);
                else handlers[name] = previous;
            }
        }
    }
}
