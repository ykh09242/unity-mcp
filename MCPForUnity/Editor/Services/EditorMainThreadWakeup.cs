using System;
using System.Collections.Generic;
using System.Reflection;
using System.Threading;
using MCPForUnity.Editor.Helpers;
using UnityEditor;

namespace MCPForUnity.Editor.Services
{
    /// <summary>Wakes queued work without requiring an Inspector repaint or changing Editor preferences.</summary>
    [InitializeOnLoad]
    internal static class EditorMainThreadWakeup
    {
        private static readonly object Gate = new();
        private static readonly LinkedList<Scheduled> Pending = new();
        private static readonly Action Signal;
        private static readonly Action RemoveTick;
        private static int _requested;
        private static int _stopped;
        internal static event Action Pump;
        internal static bool NativeWakeAvailable => Signal != null;

        static EditorMainThreadWakeup()
        {
            try
            {
                // Unity 2021.3 and 6000.0 mark SignalTick [ThreadSafe]. QueuePlayerLoopUpdate
                // is not thread-safe and does not deliver the separate native tick event.
                const BindingFlags flags = BindingFlags.Static | BindingFlags.NonPublic;
                MethodInfo signal = typeof(EditorApplication).GetMethod("SignalTick", flags, null, Type.EmptyTypes, null);
                EventInfo tick = typeof(EditorApplication).GetEvent("tick", flags);
                if (signal != null && tick?.GetAddMethod(true) != null && tick.GetRemoveMethod(true) != null)
                {
                    Action boundSignal = (Action)Delegate.CreateDelegate(typeof(Action), signal);
                    EditorApplication.CallbackFunction callback = NativeTick;
                    tick.GetAddMethod(true).Invoke(null, new object[] { callback });
                    RemoveTick = () => tick.GetRemoveMethod(true).Invoke(null, new object[] { callback });
                    Signal = boundSignal;
                }
            }
            catch (Exception exception)
            {
                McpLog.Warn("Native Editor wake-up is unavailable; using Editor update callbacks. " + exception.GetType().Name);
            }
            EditorApplication.update += Drain;
            AssemblyReloadEvents.beforeAssemblyReload += Stop;
            EditorApplication.quitting += Stop;
        }

        internal static void Request()
        {
            if (Volatile.Read(ref _stopped) != 0 || Interlocked.Exchange(ref _requested, 1) != 0)
                return;
            Signal?.Invoke();
        }

        internal static IDisposable Schedule(Action callback)
        {
            if (callback == null)
                throw new ArgumentNullException(nameof(callback));
            var scheduled = new Scheduled(callback);
            lock (Gate)
            {
                if (_stopped != 0)
                    throw new InvalidOperationException("The Editor main-thread scheduler is shutting down.");
                scheduled.Node = Pending.AddLast(scheduled);
            }
            Request();
            return scheduled;
        }

        private static void NativeTick()
        {
            if (Volatile.Read(ref _stopped) != 0 || Interlocked.Exchange(ref _requested, 0) == 0)
                return;
            // Only Unity's main-thread tick can request a player loop. This also advances
            // UnitySynchronizationContext continuations posted by asynchronous handlers.
            EditorApplication.QueuePlayerLoopUpdate();
            Pump?.Invoke();
            Drain();
        }

        private static void Drain()
        {
            Scheduled[] callbacks;
            lock (Gate)
            {
                if (Pending.Count == 0)
                    return;
                callbacks = new Scheduled[Pending.Count];
                Pending.CopyTo(callbacks, 0);
                Pending.Clear();
                foreach (Scheduled scheduled in callbacks)
                    scheduled.Node = null;
            }
            foreach (Scheduled scheduled in callbacks)
                scheduled.Invoke();
        }

        private static void Stop()
        {
            Interlocked.Exchange(ref _stopped, 1);
            RemoveTick?.Invoke();
            EditorApplication.update -= Drain;
            AssemblyReloadEvents.beforeAssemblyReload -= Stop;
            EditorApplication.quitting -= Stop;
            lock (Gate)
            {
                foreach (Scheduled scheduled in Pending)
                    scheduled.Clear();
                Pending.Clear();
            }
            Pump = null;
        }

        private sealed class Scheduled : IDisposable
        {
            private Action _callback;
            internal LinkedListNode<Scheduled> Node;

            internal Scheduled(Action callback) => _callback = callback;

            internal void Invoke() => Interlocked.Exchange(ref _callback, null)?.Invoke();

            internal void Clear() => Interlocked.Exchange(ref _callback, null);

            public void Dispose()
            {
                lock (Gate)
                {
                    Clear();
                    if (Node?.List != null)
                        Pending.Remove(Node);
                    Node = null;
                }
            }
        }
    }
}
