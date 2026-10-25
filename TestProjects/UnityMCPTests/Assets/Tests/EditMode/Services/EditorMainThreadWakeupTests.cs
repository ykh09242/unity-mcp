using System;
using System.Reflection;
using System.Threading;
using MCPForUnity.Editor.Services;
using NUnit.Framework;
using UnityEditor;

namespace MCPForUnityTests.Editor.Services
{
    public class EditorMainThreadWakeupTests
    {
        private static readonly MethodInfo NativeTick = typeof(EditorMainThreadWakeup).GetMethod("NativeTick", BindingFlags.NonPublic | BindingFlags.Static);
        private static readonly MethodInfo Update = typeof(EditorMainThreadWakeup).GetMethod("Drain", BindingFlags.NonPublic | BindingFlags.Static);

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
