using System;
using System.Collections.Generic;
using System.Reflection;
using System.Runtime.CompilerServices;
using System.Threading;
using System.Threading.Tasks;
using MCPForUnity.Runtime.Helpers;
using NUnit.Framework;
using UnityEngine;
using UnityEngine.Rendering;

namespace MCPForUnityTests.Editor.Helpers
{
    public class ScreenshotCapturerCallbackLifetimeTests
    {
        private readonly List<ScreenshotCapturer> owned = new();
        private readonly List<Texture2D> ownedTextures = new();
        private static readonly MethodInfo Complete = typeof(ScreenshotCapturer).GetMethod("Complete", BindingFlags.NonPublic | BindingFlags.Instance);

        [TearDown]
        public void TearDown()
        {
            foreach (var capturer in owned)
                if (capturer != null)
                {
                    Finish(capturer, null, true); // Consume any pending synthetic failure before destroying our helper.
                    if (capturer != null)
                        UnityEngine.Object.DestroyImmediate(capturer.gameObject);
                }
            foreach (var texture in ownedTextures)
                if (texture != null)
                    UnityEngine.Object.DestroyImmediate(texture);
            owned.Clear();
            ownedTextures.Clear();
        }

        [TestCase(false, false)]
        [TestCase(false, true)]
        [TestCase(true, false)]
        [TestCase(true, true)]
        public void Completion_ReleasesCapturedRequestFromRetainedWrapper(bool throws, bool timedOut)
        {
            var pair = BeginCaptured(throws);
            owned.Add(pair.owner);
            Collect();
            Assert.IsTrue(pair.request.IsAlive, "pending callback owns its request");
            Assert.AreEqual(throws, Finish(pair.owner, null, timedOut), "callback exception contract");
            Collect();
            Assert.IsFalse(pair.request.IsAlive, "completed wrapper must release consumed callback captures");
            GC.KeepAlive(pair.owner);
        }

        [TestCase(true)]
        [TestCase(false)]
        public void NullCallback_DestroysUndeliverableTexture(bool oneArgument)
        {
            if (SystemInfo.graphicsDeviceType == GraphicsDeviceType.Null)
                Assert.Ignore("Texture allocation requires a graphics device.");
            var capturer = oneArgument
                ? ScreenshotCapturer.Begin(1, (Action<Texture2D>)null, 60)
                : ScreenshotCapturer.Begin(1, (Action<Texture2D, bool>)null, 60);
            owned.Add(capturer);
            var texture = new Texture2D(1, 1);
            ownedTextures.Add(texture);
            Assert.IsFalse(Finish(capturer, texture, false));
            Assert.IsTrue(texture == null, "no caller accepted ownership of the captured texture");
        }

        [TestCase(false, false)]
        [TestCase(true, false)]
        [TestCase(false, true)]
        public void CancellationAndCompletionRaceConsumesCallbackExactlyOnce(bool completeFirst, bool destroyFirst)
        {
            using var cancel = new CancellationTokenSource();
            int callbacks = 0;
            int callbackThread = 0;
            int mainThread = Thread.CurrentThread.ManagedThreadId;
            var owner = ScreenshotCapturer.Begin(
                1,
                (Texture2D texture, bool timeout) =>
                {
                    callbacks++;
                    callbackThread = Thread.CurrentThread.ManagedThreadId;
                },
                cancel.Token,
                60
            );
            owned.Add(owner);
            if (completeFirst)
                Assert.IsFalse(Finish(owner, null, false));
            Assert.IsTrue(Task.Run(() => cancel.Cancel()).Wait(2000));
            if (!completeFirst)
            {
                Assert.AreEqual(0, callbacks, "The cancellation thread must not invoke a Unity-owned callback.");
                if (destroyFirst)
                    UnityEngine.Object.DestroyImmediate(owner.gameObject);
                typeof(ScreenshotCapturer).GetMethod("TickTimeout", BindingFlags.Instance | BindingFlags.NonPublic).Invoke(owner, null);
            }
            Assert.AreEqual(1, callbacks);
            Assert.AreEqual(mainThread, callbackThread);
            Assert.IsTrue(owner == null);
            Assert.IsFalse(Finish(owner, null, true), "A late timeout/destruction callback must be harmless.");
            Assert.AreEqual(1, callbacks);
        }

        [Test]
        public void CancellationReleasesConsumedRequestFromRetainedWrapper()
        {
            using var cancel = new CancellationTokenSource();
            var pair = BeginCaptured(false, cancel.Token);
            owned.Add(pair.owner);
            Collect();
            Assert.IsTrue(pair.request.IsAlive);
            cancel.Cancel();
            typeof(ScreenshotCapturer).GetMethod("TickTimeout", BindingFlags.Instance | BindingFlags.NonPublic).Invoke(pair.owner, null);
            Collect();
            Assert.IsFalse(pair.request.IsAlive, "Canceled wrapper must release its consumed callback capture.");
            GC.KeepAlive(pair.owner);
        }

        [MethodImpl(MethodImplOptions.NoInlining)]
        private static (ScreenshotCapturer owner, WeakReference request) BeginCaptured(bool throws, CancellationToken cancellationToken = default)
        {
            byte[] request = new byte[4 * 1024 * 1024];
            var owner = ScreenshotCapturer.Begin(
                1,
                (Texture2D texture, bool timeout) =>
                {
                    GC.KeepAlive(request);
                    if (throws)
                        throw new InvalidOperationException("Synthetic callback failure.");
                },
                cancellationToken,
                60
            );
            return (owner, new WeakReference(request));
        }

        [MethodImpl(MethodImplOptions.NoInlining)]
        private static bool Finish(ScreenshotCapturer owner, Texture2D texture, bool timedOut)
        {
            try
            {
                Complete.Invoke(owner, new object[] { texture, timedOut });
                return false;
            }
            catch (TargetInvocationException ex) when (ex.InnerException is InvalidOperationException)
            {
                return true;
            }
        }

        private static void Collect()
        {
            GC.Collect();
            GC.WaitForPendingFinalizers();
            GC.Collect();
        }
    }
}
