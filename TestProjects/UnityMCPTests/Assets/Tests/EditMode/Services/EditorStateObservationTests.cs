using System.Reflection;
using MCPForUnity.Editor.Services;
using Newtonsoft.Json.Linq;
using NUnit.Framework;

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
