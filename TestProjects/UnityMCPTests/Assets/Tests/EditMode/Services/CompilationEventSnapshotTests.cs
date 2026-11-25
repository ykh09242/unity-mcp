using System;
using System.Reflection;
using MCPForUnity.Editor.Services;
using MCPForUnity.Editor.Services.SourceFreshness;
using NUnit.Framework;

namespace MCPForUnity.Editor.Tests.EditMode.Services
{
    public class CompilationEventSnapshotTests
    {
        [TestCase(false)]
        [TestCase(true)]
        public void FailedReservationWritesStillPublishCompilationAndInvalidateSourceEvidence(bool throwOnWrite)
        {
            const string session = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";
            const string id = "11111111111111111111111111111111";
            var ledger = new CompilationLedger(session, "C:/fixture", _ => { });
            var sources = new SourceFreshnessLedger("project", session);
            string saved = "";
            bool writable = true;
            var owner = CompilationJobTracker.RestoreReservation(
                () => saved,
                value =>
                {
                    if (writable)
                        saved = value;
                    else if (throwOnWrite)
                        throw new InvalidOperationException("controlled checkpoint failure");
                },
                ledger.InterruptedBeforeDispatch
            );
            string generation = owner.Reserve(id, 100);
            ledger.Begin(id);
            ledger.ExplicitRequest(id);
            owner.BeginNative(generation);
            owner.NativeReturned(generation);
            ledger.Dispatched(id);

            writable = false;
            var context = new object();
            EditorStateCache.RecordCompilationStart(context, owner, ledger, () => sources.Invalidate("compilation_started"));
            Assert.AreEqual("running", (string)ledger.Query(id)["state"]);
            Assert.AreEqual("compilation_started", sources.Observation.Reason);
            EditorStateCache.RecordCompilationFinish(context, owner, ledger);
            Assert.AreEqual("finished", (string)ledger.Query(id)["state"]);
            Assert.AreEqual("no_assembly_compilation", (string)ledger.Query(id)["reason"]);
            owner.RetryCheckpointBeforeReload();
            var stale = CompilationJobTracker.RestoreReservation(() => saved, _ => { }, ledger.InterruptedBeforeDispatch);
            stale.ObserveQuiescence(true);
            stale.ObserveQuiescence(true);
            Assert.AreEqual("recovery_required", stale.Blocker(1), "The last durable handoff cannot certify the missing finish.");

            if (throwOnWrite)
            {
                owner.ObserveQuiescence(true);
                owner.ObserveQuiescence(true);
                Assert.IsNull(owner.State, "The exact native completion and later quiescence remain observable in this domain.");
            }
            writable = true;
            owner.RetryCheckpointBeforeReload();
            var recovered = CompilationJobTracker.RestoreReservation(() => saved, _ => { }, ledger.InterruptedBeforeDispatch);
            Assert.AreEqual(throwOnWrite ? null : "finished", recovered.State?.Phase, "Persist the last observed state when storage recovers.");
        }

        [TestCase(true)]
        [TestCase(false)]
        public void EventTrackedStateDoesNotReenterNativeCompilationQuery(bool compiling)
        {
            var method = ReadMethod();
            int nativeReads = 0;
            Func<bool> readNative = () =>
            {
                nativeReads++;
                throw new InvalidOperationException("event snapshot must not reenter native compilation enumeration");
            };
            Assert.That((bool)method.Invoke(null, new object[] { compiling, readNative }), Is.EqualTo(compiling));
            Assert.That(nativeReads, Is.Zero);
        }

        [TestCase(true)]
        [TestCase(false)]
        public void OrdinarySnapshotRetainsNativeCompilationQuery(bool nativeState)
        {
            var method = ReadMethod();
            int nativeReads = 0;
            Func<bool> readNative = () =>
            {
                nativeReads++;
                return nativeState;
            };
            Assert.That((bool)method.Invoke(null, new object[] { null, readNative }), Is.EqualTo(nativeState));
            Assert.That(nativeReads, Is.EqualTo(1));
        }

        private static MethodInfo ReadMethod()
        {
            // Inspect the pure nested selector without initializing the outer Editor cache.
            var selector = typeof(EditorStateCache).GetNestedType("CompilationObservation", BindingFlags.NonPublic);
            Assert.That(selector, Is.Not.Null, "compilation-event snapshots still query native compilation state");
            var method = selector.GetMethod("Read", BindingFlags.NonPublic | BindingFlags.Static);
            Assert.That(method, Is.Not.Null);
            return method;
        }
    }
}
