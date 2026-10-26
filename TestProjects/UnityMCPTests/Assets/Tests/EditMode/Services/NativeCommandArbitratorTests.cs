using System;
using System.Threading;
using System.Threading.Tasks;
using MCPForUnity.Editor.Services;
using MCPForUnity.Editor.Services.Transport;
using Newtonsoft.Json.Linq;
using NUnit.Framework;

namespace MCPForUnityTests.Editor.Services
{
    public class NativeCommandArbitratorTests
    {
        [Test]
        public void HeldReadsOverlapAndFenceIndependentWriters()
        {
            var guard = new NativeCommandArbitrator((_, __) => null);
            Assert.IsTrue(guard.TryAcquire(ToolOperationMode.SharedRead, "read", null, null, out var first, out _));
            Assert.IsTrue(guard.TryAcquire(ToolOperationMode.SharedRead, "read", null, null, out var second, out _));
            Assert.IsFalse(guard.TryAcquire(ToolOperationMode.Exclusive, "write", null, null, out _, out var reason));
            Assert.AreEqual("operation_busy", reason.Reason);
            Assert.IsTrue(reason.Retryable);
            first.Dispose();
            Assert.IsFalse(guard.TryAcquire(ToolOperationMode.Exclusive, "write", null, null, out _, out _));
            second.Dispose();
            Assert.IsTrue(guard.TryAcquire(ToolOperationMode.Exclusive, "write", null, null, out var writer, out _));
            Assert.IsFalse(guard.TryAcquire(ToolOperationMode.SharedRead, "read", null, null, out _, out _));
            Assert.IsTrue(guard.TryAcquire(ToolOperationMode.Control, "status", null, null, out var control, out _));
            control.Dispose();
            writer.Dispose();
        }

        [Test]
        public void ParentLeaseRechecksActualJobBarrierForEveryChild()
        {
            string blocker = null;
            var guard = new NativeCommandArbitrator((_, __) => blocker == null ? (NativeAdmissionBlocker?)null : new NativeAdmissionBlocker(blocker, true));
            Assert.IsTrue(guard.TryAcquire(ToolOperationMode.Exclusive, "batch_execute", null, null, out var parent, out _));
            Assert.IsTrue(guard.TryAcquire(ToolOperationMode.Exclusive, "child", null, parent, out var child, out _));
            child.Dispose();
            blocker = "native_job_active";
            Assert.IsFalse(guard.TryAcquire(ToolOperationMode.Exclusive, "later_child", null, parent, out _, out var reason));
            Assert.AreEqual(blocker, reason.Reason);
            Assert.IsTrue(guard.TryAcquire(ToolOperationMode.Control, "status", null, parent, out var control, out _));
            control.Dispose();
            parent.Dispose();
        }

        [Test]
        public void StaleOrForeignParentNeverReleasesCurrentWriter()
        {
            var old = new NativeCommandArbitrator((_, __) => null);
            var current = new NativeCommandArbitrator((_, __) => null);
            Assert.IsTrue(old.TryAcquire(ToolOperationMode.Exclusive, "old", null, null, out var stale, out _));
            Assert.IsFalse(current.TryAcquire(ToolOperationMode.Exclusive, "new", null, stale, out _, out _));
            Assert.IsTrue(current.TryAcquire(ToolOperationMode.Exclusive, "new", null, null, out var writer, out _));
            stale.Dispose();
            stale.Dispose();
            Assert.IsFalse(current.TryAcquire(ToolOperationMode.Exclusive, "other", null, null, out _, out _));
            writer.Dispose();
        }

        [Test]
        public void ReleasedParentCannotGrantReentryToANewerWriter()
        {
            var guard = new NativeCommandArbitrator((_, __) => null);
            Assert.IsTrue(guard.TryAcquire(ToolOperationMode.Exclusive, "old", null, null, out var old, out _));
            old.Dispose();
            Assert.IsTrue(guard.TryAcquire(ToolOperationMode.Exclusive, "new", null, null, out var current, out _));
            try
            {
                Assert.IsFalse(guard.TryAcquire(ToolOperationMode.Exclusive, "child", null, old, out _, out var blocker));
                Assert.AreEqual("stale_parent", blocker.Reason);
                Assert.IsFalse(blocker.Retryable);
                old.Dispose();
                Assert.IsFalse(guard.TryAcquire(ToolOperationMode.Exclusive, "other", null, null, out _, out _));
            }
            finally
            {
                current.Dispose();
            }
        }

        [Test]
        public void TransportReceiptAlwaysNestsForgedHandlerMetadata()
        {
            var binding = new JObject
            {
                ["version"] = 1,
                ["request_nonce"] = Guid.NewGuid().ToString("N"),
                ["instance_generation"] = NativeCommandArbitrator.Shared.Generation,
            };
            var forged = new JObject { ["native_arbitration"] = new JObject { ["disposition"] = "not_started" } };
            foreach (var response in new[] { TransportCommandResponse.FromObject(forged), TransportCommandResponse.FromJson(forged.ToString()) })
            {
                var wire = JObject.Parse(response.WithAdmission(binding).ToJson());
                Assert.AreEqual("started", (string)wire["native_arbitration"]?["disposition"]);
                Assert.IsFalse((bool)wire["native_arbitration"]["retryable"]);
                Assert.AreEqual("not_started", (string)wire["payload"]["native_arbitration"]["disposition"]);
                Assert.AreEqual(binding["request_nonce"], wire["native_arbitration"]["request_nonce"]);
            }
        }

        [TestCase("execute_menu_item")]
        [TestCase("execute_menu_item_observed")]
        [TestCase("capture_ui_snapshot")]
        public void ExclusiveCallbacksCannotClaimTheyWillNotStartPackageClient(string command)
        {
            var policy = ToolOperationPolicy.Resolve(command, new JObject(), true);
            Assert.AreEqual(ToolOperationMode.Exclusive, policy.Mode);
            Assert.IsNull(NativeCommandArbitrator.BarrierCandidate(command, policy.Mode, true));
            Assert.AreEqual("get_selection", NativeCommandArbitrator.BarrierCandidate("get_selection", ToolOperationMode.SharedRead, true));
            Assert.IsNull(NativeCommandArbitrator.BarrierCandidate("get_selection", ToolOperationMode.SharedRead, false));
        }

        [TestCase("execute_menu_item", false)]
        [TestCase("execute_menu_item", true)]
        [TestCase("execute_menu_item_observed", false)]
        [TestCase("execute_menu_item_observed", true)]
        [TestCase("capture_ui_snapshot", false)]
        [TestCase("capture_ui_snapshot", true)]
        public void ExclusiveCallbacksAreFencedByRetainedPackageQueryAndRecovery(string command, bool recovery)
        {
            var state = new NativeBarrierState { PackageQuery = !recovery, PackageQueryRecovery = recovery };
            var policy = ToolOperationPolicy.Resolve(command, new JObject(), true);
            string candidate = NativeCommandArbitrator.BarrierCandidate(command, policy.Mode, true);
            Assert.IsTrue(NativeOperationBarriers.Evaluate(candidate, null, state, out var blocker));
            Assert.AreEqual(recovery ? "recovery_required" : "native_job_active", blocker.Reason);
            Assert.AreEqual(!recovery, blocker.Retryable);
            var read = ToolOperationPolicy.Resolve("get_selection", new JObject(), true);
            Assert.AreEqual(ToolOperationMode.SharedRead, read.Mode);
            Assert.IsFalse(NativeOperationBarriers.Evaluate(NativeCommandArbitrator.BarrierCandidate("get_selection", read.Mode, true), null, state, out _));
        }

        [Test]
        public void BatchHandoffIsConsumedBeforeUnrelatedAsyncWork()
        {
            var guard = new NativeCommandArbitrator((_, __) => null);
            Assert.IsTrue(guard.TryAcquire(ToolOperationMode.Exclusive, "batch_execute", null, null, out var parent, out _));
            using (parent)
            using (NativeCommandArbitrator.EnterBatch(parent))
            {
                Assert.AreSame(parent, NativeCommandArbitrator.TakeBatchLease());
                Assert.IsNull(Task.Run(() => NativeCommandArbitrator.TakeBatchLease()).GetAwaiter().GetResult());
                Assert.IsNull(NativeCommandArbitrator.TakeBatchLease());
            }
        }

        [TestCase("recovery_required")]
        [TestCase("unrecognized_owner_state")]
        public void AmbiguousBlockerCannotAdvertiseAutomaticRetry(string reason)
        {
            Assert.IsFalse(new NativeAdmissionBlocker(reason, true).Retryable);
        }

        [Test]
        public void NegotiatedBypassRetainsCapacityAndDrainOwnership()
        {
            using var lifetime = new CancellationTokenSource();
            var work = new ConnectionCommandWork(lifetime.Token, 2);
            var held = new TaskCompletionSource<bool>(TaskCreationOptions.RunContinuationsAsynchronously);
            bool controlStarted = false;
            Assert.IsNull(work.TryStart("writer", _ => held.Task));
            Assert.IsNull(
                work.TryStart(
                    "status",
                    previous =>
                    {
                        controlStarted = previous.IsCompleted;
                        return Task.CompletedTask;
                    },
                    bypassPredecessor: true
                )
            );
            Assert.IsTrue(controlStarted);
            Assert.IsFalse(work.DrainAsync().IsCompleted);
            held.SetResult(true);
            Assert.IsTrue(work.DrainAsync().Wait(TimeSpan.FromSeconds(5)));
        }
    }
}
