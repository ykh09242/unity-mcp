using MCPForUnity.Editor.Services.Transport;
using Newtonsoft.Json.Linq;
using NUnit.Framework;

namespace MCPForUnityTests.Editor.Services
{
    public class NativeOperationBarriersTests
    {
        [Test]
        public void PendingNativeOwnerBlocksUntilActualReleaseRegardlessOfPublicOutcome()
        {
            var state = new NativeBarrierState { OwnedJob = true };
            Assert.IsTrue(NativeOperationBarriers.Evaluate("manage_scene", null, state, out var busy));
            Assert.AreEqual("native_job_active", busy.Reason);
            Assert.IsTrue(busy.Retryable);
            state.OwnedJob = false;
            Assert.IsFalse(NativeOperationBarriers.Evaluate("manage_scene", null, state, out _));
        }

        [Test]
        public void RecoveryIsNotRetryableAndCannotBeHiddenByActiveCompilation()
        {
            var state = new NativeBarrierState { Compiling = true, RecoveryRequired = true };
            Assert.IsTrue(NativeOperationBarriers.Evaluate("execute_code", null, state, out var blocked));
            Assert.AreEqual("recovery_required", blocked.Reason);
            Assert.IsFalse(blocked.Retryable);
            Assert.AreEqual("inspect_native_work", blocked.RecoveryAction, "A generic legacy blocker does not establish a reason to restart.");
        }

        [Test]
        public void RetainedTestObservationCanRecoverWithoutReleasingAdmission()
        {
            var detail = new NativeRecoveryDetails(
                "tests",
                "native_cleanup",
                "test_native_snapshot_unavailable",
                "recoverable",
                "wait_for_test_cleanup",
                "The retained test observer will reconcile later observations.",
                "test-job"
            );
            var state = new NativeBarrierState { OwnedJob = true, RecoveryDetails = detail };
            Assert.IsTrue(NativeOperationBarriers.Evaluate("manage_scene", null, state, out var blocked));
            Assert.AreEqual("native_job_active", blocked.Reason);
            Assert.IsTrue(blocked.Retryable);
            Assert.AreSame(detail, blocked.Recovery);
            Assert.AreEqual("wait_for_test_cleanup", blocked.RecoveryAction);
        }

        [Test]
        public void LostPackageHandleCannotBeHiddenByRecoverableCompilation()
        {
            var state = new NativeBarrierState
            {
                OwnedCompilation = "native_job_active",
                CompilationRecovery = new NativeRecoveryDetails(
                    "owned_compilation",
                    "started",
                    "compilation_running",
                    "recoverable",
                    "wait_for_pipeline_finish",
                    "Waiting for matching finish."
                ),
                PackageQueryRecovery = true,
            };
            Assert.IsTrue(NativeOperationBarriers.Evaluate("manage_scene", null, state, out var ordinary));
            Assert.AreEqual("native_job_active", ordinary.Reason);
            Assert.AreEqual("owned_compilation", ordinary.Recovery.Source);
            Assert.IsTrue(NativeOperationBarriers.Evaluate("manage_packages", new JObject { ["action"] = "list_packages" }, state, out var package));
            Assert.AreEqual("recovery_required", package.Reason);
            Assert.IsFalse(package.Retryable);
            Assert.AreEqual("package_manager", package.Recovery.Source);
            Assert.AreEqual("restart_required", package.Recovery.Mode);
            Assert.That(package.Recovery.Message, Does.Contain("Request handle"));
        }

        [Test]
        public void UnknownOwnerCannotHideAnotherOwnersKnownLostProof()
        {
            var unknown = new NativeRecoveryDetails(
                "tests",
                "native_cleanup",
                "test_callback_owner_unavailable",
                "unknown",
                "inspect_native_work",
                "Inspect tests."
            );
            var lost = new NativeRecoveryDetails(
                "build",
                "owner_lost",
                "build_completion_observer_lost",
                "restart_required",
                "inspect_native_work_then_restart_editor",
                "Build observer lost."
            );
            Assert.AreSame(lost, NativeOperationBarriers.PreferRecovery(unknown, lost));
            Assert.AreSame(lost, NativeOperationBarriers.PreferRecovery(lost, unknown));
        }

        [Test]
        public void ReadOnlyUpmRequestOnlyFencesOtherClientStartsAndArbitraryCode()
        {
            var state = new NativeBarrierState { PackageQuery = true };
            Assert.IsFalse(NativeOperationBarriers.Evaluate("manage_scene", new JObject { ["action"] = "get_hierarchy" }, state, out _));
            Assert.IsFalse(NativeOperationBarriers.Evaluate("manage_packages", new JObject { ["action"] = "get_package_info" }, state, out _));
            foreach (
                string action in new[]
                {
                    "add_package",
                    "remove_package",
                    "embed_package",
                    "list_packages",
                    "search_packages",
                    "resolve_packages",
                    "add_registry",
                    "remove_registry",
                }
            )
                Assert.IsTrue(NativeOperationBarriers.Evaluate("manage_packages", new JObject { ["action"] = action }, state, out _), action);
            Assert.IsTrue(NativeOperationBarriers.Evaluate("execute_code", null, state, out _));
            Assert.IsTrue(NativeOperationBarriers.Evaluate(null, null, state, out _), "Untrusted/custom handlers may call Client.");
        }

        [Test]
        public void MutatingUpmRequestIsGlobalAndLostQueryIsOnlyCandidateRecovery()
        {
            Assert.IsTrue(NativeOperationBarriers.Evaluate("manage_scene", null, new NativeBarrierState { PackageMutation = true }, out _));
            var lost = new NativeBarrierState { PackageQueryRecovery = true };
            Assert.IsFalse(NativeOperationBarriers.Evaluate("manage_scene", null, lost, out _));
            Assert.IsTrue(NativeOperationBarriers.Evaluate("manage_packages", new JObject { ["action"] = "list_packages" }, lost, out var blocked));
            Assert.AreEqual("recovery_required", blocked.Reason);
            Assert.IsFalse(blocked.Retryable);
        }

        [Test]
        public void StablePlayAndNetworkOnlyWorkHaveNoBlanketBarrier()
        {
            Assert.IsFalse(NativeOperationBarriers.Evaluate("manage_scene", null, new NativeBarrierState(), out _));
            Assert.IsTrue(NativeOperationBarriers.Evaluate("manage_scene", null, new NativeBarrierState { Transitioning = true }, out _));
        }

        [TestCase(true, false, "editor_compiling")]
        [TestCase(false, true, "editor_importing")]
        public void ActualPipelineFlagsAreTransient(bool compiling, bool importing, string reason)
        {
            Assert.IsTrue(
                NativeOperationBarriers.Evaluate("manage_scene", null, new NativeBarrierState { Compiling = compiling, Importing = importing }, out var blocked)
            );
            Assert.AreEqual(reason, blocked.Reason);
            Assert.IsTrue(blocked.Retryable);
        }
    }
}
