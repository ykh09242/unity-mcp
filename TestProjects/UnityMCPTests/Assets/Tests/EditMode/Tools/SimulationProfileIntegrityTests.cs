using System;
using System.Collections.Generic;
using System.Linq;
using System.Reflection;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEditor;
using UnityEngine;
using Physics3D = UnityEngine.Physics;

namespace MCPForUnityTests.EditMode.Tools
{
    [Parallelizable(ParallelScope.None)]
    public class SimulationProfileIntegrityTests
    {
        private float timeScale;
        private float fixedDelta;
        private float maximumDelta;
        private float volume;
        private bool paused;
        private Vector3 gravity;
        private readonly List<string> ownedNames = new List<string>();
        private readonly List<string> ownedReceipts = new List<string>();
        private FieldInfo faultHook;
        private object priorHook;

        [SetUp]
        public void SetUp()
        {
            Assert.IsFalse(EditorApplication.isPlayingOrWillChangePlaymode, "Global profile fixture requires stable Edit mode.");
            timeScale = Time.timeScale;
            fixedDelta = Time.fixedDeltaTime;
            maximumDelta = Time.maximumDeltaTime;
            volume = AudioListener.volume;
            paused = AudioListener.pause;
            gravity = Physics3D.gravity;
            faultHook = null;
            priorHook = null;
            ownedNames.Clear();
            ownedReceipts.Clear();
        }

        [TearDown]
        public void TearDown()
        {
            if (faultHook != null)
                faultHook.SetValue(null, priorHook);
            if (
                typeof(MCPForUnity.Editor.Tools.ManagePackages).Assembly.GetType("MCPForUnity.Editor.Tools.SimulationProfiles.ManageSimulationProfiles") != null
            )
                foreach (string id in ownedReceipts)
                    Send("restore", new JObject { ["receipt_id"] = id });
            Time.timeScale = timeScale;
            SetTimesteps(fixedDelta, maximumDelta);
            AudioListener.volume = volume;
            AudioListener.pause = paused;
            Physics3D.gravity = gravity;
            if (
                typeof(MCPForUnity.Editor.Tools.ManagePackages).Assembly.GetType("MCPForUnity.Editor.Tools.SimulationProfiles.ManageSimulationProfiles") != null
            )
                foreach (string name in ownedNames)
                    Send("delete", new JObject { ["name"] = name });
        }

        [Test]
        public void NamedCaptureDiffApplyReadbackAndRestore()
        {
            string name = Capture(
                new JObject
                {
                    ["time.timeScale"] = 0.5f,
                    ["physics3d.gravity"] = new JArray(0, -4, 0),
                    ["audio.volume"] = 0.4f,
                }
            );
            JObject diff = Send("diff", new JObject { ["name"] = name });
            Assert.IsTrue((bool)diff["success"], diff.ToString());
            Assert.IsTrue(diff["data"]["changes"].HasValues);
            JObject applied = Apply(name);
            Assert.IsTrue((bool)applied["success"], applied.ToString());
            Assert.AreEqual(0.5f, Time.timeScale);
            Assert.AreEqual(new Vector3(0, -4, 0), Physics3D.gravity);
            Assert.AreEqual(0.4f, AudioListener.volume);
            Assert.AreEqual(fixedDelta, Time.fixedDeltaTime, "A profile does not implicitly rescale unrelated Time settings.");
            JObject restored = Send("restore", new JObject { ["receipt_id"] = applied["data"]["receipt_id"] });
            Assert.IsTrue((bool)restored["success"], restored.ToString());
            Assert.AreEqual(timeScale, Time.timeScale);
            Assert.AreEqual(gravity, Physics3D.gravity);
            Assert.AreEqual(volume, AudioListener.volume);
        }

        [TestCase(0.02f, 0.333f, 0.5f, 1f, TestName = "TimestepsIncreaseAcrossCurrentCeilingAndRestore")]
        [TestCase(0.5f, 1f, 0.02f, 0.333f, TestName = "TimestepsDecreaseAcrossCurrentFloorAndRestore")]
        public void TimestepsCrossBoundaryAndRestore(float beforeFixed, float beforeMaximum, float desiredFixed, float desiredMaximum)
        {
            try
            {
                SetTimesteps(beforeFixed, beforeMaximum);
                float nativeBeforeFixed = Time.fixedDeltaTime;
                float nativeBeforeMaximum = Time.maximumDeltaTime;
                string name = Capture(new JObject { ["time.fixedDeltaTime"] = desiredFixed, ["time.maximumDeltaTime"] = desiredMaximum });
                JObject applied = Apply(name);
                Assert.IsTrue((bool)applied["success"], applied.ToString());
                Assert.That(Time.fixedDeltaTime, Is.EqualTo(desiredFixed).Within(0.0000001f));
                Assert.That(Time.maximumDeltaTime, Is.EqualTo(desiredMaximum).Within(0.0000001f));
                Assert.AreEqual(Time.fixedDeltaTime, (float)Outcome(applied, "time.fixedDeltaTime")["observed"]);
                Assert.AreEqual(Time.maximumDeltaTime, (float)Outcome(applied, "time.maximumDeltaTime")["observed"]);
                JObject restored = Send("restore", new JObject { ["receipt_id"] = applied["data"]["receipt_id"] });
                Assert.IsTrue((bool)restored["success"], restored.ToString());
                Assert.AreEqual("restored", (string)restored["data"]["state"]);
                Assert.That(Time.fixedDeltaTime, Is.EqualTo(nativeBeforeFixed).Within(0.0000001f));
                Assert.That(Time.maximumDeltaTime, Is.EqualTo(nativeBeforeMaximum).Within(0.0000001f));
            }
            finally
            {
                SetTimesteps(fixedDelta, maximumDelta);
            }
        }

        [TestCase("time.fixedDeltaTime", 0.5f, TestName = "PartialFixedTimestepAboveCurrentMaximumRefusesBeforeWrites")]
        [TestCase("time.maximumDeltaTime", 0.01f, TestName = "PartialMaximumTimestepBelowCurrentFixedRefusesBeforeWrites")]
        public void PartialInvalidTimestepRefusesBeforeWrites(string key, float value)
        {
            try
            {
                SetTimesteps(0.02f, 0.333f);
                float nativeBeforeFixed = Time.fixedDeltaTime;
                float nativeBeforeMaximum = Time.maximumDeltaTime;
                string name = Capture(new JObject());
                Type store = typeof(MCPForUnity.Editor.Tools.ManagePackages).Assembly.GetType(
                    "MCPForUnity.Editor.Tools.SimulationProfiles.SimulationProfileStore"
                );
                var profiles = (JObject)store.GetProperty("Profiles", BindingFlags.NonPublic | BindingFlags.Static).GetValue(null);
                var profile = (JObject)profiles[name];
                // Partial records are accepted by the persisted profile schema; retain a valid revision.
                profile["settings"] = new JObject { [key] = value, ["time.timeScale"] = 0.25f };
                profile["revision"] = (string)
                    store
                        .GetMethod("Hash", BindingFlags.NonPublic | BindingFlags.Static)
                        .Invoke(
                            null,
                            new object[]
                            {
                                new JObject { ["name"] = name, ["settings"] = profile["settings"].DeepClone() },
                            }
                        );
                JObject validated = Send("validate", new JObject { ["name"] = name });
                int writes = 0;
                Fault(_ => writes++);
                JObject result = Send(
                    "apply",
                    new JObject
                    {
                        ["name"] = name,
                        ["profile_revision"] = validated["data"]["profile_revision"],
                        ["current_revision"] = validated["data"]["current_revision"],
                    }
                );
                string receipt = (string)result["data"]?["receipt_id"];
                if (receipt != null)
                    ownedReceipts.Add(receipt);
                Assert.IsFalse((bool)result["success"], result.ToString());
                Assert.AreEqual(0, writes, "An invalid effective timestep pair must be rejected before any setting write.");
                Assert.IsFalse((bool)validated["data"]["applicable"], validated.ToString());
                Assert.AreEqual(nativeBeforeFixed, Time.fixedDeltaTime);
                Assert.AreEqual(nativeBeforeMaximum, Time.maximumDeltaTime);
                Assert.AreEqual(timeScale, Time.timeScale);
            }
            finally
            {
                Fault(null);
                SetTimesteps(fixedDelta, maximumDelta);
                Time.timeScale = timeScale;
            }
        }

        [Test]
        public void TimestepReadbackRetainsExactObservationAndPreservesNearbyExternalChange()
        {
            try
            {
                SetTimesteps(0.5f, 1f);
                string name = Capture(new JObject { ["time.fixedDeltaTime"] = 0.02f, ["time.maximumDeltaTime"] = 0.333f });
                JObject applied = Apply(name);
                Assert.IsTrue((bool)applied["success"], applied.ToString());
                float observed = Time.fixedDeltaTime;
                Assert.AreEqual(observed, (float)Outcome(applied, "time.fixedDeltaTime")["observed"]);
                Time.fixedDeltaTime = observed + 0.00000005f;
                float external = Time.fixedDeltaTime;
                Assert.AreNotEqual(observed, external, "The external writer must make an actual native change.");
                Assert.That(Math.Abs(external - observed), Is.LessThan(0.0000001f), "Drift is deliberately inside setter readback tolerance.");
                JObject restored = Send("restore", new JObject { ["receipt_id"] = applied["data"]["receipt_id"] });
                Assert.IsTrue((bool)restored["success"], restored.ToString());
                Assert.AreEqual(external, Time.fixedDeltaTime, "Readback tolerance must never weaken the exact ownership comparison.");
                Assert.AreEqual("external_change_preserved", (string)Outcome(restored, "time.fixedDeltaTime")["status"]);
            }
            finally
            {
                SetTimesteps(fixedDelta, maximumDelta);
            }
        }

        [Test]
        public void OutOfRangeTimestepContextFinalizesReceiptRestoresOtherDomainAndRetries()
        {
            try
            {
                SetTimesteps(0.02f, 0.333f);
                float nativeBeforeFixed = Time.fixedDeltaTime;
                float nativeBeforeMaximum = Time.maximumDeltaTime;
                string name = Capture(
                    new JObject
                    {
                        ["time.fixedDeltaTime"] = 0.125f,
                        ["time.maximumDeltaTime"] = 0.5f,
                        ["audio.volume"] = volume == 0.4f ? 0.6f : 0.4f,
                    }
                );
                JObject applied = Apply(name);
                Assert.IsTrue((bool)applied["success"], applied.ToString());
                float appliedFixed = Time.fixedDeltaTime;
                float appliedMaximum = Time.maximumDeltaTime;
                Time.maximumDeltaTime = 20f;
                Assert.Greater(Time.maximumDeltaTime, 10f, "Exercise a native value outside the tool's supported bound.");
                JObject restored = Send("restore", new JObject { ["receipt_id"] = applied["data"]["receipt_id"] });
                Assert.IsFalse((bool)restored["success"], restored.ToString());
                Assert.AreEqual("partial_restore", (string)restored["data"]["state"]);
                Assert.IsTrue((bool)restored["data"]["receipt_persisted"]);
                Assert.AreEqual(volume, AudioListener.volume, "Unavailable timestep planning must not block independent confirmed settings.");
                Assert.AreEqual(appliedFixed, Time.fixedDeltaTime);
                Assert.AreEqual(20f, Time.maximumDeltaTime);
                Assert.AreEqual("applied", (string)Outcome(restored, "time.fixedDeltaTime")["status"]);
                Assert.IsNotNull(Outcome(restored, "time.fixedDeltaTime")["restore_error"]);
                JObject retained = Send("receipt", new JObject { ["receipt_id"] = applied["data"]["receipt_id"] });
                Assert.AreEqual("partial_restore", (string)retained["data"]["state"]);
                Time.maximumDeltaTime = appliedMaximum;
                JObject retried = Send("restore", new JObject { ["receipt_id"] = applied["data"]["receipt_id"] });
                Assert.IsTrue((bool)retried["success"], retried.ToString());
                Assert.That(Time.fixedDeltaTime, Is.EqualTo(nativeBeforeFixed).Within(0.0000001f));
                Assert.That(Time.maximumDeltaTime, Is.EqualTo(nativeBeforeMaximum).Within(0.0000001f));
            }
            finally
            {
                SetTimesteps(fixedDelta, maximumDelta);
                AudioListener.volume = volume;
            }
        }

        private static JObject Outcome(JObject response, string key) =>
            ((JArray)response["data"]["outcomes"]).OfType<JObject>().Single(row => (string)row["key"] == key);

        private static void SetTimesteps(float fixedValue, float maximumValue)
        {
            if (fixedValue > Time.maximumDeltaTime)
                Time.maximumDeltaTime = maximumValue;
            Time.fixedDeltaTime = fixedValue;
            Time.maximumDeltaTime = maximumValue;
        }

        [Test]
        public void InvalidAndStaleProfilesRefuseBeforeGlobalWrites()
        {
            string invalidName = Name();
            JObject invalid = Send(
                "capture",
                new JObject
                {
                    ["name"] = invalidName,
                    ["domains"] = new JArray("time"),
                    ["settings"] = new JObject { ["time.timeScale"] = -1 },
                }
            );
            Assert.IsFalse((bool)invalid["success"], invalid.ToString());
            Assert.AreEqual(timeScale, Time.timeScale);
            string name = Capture(new JObject { ["time.timeScale"] = 0.5f });
            JObject validated = Send("validate", new JObject { ["name"] = name });
            JObject stale = Send(
                "apply",
                new JObject
                {
                    ["name"] = name,
                    ["profile_revision"] = validated["data"]["profile_revision"],
                    ["current_revision"] = "stale",
                }
            );
            Assert.IsFalse((bool)stale["success"], stale.ToString());
            StringAssert.Contains("stale_current", stale.ToString());
            Assert.AreEqual(timeScale, Time.timeScale);
        }

        [Test]
        public void RestorePreservesExternalWriterAndRestoresOtherConfirmedSettings()
        {
            string name = Capture(new JObject { ["time.timeScale"] = 0.5f, ["audio.volume"] = 0.4f });
            JObject applied = Apply(name);
            AudioListener.volume = 0.7f;
            JObject restored = Send("restore", new JObject { ["receipt_id"] = applied["data"]["receipt_id"] });
            Assert.IsTrue((bool)restored["success"], restored.ToString());
            Assert.AreEqual(timeScale, Time.timeScale);
            Assert.AreEqual(0.7f, AudioListener.volume);
            StringAssert.Contains("external_change_preserved", restored.ToString());
        }

        [Test]
        public void MidApplyFailureRetainsConfirmedChangesForConditionalRestore()
        {
            string name = Capture(new JObject { ["time.timeScale"] = 0.5f, ["physics3d.gravity"] = new JArray(0, -4, 0) });
            Fault(key =>
            {
                if (key == "physics3d.gravity")
                    throw new InvalidOperationException("Owned injected setter boundary failure.");
            });
            JObject failed = Apply(name);
            Assert.IsFalse((bool)failed["success"], failed.ToString());
            Assert.AreEqual("partial", (string)failed["data"]["state"]);
            Assert.AreEqual(0.5f, Time.timeScale, "Confirmed first write must be reported honestly after a later failure.");
            Assert.AreEqual(gravity, Physics3D.gravity);
            Fault(null);
            JObject restored = Send("restore", new JObject { ["receipt_id"] = failed["data"]["receipt_id"] });
            Assert.IsTrue((bool)restored["success"], restored.ToString());
            Assert.AreEqual(timeScale, Time.timeScale);
        }

        [Test]
        public void InterferingWriterDuringFailureIsNeverOverwrittenByRestore()
        {
            string name = Capture(new JObject { ["time.timeScale"] = 0.5f, ["physics3d.gravity"] = new JArray(0, -4, 0) });
            Fault(key =>
            {
                if (key != "physics3d.gravity")
                    return;
                Time.timeScale = 0.8f;
                throw new InvalidOperationException("Owned external-writer interference.");
            });
            JObject failed = Apply(name);
            Assert.IsFalse((bool)failed["success"], failed.ToString());
            Fault(null);
            JObject restored = Send("restore", new JObject { ["receipt_id"] = failed["data"]["receipt_id"] });
            Assert.AreEqual(0.8f, Time.timeScale);
            StringAssert.Contains("external_change_preserved", restored.ToString());
        }

        [Test]
        public void DomainCapabilitiesAndNamedProfileSurviveStoreReload()
        {
            JObject capabilities = Send("capabilities");
            Assert.IsTrue((bool)capabilities["success"], capabilities.ToString());
            foreach (string domain in new[] { "time", "physics3d", "physics2d", "audio", "input" })
                Assert.IsNotNull(capabilities["data"]["domains"][domain], domain);
            string name = Capture(new JObject { ["time.timeScale"] = 0.5f });
            Type store = typeof(MCPForUnity.Editor.Tools.ManagePackages).Assembly.GetType("MCPForUnity.Editor.Tools.SimulationProfiles.SimulationProfileStore");
            Assert.IsNotNull(store);
            store.GetMethod("Reload", BindingFlags.NonPublic | BindingFlags.Static).Invoke(null, null);
            JObject read = Send("get", new JObject { ["name"] = name });
            Assert.IsTrue((bool)read["success"], read.ToString());
            Assert.AreEqual(0.5f, (float)read["data"]["settings"]["time.timeScale"]);
        }

        [Test]
        public void RestoreFailureRetainsRemainingConfirmedRowsForRetry()
        {
            string name = Capture(new JObject { ["time.timeScale"] = 0.5f, ["physics3d.gravity"] = new JArray(0, -4, 0) });
            JObject applied = Apply(name);
            Fault(key =>
            {
                if (key == "physics3d.gravity")
                    throw new InvalidOperationException("Owned restoration boundary failure.");
            });
            JObject failed = Send("restore", new JObject { ["receipt_id"] = applied["data"]["receipt_id"] });
            Assert.IsFalse((bool)failed["success"], failed.ToString());
            Assert.AreEqual("partial_restore", (string)failed["data"]["state"]);
            Assert.AreEqual(timeScale, Time.timeScale);
            Assert.AreEqual(new Vector3(0, -4, 0), Physics3D.gravity);
            Fault(null);
            JObject retried = Send("restore", new JObject { ["receipt_id"] = applied["data"]["receipt_id"] });
            Assert.IsTrue((bool)retried["success"], retried.ToString());
            Assert.AreEqual(gravity, Physics3D.gravity);
        }

        [Test]
        public void InputDomainUsesActualBackendApplicabilityAndConditionalRoundTrip()
        {
            JObject capabilities = Send("capabilities");
            Assert.IsTrue((bool)capabilities["success"], capabilities.ToString());
            JToken input = capabilities["data"]["domains"]["input"];
            if (!(bool)input["applicable"])
            {
                JObject refusal = Send("capture", new JObject { ["name"] = Name(), ["domains"] = new JArray("input") });
                Assert.IsFalse((bool)refusal["success"], refusal.ToString());
                StringAssert.Contains("unavailable", refusal.ToString());
                return;
            }
            string baseline = Name();
            JObject captured = Send("capture", new JObject { ["name"] = baseline, ["domains"] = new JArray("input") });
            Assert.IsTrue((bool)captured["success"], captured.ToString());
            JObject values = (JObject)captured["data"]["settings"];
            string key =
                values["input.legacy.simulateMouseWithTouches"] != null ? "input.legacy.simulateMouseWithTouches" : "input.system.defaultButtonPressPoint";
            Assert.IsNotNull(values[key], capabilities.ToString());
            JToken replacement = values[key].Type == JTokenType.Boolean ? new JValue(!(bool)values[key]) : new JValue((float)values[key] == 0.4f ? 0.6f : 0.4f);
            string target = Name();
            JObject desired = Send(
                "capture",
                new JObject
                {
                    ["name"] = target,
                    ["domains"] = new JArray("input"),
                    ["settings"] = new JObject { [key] = replacement },
                }
            );
            Assert.IsTrue((bool)desired["success"], desired.ToString());
            JObject applied = Apply(target);
            Assert.IsTrue((bool)applied["success"], applied.ToString());
            JObject readback = Send("capture", new JObject { ["name"] = Name(), ["domains"] = new JArray("input") });
            Assert.AreEqual(replacement.ToString(), readback["data"]["settings"][key].ToString());
            JObject restored = Send("restore", new JObject { ["receipt_id"] = applied["data"]["receipt_id"] });
            Assert.IsTrue((bool)restored["success"], restored.ToString());
            JObject after = Send("capture", new JObject { ["name"] = Name(), ["domains"] = new JArray("input") });
            Assert.AreEqual(values[key].ToString(), after["data"]["settings"][key].ToString());
        }

        private string Name()
        {
            string name = "OwnedSimulation_" + Guid.NewGuid().ToString("N");
            ownedNames.Add(name);
            return name;
        }

        private string Capture(JObject settings)
        {
            string name = Name();
            JObject result = Send(
                "capture",
                new JObject
                {
                    ["name"] = name,
                    ["domains"] = new JArray("time", "physics3d", "audio"),
                    ["settings"] = settings,
                }
            );
            Assert.IsTrue((bool)result["success"], result.ToString());
            return name;
        }

        private JObject Apply(string name)
        {
            JObject validated = Send("validate", new JObject { ["name"] = name });
            Assert.IsTrue((bool)validated["success"], validated.ToString());
            Assert.IsTrue((bool)validated["data"]["applicable"], validated.ToString());
            JObject result = Send(
                "apply",
                new JObject
                {
                    ["name"] = name,
                    ["profile_revision"] = validated["data"]["profile_revision"],
                    ["current_revision"] = validated["data"]["current_revision"],
                }
            );
            string receipt = (string)result["data"]?["receipt_id"];
            if (receipt != null)
                ownedReceipts.Add(receipt);
            return result;
        }

        private void Fault(Action<string> callback)
        {
            if (faultHook == null)
            {
                Type apply = typeof(MCPForUnity.Editor.Tools.ManagePackages).Assembly.GetType(
                    "MCPForUnity.Editor.Tools.SimulationProfiles.SimulationProfileApply"
                );
                Assert.IsNotNull(apply);
                faultHook = apply.GetField("BeforeWrite", BindingFlags.NonPublic | BindingFlags.Static);
                Assert.IsNotNull(faultHook);
                priorHook = faultHook.GetValue(null);
            }
            faultHook.SetValue(null, callback);
        }

        private static JObject Send(string action, JObject parameters = null)
        {
            Type handler = typeof(MCPForUnity.Editor.Tools.ManagePackages).Assembly.GetType(
                "MCPForUnity.Editor.Tools.SimulationProfiles.ManageSimulationProfiles"
            );
            Assert.IsNotNull(handler, "SPEC19 requires its dedicated native simulation-profile handler.");
            JObject p = parameters ?? new JObject();
            p["action"] = action;
            return JObject.FromObject(handler.GetMethod("HandleCommand").Invoke(null, new object[] { p }));
        }
    }
}
