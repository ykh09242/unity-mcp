using System;
using System.Security.Cryptography;
using System.Text;
using System.Threading.Tasks;
using MCPForUnity.Editor.Services.Transport;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using NUnit.Framework;

namespace MCPForUnityTests.Editor.Services
{
    public class CommandResumeJournalTests
    {
        private string stored;
        private long now;
        private bool failWrite;
        private CommandResumeJournal journal;

        [SetUp]
        public void Setup()
        {
            stored = null;
            now = 1000;
            failWrite = false;
            journal = Open();
        }

        private CommandResumeJournal Open() =>
            new CommandResumeJournal(
                () => stored,
                value =>
                {
                    if (failWrite)
                        throw new InvalidOperationException("injected persistence failure");
                    stored = value;
                },
                () => now
            );

        private JObject Request(string action = "new", string id = null, long deadline = 10000) =>
            new JObject
            {
                ["version"] = 1,
                ["invocation_id"] = id ?? "11111111111111111111111111111111",
                ["editor_session_id"] = journal.SessionId,
                ["deadline_unix_ms"] = deadline,
                ["action"] = action,
            };

        [Test]
        public void TranslatedDeadlineIsNeverRecapturedAfterReload()
        {
            foreach (long skew in new[] { -60000L, 60000L })
            {
                Setup();
                long sample = 1000000 + skew;
                // Native sample transit consumed two seconds before server receipt.
                // The sender translated its remaining ten seconds against that sample.
                now = sample + 2000;
                long deadline = sample + 10000;
                var request = Request(deadline: deadline);
                var parameters = new JObject { ["value"] = 1 };
                Assert.IsNotNull(journal.Admit(request, "test", parameters).Ticket);
                now += 2000;
                journal = Open();
                request["action"] = "resume";
                request["deadline_unix_ms"] = deadline + 5000;
                Assert.AreEqual("unavailable", journal.Admit(request, "test", parameters).State, "Reconnect cannot recapture a later deadline.");
                request["deadline_unix_ms"] = deadline;
                Assert.AreEqual(
                    "unavailable",
                    journal.Admit(request, "test", new JObject { ["value"] = 2 }).State,
                    "Translation cannot weaken the payload digest."
                );
                var resumed = journal.Admit(request, "test", parameters);
                Assert.IsNotNull(resumed.Ticket);
                now = deadline;
                Assert.IsFalse(journal.TryStart(resumed.Ticket), "Main-thread and transport delays consume the original native budget.");
                Assert.AreEqual("expired", journal.Inspect(resumed.Ticket).State);
            }
        }

        [Test]
        public void TranslatedDeadlineAlreadyConsumedInTransitCannotStart()
        {
            now = 1011000;
            Assert.AreEqual("expired", journal.Admit(Request(deadline: 1010000), "test", new JObject()).State);
        }

        [Test]
        public void PublicQueueOriginUsesTheSameContinuationGateOnItsFirstNativeSend()
        {
            var cases = new[]
            {
                ("manage_gameobject", "{\"action\":\"delete\",\"target\":\"-12345\"}"),
                ("gameobject_edit", "{\"action\":\"delete\",\"target\":\"-12345\"}"),
                (
                    "gameobject_edit",
                    "{\"action\":\"modify\",\"target\":\"Player\",\"componentProperties\":\"{\\\"Custom\\\":{\\\"arbitraryReference\\\":123}}\"}"
                ),
            };
            foreach (var (name, json) in cases)
            {
                Setup();
                var request = Request();
                request["origin_generation"] = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";
                Assert.IsNull(journal.Admit(request, name, JObject.Parse(json)).Ticket, name);
                request.Remove("origin_generation");
                Assert.IsNull(journal.Admit(request, name, JObject.Parse(json)).Ticket, "Origin cannot be stripped on retry");
            }
        }

        [Test]
        public void PublicQueueOriginRevalidatesDependenciesAndCannotBeDowngraded()
        {
            bool allowed = true;
            journal = new CommandResumeJournal(() => stored, value => stored = value, () => now, (_, __) => allowed);
            var request = Request();
            request["origin_generation"] = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";
            var parameters = JObject.Parse("{\"action\":\"create\",\"name\":\"123\",\"position\":[1,2,3]}");
            var admitted = journal.Admit(request, "manage_gameobject", parameters);
            Assert.IsNotNull(admitted.Ticket);
            allowed = false;
            Assert.IsFalse(journal.TryStart(admitted.Ticket));
            journal.Release(admitted.Ticket);
            request["origin_generation"] = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb";
            Assert.IsNull(journal.Admit(request, "manage_gameobject", parameters).Ticket);
            request["origin_generation"] = JValue.CreateNull();
            Assert.IsNull(journal.Admit(request, "manage_gameobject", parameters).Ticket);
        }

        [Test]
        public void AssetComponentAssignmentsAndEntityIdentitiesCannotCrossReload()
        {
            var cases = new[]
            {
                ("asset_edit", "{\"action\":\"modify\",\"properties\":{\"MeshRenderer\":{\"sharedMaterial\":123}}}"),
                ("manage_asset", "{\"action\":\"modify\",\"properties\":{\"MeshRenderer\":{\"sharedMaterial\":123}}}"),
                ("manage_asset", "{\"action\":\"modify\",\"properties\":{\"MeshRenderer\":{\"sharedMaterial\":{\"entityID\":123}}}}"),
            };
            foreach (var (name, json) in cases)
            {
                Setup();
                var parameters = JObject.Parse(json);
                Assert.IsNotNull(journal.Admit(Request(), name, parameters).Ticket);
                journal = Open();
                Assert.IsNull(journal.Admit(Request("resume"), name, parameters).Ticket, name);
            }
        }

        [Test]
        public void QueuedReloadCanBeClaimedExactlyOnceAndStartedReloadCannotReplay()
        {
            var parameters = new JObject { ["value"] = 1 };
            var first = journal.Admit(Request(), "test", parameters);
            Assert.IsNotNull(first.Ticket);
            Assert.IsNull(journal.Admit(Request("resume"), "test", parameters).Ticket);
            journal = Open();
            var resumed = journal.Admit(Request("resume"), "test", parameters);
            Assert.IsNotNull(resumed.Ticket);
            Assert.IsTrue(journal.TryStart(resumed.Ticket));
            journal = Open();
            var uncertain = journal.Admit(Request("resume"), "test", parameters);
            Assert.AreEqual("unknown", uncertain.State);
            Assert.IsNull(uncertain.Ticket);
        }

        [Test]
        public void CompletionIsCachedWithoutInvokingAgainAndCanonicalArgumentsMatch()
        {
            var admitted = journal.Admit(Request(), "test", new JObject { ["b"] = 2, ["a"] = 1 });
            Assert.IsTrue(journal.TryStart(admitted.Ticket));
            journal.Complete(admitted.Ticket, new JObject { ["result"] = 42 });
            journal = Open();
            var replay = journal.Admit(Request("resume"), "test", new JObject { ["a"] = 1, ["b"] = 2 });
            Assert.AreEqual("completed", replay.State);
            Assert.AreEqual(42, (int)replay.Payload["result"]);
            Assert.IsNull(replay.Ticket);
        }

        private static JObject NestedResult(int arrays, bool raw)
        {
            JToken value = 42;
            if (raw)
                value = new JRaw(new string('[', arrays) + "42" + new string(']', arrays));
            else
                for (int i = 0; i < arrays; i++)
                    value = new JArray(value);
            return new JObject { ["status"] = "success", ["result"] = value };
        }

        [TestCase(60, false, true)]
        [TestCase(61, false, true)]
        [TestCase(62, false, false)]
        [TestCase(61, true, true)]
        [TestCase(62, true, false)]
        public void DeepCompletionAffectsOnlyItsOwnRetention(int arrays, bool raw, bool retained)
        {
            var admitted = journal.Admit(Request(), "test", new JObject());
            Assert.IsTrue(journal.TryStart(admitted.Ticket));
            Assert.AreEqual("completed", journal.Complete(admitted.Ticket, NestedResult(arrays, raw)).State);

            string id = "22222222222222222222222222222222";
            var next = journal.Admit(Request(id: id), "test", new JObject());
            Assert.IsTrue(journal.TryStart(next.Ticket));
            Assert.AreEqual("completed", journal.Complete(next.Ticket, new JValue(7)).State);

            journal = Open();
            var replay = journal.Admit(Request("resume"), "test", new JObject());
            Assert.IsNull(replay.Ticket);
            Assert.AreEqual(retained ? "completed" : "unavailable", replay.State);
            if (retained)
                Assert.IsTrue(JToken.DeepEquals(NestedResult(arrays, false), replay.Payload));
            else
                Assert.AreEqual("completed_result_not_retained", (string)replay.Payload["code"]);
            Assert.AreEqual(7, (int)journal.Admit(Request("resume", id), "test", new JObject()).Payload);
            Assert.IsTrue(journal.TryStart(journal.Admit(Request(id: "33333333333333333333333333333333"), "test", new JObject()).Ticket));
        }

        [Test]
        public void ExistingVersionOneSnapshotAtDepth65RestoresWithIntegrityCheck()
        {
            var admitted = journal.Admit(Request(), "test", new JObject());
            Assert.IsTrue(journal.TryStart(admitted.Ticket));
            journal.Complete(admitted.Ticket, new JValue(7));

            var snapshot = JObject.Parse(stored);
            var rows = (JArray)snapshot["entries"];
            rows[0]["result"] = NestedResult(61, false);
            using var hash = SHA256.Create();
            var digest = hash.ComputeHash(Encoding.UTF8.GetBytes(journal.SessionId + "\n" + rows.ToString(Formatting.None)));
            snapshot["digest"] = BitConverter.ToString(digest).Replace("-", "").ToLowerInvariant();
            stored = snapshot.ToString(Formatting.None);

            journal = Open();
            Assert.IsTrue(JToken.DeepEquals(rows[0]["result"], journal.Admit(Request("resume"), "test", new JObject()).Payload));
            stored = stored.Replace("\"phase\":\"completed\"", "\"phase\":\"queued\"");
            journal = Open();
            Assert.AreEqual("unavailable", journal.Admit(Request("resume"), "test", new JObject()).State);
        }

        [TestCase(0, true)]
        [TestCase(1, false)]
        public void RetentionLimitCountsSerializedUtf8Bytes(int extraBytes, bool retained)
        {
            const string prefix = "한🚀";
            string text = prefix + new string('a', CommandResumeJournal.MaxResultBytes - Encoding.UTF8.GetByteCount(prefix) - 2 + extraBytes);
            var result = new JValue(text);
            Assert.AreEqual(CommandResumeJournal.MaxResultBytes + extraBytes, Encoding.UTF8.GetByteCount(result.ToString(Formatting.None)));
            var admission = journal.Admit(Request(), "test", new JObject());
            Assert.IsTrue(journal.TryStart(admission.Ticket));
            Assert.AreEqual("completed", journal.Complete(admission.Ticket, result).State);
            journal = Open();
            var receipt = journal.Admit(Request("resume"), "test", new JObject());
            Assert.AreEqual(retained ? "completed" : "unavailable", receipt.State);
            Assert.IsNull(receipt.Ticket);
        }

        [TestCase(false)]
        [TestCase(true)]
        public void InvalidOrOversizeNormalizedRawResultIsNotRetained(bool malformed)
        {
            string json = malformed ? "[1,2" : "[" + string.Join(",", System.Linq.Enumerable.Repeat("1e9", 1000)) + "]";
            Assert.Less(Encoding.UTF8.GetByteCount(json), CommandResumeJournal.MaxResultBytes);
            var admission = journal.Admit(Request(), "test", new JObject());
            Assert.IsTrue(journal.TryStart(admission.Ticket));
            Assert.AreEqual("completed", journal.Complete(admission.Ticket, new JRaw(json)).State);
            journal = Open();
            var receipt = journal.Admit(Request("resume"), "test", new JObject());
            Assert.IsNull(receipt.Ticket);
            Assert.AreEqual("completed_result_not_retained", (string)receipt.Payload["code"]);
            Assert.IsTrue(journal.TryStart(journal.Admit(Request(id: "22222222222222222222222222222222"), "test", new JObject()).Ticket));
        }

        [Test]
        public void CancelTombstoneSurvivesReloadAndCannotBeRecreated()
        {
            journal.Admit(Request(), "test", new JObject());
            Assert.AreEqual("canceled", journal.Admit(Request("cancel"), "test", new JObject()).State);
            journal = Open();
            Assert.AreEqual("canceled", journal.Admit(Request("resume"), "test", new JObject()).State);
            Assert.AreEqual("canceled", journal.Admit(Request(), "test", new JObject()).State);
        }

        [Test]
        public void MissingChangedDeadlineAndChangedArgumentsNeverGrantContinuation()
        {
            Assert.AreEqual("unknown", journal.Admit(Request("resume"), "test", new JObject()).State);
            journal.Admit(Request(), "test", new JObject());
            Assert.IsNull(journal.Admit(Request("resume", deadline: 11000), "test", new JObject()).Ticket);
            Assert.IsNull(journal.Admit(Request("resume"), "test", new JObject { ["different"] = true }).Ticket);
            now = 10001;
            Assert.AreEqual("expired", journal.Admit(Request("resume"), "test", new JObject()).State);
        }

        [Test]
        public void FailedStartedWritePreventsInvocationAndPoisonedStoreCannotGrantAdmission()
        {
            var admitted = journal.Admit(Request(), "test", new JObject());
            failWrite = true;
            Assert.IsFalse(journal.TryStart(admitted.Ticket));
            Assert.AreEqual("unavailable", journal.Admit(Request("resume"), "test", new JObject()).State);
        }

        private JObject JournalHealth() => journal.CaptureHealth();

        private bool RecoverJournal() => journal.TryRecoverPersistence();

        [Test]
        public void PersistenceRecoveryPreservesTerminalFencesAndActiveOwnership()
        {
            var completed = journal.Admit(Request(), "test", new JObject());
            Assert.IsTrue(journal.TryStart(completed.Ticket));
            journal.Complete(completed.Ticket, new JValue(42));
            journal.Release(completed.Ticket);
            const string runningId = "22222222222222222222222222222222";
            var running = journal.Admit(Request(id: runningId), "test", new JObject());
            Assert.IsTrue(journal.TryStart(running.Ticket));
            failWrite = true;
            var canceled = Request("cancel", runningId);
            Assert.AreEqual("unavailable", journal.Admit(canceled, "test", new JObject()).State);
            Assert.AreEqual("persistence_write_failed", (string)JournalHealth()["reason"]);
            Assert.IsTrue((bool)JournalHealth()["recovery_allowed"]);
            Assert.IsFalse(RecoverJournal(), "A failed explicit retry cannot clear unavailable state.");
            failWrite = false;
            Assert.AreEqual("unavailable", journal.Admit(Request("resume"), "test", new JObject()).State, "Recovery is never automatic.");
            Assert.IsTrue(RecoverJournal());
            Assert.AreEqual(1, (int)JournalHealth()["capacity"]["active"], "Recovery cannot release the still-running owner.");
            Assert.IsNull(journal.Admit(Request("resume", runningId), "test", new JObject()).Ticket);
            Assert.AreEqual("canceled", journal.Inspect(running.Ticket).State);
            journal.Complete(running.Ticket, new JValue(7));
            journal.Release(running.Ticket);
            journal = Open();
            Assert.AreEqual(42, (int)journal.Admit(Request("resume"), "test", new JObject()).Payload);
            Assert.AreEqual("canceled", journal.Admit(Request("resume", runningId), "test", new JObject()).State);
            Assert.AreEqual(7, (int)journal.Admit(Request("resume", runningId), "test", new JObject()).Payload);
        }

        [TestCase(false)]
        [TestCase(true)]
        public void UntrustedInitialSnapshotCannotBeClearedByRecovery(bool readFailure)
        {
            stored = "{private_path_and_secret";
            int writes = 0;
            journal = new CommandResumeJournal(
                () => readFailure ? throw new InvalidOperationException("private_path_and_secret") : stored,
                _ => writes++,
                () => now
            );
            var health = JournalHealth();
            Assert.AreEqual(readFailure ? "storage_read_failed" : "snapshot_invalid", (string)health["reason"]);
            Assert.IsFalse((bool)health["recovery_allowed"]);
            Assert.IsFalse(RecoverJournal());
            Assert.AreEqual(0, writes, "Unverified fences must never be overwritten by an empty or partially read snapshot.");
            StringAssert.DoesNotContain("private_path_and_secret", health.ToString());
            Assert.IsNull(journal.Admit(Request(), "test", new JObject()).Ticket);
        }

        [Test]
        public void SustainedMutationBurstsReportSaturationAndRecoverOnlyExpiredReleasedFences()
        {
            long snapshotBytes = 0;
            int admittedCount = 0;
            int refusedCount = 0;
            var watch = System.Diagnostics.Stopwatch.StartNew();
            for (int burst = 0; burst < 3; burst++)
            {
                long deadline = now + 10000;
                for (int i = 0; i < 160; i++)
                {
                    string id = (burst * 160 + i).ToString("x32");
                    var admitted = journal.Admit(Request(id: id, deadline: deadline), "manage_gameobject", new JObject { ["action"] = "create" });
                    if (i < CommandResumeJournal.MaxEntries)
                    {
                        admittedCount++;
                        Assert.IsTrue(journal.TryStart(admitted.Ticket));
                        journal.Complete(admitted.Ticket, new JValue(i));
                        journal.Release(admitted.Ticket);
                    }
                    else
                    {
                        refusedCount++;
                        Assert.IsTrue(admitted.NotStarted);
                        Assert.AreEqual("wait_for_expiry", (string)admitted.Payload["capacity"]["recovery_action"]);
                        Assert.AreEqual(0, (int)admitted.Payload["capacity"]["available"]);
                    }
                }
                snapshotBytes = Math.Max(snapshotBytes, Encoding.UTF8.GetByteCount(stored));
                var health = JournalHealth();
                Assert.AreEqual(128, (int)health["capacity"]["terminal"]);
                Assert.AreEqual(128, (int)health["capacity"]["unexpired"]);
                Assert.AreEqual(10000, (long)health["capacity"]["retry_after_ms"]);
                var duplicate = journal.Admit(
                    Request(id: (burst * 160).ToString("x32"), deadline: deadline),
                    "manage_gameobject",
                    new JObject { ["action"] = "create" }
                );
                Assert.IsNull(duplicate.Ticket);
                Assert.AreEqual("completed", duplicate.State);
                now = deadline;
            }
            Assert.AreEqual(384, admittedCount);
            Assert.AreEqual(96, refusedCount);
            TestContext.WriteLine(
                $"journal_workload admitted={admittedCount} refused={refusedCount} max_snapshot_bytes={snapshotBytes} elapsed_ms={watch.ElapsedMilliseconds}"
            );
        }

        [Test]
        public void CompletedCapacityReportsRecoveryWithoutEvictingDuplicateFences()
        {
            for (int i = 0; i < CommandResumeJournal.MaxEntries; i++)
            {
                var accepted = journal.Admit(Request(id: i.ToString("x32"), deadline: 10000 + i), "test", new JObject());
                Assert.IsTrue(journal.TryStart(accepted.Ticket));
                journal.Complete(accepted.Ticket, new JObject { ["value"] = i });
                journal.Release(accepted.Ticket);
            }
            var refused = journal.Admit(Request(id: new string('f', 32), deadline: 20000), "test", new JObject());
            Assert.IsTrue(refused.NotStarted);
            var capacity = refused.Payload["capacity"];
            Assert.IsNotNull(capacity, "Capacity refusal must explain when safe admission can recover.");
            Assert.AreEqual(128, (int)capacity["limit"]);
            Assert.AreEqual(128, (int)capacity["occupied"]);
            Assert.AreEqual(0, (int)capacity["active"]);
            Assert.AreEqual(128, (int)capacity["terminal"]);
            Assert.AreEqual(10000, (long)capacity["earliest_reclaim_unix_ms"]);
            Assert.AreEqual(9000, (long)capacity["retry_after_ms"]);
            var duplicate = journal.Admit(Request(id: 1.ToString("x32"), deadline: 10001), "test", new JObject());
            Assert.IsNull(duplicate.Ticket, "Capacity diagnostics cannot evict an unexpired duplicate fence.");
            Assert.AreEqual("completed", duplicate.State);
            Assert.AreEqual(1, (int)duplicate.Payload["value"]);

            now = 10000;
            var recovered = journal.Admit(Request(id: new string('f', 32), deadline: 20000), "test", new JObject());
            Assert.IsNotNull(recovered.Ticket);
            Assert.AreEqual("expired", journal.Admit(Request(id: 0.ToString("x32"), deadline: 10000), "test", new JObject()).State);
            Assert.IsNull(journal.Admit(Request(id: 1.ToString("x32"), deadline: 10001), "test", new JObject()).Ticket);
        }

        [Test]
        public void ActiveCapacityDoesNotPromiseAReclaimBeforeOwnersRelease()
        {
            CommandResumeJournal.Ticket first = null;
            for (int i = 0; i < CommandResumeJournal.MaxEntries; i++)
            {
                var ticket = journal.Admit(Request(id: i.ToString("x32")), "test", new JObject()).Ticket;
                if (i == 0)
                    first = ticket;
            }
            var capacity = journal.Admit(Request(id: new string('f', 32)), "test", new JObject()).Payload["capacity"];
            Assert.IsNotNull(capacity);
            Assert.AreEqual(128, (int)capacity["active"]);
            Assert.AreEqual(0, (int)capacity["terminal"]);
            Assert.AreEqual(JTokenType.Null, capacity["earliest_reclaim_unix_ms"].Type);
            Assert.AreEqual(JTokenType.Null, capacity["retry_after_ms"].Type);
            now = 10000;
            var expiredOwners = journal.Admit(Request(id: new string('f', 32), deadline: 20000), "test", new JObject());
            Assert.IsNull(expiredOwners.Ticket);
            Assert.AreEqual(128, (int)expiredOwners.Payload["capacity"]["expired_active"]);
            Assert.AreEqual("wait_for_owner_settlement", (string)expiredOwners.Payload["capacity"]["recovery_action"]);
            journal.Release(first);
            Assert.IsNotNull(journal.Admit(Request(id: new string('f', 32), deadline: 20000), "test", new JObject()).Ticket);
        }

        [Test]
        public void CorruptStoreAndCapacityFailClosedWithoutEvictingSafetyEntries()
        {
            for (int i = 0; i < CommandResumeJournal.MaxEntries; i++)
                Assert.IsNotNull(journal.Admit(Request(id: i.ToString("x32")), "test", new JObject()).Ticket);
            var full = journal.Admit(Request(id: "ffffffffffffffffffffffffffffffff"), "test", new JObject());
            Assert.AreEqual("unavailable", full.State);
            Assert.IsNull(full.Ticket);
            Assert.IsTrue(full.NotStarted);
            Assert.AreEqual("command_resume_capacity", full.RefusalReason);
            Assert.AreEqual(full.RefusalReason, (string)full.Payload["code"]);
            stored = "{broken";
            journal = Open();
            var corrupt = journal.Admit(Request(), "test", new JObject());
            Assert.AreEqual("unavailable", corrupt.State);
            Assert.IsFalse(corrupt.NotStarted);
        }

        [Test]
        public void ConcurrentDuplicateDeliveryGrantsOnlyOneRuntimeClaim()
        {
            int claims = 0;
            Parallel.For(0, 16, _ =>
            {
                if (journal.Admit(Request(), "test", new JObject()).Ticket != null)
                    System.Threading.Interlocked.Increment(ref claims);
            });
            Assert.AreEqual(1, claims);
        }

        [Test]
        public void UnstartedDisconnectReleasesClaimWithoutCancelingIntent()
        {
            var first = journal.Admit(Request(), "test", new JObject());
            journal.Release(first.Ticket);
            Assert.IsNotNull(journal.Admit(Request("resume"), "test", new JObject()).Ticket);
        }

        [Test]
        public void LateCompletionCannotEraseCancellationAndLargeResultsNeverReplay()
        {
            var admitted = journal.Admit(Request(), "test", new JObject());
            Assert.IsTrue(journal.TryStart(admitted.Ticket));
            journal.Admit(Request("cancel"), "test", new JObject());
            Assert.AreEqual("canceled", journal.Complete(admitted.Ticket, new JObject { ["done"] = true }).State);
            var other = journal.Admit(Request(id: "22222222222222222222222222222222"), "test", new JObject());
            Assert.IsTrue(journal.TryStart(other.Ticket));
            Assert.AreEqual("completed", journal.Complete(other.Ticket, new JValue(new string('x', CommandResumeJournal.MaxResultBytes + 1))).State);
            journal = Open();
            Assert.AreEqual("unavailable", journal.Admit(Request("resume", "22222222222222222222222222222222"), "test", new JObject()).State);
        }

        [Test]
        public void LocalObjectIdsAreNotReinterpretedAcrossReloadWhileSymbolicSelectorsStayEligible()
        {
            var unsafeParameters = new JObject { ["commands"] = new JArray(new JObject { ["params"] = new JObject { ["target"] = -123 } }) };
            journal.Admit(Request(), "test", unsafeParameters);
            var symbolic = new JObject { ["target"] = "Player/Camera", ["distance"] = 2 };
            journal.Admit(Request(id: "22222222222222222222222222222222"), "test", symbolic);
            journal = Open();
            Assert.AreEqual("unavailable", journal.Admit(Request("resume"), "test", unsafeParameters).State);
            Assert.IsNotNull(journal.Admit(Request("resume", "22222222222222222222222222222222"), "test", symbolic).Ticket);
        }

        [Test]
        public void ContinuationPolicyIsRevalidatedImmediatelyBeforeStartedFence()
        {
            bool allowed = true;
            journal = new CommandResumeJournal(() => stored, value => stored = value, () => now, (_, __) => allowed);
            journal.Admit(Request(), "test", new JObject());
            journal = new CommandResumeJournal(() => stored, value => stored = value, () => now, (_, __) => allowed);
            var resumed = journal.Admit(Request("resume"), "test", new JObject());
            Assert.IsNotNull(resumed.Ticket);
            allowed = false;
            Assert.IsFalse(journal.TryStart(resumed.Ticket));
            Assert.AreEqual("unavailable", journal.Inspect(resumed.Ticket).State);
        }

        [Test]
        public void NewProcessIdentityAndMalformedEnvelopeNeverGrantExecution()
        {
            var request = Request();
            request["editor_session_id"] = "22222222222222222222222222222222";
            Assert.IsNull(journal.Admit(request, "test", new JObject()).Ticket);
            request = Request();
            request["deadline_unix_ms"] = "10000";
            Assert.IsNull(journal.Admit(request, "test", new JObject()).Ticket);
            request = Request();
            request["invocation_id"] = new JObject();
            Assert.IsNull(journal.Admit(request, "test", new JObject()).Ticket);
        }

        [Test]
        public void ValidJsonCheckpointCorruptionFailsClosedAndDateStringsRemainExact()
        {
            var admitted = journal.Admit(Request(), "test", new JObject());
            Assert.IsTrue(journal.TryStart(admitted.Ticket));
            const string iso = "2025-01-01T12:30:00.1234567+03:00";
            journal.Complete(admitted.Ticket, new JObject { ["timestamp"] = iso });
            journal = Open();
            Assert.AreEqual(iso, (string)journal.Admit(Request("resume"), "test", new JObject()).Payload["timestamp"]);
            stored = stored.Replace("\"phase\":\"completed\"", "\"phase\":\"queued\"");
            journal = Open();
            Assert.AreEqual("unavailable", journal.Admit(Request("resume"), "test", new JObject()).State);
        }

        [Test]
        public void RawTokensAndFloatingNumbersRoundtripWithoutFalseIntegrityFailure()
        {
            var admission = journal.Admit(Request(), "test", new JObject());
            Assert.IsTrue(journal.TryStart(admission.Ticket));
            journal.Complete(
                admission.Ticket,
                new JObject { ["raw"] = new JRaw("{ \"value\" : 1, \"timestamp\" : \"2025-01-01T12:30:00.1234567+03:00\" }"), ["floating"] = 1.0 }
            );
            journal = Open();
            var restored = journal.Admit(Request("resume"), "test", new JObject());
            Assert.AreEqual("completed", restored.State);
            Assert.AreEqual(1, (int)restored.Payload["raw"]["value"]);
            Assert.AreEqual("2025-01-01T12:30:00.1234567+03:00", (string)restored.Payload["raw"]["timestamp"]);
            Assert.AreEqual(1.0, (double)restored.Payload["floating"]);
        }

        [Test]
        public void ActualNumericSceneSelectorAliasesCannotContinueAfterReloadIncludingNestedBatches()
        {
            var cases = new[]
            {
                (
                    "manage_gameobject",
                    new JObject
                    {
                        ["action"] = "move_relative",
                        ["target"] = "Player",
                        ["reference_object"] = 123,
                        ["distance"] = 1,
                    }
                ),
                (
                    "gameobject_positioning",
                    new JObject
                    {
                        ["action"] = "look_at",
                        ["target"] = "Player",
                        ["look_at_target"] = "123",
                    }
                ),
                (
                    "manage_gameobject",
                    new JObject
                    {
                        ["action"] = "look_at",
                        ["target"] = "Player",
                        ["lookAtTarget"] = 123,
                    }
                ),
                (
                    "gameobject_duplicate",
                    new JObject
                    {
                        ["action"] = "duplicate",
                        ["target"] = "Player",
                        ["parent"] = "123",
                    }
                ),
                ("manage_gameobject", new JObject { ["action"] = "modify", ["name"] = "123" }),
                ("gameobject_edit", new JObject { ["action"] = "delete", ["name"] = "123" }),
                ("gameobject_edit", new JObject { ["action"] = "delete", ["name"] = 123.0 }),
                ("gameobject_placement", new JObject { ["action"] = "placement", ["targets"] = new JArray("Player", 123) }),
                (
                    "manage_cameras",
                    new JObject
                    {
                        ["action"] = "set_target",
                        ["target"] = "Camera",
                        ["properties"] = new JObject { ["follow"] = "123" },
                    }
                ),
                (
                    "manage_cinemachine",
                    new JObject
                    {
                        ["action"] = "set_target",
                        ["target"] = "Camera",
                        ["properties"] = new JObject { ["lookAt"] = 123 },
                    }
                ),
                (
                    "manage_cameras",
                    new JObject
                    {
                        ["action"] = "set_target",
                        ["target"] = "Camera",
                        ["properties"] = "{\"look_at\":\"123\"}",
                    }
                ),
                (
                    "manage_camera",
                    new JObject
                    {
                        ["action"] = "ensure_brain",
                        ["properties"] = new JObject { ["camera"] = "123" },
                    }
                ),
                ("manage_scene", new JObject { ["action"] = "screenshot", ["viewTarget"] = 123 }),
                ("scene_inspect", new JObject { ["action"] = "scene_view_frame", ["sceneViewTarget"] = "123" }),
                (
                    "manage_physics_joints",
                    new JObject
                    {
                        ["action"] = "add_joint",
                        ["target"] = "Player",
                        ["connected_body"] = "123",
                    }
                ),
                (
                    "diagnose_physics",
                    new JObject
                    {
                        ["action"] = "diagnose_pair",
                        ["target"] = "Player",
                        ["other_target"] = 123,
                    }
                ),
                (
                    "manage_ui",
                    new JObject
                    {
                        ["action"] = "attach_ui_document",
                        ["target"] = new JObject { ["find"] = "123" },
                    }
                ),
                ("get_gameobject_component", new JObject { ["id"] = 123, ["component"] = "Transform" }),
                ("manage_render_environment", new JObject { ["action"] = "skybox_set_sun", ["name"] = "123" }),
            };
            foreach (var item in cases)
            {
                stored = null;
                journal = Open();
                var admitted = journal.Admit(Request(), item.Item1, item.Item2);
                Assert.IsNotNull(admitted.Ticket, "Initial commands preserve their existing validation: " + item.Item2);
                journal.Release(admitted.Ticket);
                Assert.IsNotNull(journal.Admit(Request("resume"), item.Item1, item.Item2).Ticket, "Same-domain admission remains eligible.");
                journal = Open();
                Assert.AreEqual("unavailable", journal.Admit(Request("resume"), item.Item1, item.Item2).State, item.Item1 + " " + item.Item2);

                stored = null;
                journal = Open();
                var batch = new JObject { ["commands"] = new JArray(new JObject { ["tool"] = item.Item1, ["params"] = item.Item2.DeepClone() }) };
                journal.Admit(Request(), "batch_execute", batch);
                journal = Open();
                Assert.AreEqual("unavailable", journal.Admit(Request("resume"), "batch_execute", batch).State, "Nested: " + item.Item1 + " " + item.Item2);
            }
        }

        [Test]
        public void PrefabSourceAliasesFenceReloadAndPublicOrigin()
        {
            foreach (string selector in new[] { "target", "name" })
            foreach (bool batch in new[] { false, true })
            foreach (bool numeric in new[] { false, true })
            {
                Setup();
                var child = new JObject { ["action"] = "create_from_gameobject", [selector] = numeric ? "-12345" : "Player" };
                string command = batch ? "batch_execute" : "manage_prefabs";
                var parameters = batch ? new JObject { ["commands"] = new JArray(new JObject { ["tool"] = "manage_prefabs", ["params"] = child }) } : child;
                Assert.IsNotNull(journal.Admit(Request(), command, parameters).Ticket);
                journal = Open();
                var resumed = journal.Admit(Request("resume"), command, parameters);
                Assert.AreEqual(numeric ? "unavailable" : "queued", resumed.State, selector);
                Assert.AreEqual(!numeric, resumed.Ticket != null, selector);

                Setup();
                var origin = Request();
                origin["origin_generation"] = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";
                Assert.AreEqual(!numeric, journal.Admit(origin, command, parameters).Ticket != null, selector);
            }
        }

        [Test]
        public void ComponentAssignmentObjectReferencesAndEncodedContainersRequireFreshRequests()
        {
            var cases = new[]
            {
                (
                    "manage_gameobject",
                    new JObject
                    {
                        ["action"] = "modify",
                        ["target"] = "Player",
                        ["componentProperties"] = new JObject { ["Custom"] = new JObject { ["arbitraryReference"] = 123 } },
                    }
                ),
                (
                    "gameobject_create",
                    new JObject
                    {
                        ["action"] = "create",
                        ["name"] = "Player",
                        ["componentProperties"] = "{\"Custom\":{\"arbitraryReference\":\"123\"}}",
                    }
                ),
                (
                    "manage_gameobject",
                    new JObject
                    {
                        ["action"] = "create",
                        ["name"] = "Player",
                        ["componentsToAdd"] = new JArray(
                            new JObject
                            {
                                ["typeName"] = "Custom",
                                ["properties"] = new JObject { ["arbitraryReference"] = 123 },
                            }
                        ),
                    }
                ),
                (
                    "manage_components",
                    new JObject
                    {
                        ["action"] = "set_property",
                        ["target"] = "Player",
                        ["property"] = "arbitraryReference",
                        ["value"] = 123,
                    }
                ),
                (
                    "manage_components_selected",
                    new JObject
                    {
                        ["action"] = "set_property",
                        ["target"] = "Player",
                        ["properties"] = new JObject { ["arbitraryReference"] = "123" },
                    }
                ),
                (
                    "manage_gameobject",
                    new JObject
                    {
                        ["action"] = "modify",
                        ["target"] = "Player",
                        ["componentProperties"] = "{invalid",
                    }
                ),
                (
                    "manage_gameobject",
                    new JObject
                    {
                        ["action"] = "modify",
                        ["target"] = "Player",
                        ["componentProperties"] = new string('[', 70) + "123" + new string(']', 70),
                    }
                ),
                (
                    "manage_input",
                    new JObject { ["action"] = "ui_click", ["target"] = "mcp-oh-v2.aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa_bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb" }
                ),
            };
            foreach (var item in cases)
            {
                stored = null;
                journal = Open();
                Assert.IsNotNull(journal.Admit(Request(), item.Item1, item.Item2).Ticket);
                journal = Open();
                Assert.AreEqual("unavailable", journal.Admit(Request("resume"), item.Item1, item.Item2).State, item.Item1 + " " + item.Item2);
                stored = null;
                journal = Open();
                var nested = new JObject { ["commands"] = new JArray(new JObject { ["tool"] = item.Item1, ["params"] = item.Item2.DeepClone() }) };
                journal.Admit(Request(), "batch_execute", nested);
                journal = Open();
                Assert.AreEqual("unavailable", journal.Admit(Request("resume"), "batch_execute", nested).State, "Nested component assignment: " + item.Item1);
            }
        }

        [Test]
        public void OrdinaryNumericValuesCreationNamesAndLookAtVectorsRemainEligible()
        {
            var parameters = new JObject
            {
                ["action"] = "create",
                ["name"] = "123",
                ["position"] = new JArray(1, 2, 3),
                ["count"] = 3,
                ["distance"] = 1,
                ["parent"] = "Environment",
                ["componentProperties"] = new JObject { ["Custom"] = new JObject { ["enabled"] = true } },
            };
            journal.Admit(Request(), "gameobject_create", parameters);
            journal = Open();
            Assert.IsNotNull(journal.Admit(Request("resume"), "gameobject_create", parameters).Ticket);
            stored = null;
            journal = Open();
            parameters = new JObject
            {
                ["action"] = "look_at",
                ["target"] = "Player",
                ["look_at_target"] = new JArray(1, 2, 3),
                ["look_at_up"] = new JArray(0, 1, 0),
            };
            journal.Admit(Request(), "gameobject_positioning", parameters);
            journal = Open();
            Assert.IsNotNull(journal.Admit(Request("resume"), "gameobject_positioning", parameters).Ticket);
            stored = null;
            journal = Open();
            parameters = new JObject { ["action"] = "create_camera", ["properties"] = "{\"name\":\"123\",\"fieldOfView\":60}" };
            journal.Admit(Request(), "manage_cameras", parameters);
            journal = Open();
            Assert.IsNotNull(journal.Admit(Request("resume"), "manage_cameras", parameters).Ticket);
        }
    }
}
