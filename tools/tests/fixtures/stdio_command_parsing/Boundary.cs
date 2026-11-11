using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Threading;
using System.Threading.Tasks;
using MCPForUnity.Editor.Models;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;

namespace UnityEditor
{
    internal static class EditorApplication
    {
        internal static double timeSinceStartup => 1;
    }
}

namespace MCPForUnity.Editor.Helpers
{
    public sealed class ErrorResponse
    {
        public ErrorResponse(string message) { }
    }
}

namespace MCPForUnity.Editor.Helpers
{
    internal static class McpLog
    {
        internal static void Error(string message) { }

        internal static void Warn(string message) { }
    }
}

namespace MCPForUnity.Editor.Services.Transport
{
    internal enum TransportMode
    {
        Stdio,
    }

    internal sealed class ResponseLimitException : Exception { }

    internal static class BoundedResponseJson
    {
        internal static string Serialize(object value) => JsonConvert.SerializeObject(value);

        internal static JToken Freeze(object value) => JToken.FromObject(value);

        internal static JToken Parse(string value) => TransportJson.ParseToken(value);
    }

    internal readonly struct NativeAdmissionBlocker
    {
        internal NativeAdmissionBlocker(string reason, bool retryable)
        {
            Reason = reason;
            Retryable = retryable;
        }

        internal readonly string Reason;
        internal readonly bool Retryable;
        internal string RecoveryAction => null;
        internal object Recovery => null;
    }

    internal sealed class NativeCommandArbitrator
    {
        internal const string Capability = "native_command_arbitration_v1";
        internal static readonly NativeCommandArbitrator Shared = new();
        internal string Generation => "fixture-generation";
    }

    internal static class CommandResumeState
    {
        internal static string SessionId => "fixture-session";
    }

    internal sealed class CommandResumeJournal
    {
        internal sealed class Ticket
        {
            internal CommandResumeJournal Owner;
        }

        internal int Releases;
        internal int Bindings;

        internal static bool IsIdentity(string value) => value?.Length == 32;

        internal void Release(Ticket ticket) => Releases++;

        internal void BindCancellation(Ticket ticket, Action cancel) => Bindings++;
    }

    internal sealed class CommandProgressBuffer
    {
        internal const int MaxFrameBytes = 4096;

        internal void Close() { }
    }

    internal sealed class CommandPhaseTiming
    {
        internal enum Phase
        {
            Serialization,
        }

        internal void Start(Phase phase) { }

        internal void Stop(Phase phase) { }

        internal void ExecutionDone() { }
    }

    internal static class EditorMainThreadWakeup
    {
        internal static void Request() { }
    }

    internal static class TransportCommandDispatcher
    {
        internal static Command Captured;
        internal static int Calls;

        internal static TransportCommandOperation ExecuteTimedCommandAsync(
            Command command,
            CancellationToken token,
            TransportMode mode,
            CommandPhaseTiming timing,
            CommandProgressBuffer progress,
            CancellationToken admission,
            CancellationToken owner,
            Func<Task> complete = null
        )
        {
            Captured = command;
            Calls++;
            return new TransportCommandOperation(Task.FromResult(TransportCommandResponse.FromObject(new { status = "success" })), Task.CompletedTask, null);
        }
    }
}

namespace MCPForUnity.Editor.Services.Transport.Transports
{
    internal sealed class StdioAuthenticatedSession
    {
        internal readonly HashSet<string> Capabilities = new();
    }

    internal static partial class StdioBridgeHost
    {
        private static readonly object lockObj = new();
        private static readonly Dictionary<string, QueuedCommand> commandQueue = new();
        private static readonly LinkedList<string> commandOrder = new();
        private static readonly List<string> canceledCommandIds = new();
        private static readonly Stopwatch _uptime = Stopwatch.StartNew();
        private static readonly StdioFrameBudget fixtureInputBudget = new();
        private const int FrameIOTimeoutMs = 300000;
        private static bool isRunning = true;
        private static int processingCommands;
        private static double nextHeartbeatAt = double.MaxValue;

        private static void WriteHeartbeat(bool reloading) { }

        internal static QueuedCommand Prepare(
            string text,
            out Command initial,
            out string prelude,
            out string progressId,
            out bool business,
            bool arbitration = false
        )
        {
            var reservation = fixtureInputBudget.AcquireAsync(System.Text.Encoding.UTF8.GetByteCount(text), CancellationToken.None).Result;
#if PREPARED
            prelude = ReadProgressPrelude(text, out progressId, out var envelope).ToString();
            business = false;
            if (!TryPrepareCommandInput(text, envelope, reservation, out var input))
            {
                reservation.Dispose();
                throw new InvalidOperationException("Fixture input exceeds retained-input capacity");
            }
            initial = input.Command;
            int timeout = ResolveCommandTimeoutMs(envelope, FrameIOTimeoutMs, arbitration, out string nonce);
#else
            prelude = ReadProgressPrelude(text, out progressId).ToString();
            business = false;
            initial = null;
            try
            {
                initial = TransportJson.DeserializeCommand(text);
            }
            catch (JsonException) { }
            int timeout = ResolveCommandTimeoutMs(text, FrameIOTimeoutMs, arbitration, out string nonce);
#endif
            return new QueuedCommand
            {
                CommandJson = text,
                InputReservation = reservation,
                Tcs = new TaskCompletionSource<string>(),
                TimeoutMs = timeout,
                RequestNonce = nonce,
                Session = arbitration ? new StdioAuthenticatedSession() : null,
#if PREPARED
                Input = input,
#endif
            };
        }

        internal static string Run(QueuedCommand queued)
        {
            const string id = "fixture";
            commandQueue.Add(id, queued);
            queued.QueueNode = commandOrder.AddLast(id);
            ProcessCommands();
            return queued.Tcs.Task.GetAwaiter().GetResult();
        }

        internal static void Release(QueuedCommand queued) => ReleaseInput(queued);

        internal static bool Business(string text)
        {
#if PREPARED
            return IsProgressBusiness(text, out _);
#else
            return IsProgressBusiness(text);
#endif
        }
    }
}
