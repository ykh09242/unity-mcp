using System;
using System.Collections;
using System.Collections.Concurrent;
using System.Diagnostics;
using System.Reflection;
using System.Threading;
using System.Threading.Tasks;
using MCPForUnity.Editor.Services;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEditor;
using UnityEngine.TestTools;

namespace MCPForUnityTests.Editor.Services
{
    public class ToolSettingsRegistrationTests
    {
        [UnityTest]
        public IEnumerator OneFlight_CoalescesLatestIntentAndWaitsForItsOwnReceipt()
        {
            using var probe = new Probe();
            Task first = probe.Request();
            Assert.AreEqual(1, probe.Frames.Count);
            probe.Description = "newest";
            probe.State.NotifyToolSettingsChanged();
            Assert.AreSame(first, probe.Request());
            Assert.AreEqual(1, probe.Frames.Count);
            probe.Receipt(probe.Frames.ToArray()[0]);
            yield return Until(() => probe.Frames.Count == 2);
            JObject latest = probe.Frames.ToArray()[1];
            Assert.AreEqual("newest", (string)latest["tools"][0]["description"]);
            Assert.AreEqual("awaiting_ack", probe.State.Status.Stage);
            Assert.IsFalse(first.IsCompleted);
            probe.Receipt(latest);
            yield return Until(() => first.IsCompleted);
            Assert.AreEqual("applied", probe.State.Status.Stage);
            Assert.AreEqual((long)latest["desired_revision"], probe.State.Status.ConfirmedRevision);
        }

        [UnityTest]
        public IEnumerator Timeout_IsUnconfirmedAndDoesNotAutomaticallyResend()
        {
            using var probe = new Probe(TimeSpan.FromMilliseconds(20));
            Task request = probe.Request();
            yield return Until(() => request.IsCompleted);
            Assert.AreEqual("unconfirmed", probe.State.Status.Stage);
            Assert.AreEqual("ack_timeout", probe.State.Status.Reason);
            Assert.AreEqual(1, probe.Frames.Count);
            Assert.IsNull(probe.State.Status.ConfirmedRevision);
            probe.Receipt(probe.Frames.ToArray()[0]);
            Assert.AreEqual("applied", probe.State.Status.Stage, "A late but still owned receipt can resolve uncertainty.");
        }

        [UnityTest]
        public IEnumerator Dispose_CancelsOwnedWaitAndRejectsLateReceipt()
        {
            var probe = new Probe();
            Task pending = probe.Request();
            JObject frame = probe.Frames.ToArray()[0];
            probe.Dispose();
            yield return Until(() => pending.IsCompleted);
            Assert.AreEqual("disconnected", probe.State.Status.Stage);
            Assert.IsFalse(probe.Receipt(frame));
        }

        [UnityTest]
        public IEnumerator FailedSend_DoesNotAutomaticallyRetryOrConfirm()
        {
            using var probe = new Probe();
            probe.SendFailure = true;
            Task failed = probe.Request();
            yield return Until(() => failed.IsCompleted);
            Assert.AreEqual("send_failed", probe.State.Status.Stage);
            Assert.AreEqual(1, probe.Frames.Count);
            Assert.IsNull(probe.State.Status.ConfirmedRevision);
            probe.SendFailure = false;
            Task retry = probe.Request();
            yield return Until(() => probe.Frames.Count == 2);
            Assert.AreEqual((long)probe.Frames.ToArray()[0]["desired_revision"], (long)probe.Frames.ToArray()[1]["desired_revision"]);
            probe.Receipt(probe.Frames.ToArray()[1]);
            yield return Until(() => retry.IsCompleted);
            Assert.AreEqual("applied", probe.State.Status.Stage);
        }

        [UnityTest]
        public IEnumerator FailedCapture_ReportsBoundedPreparationFailureWithoutSendingOrConfirming()
        {
            using var probe = new Probe();
            probe.CaptureFailure = true;
            Task failed = probe.Request();
            yield return Until(() => failed.IsCompleted);
            Assert.AreEqual("preparation_failed", probe.State.Status.Stage);
            Assert.AreEqual("capture_failed", probe.State.Status.Reason);
            StringAssert.Contains(nameof(InvalidOperationException), probe.State.Status.Message);
            StringAssert.DoesNotContain("owned-sensitive-marker", probe.State.Status.Message);
            Assert.AreEqual(0, probe.Frames.Count);
            Assert.AreEqual(0, probe.State.Status.DesiredRevision);
            Assert.IsNull(probe.State.Status.ConfirmedRevision);
        }

        [UnityTest]
        public IEnumerator FailedCapture_FromObsoleteOwnerDoesNotOverwriteReplacementStatus()
        {
            yield return FailedCaptureAfterContextChange("replacement");
        }

        [UnityTest]
        public IEnumerator FailedCapture_AfterNewDesiredRevisionDoesNotOverwriteCurrentStatus()
        {
            yield return FailedCaptureAfterContextChange("superseded");
        }

        [UnityTest]
        public IEnumerator CancelledCapture_DoesNotOverwriteDisconnectedStatus()
        {
            yield return FailedCaptureAfterContextChange("cancelled");
        }

        [UnityTest]
        public IEnumerator NonLifetimeSendCancellation_FailsClearsWaiterAndAllowsRetry()
        {
            using var probe = new Probe();
            probe.SendCancellation = true;
            Task failed = probe.Request();
            yield return Until(() => failed.IsCompleted);
            Assert.AreEqual("send_failed", probe.State.Status.Stage);
            Assert.IsTrue(probe.State.Status.CanRetry);
            Assert.IsFalse(probe.HasReceiptWaiter);
            Assert.IsNull(probe.State.Status.ConfirmedRevision);
            probe.SendCancellation = false;
            Task retry = probe.State.RetryToolSettingsAsync();
            yield return Until(() => probe.Frames.Count == 2);
            probe.Receipt(probe.Frames.ToArray()[1]);
            yield return Until(() => retry.IsCompleted);
            Assert.AreEqual("applied", probe.State.Status.Stage);
        }

        [UnityTest]
        public IEnumerator LifetimeCancellation_BeforeLateOrdinaryCaptureFaultDoesNotMutateStatus()
        {
            using var lifetime = new CancellationTokenSource();
            using var probe = new Probe(token: lifetime.Token);
            probe.CaptureCompletion = new TaskCompletionSource<ToolSettingsSnapshot>(TaskCreationOptions.RunContinuationsAsynchronously);
            Task request = probe.Request();
            Assert.IsFalse(request.IsCompleted);
            lifetime.Cancel();
            ToolSettingsStatus before = probe.State.Status;
            probe.CaptureCompletion.SetException(new InvalidOperationException("owned-sensitive-marker"));
            yield return Until(() => request.IsCompleted);
            Assert.AreEqual(before.Stage, probe.State.Status.Stage);
            Assert.AreEqual(before.Reason, probe.State.Status.Reason);
            Assert.AreEqual(before.DesiredRevision, probe.State.Status.DesiredRevision);
            Assert.AreEqual(0, probe.Frames.Count);
            Assert.IsNull(probe.State.Status.ConfirmedRevision);
            Assert.IsFalse(probe.HasReceiptWaiter);
        }

        private static IEnumerator FailedCaptureAfterContextChange(string change)
        {
            using var probe = new Probe();
            probe.CaptureCompletion = new TaskCompletionSource<ToolSettingsSnapshot>(TaskCreationOptions.RunContinuationsAsynchronously);
            Task request = probe.Request();
            Assert.IsFalse(request.IsCompleted);
            if (change == "replacement")
                probe.State.SetConnection(new object(), "replacement-session", true, () => Task.CompletedTask);
            else if (change == "superseded")
                probe.State.Capture(ToolSettingsTestDriver.Tools("superseded"), new JObject { ["owned_probe"] = true });
            else
                probe.Dispose();
            ToolSettingsStatus before = probe.State.Status;
            if (change == "cancelled")
                probe.CaptureCompletion.TrySetCanceled();
            else
                probe.CaptureCompletion.TrySetException(new InvalidOperationException("owned-sensitive-marker"));
            yield return Until(() => request.IsCompleted);
            Assert.AreEqual(before.Stage, probe.State.Status.Stage);
            Assert.AreEqual(before.Reason, probe.State.Status.Reason);
            Assert.AreEqual(before.SessionId, probe.State.Status.SessionId);
            Assert.AreEqual(before.DesiredRevision, probe.State.Status.DesiredRevision);
            Assert.IsNull(probe.State.Status.ConfirmedRevision);
            Assert.AreEqual(0, probe.Frames.Count);
        }

        private static IEnumerator Until(Func<bool> condition)
        {
            var elapsed = Stopwatch.StartNew();
            while (!condition() && elapsed.Elapsed < TimeSpan.FromSeconds(5))
            {
                EditorApplication.QueuePlayerLoopUpdate();
                yield return null;
            }
            Assert.IsTrue(condition(), "The owned asynchronous prerequisite did not settle within five seconds.");
        }

        private sealed class Probe : IDisposable
        {
            internal readonly ToolSettingsState State = new();
            internal readonly ConcurrentQueue<JObject> Frames = new();
            internal string Description = "original";
            internal bool SendFailure;
            internal bool SendCancellation;
            internal bool CaptureFailure;
            internal TaskCompletionSource<ToolSettingsSnapshot> CaptureCompletion;
            private readonly object helper;

            internal Probe(TimeSpan? timeout = null, CancellationToken token = default)
            {
                Type type = typeof(ToolSettingsState).Assembly.GetType("MCPForUnity.Editor.Services.ToolSettingsRegistration");
                Assert.IsNotNull(type, "The bounded receipt/coalescing coordinator must exist.");
                Func<CancellationToken, Task<ToolSettingsSnapshot>> capture = _ =>
                {
                    if (CaptureFailure)
                        throw new InvalidOperationException("owned-sensitive-marker");
                    return CaptureCompletion?.Task
                        ?? Task.FromResult(State.Capture(ToolSettingsTestDriver.Tools(Description), new JObject { ["owned_probe"] = true }));
                };
                Func<JObject, CancellationToken, Task> send = (frame, _) =>
                {
                    Frames.Enqueue((JObject)frame.DeepClone());
                    if (SendCancellation)
                        throw new OperationCanceledException(CancellationToken.None);
                    if (SendFailure)
                        throw new InvalidOperationException("owned send failure");
                    return Task.CompletedTask;
                };
                helper = Activator.CreateInstance(type, State, new object(), "owned-session", true, capture, send, token, timeout ?? TimeSpan.FromSeconds(5));
            }

            internal Task Request() => (Task)helper.GetType().GetMethod("RequestAsync").Invoke(helper, null);

            internal bool HasReceiptWaiter => helper.GetType().GetField("_receipt", BindingFlags.Instance | BindingFlags.NonPublic).GetValue(helper) != null;

            internal bool Receipt(JObject frame) =>
                (bool)
                    helper
                        .GetType()
                        .GetMethod("HandleReceipt")
                        .Invoke(
                            helper,
                            new object[]
                            {
                                new JObject
                                {
                                    ["type"] = "tool_settings_result",
                                    ["session_id"] = "owned-session",
                                    ["settings_epoch"] = frame["settings_epoch"],
                                    ["desired_revision"] = frame["desired_revision"],
                                    ["status"] = "applied",
                                    ["catalog_revision"] = frame["desired_revision"],
                                    ["enabled_tool_count"] = 1,
                                },
                            }
                        );

            public void Dispose() => (helper as IDisposable)?.Dispose();
        }
    }
}
