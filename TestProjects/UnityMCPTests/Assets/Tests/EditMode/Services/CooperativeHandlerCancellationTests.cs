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
    public class CooperativeHandlerCancellationTests
    {
        [Test]
        public void Registration_RecognizesOptInSignatureAndKeepsLegacyWrapper()
        {
            var method = CommandRegistry.GetCommandMethod(typeof(SignatureProbe));
            Assert.AreEqual(2, method.GetParameters().Length);
            const string name = "phase7_signature_probe";
            var handlers = (IDictionary)typeof(CommandRegistry).GetField("_handlers", BindingFlags.NonPublic | BindingFlags.Static).GetValue(null);
            var previous = handlers[name];
            using var lifetime = new CancellationTokenSource();
            try
            {
                Assert.AreEqual(1, CommandRegistry.RegisterCommandTypes(new[] { typeof(SignatureProbe) }, false));
                Assert.AreEqual(true, CommandRegistry.InvokeCommandAsync(name, new JObject(), lifetime.Token).GetAwaiter().GetResult());
                Assert.AreEqual(false, CommandRegistry.InvokeCommandAsync(name, new JObject()).GetAwaiter().GetResult());
                Assert.AreEqual(false, SignatureProbe.HandleCommand(new JObject()).GetAwaiter().GetResult());
            }
            finally { if (previous == null) handlers.Remove(name); else handlers[name] = previous; }
        }

        [Test]
        public void PrestartCanceledToken_DoesNotInvokeRegisteredHandler()
        {
            const string name = "phase7_prestart_cancel";
            bool invoked = false;
            using var registration = new HandlerRegistration(name, HandlerInfo.Cooperative(name, (_, token) => { invoked = true; return Task.FromResult<object>(token); }));
            using var lifetime = new CancellationTokenSource();
            lifetime.Cancel();
            Assert.Throws<OperationCanceledException>(() => CommandRegistry.InvokeCommandAsync(name, new JObject(), lifetime.Token));
            var operation = TransportCommandDispatcher.ExecuteCommandAsync(new Command { type = name }, lifetime.Token);
            Assert.IsTrue(operation.Response.IsCanceled);
            Assert.IsTrue(operation.Completion.IsCompleted);
            Assert.IsFalse(invoked);
        }

        [TestCase(false)]
        [TestCase(true)]
        public async Task DeadlineOrDisconnect_StopsCooperativeHandlerAndSettlesExecution(bool disconnect)
        {
            const string name = "phase7_cooperative_wait";
            bool started = false;
            bool settled = false;
            using var registration = new HandlerRegistration(name, HandlerInfo.Cooperative(name, async (_, token) =>
            {
                started = true;
                try { await Task.Delay(Timeout.Infinite, token).ConfigureAwait(true); return null; }
                finally { settled = true; }
            }));
            using var connection = new CancellationTokenSource();
            using var deadline = CancellationTokenSource.CreateLinkedTokenSource(connection.Token);
            var operation = TransportCommandDispatcher.ExecuteCommandAsync(new Command { type = name }, deadline.Token);
            Assert.IsTrue(started);
            if (disconnect) connection.Cancel();
            else deadline.CancelAfter(20);
            Assert.AreSame(operation.Completion, await Task.WhenAny(operation.Completion, Task.Delay(5000)));
            Assert.IsTrue(settled, "Completion must represent handler finally cleanup, not merely canceled response waiting.");
            Assert.IsTrue(operation.Response.IsCanceled);
        }

        [Test]
        public async Task LegacyHandler_CancellationDoesNotPretendToStopActiveExecution()
        {
            const string name = "phase7_legacy_wait";
            var active = new TaskCompletionSource<object>(TaskCreationOptions.RunContinuationsAsynchronously);
            using var registration = new HandlerRegistration(name, new HandlerInfo(name, null, _ => active.Task));
            using var deadline = new CancellationTokenSource();
            var operation = TransportCommandDispatcher.ExecuteCommandAsync(new Command { type = name }, deadline.Token);
            deadline.Cancel();
            Assert.IsTrue(operation.Response.IsCanceled);
            Assert.IsFalse(active.Task.IsCompleted);
            Assert.IsFalse(operation.Completion.IsCompleted);
            active.SetResult(new { done = true });
            Assert.AreSame(operation.Completion, await Task.WhenAny(operation.Completion, Task.Delay(5000)));
        }

        [Test]
        public async Task QueuedCancellation_SkipsHandlerAndKeepsFollowingCommandOrdered()
        {
            const string name = "phase7_queued_cancel";
            bool invoked = false;
            bool nextInvoked = false;
            using var registration = new HandlerRegistration(name, HandlerInfo.Cooperative(name, (_, token) => { invoked = true; return Task.FromResult<object>(null); }));
            using var lifetime = new CancellationTokenSource();
            using var request = CancellationTokenSource.CreateLinkedTokenSource(lifetime.Token);
            var predecessor = new TaskCompletionSource<bool>(TaskCreationOptions.RunContinuationsAsynchronously);
            var owner = new ConnectionCommandWork(lifetime.Token);
            Assert.IsNull(owner.TryStart("first", _ => predecessor.Task));
            Assert.IsNull(owner.TryStart("queued", async previous =>
            {
                try
                {
                    await ConnectionCommandWork.WaitAsync(previous, request.Token);
                    var operation = TransportCommandDispatcher.ExecuteCommandAsync(new Command { type = name }, request.Token);
                    await operation.Completion;
                }
                finally { await ConnectionCommandWork.WaitAsync(previous, lifetime.Token); }
            }, request.Cancel));
            Assert.IsNull(owner.TryStart("next", async previous => { await previous; nextInvoked = true; }));
            Assert.IsTrue(owner.TryCancel("queued"));
            Assert.IsFalse(invoked);
            // Skipping the queued command may settle its own task, but the following
            // command must still wait for the earlier mutation to finish.
            Assert.IsFalse(nextInvoked);
            predecessor.SetResult(true);
            var drain = owner.DrainAsync();
            Assert.AreSame(drain, await Task.WhenAny(drain, Task.Delay(5000)));
            Assert.IsFalse(invoked);
            Assert.IsTrue(nextInvoked);
        }

        [Test]
        public async Task CooperativeCleanup_RemainsOrderedBeforeFollowingMutation()
        {
            const string first = "phase7_cancel_then_cleanup";
            const string next = "phase7_following_mutation";
            var cleanup = new TaskCompletionSource<bool>(TaskCreationOptions.RunContinuationsAsynchronously);
            bool cleaned = false;
            bool nextInvoked = false;
            using var firstRegistration = new HandlerRegistration(first, HandlerInfo.Cooperative(first, async (_, token) =>
            {
                try { await Task.Delay(Timeout.Infinite, token).ConfigureAwait(true); return null; }
                finally { await cleanup.Task.ConfigureAwait(true); cleaned = true; }
            }));
            using var nextRegistration = new HandlerRegistration(next, new HandlerInfo(next, _ => { Assert.IsTrue(cleaned); nextInvoked = true; return new { done = true }; }, null));
            using var lifetime = new CancellationTokenSource();
            using var deadline = new CancellationTokenSource();
            var owner = new ConnectionCommandWork(lifetime.Token);
            TransportCommandOperation active = null;
            Assert.IsNull(owner.TryStart("first", async previous =>
            {
                await previous.ConfigureAwait(true);
                active = TransportCommandDispatcher.ExecuteCommandAsync(new Command { type = first }, deadline.Token);
                try { await active.Response.ConfigureAwait(true); }
                catch (OperationCanceledException) { }
                await active.Completion.ConfigureAwait(true);
            }, deadline.Cancel));
            Assert.IsNull(owner.TryStart("next", async previous =>
            {
                await previous.ConfigureAwait(true);
                var operation = TransportCommandDispatcher.ExecuteCommandAsync(new Command { type = next }, lifetime.Token);
                await operation.Completion.ConfigureAwait(true);
            }));
            Assert.IsTrue(owner.TryCancel("first"));
            Assert.IsTrue(active.Response.IsCanceled);
            Assert.IsFalse(active.Completion.IsCompleted);
            Assert.IsFalse(nextInvoked);
            cleanup.SetResult(true);
            var drain = owner.DrainAsync();
            Assert.AreSame(drain, await Task.WhenAny(drain, Task.Delay(5000)));
            Assert.IsTrue(nextInvoked);
        }

        [McpForUnityTool("phase7_signature_probe", AutoRegister = false)]
        public static class SignatureProbe
        {
            public static Task<object> HandleCommand(JObject parameters) => HandleCommand(parameters, CancellationToken.None);
            public static Task<object> HandleCommand(JObject parameters, CancellationToken token) => Task.FromResult<object>(token.CanBeCanceled);
        }

        private sealed class HandlerRegistration : IDisposable
        {
            private readonly string _name;
            private readonly object _previous;
            private readonly IDictionary _handlers;
            public HandlerRegistration(string name, HandlerInfo handler)
            {
                CommandRegistry.Initialize();
                _name = name;
                _handlers = (IDictionary)typeof(CommandRegistry).GetField("_handlers", BindingFlags.NonPublic | BindingFlags.Static).GetValue(null);
                _previous = _handlers[name];
                _handlers[name] = handler;
            }
            public void Dispose() { if (_previous == null) _handlers.Remove(_name); else _handlers[_name] = _previous; }
        }
    }
}
