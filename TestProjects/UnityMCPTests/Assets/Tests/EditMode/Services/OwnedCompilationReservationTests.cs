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
        public void MissingStartBecomesRecoveryRequiredAndIdleDoesNotReleaseIt()
        {
            var owner = new OwnedCompilationReservation();
            owner.Reserve("compile-one", 100);
            Assert.AreEqual("recovery_required", owner.Blocker(100));
            Assert.AreEqual("recovery_required", owner.Blocker(100000));
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
            restored.Finished(new object());
            Assert.AreEqual("recovery_required", restored.Blocker(1));
        }

        [Test]
        public void MissingFinishIsRecoveryUntilTheActualMatchingFinishArrives()
        {
            var owner = new OwnedCompilationReservation();
            owner.Reserve("compile-one", 100);
            object context = new object();
            owner.Started(context);
            Assert.AreEqual("recovery_required", owner.Blocker(100));
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
            Assert.AreEqual("native_job_active", restored.Blocker(1));
            restored.ObserveQuiescence(false);
            Assert.AreEqual("native_job_active", restored.Blocker(1));
            restored.ObserveQuiescence(true);
            Assert.IsNull(restored.Blocker(1));
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
