using System;
using System.Reflection;
using MCPForUnity.Editor.Services.PlayScenarios;
using Newtonsoft.Json.Linq;
using NUnit.Framework;

namespace MCPForUnityTests.EditMode.Tools.PlayScenarios
{
    public sealed class PlayScenarioRecordingSceneTests
    {
        private static PlayScenarioRecordingDraft Draft(string[] scenes = null, int maxEvents = 31)
        {
            ConstructorInfo constructor = typeof(PlayScenarioRecordingDraft).GetConstructor(
                new[] { typeof(string), typeof(string), typeof(int), typeof(string), typeof(string[]) }
            );
            Assert.That(constructor, Is.Not.Null, "Explicit all-loaded context is required.");
            return (PlayScenarioRecordingDraft)
                constructor.Invoke(
                    new object[] { "scene-draft", "Assets/A.unity", maxEvents, "all_loaded", scenes ?? new[] { "Assets/A.unity", "Assets/B.unity" } }
                );
        }

        [Test]
        public void InitialContextUsesSingleThenAdditiveAndQualifiesOtherSceneClick()
        {
            var draft = Draft();
            Assert.That(draft.Click(new PlayScenarioRecordedTarget { Scene = "Assets/B.unity", TargetId = "shared" }), Is.True);
            JObject value = draft.Snapshot();
            Assert.That((int)value["event_count"], Is.EqualTo(1));
            Assert.That((bool)value["requires_review"], Is.True);
            var definition = PlayScenarioDefinition.Parse((JObject)value["scenario"]);
            Assert.That(definition.SetupSteps.Count, Is.EqualTo(2));
            Assert.That(definition.SetupSteps[0].Scene, Is.EqualTo("Assets/A.unity"));
            Assert.That((string)value["scenario"]["setup_steps"][1]["load_mode"], Is.EqualTo("additive"));
            Assert.That((string)value["scenario"]["steps"][1]["scene"], Is.EqualTo("Assets/B.unity"));
        }

        [Test]
        public void ObservedTransitionsAreWaitsInExactOrderNeverInventedEffects()
        {
            var draft = Draft();
            MethodInfo method = typeof(PlayScenarioRecordingDraft).GetMethod("SceneObserved");
            Assert.That(method, Is.Not.Null);
            Assert.That(method.Invoke(draft, new object[] { "Assets/C.unity", "loaded" }), Is.True);
            Assert.That(method.Invoke(draft, new object[] { "Assets/B.unity", "unloaded" }), Is.True);
            JArray steps = (JArray)draft.Snapshot()["scenario"]["steps"];
            Assert.That((string)steps[1]["action"], Is.EqualTo("wait_scene"));
            Assert.That((string)steps[1]["scene_state"], Is.EqualTo("loaded"));
            Assert.That((string)steps[2]["scene_state"], Is.EqualTo("unloaded"));
        }

        [Test]
        public void AllLoadedContextRejectsDuplicatesAndExcessWithoutOmittingScenes()
        {
            Assert.Throws<TargetInvocationException>(() => Draft(new[] { "Assets/A.unity", "Assets/A.unity" }));
            var scenes = new string[33];
            for (int i = 0; i < scenes.Length; i++)
                scenes[i] = "Assets/S" + i + ".unity";
            Assert.Throws<TargetInvocationException>(() => Draft(scenes));
        }

        [Test]
        public void DefaultRecorderStillOmitsOtherSceneAndBoundRemainsExact()
        {
            var legacy = new PlayScenarioRecordingDraft("legacy-draft", "Assets/A.unity");
            Assert.That(legacy.Click(new PlayScenarioRecordedTarget { Scene = "Assets/B.unity", TargetId = "shared" }), Is.False);
            var value = Draft(maxEvents: 1);
            Assert.That(value.Click(new PlayScenarioRecordedTarget { Scene = "Assets/B.unity", TargetId = "shared" }), Is.True);
            Assert.That(value.Recording, Is.False);
            Assert.That((int)value.Snapshot()["event_count"], Is.EqualTo(1));
        }
    }
}
