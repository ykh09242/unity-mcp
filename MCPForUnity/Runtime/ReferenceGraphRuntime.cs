using System;
using System.Threading;
using UnityEngine;

namespace MCPForUnity.Runtime
{
    /// <summary>Explicit wrapper hooks. No subscriptions, resource ownership or automatic interception.</summary>
    public static class ReferenceGraphRuntime
    {
        private static readonly object Gate = new object();
        private static Subscription[] observers = Array.Empty<Subscription>();
        public static bool Enabled => Volatile.Read(ref observers).Length != 0;

        public static void Load(string scheme, string key, UnityEngine.Object asset = null, string ownerPath = "")
        {
            if (Enabled)
                Publish(new ReferenceGraphRuntimeEvent("load", scheme, key, "", asset, ownerPath));
        }

        /// <summary>Call after a successful acquisition; retain this scalar token until the actual release.</summary>
        public static string Acquire(string scheme, string key, UnityEngine.Object asset = null, string ownerPath = "")
        {
            if (!Enabled)
                return null;
            string token = Guid.NewGuid().ToString("N");
            Publish(new ReferenceGraphRuntimeEvent("acquire", scheme, key, token, asset, ownerPath));
            return token;
        }

        /// <summary>Call after the game releases its resource. This method does not release the resource.</summary>
        public static void Release(string token)
        {
            if (Enabled && !string.IsNullOrEmpty(token))
                Publish(new ReferenceGraphRuntimeEvent("release", "", "", token, null, ""));
        }

        internal static IDisposable Observe(Action<ReferenceGraphRuntimeEvent> action)
        {
            if (action == null)
                throw new ArgumentNullException(nameof(action));
            var subscription = new Subscription(action);
            lock (Gate)
            {
                var next = new Subscription[observers.Length + 1];
                Array.Copy(observers, next, observers.Length);
                next[next.Length - 1] = subscription;
                Volatile.Write(ref observers, next);
            }
            return subscription;
        }

        private static void Publish(ReferenceGraphRuntimeEvent observation)
        {
            // Snapshot arrays change only when consumers subscribe/unsubscribe.
            foreach (Subscription subscriber in Volatile.Read(ref observers))
                subscriber.Send(observation);
        }

        private sealed class Subscription : IDisposable
        {
            private Action<ReferenceGraphRuntimeEvent> callback;

            internal Subscription(Action<ReferenceGraphRuntimeEvent> action) => callback = action;

            internal void Send(ReferenceGraphRuntimeEvent observation)
            {
                try
                {
                    Volatile.Read(ref callback)?.Invoke(observation);
                }
                catch (Exception)
                {
                    // Observers own their diagnostics; a bad observer cannot break gameplay.
                    Dispose();
                }
            }

            public void Dispose()
            {
                if (Interlocked.Exchange(ref callback, null) == null)
                    return;
                lock (Gate)
                {
                    int index = Array.IndexOf(observers, this);
                    if (index < 0)
                        return;
                    var next = new Subscription[observers.Length - 1];
                    Array.Copy(observers, 0, next, 0, index);
                    Array.Copy(observers, index + 1, next, index, next.Length - index);
                    Volatile.Write(ref observers, next);
                }
            }
        }
    }

    internal readonly struct ReferenceGraphRuntimeEvent
    {
        internal readonly string Kind,
            Scheme,
            Key,
            Handle,
            OwnerPath;
        internal readonly UnityEngine.Object Asset;

        internal ReferenceGraphRuntimeEvent(string kind, string scheme, string key, string handle, UnityEngine.Object asset, string ownerPath)
        {
            Kind = kind;
            Scheme = scheme ?? "";
            Key = key ?? "";
            Handle = handle;
            Asset = asset;
            OwnerPath = ownerPath ?? "";
        }
    }
}
