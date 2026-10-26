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
