using System.Collections.Generic;

namespace MCPForUnity.Editor.Services.PlayScenarios
{
    /// <summary>A bounded handoff from Unity's threaded log callback to the editor thread.</summary>
    public sealed class PlayScenarioLogBuffer
    {
        public const int Capacity = 50;
        private readonly object _gate = new object();
        private readonly Queue<PlayScenarioLog> _pending = new Queue<PlayScenarioLog>();
        private int _dropped;
        private bool _closed;

        public void Add(long timestamp, string type, string message, string stackTrace)
        {
            var entry = new PlayScenarioLog
            {
                TimestampUnixMs = timestamp,
                Type = type,
                Message = PlayScenarioEngine.Bounded(message, 1024),
                StackTrace = PlayScenarioEngine.Bounded(stackTrace, 2048),
            };
            lock (_gate)
            {
                if (_closed)
                    return;
                if (_pending.Count == Capacity)
                {
                    _pending.Dequeue();
                    if (_dropped < int.MaxValue)
                        _dropped++;
                }
                _pending.Enqueue(entry);
            }
        }

        public void DrainTo(PlayScenarioRun run)
        {
            lock (_gate)
            {
                long dropped = (long)run.DroppedLogCount + _dropped;
                while (_pending.Count > 0)
                {
                    if (run.Logs.Count == Capacity)
                    {
                        run.Logs.RemoveAt(0);
                        dropped++;
                    }
                    run.Logs.Add(_pending.Dequeue());
                }
                run.DroppedLogCount = (int)System.Math.Min(int.MaxValue, dropped);
                _dropped = 0;
            }
        }

        public void Close()
        {
            lock (_gate)
                _closed = true;
        }
    }
}
