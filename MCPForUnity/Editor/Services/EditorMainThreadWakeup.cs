using System;
using System.Collections.Generic;
using System.Diagnostics;
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
        private static int _draining;
        private static long _nextSequence;
        private const int MaxCallbacksPerTick = 64;
        private static readonly long TickBudget = Math.Max(1, Stopwatch.Frequency / 250);
        internal static event Action Pump;
        internal static bool NativeWakeAvailable => Signal != null;
        internal static string WakeMode => NativeWakeAvailable ? "native_tick" : "editor_update";
        internal static string NativeWakeFallbackReason { get; }

        static EditorMainThreadWakeup()
        {
            NativeWakeFallbackReason = ResolveNativeWake(typeof(EditorApplication), out Signal, out RemoveTick);
            if (NativeWakeFallbackReason != null)
                McpLog.Warn("Native Editor wake-up is unavailable; using Editor update callbacks. Reason: " + NativeWakeFallbackReason);
            EditorApplication.update += Drain;
            AssemblyReloadEvents.beforeAssemblyReload += Stop;
            EditorApplication.quitting += Stop;
        }

        private static string ResolveNativeWake(Type editorType, out Action signalWake, out Action removeTick)
        {
            signalWake = null;
            removeTick = null;
            try
            {
                // Unity 2021.3 and 6000.0 mark SignalTick [ThreadSafe]. QueuePlayerLoopUpdate
                // is not thread-safe and does not deliver the separate native tick event.
                const BindingFlags flags = BindingFlags.Static | BindingFlags.NonPublic;
                MethodInfo signal = editorType.GetMethod("SignalTick", flags, null, Type.EmptyTypes, null);
                if (signal == null)
                    return "signal_tick_missing";
                EventInfo tick = editorType.GetEvent("tick", flags);
                if (tick == null)
                    return "tick_event_missing";
                if (tick.GetAddMethod(true) == null || tick.GetRemoveMethod(true) == null)
                    return "tick_accessors_missing";
                Action boundSignal = (Action)Delegate.CreateDelegate(typeof(Action), signal);
                EditorApplication.CallbackFunction callback = NativeTick;
                Action boundRemove = () => tick.GetRemoveMethod(true).Invoke(null, new object[] { callback });
                tick.GetAddMethod(true).Invoke(null, new object[] { callback });
                removeTick = boundRemove;
                signalWake = boundSignal;
                return null;
            }
            catch (Exception)
            {
                return "reflection_error";
            }
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
                scheduled.Sequence = ++_nextSequence;
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
            if (Interlocked.Exchange(ref _draining, 1) != 0)
                return;
            long started = Stopwatch.GetTimestamp();
            try
            {
                long lastSequence;
                lock (Gate)
                    lastSequence = _nextSequence;
                for (int count = 0; count < MaxCallbacksPerTick; count++)
                {
                    if (count > 0 && Stopwatch.GetTimestamp() - started >= TickBudget)
                        break;
                    Scheduled scheduled;
                    lock (Gate)
                    {
                        if (_stopped != 0 || Pending.First == null || Pending.First.Value.Sequence > lastSequence)
                            break;
                        scheduled = Pending.First.Value;
                        Pending.RemoveFirst();
                        scheduled.Node = null;
                    }
                    try
                    {
                        if (Volatile.Read(ref _stopped) == 0)
                            scheduled.Invoke();
                        else
                            scheduled.Clear();
                    }
                    catch (Exception exception)
                    {
                        UnityEngine.Debug.LogException(exception);
                    }
                }
            }
            finally
            {
                Volatile.Write(ref _draining, 0);
                // A bounded slice must arrange another native tick even if no new
                // producer arrives. Public update remains the fallback pump.
                bool remaining;
                lock (Gate)
                    remaining = _stopped == 0 && Pending.Count != 0;
                if (remaining)
                    Request();
            }
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
            internal long Sequence;

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
