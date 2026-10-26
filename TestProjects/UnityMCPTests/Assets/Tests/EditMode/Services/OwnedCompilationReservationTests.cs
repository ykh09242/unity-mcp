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
            owner.Reserve("compile-two", 100);
            object currentContext = new object();
            owner.Started(currentContext);
            owner.Finished(oldContext);
            Assert.AreEqual("native_job_active", owner.Blocker(1));
            owner.Finished(currentContext);
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
            Assert.IsNull(owner.Blocker(101));
        }

        [Test]
        public void SuccessfulFinishBeforeReloadLeavesNoPendingReservation()
        {
            var owner = new OwnedCompilationReservation();
            owner.Reserve("compile-one", 100);
            object context = new object();
            owner.Started(context);
            owner.Finished(context);
            Assert.IsNull(new OwnedCompilationReservation(owner.State).Blocker(1));
        }
    }
}
