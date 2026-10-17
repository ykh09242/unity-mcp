using System;
using System.Diagnostics;
using System.Threading;
using MCPForUnity.Editor.Helpers;
using Newtonsoft.Json.Linq;

namespace MCPForUnity.Editor.Services.Transport
{
    // Local opt-in diagnostics only. Never retain a command, request ID, payload or exception.
    internal sealed class CommandPhaseTiming
    {
        internal enum Transport
        {
            Direct,
            WebSocket,
            Stdio,
        }

        internal enum Phase
        {
            Queue,
            Handler,
            Prepare,
            Serialization,
            Send,
        }

        internal enum Delivery
        {
            Unobserved,
            Sent,
            Canceled,
            Failed,
        }

        private static long _admissions;
        private readonly object _gate = new();
        private readonly Func<long> _clock;
        private readonly long _frequency;
        private readonly Action<JObject> _sink;
        private readonly Transport _transport;
        private readonly long[] _starts = new long[5];
        private readonly long[] _elapsed = new long[5];
        private readonly string _correlation = Guid.NewGuid().ToString("N");
        private long _admitted;
        private long _deliveryAt;
        private int _active;
        private int _measured;
        private bool _failed;
        private bool _executionDone;
        private bool _deliveryDone;
        private bool _lateExecution;
        private bool _emitted;
        private Delivery _delivery;

        internal static bool ShouldSample(long admission) => (admission & 63) == 1;

        internal static CommandPhaseTiming Create(bool enabled, Transport transport)
        {
            if (!enabled || !ShouldSample(Interlocked.Increment(ref _admissions)))
                return null;
            try
            {
                return new CommandPhaseTiming(Stopwatch.GetTimestamp, Stopwatch.Frequency, McpLogRecord.LogTiming, transport);
            }
            catch
            {
                return null;
            }
        }

        // Instance-only clock/sink seam; tests do not change global preferences or clocks.
        internal CommandPhaseTiming(Func<long> clock, long frequency, Action<JObject> sink, Transport transport)
        {
            _clock = clock;
            _frequency = frequency;
            _sink = sink;
            _transport = transport;
            Observe(() =>
            {
                if (frequency <= 0)
                    throw new InvalidOperationException();
                _admitted = clock();
                _starts[(int)Phase.Queue] = _admitted;
                _active = 1 << (int)Phase.Queue;
            });
        }

        internal void Start(Phase phase) =>
            Observe(() =>
            {
                int bit = 1 << (int)phase;
                if ((_active & bit) != 0)
                {
                    // Queue is carried across existing admission/dispatcher owners.
                    // Other overlapping observations are ambiguous: omit the diagnostic.
                    if (phase != Phase.Queue)
                        _failed = true;
                    return;
                }
                _starts[(int)phase] = _clock();
                _active |= bit;
            });

        internal void Stop(Phase phase) =>
            Observe(() =>
            {
                int bit = 1 << (int)phase;
                if ((_active & bit) == 0)
                    return;
                long elapsed = _clock() - _starts[(int)phase];
                if (elapsed < 0)
                    throw new InvalidOperationException();
                _elapsed[(int)phase] = checked(_elapsed[(int)phase] + elapsed);
                _active &= ~bit;
                _measured |= bit;
            });

        internal void ExecutionDone() =>
            Observe(() =>
            {
                if (_executionDone)
                    return;
                _lateExecution = _deliveryDone;
                _executionDone = true;
                EmitIfComplete();
            });

        internal void DeliveryDone(Delivery delivery) =>
            Observe(() =>
            {
                if (_deliveryDone)
                    return;
                _deliveryAt = _clock();
                if (_deliveryAt < _admitted)
                    throw new InvalidOperationException();
                _delivery = delivery;
                _deliveryDone = true;
                EmitIfComplete();
            });

        private void Observe(Action observation)
        {
            lock (_gate)
            {
                if (_failed || _emitted)
                    return;
                try
                {
                    observation();
                }
                catch
                {
                    _failed = true;
                }
            }
        }

        private void EmitIfComplete()
        {
            if (!_executionDone || !_deliveryDone || _emitted)
                return;
            _emitted = true;
            var row = new JObject
            {
                ["event"] = "phase_timing",
                ["sample_every"] = 64,
                ["correlation"] = _correlation,
                ["transport"] =
                    _transport == Transport.WebSocket ? "websocket"
                    : _transport == Transport.Stdio ? "stdio"
                    : "direct",
                ["delivery"] =
                    _delivery == Delivery.Sent ? "sent"
                    : _delivery == Delivery.Canceled ? "canceled"
                    : _delivery == Delivery.Failed ? "failed"
                    : "unobserved",
                ["execution_settled"] = true,
                ["execution_settled_after_delivery"] = _lateExecution,
                ["total_ms"] = Milliseconds(_deliveryAt - _admitted),
            };
            var incomplete = new JArray();
            string[] names = { "queue", "handler", "prepare", "serialization", "send" };
            for (int i = 0; i < names.Length; i++)
            {
                if ((_active & (1 << i)) != 0)
                    incomplete.Add(names[i]);
                else if ((_measured & (1 << i)) != 0)
                    row[names[i] + "_ms"] = Milliseconds(_elapsed[i]);
            }
            row["incomplete_stages"] = incomplete;
            _sink(row);
        }

        private double Milliseconds(long ticks) => ticks * (1000d / _frequency);
    }
}
