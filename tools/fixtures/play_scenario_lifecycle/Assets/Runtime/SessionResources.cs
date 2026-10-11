using System;
using System.IO;
using MCPForUnity.Runtime;
using UnityEngine;

namespace UnityMcpLifecycleSample
{
    public sealed class SessionResources : IDisposable
    {
        public string OwnerId { get; } = Guid.NewGuid().ToString("N");
        public SessionConfig Clone { get; private set; }
        public MemoryStream Stream { get; private set; }
        public int EventsReceived { get; private set; }
        private IDisposable _subscription;
        private IDisposable _handle;
        private bool _disposed;

        public SessionResources(SessionConfig source)
        {
            if (source == null)
                throw new ArgumentNullException(nameof(source));
            try
            {
                Clone = UnityEngine.Object.Instantiate(source);
                Clone.name = "Session clone " + OwnerId;
                Clone.hideFlags = HideFlags.DontUnloadUnusedAsset;
                string owner = "lifecycle-session:" + OwnerId;
                PlayScenarioResourceTracker.RegisterScriptableObject(Clone, owner: owner);
                SampleSignals.Pulse += OnPulse;
                _subscription = PlayScenarioResourceTracker.RegisterSubscription(owner: owner);
                Stream = new MemoryStream(new byte[64], writable: true);
                Stream.WriteByte(1);
                _handle = PlayScenarioResourceTracker.RegisterHandle(owner: owner);
            }
            catch
            {
                Dispose();
                throw;
            }
        }

        private void OnPulse() => EventsReceived++;

        public void Dispose()
        {
            if (_disposed)
                return;
            _disposed = true;
            SampleSignals.Pulse -= OnPulse;
            _subscription?.Dispose();
            _subscription = null;
            Stream?.Dispose();
            _handle?.Dispose();
            _handle = null;
            if (Clone != null)
            {
                if (Application.isPlaying)
                    UnityEngine.Object.Destroy(Clone);
                else
                    UnityEngine.Object.DestroyImmediate(Clone);
                Clone = null;
            }
        }
    }

    public static class SampleSignals
    {
        public static event Action Pulse;
        public static int ListenerCount => Pulse?.GetInvocationList().Length ?? 0;

        public static void Publish() => Pulse?.Invoke();
    }
}
