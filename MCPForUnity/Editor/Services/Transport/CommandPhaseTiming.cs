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
            Import,
        }

        internal enum Delivery
        {
            Unobserved,
            Sent,
            Canceled,
            Failed,
        }

        internal const string Capability = "phase_timing_v1";
        private static readonly AsyncLocal<CommandPhaseTiming> CurrentHandler = new();
        private static long _admissions;
        private readonly object _gate = new();
        private readonly Func<long> _clock;
        private readonly long _frequency;
        private readonly Action<JObject> _sink;
        private readonly Transport _transport;
        private readonly long[] _starts = new long[6];
        private readonly long[] _elapsed = new long[6];
        private readonly string _correlation;
        private readonly bool _serverCorrelation;
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

        internal static CommandPhaseTiming CreateForCommand(bool enabled, bool negotiated, JObject payload)
        {
            if (!enabled)
                return null;
            try
            {
                var diagnostic = payload["diagnostics"] as JObject;
                string token = diagnostic?.Count == 1 && diagnostic["correlation"]?.Type == JTokenType.String ? diagnostic.Value<string>("correlation") : null;
                if (negotiated && IsCorrelation(token))
                    return new CommandPhaseTiming(Stopwatch.GetTimestamp, Stopwatch.Frequency, McpLogRecord.LogTiming, Transport.WebSocket, token);
            }
            catch { }
            return Create(enabled, Transport.WebSocket);
        }

        private static bool IsCorrelation(string value)
        {
            if (value == null || value.Length != 32)
                return false;
            foreach (char c in value)
                if (!(c >= '0' && c <= '9') && !(c >= 'a' && c <= 'f'))
                    return false;
            return true;
        }

        internal readonly struct HandlerScope : IDisposable
        {
            private readonly CommandPhaseTiming _previous;
            private readonly bool _changed;

            internal HandlerScope(CommandPhaseTiming timing)
            {
                _previous = CurrentHandler.Value;
                _changed = !ReferenceEquals(_previous, timing);
                if (_changed)
                    CurrentHandler.Value = timing;
            }

            public void Dispose()
            {
                if (_changed)
                    CurrentHandler.Value = _previous;
            }
        }

        internal readonly struct ImportScope : IDisposable
        {
            private readonly CommandPhaseTiming _timing;

            internal ImportScope(CommandPhaseTiming timing)
            {
                _timing = timing;
                timing?.Start(Phase.Import);
            }

            public void Dispose() => _timing?.Stop(Phase.Import);
        }

        internal static HandlerScope EnterHandler(CommandPhaseTiming timing) => new(timing);

        internal static ImportScope MeasureImport() => new(CurrentHandler.Value);

        // Instance-only clock/sink seam; tests do not change global preferences or clocks.
        internal CommandPhaseTiming(Func<long> clock, long frequency, Action<JObject> sink, Transport transport)
            : this(clock, frequency, sink, transport, null) { }

        internal CommandPhaseTiming(Func<long> clock, long frequency, Action<JObject> sink, Transport transport, string correlation)
        {
            _serverCorrelation = IsCorrelation(correlation);
            _correlation = _serverCorrelation ? correlation : Guid.NewGuid().ToString("N");
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
                ["correlation_source"] = _serverCorrelation ? "server" : "native",
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
            string[] names = { "queue", "handler", "prepare", "serialization", "send", "import" };
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
