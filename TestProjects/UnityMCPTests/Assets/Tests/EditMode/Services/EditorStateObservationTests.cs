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
    }
}
