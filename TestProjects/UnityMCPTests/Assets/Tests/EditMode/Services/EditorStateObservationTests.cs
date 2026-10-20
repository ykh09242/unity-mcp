using System;
using System.Reflection;
using MCPForUnity.Editor.Services;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using Unity.Profiling;
using UnityEditor;

namespace MCPForUnityTests.Editor.Services
{
    // The nested JSON helper has no Unity dependency or outer-cache initialization.
    public class EditorStateObservationTests
    {
        [Test]
        public void ObservationRefreshPreservesSequenceActivityAndEveryOtherField()
        {
            var snapshot = JObject.Parse(
                @"{""schema_version"":""unity-mcp/editor_state@2"",""observed_at_unix_ms"":100,""sequence"":7,""activity"":{""phase"":""idle"",""since_unix_ms"":90,""reasons"":[""tick""]},""tests"":{""mode"":null},""settings"":{""batch_execute_max_commands"":25}}"
            );
            var before = (JObject)snapshot.DeepClone();
            Observe(snapshot, 5000);
            Assert.AreEqual(5000, snapshot.Value<long>("observed_at_unix_ms"));
            before.Remove("observed_at_unix_ms");
            var after = (JObject)snapshot.DeepClone();
            after.Remove("observed_at_unix_ms");
            Assert.IsTrue(JToken.DeepEquals(before, after));
        }

        [Test]
        public void ObservationRefreshRetainsCachedContentObjects()
        {
            var snapshot = new JObject
            {
                ["observed_at_unix_ms"] = 1,
                ["editor"] = new JObject { ["is_focused"] = true },
            };
            var editor = snapshot["editor"];
            Observe(snapshot, 2);
            Assert.AreSame(editor, snapshot["editor"], "An unchanged observation must not rebuild content.");
        }

        [Test]
        public void ObservationRefreshDoesNotMutatePreviouslyReturnedClone()
        {
            var snapshot = new JObject { ["observed_at_unix_ms"] = 10, ["sequence"] = 3 };
            var returned = (JObject)snapshot.DeepClone();
            Observe(snapshot, 20);
            Assert.AreEqual(10, returned.Value<long>("observed_at_unix_ms"));
            Assert.AreEqual(3, snapshot.Value<long>("sequence"));
        }

        [TestCase(PlayModeStateChange.ExitingEditMode, true)]
        [TestCase(PlayModeStateChange.EnteredPlayMode, false)]
        [TestCase(PlayModeStateChange.ExitingPlayMode, true)]
        [TestCase(PlayModeStateChange.EnteredEditMode, false)]
        public void PlayTransitionEdgesSurviveUntilTheMatchingEnteredEvent(PlayModeStateChange state, bool changing)
        {
            const string key = "MCPForUnity.EditorState.PlayTransition";
            bool prior = SessionState.GetBool(key, false);
            var handler = typeof(EditorStateCache).GetMethod("OnPlayModeStateChanged", BindingFlags.NonPublic | BindingFlags.Static);
            try
            {
                handler.Invoke(null, new object[] { state });
                Assert.That(EditorStateCache.GetSnapshot()["editor"]["play_mode"]["is_changing"].Value<bool>(), Is.EqualTo(changing));
            }
            finally
            {
                SessionState.SetBool(key, prior);
                typeof(EditorStateCache)
                    .GetMethod("ForceUpdate", BindingFlags.NonPublic | BindingFlags.Static)
                    .Invoke(null, new object[] { "restore-transition-test", null });
            }
        }

        [Test]
        public void WarmHeartbeatDoesNotCloneUnchangedContent()
        {
            var notify = (Action)
                Delegate.CreateDelegate(typeof(Action), typeof(EditorStateCache).GetMethod("NotifyObservers", BindingFlags.NonPublic | BindingFlags.Static));
            using (EditorStateCache.Subscribe(_ => { }))
            {
                notify();
                using (
                    var recorder = ProfilerRecorder.StartNew(
                        ProfilerCategory.Memory,
                        "GC.Alloc",
                        32768,
                        ProfilerRecorderOptions.StartImmediately | ProfilerRecorderOptions.CollectOnlyOnCurrentThread
                    )
                )
                {
                    for (int i = 0; i < 100; i++)
                        notify();
                    recorder.Stop();
                    TestContext.WriteLine("Heartbeat allocation events for 100 unchanged observations: " + recorder.Count);
                    Assert.LessOrEqual(recorder.Count, 400, "Unchanged heartbeat delivery must not clone the state tree.");
                }
            }
        }

        [Test]
        public void ImmutableWireFramesPreserveAllFieldsAndIndependentObservationTimes()
        {
            var snapshot = new JObject
            {
                ["schema_version"] = "unity-mcp/editor_state@2",
                ["sequence"] = 9223372036854775806L,
                ["observed_at_unix_ms"] = 1,
                ["editor"] = new JObject { ["scene"] = "quoted \"name\"" },
                ["activity"] = new JObject { ["since_unix_ms"] = 5 },
                ["compilation"] = JValue.CreateNull(),
            };
            string epoch = "epoch\"quoted";
            var content = new EditorStateFrame.Content(epoch, snapshot);
            var first = content.Observe(100);
            var second = content.Observe(200);
            snapshot["editor"]["scene"] = "mutated";
            foreach (var frame in new[] { first, second })
            {
                var wire = JObject.Parse(frame.ToJson());
                Assert.AreEqual("editor_state", (string)wire["type"]);
                Assert.AreEqual(epoch, (string)wire["epoch"]);
                Assert.AreEqual(9223372036854775806L, (long)wire["sequence"]);
                Assert.AreEqual(frame.ObservedAtUnixMs, (long)wire["observed_at_unix_ms"]);
                Assert.AreEqual(frame.ObservedAtUnixMs, (long)wire["state"]["observed_at_unix_ms"]);
                Assert.AreEqual("quoted \"name\"", (string)wire["state"]["editor"]["scene"]);
                Assert.AreEqual(5, (int)wire["state"]["activity"]["since_unix_ms"]);
                Assert.AreEqual(JTokenType.Null, wire["state"]["compilation"].Type);
            }
            Assert.AreEqual(100, first.ObservedAtUnixMs);
        }

        private static void Observe(JObject snapshot, long time)
        {
            var helper = typeof(EditorStateCache).GetNestedType("SnapshotObservation", BindingFlags.NonPublic);
            Assert.IsNotNull(helper);
            helper.GetMethod("UpdateTimestamp", BindingFlags.NonPublic | BindingFlags.Static).Invoke(null, new object[] { snapshot, time });
        }

        [Test]
        public void ContentRebuildRetainsStartOfUnchangedActivity()
        {
            var cache = typeof(EditorStateCache);
            var build = cache.GetMethod("BuildSnapshot", BindingFlags.NonPublic | BindingFlags.Static);
            var since = cache.GetField("_activitySinceUnixMs", BindingFlags.NonPublic | BindingFlags.Static);
            build.Invoke(null, new object[] { "activity-test-start" });
            long prior = (long)since.GetValue(null);
            long started = prior - 60000;
            try
            {
                since.SetValue(null, started);
                var rebuilt = (JObject)build.Invoke(null, new object[] { "different-content-reason" });
                Assert.AreEqual(
                    started,
                    rebuilt["activity"]["since_unix_ms"].Value<long>(),
                    "Unrelated snapshot updates must not conceal prolonged editor activity."
                );
            }
            finally
            {
                since.SetValue(null, prior);
            }
        }

        [Test]
        public void ChangedActivityStartsANewDuration()
        {
            var cache = typeof(EditorStateCache);
            var build = cache.GetMethod("BuildSnapshot", BindingFlags.NonPublic | BindingFlags.Static);
            var phase = cache.GetField("_lastTrackedActivityPhase", BindingFlags.NonPublic | BindingFlags.Static);
            var since = cache.GetField("_activitySinceUnixMs", BindingFlags.NonPublic | BindingFlags.Static);
            var initial = (JObject)build.Invoke(null, new object[] { "activity-test-start" });
            string priorPhase = (string)phase.GetValue(null);
            long priorSince = (long)since.GetValue(null);
            try
            {
                phase.SetValue(null, "previous-activity");
                since.SetValue(null, 1L);
                var rebuilt = (JObject)build.Invoke(null, new object[] { "changed-phase" });
                Assert.AreEqual(rebuilt["observed_at_unix_ms"].Value<long>(), rebuilt["activity"]["since_unix_ms"].Value<long>());
            }
            finally
            {
                phase.SetValue(null, priorPhase);
                since.SetValue(null, priorSince);
            }
        }
    }
}
