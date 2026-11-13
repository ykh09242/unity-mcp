using System;
using System.Linq;
using System.Reflection;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEditor.SceneManagement;
using UnityEngine;
using UnityEngine.SceneManagement;
using EditorSelection = UnityEditor.Selection;
using NativeSelection = MCPForUnity.Editor.Resources.Editor.Selection;

namespace MCPForUnityTests.Editor.Tools
{
    public class SelectionPagingTests
    {
        private UnityEngine.Object[] previousSelection;
        private UnityEngine.Object previousActive;
        private Scene previousScene;
        private Scene scene;
        private FieldInfo observation;
        private object previousObservation;
        private int described;

        [SetUp]
        public void SetUp()
        {
            described = 0;
            previousSelection = EditorSelection.objects;
            previousActive = EditorSelection.activeObject;
            previousScene = SceneManager.GetActiveScene();
            scene = EditorSceneManager.NewScene(NewSceneSetup.EmptyScene, NewSceneMode.Additive);
            observation = typeof(NativeSelection).GetField("DescriptionObserved", BindingFlags.NonPublic | BindingFlags.Static);
            previousObservation = observation.GetValue(null);
            observation.SetValue(null, (Action)(() => described++));
            EditorSelection.objects = Array.Empty<UnityEngine.Object>();
        }

        [TearDown]
        public void TearDown()
        {
            observation.SetValue(null, previousObservation);
            EditorSceneManager.CloseScene(scene, true);
            if (previousScene.IsValid() && previousScene.isLoaded)
                SceneManager.SetActiveScene(previousScene);
            EditorSelection.objects = previousSelection;
            EditorSelection.activeObject = previousActive;
        }

        private GameObject[] Select(int count)
        {
            var selected = new GameObject[count];
            for (int i = 0; i < count; i++)
            {
                selected[i] = new GameObject("Selection fixture " + i);
                SceneManager.MoveGameObjectToScene(selected[i], scene);
            }
            EditorSelection.objects = selected;
            return selected;
        }

        private static JObject Read(JObject parameters = null)
        {
            parameters ??= new JObject();
            parameters["paged"] = true;
            return JObject.FromObject(NativeSelection.HandleCommand(parameters));
        }

        [Test]
        public void TenThousandSelectionsDescribeOnlyRequestedPageAndCountsOnlyDescribesNone()
        {
            // Given a large live Editor selection, not a synthetic transport response.
            Select(10000);
            // When only counts and active metadata are requested.
            var counts = Read(new JObject { ["fields"] = "none" });
            Assert.IsTrue(counts.Value<bool>("success"));
            Assert.AreEqual(10000, counts["data"].Value<int>("objectCount"));
            Assert.AreEqual(10000, counts["data"].Value<int>("gameObjectCount"));
            Assert.AreEqual(0, described);
            Assert.IsNull(counts["data"]["objects"]);
            Assert.IsFalse(counts["data"].Value<bool>("hasMore"));

            // Then each continuation describes only its seven requested rows.
            var first = Read(new JObject { ["page_size"] = 7 });
            Assert.IsTrue(first.Value<bool>("success"));
            Assert.AreEqual(7, described);
            var firstRows = (JArray)first["data"]["objects"];
            Assert.AreEqual(7, firstRows.Count);
            Assert.IsNull(first["data"]["gameObjects"]);
            string cursor = first["data"].Value<string>("nextCursor");
            var second = Read(new JObject { ["page_size"] = 7, ["cursor"] = cursor });
            Assert.IsTrue(second.Value<bool>("success"));
            Assert.AreEqual(14, described);
            Assert.AreEqual(7, second["data"].Value<int>("offset"));
            Assert.IsEmpty(
                firstRows.Select(row => row.Value<int>("instanceID")).Intersect(((JArray)second["data"]["objects"]).Select(row => row.Value<int>("instanceID")))
            );
        }

        [TestCase(false)]
        [TestCase(true)]
        public void SelectionChangeAndChangedPageOptionsInvalidateCursor(bool delayEvent)
        {
            // Given a cursor for a particular projection and selection revision.
            var selected = Select(3);
            var first = Read(new JObject { ["page_size"] = 1 });
            string cursor = first["data"].Value<string>("nextCursor");
            var changedOptions = Read(new JObject { ["page_size"] = 2, ["cursor"] = cursor });
            Assert.AreEqual("stale_cursor", changedOptions.Value<string>("error"));
            var changedFields = Read(
                new JObject
                {
                    ["page_size"] = 1,
                    ["fields"] = "all",
                    ["cursor"] = cursor,
                }
            );
            Assert.AreEqual("stale_cursor", changedFields.Value<string>("error"));
            var invalidate = (Action)
                Delegate.CreateDelegate(typeof(Action), typeof(NativeSelection).GetMethod("InvalidatePages", BindingFlags.NonPublic | BindingFlags.Static));
            if (delayEvent)
                EditorSelection.selectionChanged -= invalidate;
            try
            {
                // When selection changes but retains the same number of objects, even
                // before the resource receives the Editor's selectionChanged event.
                var replacement = new GameObject("Replacement");
                SceneManager.MoveGameObjectToScene(replacement, scene);
                EditorSelection.objects = new UnityEngine.Object[] { selected[2], selected[1], replacement };
                var changed = Read(new JObject { ["page_size"] = 1, ["cursor"] = cursor });
                // Then the previous cursor cannot mix the old and new selection.
                Assert.AreEqual("stale_cursor", changed.Value<string>("error"));
                Assert.AreNotEqual(first["data"].Value<string>("selectionRevision"), Read()["data"].Value<string>("selectionRevision"));
            }
            finally
            {
                if (delayEvent)
                    EditorSelection.selectionChanged += invalidate;
            }
        }

        [Test]
        public void ExpiredEvictedAndUnknownSessionCursorsRequireRestart()
        {
            // Given a live cursor, its bounded record expires after 30 seconds.
            Select(2);
            string expired = Read(new JObject { ["page_size"] = 1 })["data"].Value<string>("nextCursor");
            var cursors = (System.Collections.IDictionary)
                typeof(NativeSelection).GetField("Cursors", BindingFlags.NonPublic | BindingFlags.Static).GetValue(null);
            object record = cursors[expired];
            record.GetType().GetField("Created", BindingFlags.NonPublic | BindingFlags.Instance).SetValue(record, -100d);
            Assert.AreEqual("stale_cursor", Read(new JObject { ["page_size"] = 1, ["cursor"] = expired }).Value<string>("error"));
            // When more than 64 outstanding page cursors are created, old records are evicted.
            string evicted = Read(new JObject { ["page_size"] = 1 })["data"].Value<string>("nextCursor");
            for (int i = 0; i < 64; i++)
                Read(new JObject { ["page_size"] = 1 });
            Assert.LessOrEqual(cursors.Count, 64);
            Assert.AreEqual("stale_cursor", Read(new JObject { ["page_size"] = 1, ["cursor"] = evicted }).Value<string>("error"));
            // Then a token absent from this native session is also rejected.
            Assert.AreEqual("stale_cursor", Read(new JObject { ["page_size"] = 1, ["cursor"] = new string('f', 32) }).Value<string>("error"));
        }

        [Test]
        public void EmptyAndProjectedPagesPreserveLegacySelection()
        {
            // Given an empty selection.
            var empty = Read();
            Assert.IsTrue(empty.Value<bool>("success"));
            Assert.AreEqual(0, ((JArray)empty["data"]["objects"]).Count);
            Assert.IsNull(empty["data"].Value<string>("nextCursor"));
            Select(3);
            // When all collections are requested, each has an independent page bound.
            var page = Read(new JObject { ["fields"] = "all", ["page_size"] = 2 });
            Assert.AreEqual(2, ((JArray)page["data"]["objects"]).Count);
            Assert.AreEqual(2, ((JArray)page["data"]["gameObjects"]).Count);
            Assert.AreEqual(0, ((JArray)page["data"]["assetGUIDs"]).Count);
            Assert.AreEqual(4, described);
            // Then the legacy command still returns its complete three collections.
            var legacy = JObject.FromObject(NativeSelection.HandleCommand(new JObject()));
            Assert.AreEqual(3, ((JArray)legacy["data"]["objects"]).Count);
            Assert.AreEqual(3, ((JArray)legacy["data"]["gameObjects"]).Count);
            Assert.IsNull(legacy["data"]["selectionRevision"]);
        }

        [TestCase(0)]
        [TestCase(501)]
        [TestCase(true)]
        [TestCase("1.5")]
        public void NativePageBoundsRejectInvalidRequestsBeforeDescriptions(object size)
        {
            // Given an invalid direct native page request, no description should run.
            var result = Read(new JObject { ["page_size"] = JToken.FromObject(size) });
            Assert.AreEqual("invalid_selection_page", result.Value<string>("error"));
            Assert.AreEqual(0, described);
        }
    }
}
