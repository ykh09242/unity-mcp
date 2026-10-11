using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using MCPForUnity.Editor.Services.PlayScenarios;
using MCPForUnity.Runtime.PlayScenarios;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEngine;

namespace MCPForUnityTests.EditMode.Tools.PlayScenarios
{
    public sealed class PlayScenarioRecordingTests
    {
        private const string Menu = "Assets/Scenes/Menu.unity";
        private const string Game = "Assets/Scenes/Game.unity";
        private readonly List<GameObject> objects = new List<GameObject>();

        [TearDown]
        public void TearDown()
        {
            foreach (GameObject value in objects)
                if (value != null)
                    UnityEngine.Object.DestroyImmediate(value);
            objects.Clear();
        }

        private GameObject Object(string name, Transform parent = null)
        {
            var value = new GameObject(name);
            value.transform.SetParent(parent);
            objects.Add(value);
            return value;
        }

        [Test]
        public void RecordedTransitionsPreserveActionsWithoutInventedStateOrTiming()
        {
            var draft = new PlayScenarioRecordingDraft("menu-draft", Menu);
            Assert.That(draft.Click(new PlayScenarioRecordedTarget { Scene = Menu, TargetId = "menu.start" }), Is.True);
            Assert.That(draft.SceneChanged(Game), Is.True);
            Assert.That(draft.Click(new PlayScenarioRecordedTarget { Scene = Game, Target = "Canvas/Back" }), Is.True);
            Assert.That(draft.SceneChanged(Menu), Is.True);
            PlayScenarioDefinition definition = draft.Definition();
            Assert.That(definition.Draft, Is.True);
            Assert.That(definition.Steps.Select(step => step.Action), Is.EqualTo(new[] { "load_scene", "click_ui", "wait_scene", "click_ui", "wait_scene" }));
            Assert.That(definition.Steps[1].TargetId, Is.EqualTo("menu.start"));
            Assert.That(definition.Steps[1].ClickMode, Is.EqualTo("raycast"));
            Assert.That(definition.Steps[2].Scene, Is.EqualTo(Game));
            Assert.That(definition.Steps.All(step => step.Property == null && step.StateEquals == null && step.StableForMs == null), Is.True);
        }

        [Test]
        public void EventLimitIncludesTransitionsAndStopsBeforeDefinitionOverflow()
        {
            var draft = new PlayScenarioRecordingDraft("bounded-draft", Menu, 2);
            draft.Click(new PlayScenarioRecordedTarget { Scene = Menu, Target = "Canvas/Start" });
            draft.SceneChanged(Game);
            Assert.That(draft.Recording, Is.False);
            Assert.That(draft.Status, Is.EqualTo("limit_reached"));
            Assert.That(draft.Click(new PlayScenarioRecordedTarget { Scene = Game, Target = "Canvas/Back" }), Is.False);
            Assert.That(draft.Definition().Steps.Count, Is.EqualTo(3));
            Assert.That((int)draft.Snapshot()["event_count"], Is.EqualTo(2));
            Assert.That(draft.Snapshot()["diagnostics"].Count(), Is.EqualTo(1));
            Assert.Throws<ArgumentException>(() => new PlayScenarioRecordingDraft("oversized-draft", Menu, 32));
        }

        [Test]
        public void AmbiguousAndAdditiveTargetsAreDiagnosedWithoutReliableSteps()
        {
            var draft = new PlayScenarioRecordingDraft("omitted-draft", Menu);
            Assert.That(draft.Click(new PlayScenarioRecordedTarget { Scene = Menu, Error = "Duplicate stable ID" }), Is.False);
            Assert.That(draft.Click(new PlayScenarioRecordedTarget { Scene = Game, Target = "Canvas/Start" }), Is.False);
            Assert.That(draft.Definition().Steps.Count, Is.EqualTo(1));
            Assert.That(draft.Snapshot()["diagnostics"].Count(), Is.EqualTo(2));
            for (int index = 0; index < 40; index++)
                draft.Diagnose(index + new string('x', 600));
            Assert.That(draft.Snapshot()["diagnostics"].Count(), Is.EqualTo(32));
            Assert.That(draft.Snapshot()["diagnostics"].All(value => ((string)value).Length <= 512), Is.True);
        }

        [Test]
        public void OnlyExplicitCheckpointsRequestScreenshotsAndCannotInventAssertions()
        {
            var draft = new PlayScenarioRecordingDraft("checkpoint-draft", Menu);
            Assert.That(draft.Definition().Steps.All(step => step.Screenshot == null), Is.True);
            draft.SceneChanged(Game);
            draft.Checkpoint("after-start");
            PlayScenarioStep checkpoint = draft.Definition().Steps.Last();
            Assert.That(checkpoint.Action, Is.EqualTo("wait_scene"));
            Assert.That(checkpoint.Scene, Is.EqualTo(Game));
            Assert.That(checkpoint.Screenshot.Name, Is.EqualTo("after-start"));
            Assert.That(checkpoint.Property, Is.Null);
            Assert.Throws<ArgumentException>(() => draft.Checkpoint("after-start"));
            Assert.Throws<ArgumentException>(() => draft.Checkpoint("../invalid"));
        }

        [Test]
        public void CancelInterruptAndNewSessionDoNotCarryEventsAcrossLifecycles()
        {
            var cancelled = new PlayScenarioRecordingDraft("cancelled-draft", Menu);
            cancelled.Cancel();
            Assert.That(cancelled.Recording, Is.False);
            Assert.That(cancelled.Saved, Is.False);
            Assert.Throws<InvalidOperationException>(() => cancelled.MarkSaved());
            var interrupted = new PlayScenarioRecordingDraft("interrupted-draft", Menu);
            interrupted.Finish("interrupted", "assembly_reload");
            Assert.That(interrupted.SceneChanged(Game), Is.False);
            Assert.That((string)interrupted.Snapshot()["reason"], Is.EqualTo("assembly_reload"));
            var fresh = new PlayScenarioRecordingDraft("fresh-draft", Game);
            Assert.That(fresh.Id, Is.Not.EqualTo(interrupted.Id));
            Assert.That((int)fresh.Snapshot()["event_count"], Is.Zero);
            JObject snapshot = fresh.Snapshot();
            ((JArray)snapshot["scenario"]["steps"]).Clear();
            Assert.That(fresh.Definition().Steps.Count, Is.EqualTo(1), "Snapshots must not expose mutable internal step storage.");
        }

        [Test]
        public void SavedDraftRoundTripsThroughValidatedStoreWithoutLosingReviewMarker()
        {
            string directory = Path.Combine(Path.GetTempPath(), "mcp-recording-" + Guid.NewGuid().ToString("N"));
            try
            {
                var draft = new PlayScenarioRecordingDraft("saved-draft", Menu);
                draft.Click(new PlayScenarioRecordedTarget { Scene = Menu, TargetId = "start" });
                var store = new PlayScenarioStore(directory);
                store.Save(draft.Definition());
                PlayScenarioDefinition saved = store.Get(draft.Name);
                Assert.That(saved.Draft, Is.True);
                Assert.That(saved.Tags, Does.Contain("recorded-draft"));
                Assert.That(saved.Steps[1].TargetId, Is.EqualTo("start"));
            }
            finally
            {
                if (Directory.Exists(directory))
                    Directory.Delete(directory, true);
            }
        }

        [Test]
        public void ExactPathsDetectDuplicateSiblingsAndStableIdsOverrideAmbiguousNames()
        {
            GameObject root = Object("RecordingRoot-" + Guid.NewGuid().ToString("N"));
            GameObject first = Object("Start", root.transform);
            GameObject second = Object("Start", root.transform);
            Assert.That(PlayScenarioRecordingService.DescribeTarget(first).Error, Does.Contain("ambiguous"));
            PlayScenarioTarget identity = first.AddComponent<PlayScenarioTarget>();
            identity.TargetId = "recorder.unique." + Guid.NewGuid().ToString("N");
            Assert.That(PlayScenarioRecordingService.DescribeTarget(first).TargetId, Is.EqualTo(identity.TargetId));
            second.AddComponent<PlayScenarioTarget>().TargetId = identity.TargetId;
            Assert.That(PlayScenarioRecordingService.DescribeTarget(first).Error, Does.Contain("duplicate"));
        }

        [Test]
        public void UnambiguousHierarchyPathsRemainExactAndRejectUnrepresentableNames()
        {
            GameObject root = Object("RecordingRoot-" + Guid.NewGuid().ToString("N"));
            GameObject child = Object("Start", root.transform);
            Assert.That(PlayScenarioRecordingService.DescribeTarget(child).Target, Is.EqualTo(root.name + "/Start"));
            child.name = "Start/Other";
            Assert.That(PlayScenarioRecordingService.DescribeTarget(child).Error, Does.Contain("unsupported"));
        }

        [Test]
        public void InvalidObservedSceneStopsCaptureAndKeepsPreviousValidDraft()
        {
            var draft = new PlayScenarioRecordingDraft("invalid-scene-draft", Menu);
            Assert.That(draft.SceneChanged("Unsaved scene"), Is.False);
            Assert.That(draft.Status, Is.EqualTo("interrupted"));
            Assert.That(draft.Definition().Steps.Count, Is.EqualTo(1));
            Assert.That(draft.Definition().Steps[0].Scene, Is.EqualTo(Menu));
        }
    }
}
