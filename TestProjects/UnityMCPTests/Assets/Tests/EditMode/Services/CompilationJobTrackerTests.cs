using System;
using System.Linq;
using MCPForUnity.Editor.Services;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEditor.Compilation;

namespace MCPForUnityTests.Editor.Services
{
    public class CompilationJobTrackerTests
    {
        [Test]
        public void OverflowDiagnosticBurstPreservesBoundedPersistedOutcome()
        {
            var ledger = Ledger();
            ledger.Begin(Id);
            ledger.ExplicitRequest(Id);
            ledger.Dispatched(Id);
            var context = new object();
            ledger.Started(context);
            var messages = Enumerable.Repeat(Message(false, new string('x', 1000)), 1000).ToArray();
            ledger.Assembly("Library/ScriptAssemblies/A.dll", messages);
            ledger.Assembly("B.dll", new[] { Message(true, "Final failure") });
            ledger.Finished(context);
            var result = Ledger(_saved).Query(Id);
            Assert.AreEqual("failed", (string)result["outcome"]);
            Assert.AreEqual(1000, (int)result["warning_count"]);
            Assert.AreEqual(1, (int)result["error_count"]);
            Assert.IsTrue((bool)result["diagnostics_truncated"]);
            Assert.LessOrEqual(System.Text.Encoding.UTF8.GetByteCount(result.ToString()), 65536);
        }

        private const string Session = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";
        private const string Id = "11111111111111111111111111111111";
        private const string Other = "22222222222222222222222222222222";
        private string _saved;
        private long _time;

        private CompilationLedger Ledger(string restored = null) => new(Session, "C:/fixture", value => _saved = value, restored, () => ++_time);

        private static CompilerMessage Message(bool error, string message = "Fixture compiler message", string file = "Assets/Fixture.cs") =>
            new()
            {
                type = error ? CompilerMessageType.Error : CompilerMessageType.Warning,
                message = message,
                file = file,
                line = 7,
                column = 9,
            };

        private static void Cycle(CompilationLedger ledger, object context, bool error)
        {
            ledger.Started(context);
            ledger.Assembly("Library/ScriptAssemblies/A.dll", new[] { Message(error) });
            ledger.Finished(context);
        }

        [TestCase(false, "success")]
        [TestCase(true, "failed")]
        public void ExplicitCycleReturnsCompilerOutcomeRegardlessOfReadiness(bool error, string outcome)
        {
            // Given a request armed for explicit compilation.
            var ledger = Ledger();
            ledger.Begin(Id);
            ledger.ExplicitRequest(Id);
            ledger.Dispatched(Id);
            // When an attributed assembly cycle finishes.
            Cycle(ledger, new object(), error);
            // Then diagnostics and outcome belong to that request.
            var result = ledger.Query(Id);
            Assert.AreEqual(outcome, (string)result["outcome"]);
            Assert.AreEqual("observed_after_request", (string)result["attribution"]);
            Assert.AreEqual(1, (int)result["generation"]);
            Assert.AreEqual(7, (int)result["diagnostics"][0]["line"]);
        }

        [Test]
        public void RefreshCycleCannotFinalizeBeforeExplicitSecondCycleFails()
        {
            // Given compilation can complete synchronously inside Refresh.
            var ledger = Ledger();
            ledger.Begin(Id);
            Cycle(ledger, new object(), false);
            Assert.AreEqual("unknown", (string)ledger.Query(Id)["outcome"]);
            ledger.ExplicitRequest(Id);
            ledger.Dispatched(Id);
            // When the later explicit compilation fails.
            Cycle(ledger, new object(), true);
            // Then neither successful G1 nor failed G2 is misattributed as R's certain result.
            var result = ledger.Query(Id);
            Assert.AreEqual("unknown", (string)result["outcome"]);
            Assert.AreEqual("multiple_candidate_cycles", (string)result["reason"]);
            CollectionAssert.AreEqual(new long[] { 1, 2 }, result["candidate_generations"].Values<long>().ToArray());
            Assert.AreEqual("failed", (string)ledger.Summary()["outcome"]);
        }

        [Test]
        public void SynchronousExplicitCycleWaitsForDispatchBoundary()
        {
            // Given an explicit request method can fire events synchronously.
            var ledger = Ledger();
            ledger.Begin(Id);
            ledger.ExplicitRequest(Id);
            // When events finish before the request method returns.
            Cycle(ledger, new object(), false);
            Assert.AreEqual("unknown", (string)ledger.Query(Id)["outcome"]);
            ledger.Dispatched(Id);
            // Then only the persisted dispatch boundary permits success.
            Assert.AreEqual("success", (string)ledger.Query(Id)["outcome"]);
        }

        [Test]
        public void RefreshOnlyCycleRemainsUnknownWithoutExplicitStart()
        {
            var ledger = Ledger();
            ledger.Begin(Id);
            Cycle(ledger, new object(), false);
            ledger.ExplicitRequest(Id);
            ledger.Dispatched(Id);
            Assert.AreEqual("unknown", (string)ledger.Query(Id)["outcome"]);
            Assert.AreEqual("refresh_phase_cycle", (string)ledger.Query(Id)["reason"]);
        }

        [Test]
        public void PreexistingCycleCannotSatisfyNewRequest()
        {
            var ledger = Ledger();
            var context = new object();
            ledger.Started(context);
            ledger.Begin(Id);
            ledger.ExplicitRequest(Id);
            ledger.Dispatched(Id);
            ledger.Assembly("A.dll", new[] { Message(false) });
            ledger.Finished(context);
            Assert.AreEqual("unknown", (string)ledger.Query(Id)["outcome"]);
            Assert.AreEqual("preexisting_compile", (string)ledger.Query(Id)["reason"]);
        }

        [Test]
        public void CoalescedPendingRequestsAreBothUnknown()
        {
            var ledger = Ledger();
            ledger.Begin(Id);
            ledger.ExplicitRequest(Id);
            ledger.Dispatched(Id);
            ledger.Begin(Other);
            ledger.ExplicitRequest(Other);
            ledger.Dispatched(Other);
            Cycle(ledger, new object(), false);
            Assert.AreEqual("unknown", (string)ledger.Query(Id)["outcome"]);
            Assert.AreEqual("unknown", (string)ledger.Query(Other)["outcome"]);
        }

        [Test]
        public void OverlappingContextsCannotCertifyAssemblyOwnership()
        {
            var ledger = Ledger();
            ledger.Begin(Id);
            ledger.ExplicitRequest(Id);
            ledger.Dispatched(Id);
            var first = new object();
            var second = new object();
            ledger.Started(first);
            ledger.Started(second);
            ledger.Assembly("A.dll", new[] { Message(false) });
            ledger.Finished(first);
            ledger.Finished(second);
            Assert.AreEqual("unknown", (string)ledger.Query(Id)["outcome"]);
        }

        [Test]
        public void MultiAssemblyErrorsAggregateAndTruncationNeverHidesFailures()
        {
            var ledger = Ledger();
            ledger.Begin(Id);
            ledger.ExplicitRequest(Id);
            ledger.Dispatched(Id);
            var context = new object();
            ledger.Started(context);
            ledger.Assembly("A.dll", Enumerable.Range(0, 110).Select(_ => Message(false, new string('x', 1024))).ToArray());
            ledger.Assembly("B.dll", new[] { Message(true, "Last assembly error") });
            ledger.Finished(context);
            var result = ledger.Query(Id);
            Assert.AreEqual("failed", (string)result["outcome"]);
            Assert.AreEqual(1, (int)result["error_count"]);
            Assert.AreEqual(110, (int)result["warning_count"]);
            Assert.AreEqual(2, (int)result["assembly_count"]);
            Assert.IsTrue((bool)result["diagnostics_truncated"]);
            Assert.LessOrEqual(((JArray)result["diagnostics"]).Count, 100);
            Assert.LessOrEqual(System.Text.Encoding.UTF8.GetByteCount(result.ToString()), 65536);
        }

        [TestCase("X:/sdk/foo.dll")]
        [TestCase("X:\\sdk\\folder with spaces\\foo.dll")]
        [TestCase("\\\\server\\share\\sdk.dll")]
        [TestCase("\\\\?\\C:\\sdk\\foo.dll")]
        [TestCase("/opt/sdk/foo.dll")]
        public void ExternalAbsolutePathsAreOmittedFromDiagnosticText(string path)
        {
            var ledger = Ledger();
            ledger.Begin(Id);
            ledger.ExplicitRequest(Id);
            ledger.Dispatched(Id);
            var context = new object();
            ledger.Started(context);
            ledger.Assembly(path, new[] { Message(true, "CS0006 metadata file '" + path + "' not found", path) });
            ledger.Finished(context);
            string json = ledger.Query(Id).ToString();
            StringAssert.DoesNotContain(path, json);
            Assert.IsTrue((bool)ledger.Query(Id)["diagnostics"][0]["diagnostic_text_omitted"]);
            Assert.AreEqual("failed", (string)ledger.Query(Id)["outcome"]);
        }

        [Test]
        public void TerminalResultsSurviveReloadAndLaterRequestIndependently()
        {
            var ledger = Ledger();
            ledger.Begin(Id);
            ledger.ExplicitRequest(Id);
            ledger.Dispatched(Id);
            Cycle(ledger, new object(), true);
            var restored = Ledger(_saved);
            Assert.AreEqual("failed", (string)restored.Query(Id)["outcome"]);
            restored.Begin(Other);
            restored.ExplicitRequest(Other);
            restored.Dispatched(Other);
            Cycle(restored, new object(), false);
            Assert.AreEqual("failed", (string)restored.Query(Id)["outcome"]);
            Assert.AreEqual("success", (string)restored.Query(Other)["outcome"]);
            Assert.AreEqual(2, (int)restored.Query(Other)["generation"]);
        }

        [Test]
        public void NonterminalRestorationIsUnknownNotSuccess()
        {
            var ledger = Ledger();
            ledger.Begin(Id);
            ledger.ExplicitRequest(Id);
            ledger.Dispatched(Id);
            ledger.Started(new object());
            var restored = Ledger(_saved);
            Assert.AreEqual("unknown", (string)restored.Query(Id)["outcome"]);
            Assert.AreEqual("interrupted", (string)restored.Query(Id)["reason"]);
        }

        [TestCase("not json")]
        [TestCase("{}")]
        [TestCase("{\"schema\":\"1\"}")]
        public void CorruptHistoryReturnsUnavailableWithoutThrowing(string history)
        {
            Assert.AreEqual("result_unavailable", (string)Ledger(history).Query(Id)["reason"]);
        }

        [Test]
        public void NoAssemblyAndMismatchedFinishCannotCertifySuccess()
        {
            var ledger = Ledger();
            ledger.Begin(Id);
            ledger.ExplicitRequest(Id);
            ledger.Dispatched(Id);
            var context = new object();
            ledger.Started(context);
            ledger.Finished(new object());
            ledger.Finished(context);
            Assert.AreEqual("unknown", (string)ledger.Query(Id)["outcome"]);
        }

        [Test]
        public void DuplicateRequestCannotScheduleAgainAndHistoryIsBounded()
        {
            var ledger = Ledger();
            Assert.IsTrue(ledger.Begin(Id));
            Assert.IsFalse(ledger.Begin(Id));
            ledger.Reject(Id);
            for (int i = 0; i < 60; i++)
            {
                string id = Guid.NewGuid().ToString("N");
                ledger.Begin(id);
                ledger.ExplicitRequest(id);
                ledger.Dispatched(id);
                Cycle(ledger, new object(), false);
            }
            var persisted = JObject.Parse(_saved);
            Assert.LessOrEqual(((JArray)persisted["requests"]).Count + ((JArray)persisted["runs"]).Count, 16);
            Assert.AreEqual("result_unavailable", (string)ledger.Query(Id)["reason"]);
        }

        [TestCase("Errors", "\"1\"")]
        [TestCase("Warnings", "-1")]
        [TestCase("Generation", "0")]
        [TestCase("Complete", "\"true\"")]
        public void UntrustedPersistedRunFieldsNeverRestoreSuccess(string field, string json)
        {
            var ledger = Ledger();
            ledger.Begin(Id);
            ledger.ExplicitRequest(Id);
            ledger.Dispatched(Id);
            Cycle(ledger, new object(), false);
            var history = JObject.Parse(_saved);
            history["runs"][0][field] = JToken.Parse(json);
            var restored = Ledger(history.ToString());
            Assert.AreEqual("unknown", (string)restored.Query(Id)["outcome"]);
            Assert.AreEqual("result_unavailable", (string)restored.Query(Id)["reason"]);
        }

        [Test]
        public void RefreshRunCrossingExplicitBoundaryRemainsAmbiguous()
        {
            var ledger = Ledger();
            ledger.Begin(Id);
            var context = new object();
            ledger.Started(context);
            ledger.ExplicitRequest(Id);
            ledger.Dispatched(Id);
            ledger.Assembly("A.dll", new[] { Message(false) });
            ledger.Finished(context);
            Assert.AreEqual("unknown", (string)ledger.Query(Id)["outcome"]);
        }

        [Test]
        public void NoAssemblyCachedRunCannotCertifySuccess()
        {
            var ledger = Ledger();
            ledger.Begin(Id);
            ledger.ExplicitRequest(Id);
            ledger.Dispatched(Id);
            var context = new object();
            ledger.Started(context);
            ledger.Finished(context);
            Assert.AreEqual("unknown", (string)ledger.Query(Id)["outcome"]);
            Assert.AreEqual("incomplete_events", (string)ledger.Query(Id)["reason"]);
        }

        [Test]
        public void ContradictoryPersistedSeverityCountsCannotCertifySuccess()
        {
            var ledger = Ledger();
            ledger.Begin(Id);
            ledger.ExplicitRequest(Id);
            ledger.Dispatched(Id);
            Cycle(ledger, new object(), true);
            var history = JObject.Parse(_saved);
            history["runs"][0]["Errors"] = 0;
            history["runs"][0]["Warnings"] = 1;
            Assert.AreEqual("result_unavailable", (string)Ledger(history.ToString()).Query(Id)["reason"]);
        }

        [Test]
        public void InterruptedRestorationDoesNotPoisonLaterIndependentRecovery()
        {
            var ledger = Ledger();
            ledger.Begin(Id);
            ledger.ExplicitRequest(Id);
            ledger.Dispatched(Id);
            ledger.Started(new object());
            var restored = Ledger(_saved);
            Assert.AreEqual("unknown", (string)restored.Summary()["outcome"]);
            restored.Begin(Other);
            restored.ExplicitRequest(Other);
            restored.Dispatched(Other);
            Cycle(restored, new object(), false);
            Assert.AreEqual("interrupted", (string)restored.Query(Id)["reason"]);
            Assert.AreEqual("success", (string)restored.Query(Other)["outcome"]);
            var secondReload = Ledger(_saved);
            Assert.AreEqual("interrupted", (string)secondReload.Query(Id)["reason"]);
            Assert.AreEqual("success", (string)secondReload.Query(Other)["outcome"]);
        }

        [Test]
        public void RequestBetweenRefreshAndExplicitCyclesCannotClaimPriorPendingWork()
        {
            var ledger = Ledger();
            ledger.Begin(Id);
            Cycle(ledger, new object(), false);
            ledger.ExplicitRequest(Id);
            ledger.Dispatched(Id);
            ledger.Begin(Other);
            ledger.ExplicitRequest(Other);
            ledger.Dispatched(Other);
            Cycle(ledger, new object(), false);
            Assert.AreEqual("unknown", (string)ledger.Query(Id)["outcome"]);
            Assert.AreEqual("unknown", (string)ledger.Query(Other)["outcome"]);
        }

        [Test]
        public void CorruptHistoryRotatesIdentityBeforeNewRunAndSecondReload()
        {
            var ledger = Ledger();
            ledger.Begin(Id);
            ledger.ExplicitRequest(Id);
            ledger.Dispatched(Id);
            Cycle(ledger, new object(), false);
            string originalSession = (string)ledger.Summary()["session_id"];
            var corrupt = Ledger("not json");
            string replacementSession = (string)corrupt.Summary()["session_id"];
            Assert.AreNotEqual(originalSession, replacementSession);
            corrupt.Begin(Other);
            corrupt.ExplicitRequest(Other);
            corrupt.Dispatched(Other);
            Cycle(corrupt, new object(), false);
            var reloaded = new CompilationLedger(replacementSession, "C:/fixture", value => _saved = value, _saved);
            Assert.AreEqual(replacementSession, (string)reloaded.Query(Other)["session_id"]);
            Assert.AreEqual("success", (string)reloaded.Query(Other)["outcome"]);
            Assert.AreNotEqual(originalSession, (string)reloaded.Query(Other)["session_id"]);
        }

        [Test]
        public void PersistedRequestCannotEraseItsExplicitEligibility()
        {
            var ledger = Ledger();
            ledger.Begin(Id);
            ledger.ExplicitRequest(Id);
            ledger.Dispatched(Id);
            Cycle(ledger, new object(), false);
            var history = JObject.Parse(_saved);
            history["requests"][0]["ExplicitGeneration"] = null;
            Assert.AreEqual("result_unavailable", (string)Ledger(history.ToString()).Query(Id)["reason"]);
        }

        [Test]
        public void PersistenceFailureNeverCertifiesSuccess()
        {
            var ledger = new CompilationLedger(Session, "C:/fixture", _ => throw new InvalidOperationException("fixture"));
            ledger.Begin(Id);
            ledger.ExplicitRequest(Id);
            ledger.Dispatched(Id);
            Cycle(ledger, new object(), false);
            Assert.AreEqual("unknown", (string)ledger.Query(Id)["outcome"]);
        }
    }
}
