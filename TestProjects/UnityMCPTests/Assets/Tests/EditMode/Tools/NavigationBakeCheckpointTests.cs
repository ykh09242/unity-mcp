using System;
using System.Collections;
using System.Reflection;
using System.Threading.Tasks;
using MCPForUnity.Editor.Services;
using MCPForUnity.Editor.Services.Transport;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEditor;
using UnityEngine.TestTools;

namespace MCPForUnityTests.EditMode.Tools
{
    public class NavigationBakeCheckpointTests
    {
        private Type Checkpoint =>
            typeof(MCPForUnity.Editor.Tools.ManagePackages).Assembly.GetType("MCPForUnity.Editor.Tools.Navigation.NavigationBakeCheckpoint");

        private object Call(string name, params object[] args)
        {
            Assert.IsNotNull(Checkpoint, "Navigation checkpoint recovery must be available without the optional navigation package.");
            try
            {
                return Checkpoint.GetMethod(name, BindingFlags.Static | BindingFlags.NonPublic).Invoke(null, args);
            }
            catch (TargetInvocationException ex)
            {
                throw ex.InnerException;
            }
        }

        private JObject Queued() =>
            new JObject
            {
                ["job_id"] = "owned",
                ["state"] = "queued",
                ["native_invoked"] = false,
                ["deadline_utc"] = DateTime.UtcNow.AddMinutes(1).ToString("O"),
                ["checkpoint"] = new JObject
                {
                    ["version"] = 1,
                    ["target"] = "stable",
                    ["scene_guid"] = "scene",
                    ["scene_hash"] = "hash",
                    ["configuration_hash"] = "config",
                    ["backend"] = "backend",
                },
            };

        private JArray Restore(JObject job) => (JArray)Call("Restore", new JArray(job).ToString());

        private bool Advance(JObject job, Action validate, Action bake, Func<string> blocked = null, Action save = null) =>
            (bool)Call("Advance", job, DateTime.UtcNow, validate, bake, blocked ?? (() => null), save ?? (() => { }));

        private static async Task HoldExclusiveUntil(Task settlement)
        {
            Assert.IsTrue(
                NativeCommandArbitrator.Shared.TryAcquire(ToolOperationMode.Exclusive, "execute_code", null, null, out var lease, out var reason),
                reason.Reason
            );
            using (lease)
                await settlement;
        }

        private static bool ExclusiveIsHeld()
        {
            bool acquired = NativeCommandArbitrator.Shared.TryAcquire(
                ToolOperationMode.Exclusive,
                "manage_navigation",
                new JObject { ["action"] = "bake" },
                null,
                out var lease,
                out var blocker
            );
            lease?.Dispose();
            return !acquired && blocker.Reason == "operation_busy";
        }

        private static void AssertSharedIsReleased()
        {
            bool acquired = NativeCommandArbitrator.Shared.TryAcquire(
                ToolOperationMode.Exclusive,
                "manage_navigation",
                new JObject { ["action"] = "bake" },
                null,
                out var lease,
                out var reason
            );
            lease?.Dispose();
            Assert.IsTrue(acquired, reason.Reason);
        }

        [UnityTest]
        public IEnumerator DeferredPumpWaitsForActualExclusiveAsyncOwnerThenInvokesOnce()
        {
            JObject job = Queued();
            var settlement = new TaskCompletionSource<bool>();
            Task owner = HoldExclusiveUntil(settlement.Task);
            int polls = 0;
            int validations = 0;
            int invocations = 0;
            bool validationOwned = false;
            bool invocationOwned = false;
            bool saveOwned = true;
            int ownedSaves = 0;
            EditorApplication.CallbackFunction pump = () =>
            {
                polls++;
                Advance(
                    job,
                    () =>
                    {
                        validations++;
                        validationOwned = ExclusiveIsHeld();
                    },
                    () =>
                    {
                        invocations++;
                        invocationOwned = ExclusiveIsHeld();
                    },
                    save: () =>
                    {
                        if ((string)job["state"] == "running" || (string)job["state"] == "completed")
                        {
                            ownedSaves++;
                            saveOwned &= ExclusiveIsHeld();
                        }
                    }
                );
            };
            EditorApplication.update += pump;
            try
            {
                for (int frame = 0; frame < 3; frame++)
                    yield return null;
                Assert.Greater(polls, 0, "The deferred callback must run while the async owner is unsettled.");
                Assert.IsFalse(owner.IsCompleted);
                Assert.AreEqual("queued", (string)job["state"]);
                Assert.IsFalse((bool)job["native_invoked"]);
                Assert.AreEqual(0, validations, "Target validation must also wait for ownership.");
                Assert.AreEqual(0, invocations);
                StringAssert.Contains("operation_busy", (string)job["detail"]);

                // Round-trip exactly the retained, unstarted reservation while ownership is held.
                string deadline = (string)job["deadline_utc"];
                job = (JObject)Restore(job)[0];
                Assert.AreEqual(deadline, (string)job["deadline_utc"]);
                Assert.IsFalse(Advance(job, () => validations++, () => invocations++));
                settlement.SetResult(true);
                for (int frame = 0; frame < 20 && (!owner.IsCompleted || invocations == 0); frame++)
                    yield return null;
                Assert.IsTrue(owner.IsCompleted);
                Assert.IsFalse(owner.IsFaulted, owner.Exception?.ToString());
                Assert.AreEqual("completed", (string)job["state"]);
                Assert.AreEqual(1, validations);
                Assert.AreEqual(1, invocations);
                Assert.IsTrue(validationOwned);
                Assert.IsTrue(invocationOwned);
                Assert.IsTrue(saveOwned);
                Assert.AreEqual(2, ownedSaves, "The invocation marker and final completion save must both retain the lease.");
                yield return null;
                Assert.AreEqual(1, invocations, "Later updates must never replay native entry.");
                AssertSharedIsReleased();
            }
            finally
            {
                EditorApplication.update -= pump;
                settlement.TrySetResult(true);
            }
        }

        [TestCase("cancel")]
        [TestCase("validation_error")]
        [TestCase("native_error")]
        [TestCase("save_error")]
        [TestCase("final_barrier")]
        public void ActualSharedLeaseReleasesAfterEverySettlement(string outcome)
        {
            JObject job = Queued();
            int invokes = 0;
            int barriers = 0;
            bool validationOwned = false;
            bool invocationOwned = false;
            bool saveOwned = false;
            Action run = () =>
                Advance(
                    job,
                    () =>
                    {
                        validationOwned = ExclusiveIsHeld();
                        if (outcome == "cancel")
                            job["state"] = "cancelled";
                        if (outcome == "validation_error")
                            throw new InvalidOperationException("validation failed");
                    },
                    () =>
                    {
                        invokes++;
                        invocationOwned = ExclusiveIsHeld();
                        throw new InvalidOperationException("native completion uncertain");
                    },
                    () => ++barriers == 2 && outcome == "final_barrier" ? "native_job_active" : null,
                    () =>
                    {
                        saveOwned = ExclusiveIsHeld();
                        if (outcome == "save_error")
                            throw new InvalidOperationException("checkpoint write failed");
                    }
                );
            if (outcome == "save_error")
                Assert.Throws<InvalidOperationException>(() => run());
            else
                run();
            Assert.IsTrue(validationOwned);
            Assert.AreEqual(outcome == "native_error" ? 1 : 0, invokes);
            if (invokes > 0)
                Assert.IsTrue(invocationOwned);
            if (outcome != "cancel")
                Assert.IsTrue(saveOwned);
            AssertSharedIsReleased();

            JObject restored = (JObject)Restore(job)[0];
            if (outcome == "final_barrier")
                Assert.IsTrue(Advance(restored, () => { }, () => invokes++));
            else
                Assert.IsFalse(Advance(restored, () => Assert.Fail("validation replay"), () => Assert.Fail("native replay")));
            AssertSharedIsReleased();
        }

        [Test]
        public void RepeatedReloadOfQueuedJobPreservesIdDeadlineAndInvokesOnce()
        {
            JObject initial = Queued();
            JObject job = (JObject)Restore((JObject)Restore(initial)[0])[0];
            Assert.AreEqual("owned", (string)job["job_id"]);
            Assert.AreEqual((string)initial["deadline_utc"], (string)job["deadline_utc"]);
            int count = 0;
            Assert.IsTrue(Advance(job, () => { }, () => count++));
            Assert.IsFalse(Advance(job, () => { }, () => count++));
            Assert.AreEqual(1, count);
        }

        [Test]
        public void CancelledAcrossReloadNeverInvokes()
        {
            JObject job = Queued();
            job["state"] = "cancelled";
            job = (JObject)Restore(job)[0];
            Assert.IsFalse(Advance(job, () => Assert.Fail("validation"), () => Assert.Fail("bake")));
            Assert.AreEqual("cancelled", (string)job["state"]);
        }

        [Test]
        public void StartedAndUncertainJobsNeverReplay()
        {
            foreach (string state in new[] { "queued", "running" })
            {
                JObject job = Queued();
                job["state"] = state;
                job["native_invoked"] = true;
                job = (JObject)Restore(job)[0];
                Assert.AreEqual("interrupted", (string)job["state"]);
                Assert.IsFalse(Advance(job, () => Assert.Fail("validation"), () => Assert.Fail("bake")));
            }
        }

        [Test]
        public void ChangedMissingTargetOrConfigurationRefusesWithoutInvocation()
        {
            JObject job = (JObject)Restore(Queued())[0];
            int count = 0;
            Assert.IsFalse(Advance(job, () => throw new InvalidOperationException("target/configuration changed"), () => count++));
            Assert.AreEqual("failed", (string)job["state"]);
            Assert.AreEqual(false, (bool)job["native_invoked"]);
            Assert.AreEqual(0, count);
        }

        [Test]
        public void ForeignOwnerAtFinalBarrierWaitsThenInvokesOnce()
        {
            JObject job = Queued();
            int count = 0;
            int calls = 0;
            Assert.IsFalse(Advance(job, () => { }, () => count++, () => ++calls == 2 ? "foreign_owner" : null));
            Assert.AreEqual("queued", (string)job["state"]);
            Assert.IsTrue(Advance(job, () => { }, () => count++));
            Assert.AreEqual(1, count);
        }

        [Test]
        public void MarkerIsPersistedBeforeNativeInvocationAndReloadCannotRepeat()
        {
            JObject job = Queued();
            string saved = null;
            Assert.IsTrue(
                Advance(
                    job,
                    () => { },
                    () =>
                    {
                        Assert.IsTrue(ExclusiveIsHeld());
                        JObject durable = (JObject)JArray.Parse(saved)[0];
                        Assert.AreEqual(true, (bool)durable["native_invoked"]);
                        Assert.AreEqual("interrupted", (string)((JArray)Call("Restore", saved))[0]["state"]);
                    },
                    save: () => saved = new JArray(job).ToString()
                )
            );
            AssertSharedIsReleased();
        }

        [Test]
        public void DeadlineIsNotExtendedByReloadOrForeignOwner()
        {
            JObject job = Queued();
            job["deadline_utc"] = DateTime.UtcNow.AddSeconds(-1).ToString("O");
            job = (JObject)Restore(job)[0];
            Assert.IsFalse(Advance(job, () => Assert.Fail("validation"), () => Assert.Fail("bake"), () => "foreign_owner"));
            Assert.AreEqual("failed", (string)job["state"]);
            StringAssert.Contains("deadline", (string)job["detail"]);
        }

        [Test]
        public void CancellationDuringValidationDoesNotEnterNativeBake()
        {
            JObject job = Queued();
            int count = 0;
            Assert.IsFalse(Advance(job, () => job["state"] = "cancelled", () => count++));
            Assert.AreEqual("cancelled", (string)job["state"]);
            Assert.AreEqual(0, count);
        }

        [Test]
        public void CheckpointWriteFailureNeverInvokesNativeBake()
        {
            JObject job = Queued();
            int count = 0;
            Assert.Throws<InvalidOperationException>(() =>
                Advance(job, () => { }, () => count++, save: () => throw new InvalidOperationException("storage unavailable"))
            );
            Assert.AreEqual(0, count);
            Assert.IsFalse(Advance((JObject)Restore(job)[0], () => Assert.Fail("validation"), () => Assert.Fail("bake")));
        }

        [Test]
        public void MalformedCheckpointsFailClosedAndRemainBounded()
        {
            foreach (string raw in new[] { "{", new string('x', 262145), "[1]", "[] []" })
            {
                JArray jobs = (JArray)Call("Restore", raw);
                Assert.AreEqual(1, jobs.Count);
                Assert.AreEqual("unavailable", (string)jobs[0]["state"]);
            }
            JObject job = Queued();
            job["native_invoked"] = "false";
            Assert.AreEqual("interrupted", (string)Restore(job)[0]["state"]);
            job = Queued();
            job["checkpoint"] = null;
            Assert.AreEqual("interrupted", (string)Restore(job)[0]["state"]);
            job = Queued();
            job["checkpoint"]["version"] = "1";
            Assert.AreEqual("interrupted", (string)Restore(job)[0]["state"]);
        }
    }
}
