using System;
using System.Collections.Generic;
using MCPForUnity.Editor.Services;
using Newtonsoft.Json.Linq;
#if LEGACY_EDITOR_STATE
using Observation = Newtonsoft.Json.Linq.JObject;
#else
using Observation = MCPForUnity.Editor.Services.EditorStateFrame;
#endif

namespace UnityEditor
{
    internal static class EditorApplication
    {
        internal static void QueuePlayerLoopUpdate() { }
    }
}

namespace MCPForUnity.Editor.Helpers
{
    internal static class McpLog
    {
        internal static void Warn(string message) { }
    }

    // Unity boundary: synchronous initial observation and idempotent unsubscription.
    internal static class EditorStateCache
    {
        private static readonly object Gate = new();
        private static readonly List<Action<Observation>> Observers = new();
        internal static Action<object> BeforeInitial;
        internal static Action<object> BeforeReturn;
        internal static bool FailSubscribe;
        internal static string Epoch => "test-epoch";
        internal static int Count
        {
            get
            {
                lock (Gate)
                    return Observers.Count;
            }
        }
        internal static int Removed;

        internal static IDisposable Subscribe(Action<Observation> observer)
        {
            if (FailSubscribe)
                throw new InvalidOperationException("subscribe failed");
            lock (Gate)
                Observers.Add(observer);
            try
            {
                BeforeInitial?.Invoke(observer.Target);
                observer(CreateObservation(1));
                BeforeReturn?.Invoke(observer.Target);
            }
            catch
            {
                lock (Gate)
                    Observers.Remove(observer);
                throw;
            }
            return new Subscription(observer);
        }

        internal static void Emit(int sequence)
        {
            Action<Observation>[] observers;
            lock (Gate)
                observers = Observers.ToArray();
            foreach (var observer in observers)
                observer(CreateObservation(sequence));
        }

        private static Observation CreateObservation(int sequence)
        {
#if LEGACY_EDITOR_STATE
            return new JObject { ["sequence"] = sequence, ["observed_at_unix_ms"] = sequence };
#else
            return new EditorStateFrame.Content(Epoch, new JObject { ["sequence"] = sequence }).Observe(sequence);
#endif
        }

        internal static void Reset()
        {
            lock (Gate)
                Observers.Clear();
            BeforeInitial = null;
            BeforeReturn = null;
            FailSubscribe = false;
            Removed = 0;
        }

        private sealed class Subscription : IDisposable
        {
            private Action<Observation> _observer;

            internal Subscription(Action<Observation> observer)
            {
                _observer = observer;
            }

            public void Dispose()
            {
                var observer = System.Threading.Interlocked.Exchange(ref _observer, null);
                if (observer == null)
                    return;
                lock (Gate)
                {
                    if (Observers.Remove(observer))
                        Removed++;
                }
            }
        }
    }
}
