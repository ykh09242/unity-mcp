using System;
using System.Collections;
using System.Diagnostics;
using System.Linq;
using System.Reflection;
using System.Threading;
using System.Threading.Tasks;
using MCPForUnity.Editor.Services;
using MCPForUnity.Editor.Services.Transport.Transports;
using MCPForUnity.Editor.Tools;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEditor;
using UnityEngine;
using UnityEngine.TestTools;

namespace MCPForUnityTests.Editor.Services
{
    public class EditorMainThreadWakeupTests
    {
        private static readonly MethodInfo NativeTick = typeof(EditorMainThreadWakeup).GetMethod("NativeTick", BindingFlags.NonPublic | BindingFlags.Static);
        private static readonly MethodInfo Update = typeof(EditorMainThreadWakeup).GetMethod("Drain", BindingFlags.NonPublic | BindingFlags.Static);

        [Test]
        public async Task StdioIngress_RunsFromActualNativeTickWithoutItsUpdateCallback()
        {
            Assert.IsFalse(StdioBridgeHost.IsRunning, "Use an idle test Editor; never replace another listener.");
            if (!EditorMainThreadWakeup.NativeWakeAvailable)
                Assert.Ignore("Native tick is unavailable; the separate fallback tests still apply.");
            const BindingFlags flags = BindingFlags.NonPublic | BindingFlags.Static;
            const string stopKey = "MCPForUnity.StdioExplicitlyStopped";
            bool stopped = StdioBridgeHost.IsExplicitlyStopped;
            bool hadStop = SessionState.GetBool(stopKey, false) == SessionState.GetBool(stopKey, true);
            CommandRegistry.Initialize();
            var handlers = (IDictionary)typeof(CommandRegistry).GetField("_handlers", flags).GetValue(null);
            var pump = (EditorApplication.CallbackFunction)
                Delegate.CreateDelegate(typeof(EditorApplication.CallbackFunction), typeof(StdioBridgeHost).GetMethod("ProcessCommands", flags));
            string name = "stdio_native_tick_" + Guid.NewGuid().ToString("N");
            string token = new string('d', 64);
            int calls = 0;
            handlers[name] = new HandlerInfo(name, _ => new { calls = ++calls }, null);
            try
            {
                StdioBridgeHost.StartOwned(0, token);
                EditorApplication.update -= pump;
                using var peer = StdioBridgeLifecycleTests.Connect(StdioBridgeHost.GetCurrentPort(), token);
                StdioBridgeLifecycleTests.Send(peer.GetStream(), new JObject { ["type"] = name }.ToString(Formatting.None));
                var watch = Stopwatch.StartNew();
                while (calls == 0 && watch.ElapsedMilliseconds < 5000)
                    await Task.Delay(10);
                Assert.AreEqual(1, calls, "Actual native tick delivery must admit stdio when its regular update callback is absent.");
                pump();
                NativeTick.Invoke(null, null);
                Assert.AreEqual(1, calls, "Fallback and native callbacks must not execute an admitted command twice.");
            }
            finally
            {
                EditorApplication.update += pump;
                StdioBridgeHost.Stop();
                var watch = Stopwatch.StartNew();
                while ((StdioBridgeHost.QueuedCommandCount != 0 || StdioBridgeHost.ReservedInputBytes != 0) && watch.ElapsedMilliseconds < 5000)
                    await Task.Yield();
                handlers.Remove(name);
                typeof(StdioBridgeHost).GetField("explicitlyStopped", flags).SetValue(null, stopped);
                if (hadStop)
                    SessionState.SetBool(stopKey, stopped);
                else
                    SessionState.EraseBool(stopKey);
            }
            Assert.AreEqual(0, StdioBridgeHost.QueuedCommandCount);
            Assert.AreEqual(0, StdioBridgeHost.ReservedInputBytes);
            var subscribers = (Action)typeof(EditorMainThreadWakeup).GetField("Pump", flags).GetValue(null);
            Assert.IsFalse(
                subscribers?.GetInvocationList().Any(callback => callback.Method.DeclaringType == typeof(StdioBridgeHost)) ?? false,
                "Stop must detach the stdio native pump."
            );
            Assert.IsFalse(EditorApplication.update?.GetInvocationList().Contains(pump) ?? false, "Stop must detach the fallback pump.");
        }

        [Test]
        public void ScheduledWork_RunsFromNativeTickWithoutAnInspectorDelayCall()
        {
            int calls = 0;
            using var scheduled = EditorMainThreadWakeup.Schedule(() => calls++);
            Assert.That(calls, Is.Zero, "The response must return before a Play transition can reload its domain.");
            NativeTick.Invoke(null, null);
            Assert.That(calls, Is.EqualTo(1));
            Update.Invoke(null, null);
            NativeTick.Invoke(null, null);
            Assert.That(calls, Is.EqualTo(1), "Update and native tick must not replay the same work.");
        }

        [Test]
        public void DisposedWork_IsNeverInvokedByALateTick()
        {
            int calls = 0;
            var scheduled = EditorMainThreadWakeup.Schedule(() => calls++);
            scheduled.Dispose();
            NativeTick.Invoke(null, null);
            Update.Invoke(null, null);
            Assert.That(calls, Is.Zero);
        }

        [TestCase(false)]
        [TestCase(true)]
        public void StopDuringDrain_SkipsTheRestOfTheAlreadyDequeuedBatch(bool nativeTick)
        {
            const BindingFlags flags = BindingFlags.NonPublic | BindingFlags.Static;
            Type scheduler = typeof(EditorMainThreadWakeup);
            MethodInfo stop = scheduler.GetMethod("Stop", flags);
            FieldInfo stopped = scheduler.GetField("_stopped", flags);
            FieldInfo requested = scheduler.GetField("_requested", flags);
            FieldInfo pump = scheduler.GetField("Pump", flags);
            object previousStopped = stopped.GetValue(null);
            object previousRequested = requested.GetValue(null);
            object previousPump = pump.GetValue(null);
            EditorApplication.CallbackFunction previousUpdate = EditorApplication.update;
            int firstCalls = 0;
            int laterCalls = 0;
            using var first = EditorMainThreadWakeup.Schedule(() =>
            {
                firstCalls++;
                stop.Invoke(null, null);
            });
            using var later = EditorMainThreadWakeup.Schedule(() => laterCalls++);
            try
            {
                (nativeTick ? NativeTick : Update).Invoke(null, null);
                Assert.That(firstCalls, Is.EqualTo(1));
                Assert.That(laterCalls, Is.Zero, "Stop must invalidate callbacks already copied out of the pending queue.");
                NativeTick.Invoke(null, null);
                Update.Invoke(null, null);
                Assert.That(laterCalls, Is.Zero, "A late tick or public update must not revive the stopped batch.");
                Assert.Throws<InvalidOperationException>(() => EditorMainThreadWakeup.Schedule(() => laterCalls++));
            }
            finally
            {
                // Stop removes only this helper's subscriptions. Restore them so running
                // this regression cannot disable the user's Editor or transport pump.
                stopped.SetValue(null, previousStopped);
                requested.SetValue(null, previousRequested);
                pump.SetValue(null, previousPump);
                EditorApplication.update = previousUpdate;
                Action stopCallback = (Action)Delegate.CreateDelegate(typeof(Action), stop);
                EditorApplication.quitting += stopCallback;
                EventInfo reload = typeof(AssemblyReloadEvents).GetEvent("beforeAssemblyReload");
                reload.AddEventHandler(null, Delegate.CreateDelegate(reload.EventHandlerType, stop));
                if (EditorMainThreadWakeup.NativeWakeAvailable)
                {
                    EventInfo tick = typeof(EditorApplication).GetEvent("tick", flags);
                    var tickCallback = (EditorApplication.CallbackFunction)Delegate.CreateDelegate(typeof(EditorApplication.CallbackFunction), NativeTick);
                    tick.GetAddMethod(true).Invoke(null, new object[] { tickCallback });
                }
            }
        }

        [Test]
        public void DisposingLaterWork_SkipsItAfterTheBatchHasAlreadyBeenDequeued()
        {
            int laterCalls = 0;
            IDisposable later = null;
            using var first = EditorMainThreadWakeup.Schedule(() => later.Dispose());
            using (later = EditorMainThreadWakeup.Schedule(() => laterCalls++))
            {
                NativeTick.Invoke(null, null);
                Update.Invoke(null, null);
                Assert.That(laterCalls, Is.Zero);
            }
        }

        [TestCase(false)]
        [TestCase(true)]
        public void ThrowingWork_DoesNotLoseLaterOrCancelledWork(bool nativeTick)
        {
            int laterCalls = 0;
            int cancelledCalls = 0;
            IDisposable cancelled = null;
            using var throwing = EditorMainThreadWakeup.Schedule(() =>
            {
                cancelled.Dispose();
                throw new InvalidOperationException("Scheduled callback regression");
            });
            using var later = EditorMainThreadWakeup.Schedule(() => laterCalls++);
            using (cancelled = EditorMainThreadWakeup.Schedule(() => cancelledCalls++))
            {
                LogAssert.Expect(LogType.Exception, new System.Text.RegularExpressions.Regex("InvalidOperationException: Scheduled callback regression"));
                Assert.DoesNotThrow(() => (nativeTick ? NativeTick : Update).Invoke(null, null));
                NativeTick.Invoke(null, null);
                Update.Invoke(null, null);
                Assert.That(laterCalls, Is.EqualTo(1));
                Assert.That(cancelledCalls, Is.Zero);
            }
        }

        [Test]
        public void MissingNativeMembersExposeFallbackAndPublicUpdateStillDrainsOnce()
        {
            AssertFallback(typeof(MissingNativeMembers), "signal_tick_missing");
        }

        [Test]
        public void ReflectionFailureExposesFallbackAndPublicUpdateStillDrainsOnce()
        {
            AssertFallback(typeof(ThrowingNativeMembers), "reflection_error");
        }

        private static void AssertFallback(Type nativeType, string expectedReason)
        {
            var resolve = typeof(EditorMainThreadWakeup).GetMethod("ResolveNativeWake", BindingFlags.NonPublic | BindingFlags.Static);
            Assert.IsNotNull(resolve, "Native binding must expose the reason for a safe fallback.");
            object[] arguments = { nativeType, null, null };
            Assert.AreEqual(expectedReason, resolve.Invoke(null, arguments));
            Assert.IsNull(arguments[1]);
            Assert.IsNull(arguments[2]);
            int calls = 0;
            using var scheduled = EditorMainThreadWakeup.Schedule(() => calls++);
            Update.Invoke(null, null);
            Update.Invoke(null, null);
            Assert.AreEqual(1, calls);
        }

        private static class MissingNativeMembers { }

        private static class ThrowingNativeMembers
        {
            private static void SignalTick() { }

            private static event EditorApplication.CallbackFunction tick
            {
                add => throw new InvalidOperationException("Injected native binding failure");
                remove { }
            }
        }

        [Test]
        public void PublicUpdate_RemainsAOnceOnlyFallback()
        {
            int calls = 0;
            using var scheduled = EditorMainThreadWakeup.Schedule(() => calls++);
            Update.Invoke(null, null);
            NativeTick.Invoke(null, null);
            Assert.That(calls, Is.EqualTo(1));
        }

        [Test]
        public void BackgroundAdmission_SignalsBeforeAnySynchronizationContextCallbackRuns()
        {
            Update.Invoke(null, null);
            var thread = new Thread(EditorMainThreadWakeup.Request);
            thread.Start();
            Assert.That(thread.Join(TimeSpan.FromSeconds(5)), Is.True);
            Assert.That(typeof(EditorMainThreadWakeup).GetField("_requested", BindingFlags.NonPublic | BindingFlags.Static).GetValue(null), Is.EqualTo(1));
            NativeTick.Invoke(null, null);
        }
    }
}
