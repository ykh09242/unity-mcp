using System;
using System.Reflection;
using Newtonsoft.Json.Linq;
using NUnit.Framework;

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
                        JObject durable = (JObject)JArray.Parse(saved)[0];
                        Assert.AreEqual(true, (bool)durable["native_invoked"]);
                        Assert.AreEqual("interrupted", (string)((JArray)Call("Restore", saved))[0]["state"]);
                    },
                    save: () => saved = new JArray(job).ToString()
                )
            );
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
