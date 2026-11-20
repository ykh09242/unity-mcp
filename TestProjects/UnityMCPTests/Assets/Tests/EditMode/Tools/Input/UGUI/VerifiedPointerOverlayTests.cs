#if MCP_INPUT_UGUI
using System;
using System.Collections;
using System.Linq;
using MCPForUnity.Editor.Services.PlayScenarios;
using MCPForUnity.Editor.Tools;
using MCPForUnity.Runtime.Helpers;
using MCPForUnity.Runtime.PlayScenarios;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEngine;
using UnityEngine.EventSystems;
using UnityEngine.TestTools;
using UnityEngine.UI;

namespace MCPForUnityTests.EditMode.Tools.Input
{
    internal sealed class VerifiedPointerReceiptProbe : IPlayScenarioStateProvider
    {
        public int Deliveries { get; private set; }
        public Vector2 DeliveredPosition { get; private set; }
        public GameObject DeliveredHit { get; private set; }
        public bool Opened { get; private set; }

        public void Open() => Opened = true;

        public void RecordPointer(BaseEventData eventData)
        {
            var data = (PointerEventData)eventData;
            Deliveries++;
            DeliveredPosition = data.position;
            DeliveredHit = data.pointerCurrentRaycast.gameObject;
        }

        public bool TryRead(out PlayScenarioStateValue value)
        {
            value = PlayScenarioStateValue.FromBoolean(Opened);
            return true;
        }
    }

    public partial class UguiRaycastDiagnosticsTests
    {
        [UnityTest]
        public IEnumerator TransparentOverlayAndRepairUseTheSamePointAndDifferentGameState()
        {
            var group = cover.AddComponent<CanvasGroup>();
            group.alpha = 0f;
            cover.AddComponent<EventTrigger>();
            var probe = new VerifiedPointerReceiptProbe();
            button.GetComponent<Button>().onClick.AddListener(probe.Open);
            var trigger = button.AddComponent<EventTrigger>();
            var entry = new EventTrigger.Entry { eventID = EventTriggerType.PointerClick };
            entry.callback.AddListener(probe.RecordPointer);
            trigger.triggers.Add(entry);
            yield return null;
            Canvas.ForceUpdateCanvases();
            Assert.IsTrue(group.blocksRaycasts);
            Assert.IsFalse(cover.GetComponent<Image>().canvasRenderer.cull, "The transparent overlay remains in native raycast routing.");

            const string stateId = "verified-pointer.opened";
            var definition = new PlayScenarioDefinition
            {
                Name = "verified-pointer-overlay",
                PollIntervalMs = 100,
                CompletionStableMs = 0,
                Steps =
                {
                    new PlayScenarioStep
                    {
                        Name = "Initially closed",
                        Action = "wait_state",
                        StateId = stateId,
                        StateEquals = false,
                    },
                    new PlayScenarioStep
                    {
                        Name = "Click at button center",
                        Action = "click_ui",
                        ClickMode = "raycast",
                        Target = canvas.name + "/" + button.name,
                        TimeoutSeconds = 1,
                    },
                    new PlayScenarioStep
                    {
                        Name = "Panel opens",
                        Action = "wait_state",
                        StateId = stateId,
                        StateEquals = true,
                    },
                },
            };
            using (PlayScenarioStateRegistry.Register(stateId, probe))
            {
                var request = Request();
                int width = Screen.width;
                int height = Screen.height;
                Vector2 position = Point();
                var blockedHits = VerifiedPointerHits(request, cover, button);
                var blockedRun = RunVerifiedPointerSteps(definition);
                Assert.AreEqual("timed_out", blockedRun.Status, blockedRun.Error);
                Assert.AreEqual("input_blocked", blockedRun.Failure?.Code, blockedRun.Error);
                Assert.AreEqual("Click at button center", blockedRun.Steps.Single(step => step.Status == "timed_out").Name);
                Assert.AreEqual("skipped", blockedRun.Steps[2].Status);
                Assert.IsFalse(probe.Opened);
                Assert.AreEqual(0, probe.Deliveries);
                var blocked = PointerAttempt(blockedHits, blockedRun, probe);

                group.blocksRaycasts = false;
                Canvas.ForceUpdateCanvases();
                Assert.IsTrue(cover.activeInHierarchy);
                Assert.AreEqual(0f, group.alpha);
                Assert.AreEqual(width, Screen.width);
                Assert.AreEqual(height, Screen.height);
                Assert.AreEqual(position, Point());
                var repairedHits = VerifiedPointerHits(request, button);
                var repairedRun = RunVerifiedPointerSteps(definition);
                Assert.AreEqual("succeeded", repairedRun.Status, repairedRun.Error);
                Assert.That(repairedRun.Steps.Select(step => step.Status), Is.All.EqualTo("passed"));
                Assert.IsTrue(probe.Opened);
                Assert.AreEqual(1, probe.Deliveries);
                Assert.AreEqual(position, probe.DeliveredPosition);
                Assert.AreSame(button, probe.DeliveredHit);
                Assert.AreEqual(blockedRun.Reproduction.DefinitionHash, repairedRun.Reproduction.DefinitionHash);

                Export(
                    "verified-pointer-overlay.json",
                    new JObject
                    {
                        ["unity_version"] = Application.unityVersion,
                        ["graphics_device"] = SystemInfo.graphicsDeviceType.ToString(),
                        ["viewport"] = new JArray(width, height),
                        ["screen_position"] = new JArray(position.x, position.y),
                        ["blocked"] = blocked,
                        ["repaired"] = PointerAttempt(repairedHits, repairedRun, probe),
                    }
                );
            }
        }

        private JObject VerifiedPointerHits(JObject request, params GameObject[] expected)
        {
            var reply = JObject.FromObject(ManageUGUI.HandleCommand(request));
            EqualNativeOrder(reply);
            CollectionAssert.AreEqual(expected.Select(hit => hit.GetInstanceIDCompat()), reply["data"]["hits"].Select(hit => hit.Value<int>("game_object_id")));
            Assert.AreEqual(expected[0] == button, reply["data"].Value<bool>("first_hit_targets_handler"));
            Assert.AreEqual("not_attempted", (string)reply["data"]["input"]["delivery"]);
            return reply;
        }

        private static JObject PointerAttempt(JObject raycast, PlayScenarioRun run, VerifiedPointerReceiptProbe probe) =>
            new JObject
            {
                ["raycast"] = raycast,
                ["scenario_run"] = JObject.FromObject(run),
                ["observed_open"] = probe.Opened,
                ["pointer_deliveries"] = probe.Deliveries,
                ["delivered_position"] = probe.Deliveries == 0 ? null : new JArray(probe.DeliveredPosition.x, probe.DeliveredPosition.y),
            };

        private static PlayScenarioRun RunVerifiedPointerSteps(PlayScenarioDefinition definition)
        {
            long now = DateTimeOffset.UtcNow.ToUnixTimeMilliseconds();
            var run = PlayScenarioEngine.Create(definition, Guid.NewGuid().ToString("N"), 1, 10, now);
            var engine = new PlayScenarioEngine(run, new UnityPlayScenarioHost());
            engine.EnteredPlayMode();
            // The clock controls deadlines only. Each poll observes and dispatches through the real Editor host.
            for (int poll = 0; engine.Running && poll < 50; poll++)
                engine.Tick(now + poll * definition.PollIntervalMs, true);
            Assert.IsFalse(engine.Running, "The owned scenario did not settle.");
            return run;
        }
    }
}
#endif
