#if MCP_INPUT_UGUI
using System;
using System.Collections;
using System.Linq;
using MCPForUnity.Editor.Services.PlayScenarios;
using MCPForUnity.Runtime.PlayScenarios;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEngine;
using UnityEngine.SceneManagement;
using UnityEngine.TestTools;
using UnityEngine.UI;

namespace MCPForUnityTests.PlayScenarios.Integration
{
    public partial class PlayScenarioNativeFlowTests
    {
        [UnityTest]
        public IEnumerator NativeStableIdTracksRenameReparentInactiveDuplicateAndDestroyWithoutRetainingObjects()
        {
            yield return new EnterPlayMode();
            Assert.That(Application.isPlaying, Is.True);
            Assert.That(SceneManager.GetActiveScene().IsValid() && SceneManager.GetActiveScene().isLoaded, Is.True);
            var target = new GameObject("OriginalTarget");
            PlayScenarioTarget marker = target.AddComponent<PlayScenarioTarget>();
            Assert.That(marker, Is.Not.Null, "The native Runtime marker must attach before the identity lifecycle can be tested.");
            marker.TargetId = "operations.player";
            var host = new UnityPlayScenarioHost();
            var step = new PlayScenarioStep
            {
                Name = "ID",
                Action = "wait_object",
                TargetId = "operations.player",
            };
            Assert.That(host.Evaluate(step, true).Ready, Is.True);
            var parent = new GameObject("NewParent");
            target.transform.SetParent(parent.transform);
            target.name = "RenamedTarget";
            Assert.That(host.Evaluate(step, false).Ready, Is.True);
            target.SetActive(false);
            Assert.That(host.Evaluate(step, false).Ready, Is.False);
            step.Active = false;
            Assert.That(host.Evaluate(step, false).Ready, Is.True);
            var duplicate = new GameObject("Duplicate");
            duplicate.AddComponent<PlayScenarioTarget>().TargetId = "operations.player";
            duplicate.SetActive(false);
            AssertNativeIdAmbiguity(host, step);
            UnityEngine.Object.Destroy(duplicate);
            yield return null;
            Assert.That(host.Evaluate(step, false).Ready, Is.True);
            host.Release();
            host.Release();
            Assert.That(target != null, Is.True);
            UnityEngine.Object.Destroy(target);
            yield return null;
            step.Active = null;
            step.Count = 0;
            Assert.That(host.Evaluate(step, true).Ready, Is.True);
            host.Release();
            UnityEngine.Object.Destroy(parent);
        }

        private static void AssertNativeIdAmbiguity(UnityPlayScenarioHost host, PlayScenarioStep step)
        {
            // Keep this lambda outside the iterator: its display class cannot survive EnterPlayMode domain reload.
            var error = Assert.Throws<PlayScenarioException>(() => host.Evaluate(step, false));
            Assert.That(error.Failure.Code, Is.EqualTo("target_ambiguous"));
        }

        [UnityTest]
        public IEnumerator SavedIdRaycastRunWaitsThroughCoverThenClicksRenamedTargetOnce()
        {
            Save(
                Context.NextName,
                new JArray(
                    SceneStep("load", "load_scene", Hardening),
                    new JObject
                    {
                        ["name"] = "wait ID",
                        ["action"] = "wait_object",
                        ["target_id"] = "operations.start",
                        ["timeout_seconds"] = 10,
                    },
                    new JObject
                    {
                        ["name"] = "raycast ID",
                        ["action"] = "click_ui",
                        ["target_id"] = "operations.start",
                        ["click_mode"] = "raycast",
                        ["timeout_seconds"] = 10,
                    }
                )
            );
            yield return new EnterPlayMode();
            Command(
                new JObject
                {
                    ["action"] = "run",
                    ["name"] = Context.NextName,
                    ["job_id"] = NextId,
                    ["timeout_seconds"] = 30,
                }
            );
            GameObject button = null;
            double deadline = Time.realtimeSinceStartupAsDouble + 8;
            while (button == null && Time.realtimeSinceStartupAsDouble < deadline)
            {
                if (SceneManager.GetActiveScene().path == Hardening)
                    button = GameObject.Find("ScenarioBootstrap/Canvas/StartButton");
                yield return null;
            }
            Assert.That(button, Is.Not.Null);
            GameObject.Find("ScenarioBootstrap/Canvas/CleanupButton").GetComponent<Image>().raycastTarget = false;
            var cover = new GameObject("OperationsCover", typeof(RectTransform), typeof(Image));
            cover.transform.SetParent(button.transform.parent, false);
            ((RectTransform)cover.transform).sizeDelta = new Vector2(400, 400);
            button.AddComponent<PlayScenarioTarget>().TargetId = "operations.start";
            button.name = "RenamedStart";
            var container = new GameObject("Reparented", typeof(RectTransform));
            container.transform.SetParent(button.transform.parent, false);
            button.transform.SetParent(container.transform, false);
            cover.transform.SetAsLastSibling();
            deadline = Time.realtimeSinceStartupAsDouble + 5;
            while (
                (int)Status(NextId)["steps"][2]["poll_count"] < 3
                && (string)Status(NextId)["status"] == "running"
                && Time.realtimeSinceStartupAsDouble < deadline
            )
                yield return null;
            JObject waiting = Status(NextId);
            Assert.That((string)waiting["status"], Is.EqualTo("running"), waiting.ToString());
            Assert.That((int)waiting["steps"][2]["poll_count"], Is.GreaterThanOrEqualTo(3));
            Assert.That(waiting["logs"].Count(log => (string)log["message"] == "PLAY_SCENARIO_QA_HARDENING_START"), Is.Zero);
            UnityEngine.Object.Destroy(cover);
            yield return Finish(NextId);
            JObject done = Status(NextId);
            Assert.That((string)done["status"], Is.EqualTo("succeeded"), done.ToString());
            Assert.That(done["logs"].Count(log => (string)log["message"] == "PLAY_SCENARIO_QA_HARDENING_START"), Is.EqualTo(1));
            AssertReport(done);
        }
    }
}
#endif
