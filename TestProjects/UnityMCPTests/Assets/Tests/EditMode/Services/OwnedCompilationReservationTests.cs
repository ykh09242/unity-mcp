using System;
using MCPForUnity.Editor.Services;
using NUnit.Framework;

namespace MCPForUnityTests.Editor.Services
{
    public class OwnedCompilationReservationTests
    {
        [Test]
        public void DelayedStartIsOwnedUntilMatchingFinishEvenWithNoAssemblies()
        {
            var owner = new OwnedCompilationReservation();
            string generation = owner.Reserve("compile-one", 100);
            Assert.AreEqual("native_job_active", owner.Blocker(10));
            object context = new object();
            owner.Started(context);
            Assert.AreEqual("native_job_active", owner.Blocker(20));
            owner.Finished(new object());
            Assert.AreEqual("native_job_active", owner.Blocker(20));
            owner.Finished(context);
            Quiesce(owner);
            Assert.IsNull(owner.Blocker(200));
            Assert.IsFalse(owner.AbortBeforeStart(generation));
        }

        [Test]
        public void StartAfterCallerDeadlineStillPairsWithItsMatchingFinish()
        {
            var owner = new OwnedCompilationReservation();
            owner.Reserve("compile-one", 100);
            owner.Blocker(100);
            object context = new object();
            owner.Started(context);
            owner.Finished(new object());
            Quiesce(owner);
            Assert.IsNotNull(owner.Blocker(101), "An unrelated finish cannot release the late pipeline start.");
            owner.Finished(context);
            owner.ObserveQuiescence(true);
            Assert.IsNotNull(owner.Blocker(101), "The finish callback turn cannot release native ownership.");
            owner.ObserveQuiescence(true);
            Assert.IsNull(owner.Blocker(101));
            Assert.IsNull(owner.Recovery(101));
        }

        [Test]
        public void CallerDeadlineDoesNotPreventGenerationCheckedAbortBeforeInvocation()
        {
            var owner = new OwnedCompilationReservation();
            string generation = owner.Reserve("compile-one", 100);
            owner.Blocker(100);
            Assert.IsFalse(owner.AbortBeforeStart(Guid.NewGuid().ToString("N")));
            Assert.IsTrue(owner.AbortBeforeStart(generation));
            Assert.IsNull(owner.Blocker(101));
        }

        [Test]
        public void MissingStartRetainsOwnershipAfterCallerDeadlineWithoutRequiringRestart()
        {
            var owner = new OwnedCompilationReservation();
            string generation = owner.Reserve("compile-one", 100);
            Assert.AreEqual("native_job_active", owner.Blocker(100));
            Quiesce(owner);
            Assert.AreEqual("native_job_active", owner.Blocker(100000));
            var recovery = owner.Recovery(100000);
            Assert.AreEqual("owned_compilation", recovery.Source);
            Assert.AreEqual("pending", recovery.Phase);
            Assert.AreEqual("compilation_start_wait_expired", recovery.Reason);
            Assert.AreEqual("recoverable", recovery.Mode);
            Assert.AreEqual("wait_for_compilation_start", recovery.Action);
            Assert.AreEqual("compile-one", recovery.JobId);
            Assert.AreEqual(generation, recovery.Generation);
            Assert.Throws<InvalidOperationException>(() => owner.Reserve("compile-two", 200000));
        }

        [Test]
        public void PreStartFailureAndStaleGenerationCannotReleaseNewReservation()
        {
            var owner = new OwnedCompilationReservation();
            string first = owner.Reserve("compile-one", 100);
            Assert.IsTrue(owner.AbortBeforeStart(first));
            string second = owner.Reserve("compile-two", 100);
            Assert.IsFalse(owner.AbortBeforeStart(first));
            object context = new object();
            owner.Started(context);
            Assert.IsFalse(owner.AbortBeforeStart(second));
            Assert.AreEqual("native_job_active", owner.Blocker(10));
            owner.Finished(context);
            Quiesce(owner);
            Assert.IsNull(owner.Blocker(10));
        }

        [Test]
        public void StalePipelineFinishCannotReleaseTheNextGeneration()
        {
            var owner = new OwnedCompilationReservation();
            owner.Reserve("compile-one", 100);
            object oldContext = new object();
            owner.Started(oldContext);
            owner.Finished(oldContext);
            Quiesce(owner);
            owner.Reserve("compile-two", 100);
            object currentContext = new object();
            owner.Started(currentContext);
            owner.Finished(oldContext);
            Assert.AreEqual("native_job_active", owner.Blocker(1));
            owner.Finished(currentContext);
            Quiesce(owner);
            Assert.IsNull(owner.Blocker(1));
        }

        [Test]
        public void FailedPersistenceBeforeInvocationDoesNotCreateNativeOwnership()
        {
            var owner = new OwnedCompilationReservation { Changed = () => throw new InvalidOperationException("controlled") };
            Assert.Throws<InvalidOperationException>(() => owner.Reserve("compile-one", 100));
            Assert.IsNull(owner.Blocker(1));
        }

        [Test]
        public void ReloadCannotRestoreLostContextAsACompletedOperation()
        {
            var owner = new OwnedCompilationReservation();
            owner.Reserve("compile-one", 100);
            var restored = new OwnedCompilationReservation(owner.State);
            Assert.AreEqual("recovery_required", restored.Blocker(1));
            var recovery = restored.Recovery(1);
            Assert.AreEqual("compilation_context_lost_on_reload", recovery.Reason);
            Assert.AreEqual("restart_required", recovery.Mode);
            Assert.AreEqual("inspect_native_work_then_restart_editor", recovery.Action);
            object unrelatedContext = new object();
            restored.Started(unrelatedContext);
            restored.Finished(unrelatedContext);
            Quiesce(restored);
            Assert.AreEqual("recovery_required", restored.Blocker(1));
        }

        [Test]
        public void RunningCompilationRemainsRecoverableUntilItsLateMatchingFinishArrives()
        {
            var owner = new OwnedCompilationReservation();
            owner.Reserve("compile-one", 100);
            object context = new object();
            owner.Started(context);
            Assert.AreEqual("native_job_active", owner.Blocker(100));
            var recovery = owner.Recovery(100);
            Assert.AreEqual("started", recovery.Phase);
            Assert.AreEqual("compilation_finish_wait_expired", recovery.Reason);
            Assert.AreEqual("recoverable", recovery.Mode);
            Assert.AreEqual("wait_for_pipeline_finish", recovery.Action);
            owner.Finished(context);
            Quiesce(owner);
            Assert.IsNull(owner.Blocker(101));
        }

        [Test]
        public void SuccessfulFinishBeforeReloadRetainsWitnessUntilNativeQuiescence()
        {
            var owner = new OwnedCompilationReservation();
            owner.Reserve("compile-one", 100);
            object context = new object();
            owner.Started(context);
            owner.Finished(context);
            var restored = new OwnedCompilationReservation(owner.State);
            Assert.AreEqual("native_job_active", restored.Blocker(101));
            var recovery = restored.Recovery(101);
            Assert.AreEqual("finished", recovery.Phase);
            Assert.AreEqual("compilation_finished_awaiting_quiescence", recovery.Reason);
            Assert.AreEqual("recoverable", recovery.Mode);
            Assert.AreEqual("wait_for_editor_quiescence", recovery.Action);
            restored.ObserveQuiescence(false);
            Assert.AreEqual("native_job_active", restored.Blocker(1));
            restored.ObserveQuiescence(true);
            Assert.IsNull(restored.Blocker(1));
        }

        [Test]
        public void LegacyFinishedCheckpointRetainsCompletionProofDespiteItsExpiredRecoveryPhase()
        {
            var restored = new OwnedCompilationReservation(
                new OwnedCompilationReservation.Checkpoint
                {
                    JobId = "compile-one",
                    Generation = Guid.NewGuid().ToString("N"),
                    Phase = "recovery_required",
                    Deadline = 100,
                    PipelineFinished = true,
                }
            );
            Assert.AreEqual("native_job_active", restored.Blocker(101));
            Assert.AreEqual("recoverable", restored.Recovery(101).Mode);
            Quiesce(restored);
            Assert.IsNull(restored.Blocker(101));
        }

        [Test]
        public void InvalidCheckpointExplainsWhyOwnershipCannotBeRecovered()
        {
            var restored = new OwnedCompilationReservation(
                new OwnedCompilationReservation.Checkpoint
                {
                    JobId = "compile-one",
                    Generation = Guid.NewGuid().ToString("N"),
                    Phase = "unsupported",
                    PipelineFinished = true,
                }
            );
            var recovery = restored.Recovery(1);
            Assert.AreEqual("recovery_required", restored.Blocker(1));
            Assert.AreEqual("invalid_compilation_checkpoint", recovery.Reason);
            Assert.AreEqual("restart_required", recovery.Mode);
            Assert.IsNull(recovery.JobId);
            Assert.IsNull(recovery.Generation);
            Quiesce(restored);
            Assert.AreEqual("recovery_required", restored.Blocker(1));
        }

        [Test]
        public void MissingPipelineContextExplainsWhyAnUnpairedFinishCannotRecover()
        {
            var owner = new OwnedCompilationReservation();
            owner.Reserve("compile-one", 100);
            owner.Started(null);
            var recovery = owner.Recovery(1);
            Assert.AreEqual("compilation_context_unavailable", recovery.Reason);
            Assert.AreEqual("restart_required", recovery.Mode);
            Assert.AreEqual("inspect_native_work_then_restart_editor", recovery.Action);
            owner.Finished(null);
            Quiesce(owner);
            Assert.AreEqual("recovery_required", owner.Blocker(1));
            var restored = new OwnedCompilationReservation(owner.State);
            Assert.AreEqual(
                "compilation_context_unavailable",
                restored.Recovery(1).Reason,
                "Reload must preserve the known reason for missing finish evidence."
            );
            Assert.AreEqual("recovery_required", restored.Blocker(1));
        }

        [Test]
        public void ReloadedRunningCompilationCannotAdoptAnUnrelatedNewPipelineContext()
        {
            var owner = new OwnedCompilationReservation();
            owner.Reserve("compile-one", 100);
            owner.Started(new object());
            var restored = new OwnedCompilationReservation(owner.State);
            object unrelatedContext = new object();
            restored.Started(unrelatedContext);
            restored.Finished(unrelatedContext);
            Quiesce(restored);
            Assert.AreEqual("recovery_required", restored.Blocker(101));
            Assert.AreEqual("compilation_context_lost_on_reload", restored.Recovery(101).Reason);
        }

        [Test]
        public void DeadlineDiagnosticsDoNotMutateOrPersistTheNativeLifecycle()
        {
            int saves = 0;
            var owner = new OwnedCompilationReservation { Changed = () => saves++ };
            owner.Reserve("compile-one", 100);
            Assert.AreEqual("compilation_start_pending", owner.Recovery(99).Reason);
            Assert.AreEqual("native_job_active", owner.Blocker(100));
            Assert.AreEqual("compilation_start_wait_expired", owner.Recovery(100).Reason);
            Assert.AreEqual("pending", owner.State.Phase);
            Assert.AreEqual(1, saves, "A status read must not rewrite native ownership because the caller budget expired.");
        }

        [Test]
        public void RefreshFinishCannotReleaseBeforeTheOwnedTriggerDecision()
        {
            var owner = new OwnedCompilationReservation();
            string generation = owner.Reserve("compile-one", 100, refreshing: true);
            object context = new object();
            owner.Started(context);
            owner.Finished(context);
            Quiesce(owner);
            Assert.AreEqual("native_job_active", owner.Blocker(1));
            Assert.IsTrue(owner.CompleteRefreshDecision(generation));
            owner.ObserveQuiescence(true);
            Assert.AreEqual("native_job_active", owner.Blocker(1), "Pipeline Finished alone cannot admit inside its callback turn.");
            owner.ObserveQuiescence(false);
            Assert.AreEqual("native_job_active", owner.Blocker(1), "Raw compile/import/reload activity still owns resources.");
            owner.ObserveQuiescence(true);
            Assert.IsNull(owner.Blocker(1));
        }

        [Test]
        public void ZeroEventRefreshFallsThroughToPendingExplicitRequest()
        {
            var owner = new OwnedCompilationReservation();
            string generation = owner.Reserve("compile-one", 100, refreshing: true);
            Assert.IsFalse(owner.CompleteRefreshDecision(generation));
            Quiesce(owner);
            Assert.AreEqual("native_job_active", owner.Blocker(1));
            Assert.IsTrue(owner.AbortBeforeStart(generation));
        }

        private static void Quiesce(OwnedCompilationReservation owner)
        {
            owner.ObserveQuiescence(true);
            owner.ObserveQuiescence(true);
        }
    }
}
