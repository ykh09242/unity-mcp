using MCPForUnity.Editor.Tools.Animation;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEditor;
using UnityEngine;
using static MCPForUnityTests.Editor.TestUtilities;

namespace MCPForUnityTests.Editor.Tools
{
    public class AnimationEventUndoTests
    {
        private const string Root = "Assets/Temp/AnimationEventUndoTests";
        private const string Path = Root + "/Events.anim";
        private AnimationClip clip;
        private AnimationEvent[] original;

        [SetUp]
        public void SetUp()
        {
            EnsureFolder(Root);
            clip = new AnimationClip();
            clip.SetCurve("", typeof(Transform), "localPosition.x", AnimationCurve.Linear(0f, 0f, 1f, 1f));
            AssetDatabase.CreateAsset(clip, Path);
            var reference = new AnimationClip();
            AssetDatabase.CreateAsset(reference, Root + "/Reference.anim");
            AnimationUtility.SetAnimationEvents(
                clip,
                new[]
                {
                    new AnimationEvent
                    {
                        time = 0.1f,
                        functionName = "Repeat",
                        stringParameter = "first",
                        floatParameter = 1.25f,
                        intParameter = 7,
                        objectReferenceParameter = reference,
                        messageOptions = SendMessageOptions.DontRequireReceiver,
                    },
                    new AnimationEvent
                    {
                        time = 0.4f,
                        functionName = "Keep",
                        stringParameter = "middle",
                        intParameter = 11,
                    },
                    new AnimationEvent
                    {
                        time = 0.8f,
                        functionName = "Repeat",
                        stringParameter = "last",
                        floatParameter = -2f,
                        intParameter = -3,
                    },
                }
            );
            AssetDatabase.SaveAssets();
            original = AnimationUtility.GetAnimationEvents(clip);
            Undo.IncrementCurrentGroup();
        }

        [TearDown]
        public void TearDown()
        {
            Undo.ClearUndo(clip);
            AssetDatabase.DeleteAsset(Root);
            CleanupEmptyParentFolders(Root);
        }

        [Test]
        public void AddEvent_UndoRestoresCompleteOriginalArray()
        {
            var result = Invoke(
                new JObject
                {
                    ["action"] = "clip_add_event",
                    ["time"] = 0.6f,
                    ["function_name"] = "Added",
                    ["string_parameter"] = "payload",
                    ["float_parameter"] = 3.5f,
                    ["int_parameter"] = 19,
                }
            );
            Assert.IsTrue(result.Value<bool>("success"), result.ToString());
            var added = AnimationUtility.GetAnimationEvents(clip);
            Assert.AreEqual(4, added.Length);
            var newEvent = System.Array.Find(added, e => e.functionName == "Added");
            Assert.IsNotNull(newEvent);
            Assert.AreEqual(0.6f, newEvent.time);
            Assert.AreEqual("payload", newEvent.stringParameter);
            Assert.AreEqual(3.5f, newEvent.floatParameter);
            Assert.AreEqual(19, newEvent.intParameter);

            Undo.FlushUndoRecordObjects();
            Undo.PerformUndo();
            AssertEventsEqual(original, AnimationUtility.GetAnimationEvents(clip));
        }

        [TestCase(false)]
        [TestCase(true)]
        public void RemoveEvent_UndoRestoresCompleteOriginalArray(bool byIndex)
        {
            var request = new JObject { ["action"] = "clip_remove_event" };
            if (byIndex)
                request["event_index"] = 1;
            else
                request["function_name"] = "Repeat";
            var result = Invoke(request);
            Assert.IsTrue(result.Value<bool>("success"), result.ToString());
            Assert.AreEqual(byIndex ? 1 : 2, result["data"].Value<int>("removedCount"));
            var remaining = AnimationUtility.GetAnimationEvents(clip);
            AssertEventsEqual(byIndex ? new[] { original[0], original[2] } : new[] { original[1] }, remaining);

            Undo.FlushUndoRecordObjects();
            Undo.PerformUndo();
            AssertEventsEqual(original, AnimationUtility.GetAnimationEvents(clip));
        }

        [TestCase("clip_add_event", "function_name", "")]
        [TestCase("clip_remove_event", "event_index", -1)]
        [TestCase("clip_remove_event", "event_index", 99)]
        [TestCase("clip_remove_event", "function_name", "Missing")]
        public void InvalidEventRequest_PreservesCompleteArray(string action, string field, object value)
        {
            var result = Invoke(new JObject { ["action"] = action, [field] = JToken.FromObject(value) });
            Assert.IsFalse(result.Value<bool>("success"), result.ToString());
            AssertEventsEqual(original, AnimationUtility.GetAnimationEvents(clip));
        }

        private static JObject Invoke(JObject request)
        {
            request["clip_path"] = Path;
            return ToJObject(ManageAnimation.HandleCommand(request));
        }

        private static void AssertEventsEqual(AnimationEvent[] expected, AnimationEvent[] actual)
        {
            Assert.AreEqual(expected.Length, actual.Length);
            for (int i = 0; i < expected.Length; i++)
            {
                Assert.AreEqual(expected[i].time, actual[i].time, $"event {i}: time");
                Assert.AreEqual(expected[i].functionName, actual[i].functionName, $"event {i}: function");
                Assert.AreEqual(expected[i].stringParameter, actual[i].stringParameter, $"event {i}: string");
                Assert.AreEqual(expected[i].floatParameter, actual[i].floatParameter, $"event {i}: float");
                Assert.AreEqual(expected[i].intParameter, actual[i].intParameter, $"event {i}: int");
                Assert.AreEqual(expected[i].objectReferenceParameter, actual[i].objectReferenceParameter, $"event {i}: object");
                Assert.AreEqual(expected[i].messageOptions, actual[i].messageOptions, $"event {i}: message options");
            }
        }
    }
}
