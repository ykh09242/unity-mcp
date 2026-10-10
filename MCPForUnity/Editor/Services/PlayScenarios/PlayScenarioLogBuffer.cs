using System;
using System.Collections.Generic;

namespace MCPForUnity.Editor.Services.PlayScenarios
{
    /// <summary>Bounded threaded log tail with sticky unexpected-error state independent of rotation.</summary>
    public sealed class PlayScenarioLogBuffer
    {
        public const int Capacity = 50;
        private readonly object _gate = new object();
        private readonly Queue<PlayScenarioLog> _pending = new Queue<PlayScenarioLog>();
        private readonly bool _strict;
        private readonly HashSet<string> _allowed;
        private int _dropped;
        private int _unexpected;
        private string _firstUnexpected;
        private string _lastUnexpected;
        private bool _closed;

        public PlayScenarioLogBuffer(PlayScenarioLogPolicy policy = null)
        {
            policy = policy ?? new PlayScenarioLogPolicy();
            _strict = policy.Mode == "strict";
            _allowed = new HashSet<string>(policy.AllowedMessages, StringComparer.Ordinal);
        }

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
                // Match the complete callback message before truncation, even when its tail entry is dropped.
                if (_strict && (type == "Error" || type == "Assert" || type == "Exception") && !_allowed.Contains(message))
                {
                    if (_unexpected < int.MaxValue)
                        _unexpected++;
                    _lastUnexpected = PlayScenarioEngine.Bounded(type + ": " + message, 4096);
                    if (_firstUnexpected == null)
                        _firstUnexpected = _lastUnexpected;
                }
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
            if (run == null)
                throw new ArgumentNullException(nameof(run));
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
                run.DroppedLogCount = (int)Math.Min(int.MaxValue, dropped);
                run.UnexpectedLogCount = (int)Math.Min(int.MaxValue, (long)run.UnexpectedLogCount + _unexpected);
                if (run.UnexpectedLogError == null)
                    run.UnexpectedLogError = _firstUnexpected;
                if (_unexpected > 0)
                    run.LastUnexpectedLogError = _lastUnexpected;
                _dropped = 0;
                _unexpected = 0;
            }
        }

        public void Close()
        {
            lock (_gate)
                _closed = true;
        }
    }
}
