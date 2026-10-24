using System;
using System.Threading;

namespace MCPForUnity.Editor.Setup
{
    /// <summary>Cancellation requests retain ownership until the service acknowledges completion.</summary>
    internal sealed class SkillInstallerOperation : IDisposable
    {
        private volatile CancellationTokenSource _current;
        public bool IsRunning => _current != null;
        public bool CancellationRequested => _current?.IsCancellationRequested == true;
        public string Phase { get; private set; }

        public CancellationTokenSource Begin(string phase)
        {
            if (IsRunning)
                return null;
            Phase = phase;
            return _current = new CancellationTokenSource();
        }

        public bool Owns(CancellationTokenSource attempt) => attempt != null && ReferenceEquals(_current, attempt);

        public void RequestCancellation() => _current?.Cancel();

        public bool Complete(CancellationTokenSource attempt)
        {
            if (!Owns(attempt))
                return false;
            _current = null;
            attempt.Dispose();
            return true;
        }

        public void Dispose()
        {
            var attempt = _current;
            _current = null;
            attempt?.Cancel();
            attempt?.Dispose();
        }
    }
}
