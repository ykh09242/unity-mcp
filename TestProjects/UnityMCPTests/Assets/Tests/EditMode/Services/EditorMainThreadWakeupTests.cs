using System;
using System.Reflection;
using System.Threading;
using MCPForUnity.Editor.Services;
using NUnit.Framework;

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
