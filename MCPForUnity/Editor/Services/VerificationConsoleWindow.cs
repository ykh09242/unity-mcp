using System;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Editor.Tools;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEngine;

namespace MCPForUnity.Editor.Services
{
    // The checkpoint is armed on the Editor thread before run_tests is sent. Only
    // log callbacks access the findings off-thread. A domain reload destroys the proof.
    internal sealed class VerificationConsoleWindow
    {
        internal const int FindingLimit = 12;
        internal const int MessageLimit = 256;
        internal static readonly string Generation = Guid.NewGuid().ToString("N");
        private static VerificationConsoleWindow active;

        internal readonly string Id = Guid.NewGuid().ToString("N");
        private readonly object gate = new();
        private readonly JArray findings = new();
        private readonly Func<double> clock;
        private double expires;
        private string jobId;
        private string end;
        private int errors;
        private bool messageTruncated;

        internal VerificationConsoleWindow(Func<double> clock)
        {
            this.clock = clock;
            expires = clock() + 30;
            // ReadConsole initializes its Editor reflection in its type initializer.
            System.Runtime.CompilerServices.RuntimeHelpers.RunClassConstructor(typeof(ReadConsole).TypeHandle);
            Application.logMessageReceivedThreaded += Record;
            EditorApplication.update += Expire;
            AssemblyReloadEvents.beforeAssemblyReload += OnReload;
        }

        internal static object Begin()
        {
            active?.Expire();
            if (active != null || TestJobManager.HasRunningJob || TestJobManager.NativeAdmissionBlocked)
                return ConsoleTailCursor.Failure("verification_console_busy");
            active = new VerificationConsoleWindow(() => EditorApplication.timeSinceStartup);
            return new SuccessResponse("Verification console observation armed.", new { window_id = active.Id, generation = Generation });
        }

        internal static VerificationConsoleWindow Claim(string windowId, string owner, long timeoutMs)
        {
            var window = active;
            if (window == null)
                return null;
            // Even an ordinary run invalidates an unclaimed interval. It can no
            // longer be attributed to a later job.
            return window.Bind(windowId, owner, timeoutMs) ? window : null;
        }

        internal bool Bind(string windowId, string owner, long timeoutMs)
        {
            Expire();
            if (end != null || jobId != null)
                return false;
            if (windowId != Id || string.IsNullOrEmpty(owner) || timeoutMs <= 0)
            {
                Stop("unclaimed");
                return false;
            }
            jobId = owner;
            expires = clock() + Math.Min(timeoutMs, 600000) / 1000.0 + 30;
            return true;
        }

        internal static void Discard(string windowId)
        {
            if (active != null && active.Id == windowId)
                active.Stop("discarded");
        }

        internal void Expire()
        {
            if (clock() >= expires)
                Stop("expired");
        }

        private void OnReload() => Stop("generation_changed");

        internal void Stop(string reason)
        {
            lock (gate)
            {
                if (end != null)
                    return;
                end = clock() >= expires ? "expired" : reason;
            }
            Application.logMessageReceivedThreaded -= Record;
            EditorApplication.update -= Expire;
            AssemblyReloadEvents.beforeAssemblyReload -= OnReload;
            if (active == this)
                active = null;
        }

        internal void Record(string message, string stackTrace, LogType type)
        {
            if (type != LogType.Error && type != LogType.Assert && type != LogType.Exception)
                return;
            lock (gate)
            {
                if (end != null)
                    return;
                message ??= string.Empty;
                int length = Math.Min(message.Length, MessageLimit);
                if (length < message.Length && char.IsHighSurrogate(message[length - 1]))
                    length--;
                string preview = message.Substring(0, length);
                if (ReadConsole.IsMcpLogMessage(preview))
                    return;
                if (errors < int.MaxValue)
                    errors++;
                if (findings.Count < FindingLimit)
                {
                    string bounded = TestJobResultStore.BoundedText(preview, MessageLimit, out int bytes);
                    findings.Add(new JObject { ["type"] = type.ToString(), ["message"] = bounded });
                    messageTruncated |= length < message.Length || bytes > MessageLimit;
                }
            }
        }

        internal object Read(string windowId, string owner)
        {
            if (Id != windowId || jobId == null || jobId != owner)
                return ConsoleTailCursor.Failure("verification_console_owner_changed");
            Expire();
            lock (gate)
                return new SuccessResponse(
                    "Job-scoped application log observation.",
                    new
                    {
                        window_id = Id,
                        generation = Generation,
                        job_id = jobId,
                        end = end ?? "running",
                        observed_error_count = errors,
                        findings = findings.DeepClone(),
                        messages_truncated = messageTruncated,
                    }
                );
        }
    }
}
