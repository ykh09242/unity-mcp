#if MCP_INPUT_SYSTEM && MCP_INPUT_UGUI && ENABLE_INPUT_SYSTEM
using System;
using System.Collections;
using System.Collections.Generic;
using System.Diagnostics;
using System.Linq;
using MCPForUnity.Editor.Services.PlayScenarios;
using MCPForUnity.Editor.Tools.Input;
using MCPForUnity.Runtime.PlayScenarios;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEditor;
using UnityEngine;
using UnityEngine.EventSystems;
using UnityEngine.InputSystem;
using UnityEngine.InputSystem.UI;
using UnityEngine.TestTools;
using UnityEngine.UI;
using UnityEngine.UIElements;
using InputApi = UnityEngine.InputSystem.InputSystem;
using InputMouse = UnityEngine.InputSystem.Mouse;
using Object = UnityEngine.Object;
using UguiButton = UnityEngine.UI.Button;
using UguiImage = UnityEngine.UI.Image;

namespace MCPForUnityTests.EditMode.Tools.Input
{
    public sealed class MixedUiRouteButton : UguiButton
    {
        internal RoutedPointerTrace Trace;

        public override void OnPointerDown(PointerEventData eventData)
        {
            Trace.RecordUgui(RoutedPointerEvent.Down, gameObject, eventData);
            base.OnPointerDown(eventData);
        }

        public override void OnPointerUp(PointerEventData eventData)
        {
            Trace.RecordUgui(RoutedPointerEvent.Up, gameObject, eventData);
            base.OnPointerUp(eventData);
        }

        public override void OnPointerClick(PointerEventData eventData)
        {
            Trace.RecordUgui(RoutedPointerEvent.Click, gameObject, eventData);
            base.OnPointerClick(eventData);
        }
    }

    public sealed class MixedUiPointerRouteTests
    {
        private const string StateId = "mixed-ui.inventory-open";
        private const string ButtonName = "OpenInventory";
        private const string ParentName = "OverlayContainer";
        private const string OverlayName = "TransparentOverlay";
        private const int ReleaseStepIndex = 4;
        private static readonly Vector2 Offset = new Vector2(32, 32);
        private static readonly Vector2 Size = new Vector2(100, 60);
        private static readonly Vector2 LogicalCenter = Offset + Size / 2;

        private sealed class Inventory : IPlayScenarioStateProvider
        {
            public int OpenCount;
            public bool IsOpen => OpenCount > 0;

            public bool TryRead(out PlayScenarioStateValue value)
            {
                value = PlayScenarioStateValue.FromBoolean(IsOpen);
                return true;
            }
        }

        [UnityTest]
        public IEnumerator SharedPointerSwitchesFromToolkitToUguiAfterPickingRepairAtBothScales()
        {
            yield return new EnterPlayMode();
            var input = new InputTestFixture();
            input.Setup();
            EventSystem previous = EventSystem.current;
            var trace = new RoutedPointerTrace();
            var roots = new GameObject("MixedUiPointerFixture");
            GameObject canvasRoot = null;
            PanelSettings settings = null;
            InputActionAsset asset = null;
            InputActionReference point = null;
            InputActionReference press = null;
            try
            {
                InputApi.settings.updateMode = InputSettings.UpdateMode.ProcessEventsInDynamicUpdate;
                InputApi.settings.backgroundBehavior = InputSettings.BackgroundBehavior.IgnoreFocus;
                asset = ScriptableObject.CreateInstance<InputActionAsset>();
                InputActionMap actions = asset.AddActionMap("MixedUiFixture");
                point = InputActionReference.Create(actions.AddAction("Point", InputActionType.PassThrough, "<Mouse>/position"));
                press = InputActionReference.Create(actions.AddAction("Press", InputActionType.PassThrough, "<Mouse>/leftButton"));
                var buttonInputs = new List<(string Device, int Id, bool Pressed)>();
                press.action.performed += received =>
                    buttonInputs.Add((received.control.device.name, received.control.device.deviceId, received.ReadValue<float>() > 0.5f));
                GameObject eventsRoot = NewChild(roots, "MixedUiEvents");
                eventsRoot.SetActive(false);
                var events = eventsRoot.AddComponent<EventSystem>();
                var module = eventsRoot.AddComponent<InputSystemUIInputModule>();
                module.actionsAsset = asset;
                module.point = point;
                module.leftClick = press;
                eventsRoot.SetActive(true);
                EventSystem.current = events;

                canvasRoot = new GameObject("InventoryCanvas", typeof(RectTransform), typeof(Canvas), typeof(CanvasScaler), typeof(GraphicRaycaster));
                var canvas = canvasRoot.GetComponent<Canvas>();
                canvas.renderMode = RenderMode.ScreenSpaceOverlay;
                canvas.sortingOrder = 0;
                var canvasScaler = canvasRoot.GetComponent<CanvasScaler>();
                var buttonRoot = new GameObject(ButtonName, typeof(RectTransform), typeof(UguiImage), typeof(MixedUiRouteButton));
                buttonRoot.transform.SetParent(canvasRoot.transform, false);
                var rect = (RectTransform)buttonRoot.transform;
                rect.anchorMin = rect.anchorMax = rect.pivot = new Vector2(0, 1);
                rect.anchoredPosition = new Vector2(Offset.x, -Offset.y);
                rect.sizeDelta = Size;
                var button = buttonRoot.GetComponent<MixedUiRouteButton>();
                button.Trace = trace;
                var inventory = new Inventory();
                button.onClick.AddListener(() => inventory.OpenCount++);
                using var registration = PlayScenarioStateRegistry.Register(StateId, inventory);

                settings = ScriptableObject.CreateInstance<PanelSettings>();
                settings.name = "MixedUiToolkitPanel";
                settings.scaleMode = PanelScaleMode.ConstantPixelSize;
                settings.sortingOrder = 30;
                settings.themeStyleSheet = AssetDatabase.LoadAssetAtPath<ThemeStyleSheet>("Assets/UI Toolkit/UnityThemes/UnityDefaultRuntimeTheme.tss");
                Assert.That(settings.themeStyleSheet, Is.Not.Null, "The prepared fixture must include its runtime theme.");
                var document = NewChild(roots, "MixedUiDocument").AddComponent<UIDocument>();
                document.panelSettings = settings;
                VisualElement root = document.rootVisualElement;
                root.pickingMode = PickingMode.Ignore;
                var parent = new VisualElement { name = ParentName, pickingMode = PickingMode.Ignore };
                var overlay = new VisualElement { name = OverlayName };
                parent.style.position = Position.Absolute;
                parent.style.left = Offset.x;
                parent.style.top = Offset.y;
                parent.style.width = Size.x;
                parent.style.height = Size.y;
                overlay.style.flexGrow = 1;
                root.Add(parent);
                parent.Add(overlay);
                trace.WatchToolkit(parent);
                trace.WatchToolkit(overlay);

                foreach (float scale in new[] { 1f, 1.5f })
                {
                    canvasScaler.scaleFactor = scale;
                    settings.scale = scale;
                    overlay.pickingMode = PickingMode.Position;
                    Vector2 pointScreen = default;
                    Vector2 pointPanel = default;
                    bool settled = false;
                    var layoutClock = Stopwatch.StartNew();
                    while (!settled && layoutClock.ElapsedMilliseconds < 4000)
                    {
                        yield return null;
                        Canvas.ForceUpdateCanvases();
                        pointScreen = RectTransformUtility.WorldToScreenPoint(null, rect.TransformPoint(rect.rect.center));
                        var fromTop = new Vector2(pointScreen.x, Screen.height - pointScreen.y);
                        if (overlay.panel == null || events.currentInputModule != module)
                            continue;
                        pointPanel = RuntimePanelUtils.ScreenToPanel(overlay.panel, fromTop);
                        settled =
                            Near(fromTop, LogicalCenter * scale)
                            && Near(pointPanel, LogicalCenter)
                            && overlay.worldBound.Contains(pointPanel)
                            && overlay.panel.Pick(pointPanel) == overlay;
                    }
                    Assert.That(settled, Is.True, $"Both real layouts must settle at scale {scale}; screen {pointScreen}, panel {pointPanel}.");
                    Assert.That(new Rect(0, 0, Screen.width, Screen.height).Contains(pointScreen), Is.True);
                    Assert.That(buttonRoot.GetComponent<UguiImage>().depth, Is.GreaterThanOrEqualTo(0));
                    Assert.That(point.action.enabled && press.action.enabled, Is.True);

                    foreach (bool repaired in new[] { false, true })
                    {
                        inventory.OpenCount = 0;
                        overlay.pickingMode = repaired ? PickingMode.Ignore : PickingMode.Position;
                        // Let the normal panel update invalidate its pick cache before reusing the same pixel.
                        yield return null;
                        VisualElement geometricPick = overlay.panel.Pick(pointPanel);
                        Assert.That(geometricPick, Is.SameAs(repaired ? null : overlay));
                        trace.Clear();
                        buttonInputs.Clear();
                        PlayScenarioDefinition definition = ClickAndCheck(pointScreen);
                        long started = DateTimeOffset.UtcNow.ToUnixTimeMilliseconds();
                        var run = PlayScenarioEngine.Create(definition, Guid.NewGuid().ToString("N"), 1, 8, started);
                        var engine = new PlayScenarioEngine(run, new UnityPlayScenarioHost());
                        engine.EnteredPlayMode();
                        try
                        {
                            var clock = Stopwatch.StartNew();
                            while (engine.Running && clock.ElapsedMilliseconds < 10000)
                            {
                                long now = started + clock.ElapsedMilliseconds;
                                if (now >= run.NextPollUnixMs && run.Cursor == ReleaseStepIndex && run.Steps[ReleaseStepIndex].Status == "pending")
                                    Assert.That(
                                        InputMouse.current?.leftButton.isPressed,
                                        Is.True,
                                        "The virtual button must remain held until explicit release."
                                    );
                                engine.Tick(now, true);
                                // The isolated Input System is pumped by the native player loop while in Play Mode.
                                yield return null;
                            }
                        }
                        finally
                        {
                            engine.Release();
                        }

                        TestContext.WriteLine(
                            new JObject
                            {
                                ["fixture"] = "mixed-ui-pointer",
                                ["unity_version"] = Application.unityVersion,
                                ["input_source"] = "scenario_virtual_mouse",
                                ["scale"] = scale,
                                ["canvas_scale"] = canvas.scaleFactor,
                                ["viewport"] = new JArray(Screen.width, Screen.height),
                                ["repaired"] = repaired,
                                ["screen_point"] = Coordinates(pointScreen),
                                ["panel_point"] = Coordinates(pointPanel),
                                ["toolkit_geometric_pick"] = geometricPick?.name,
                                ["game_state_open"] = inventory.IsOpen,
                                ["game_state_open_count"] = inventory.OpenCount,
                                ["trace_truncated"] = trace.Truncated,
                                ["input_action_edges"] = new JArray(
                                    buttonInputs.Select(edge => new JObject
                                    {
                                        ["device"] = edge.Device,
                                        ["device_id"] = edge.Id,
                                        ["pressed"] = edge.Pressed,
                                    })
                                ),
                                ["events"] = new JArray(trace.Samples.Select(Sample)),
                                ["scenario"] = JObject.FromObject(run),
                            }.ToString(Formatting.None)
                        );

                        Assert.That(trace.Truncated, Is.False);
                        Assert.That(buttonInputs.Select(edge => edge.Pressed), Is.EqualTo(new[] { true, false }));
                        Assert.That(buttonInputs.Select(edge => edge.Device).Distinct(), Is.EqualTo(new[] { "MCPVirtualMouse" }));
                        Assert.That(buttonInputs.Select(edge => edge.Id).Distinct().Count(), Is.EqualTo(1));
                        Assert.That(InputApi.devices.Any(device => device.name == "MCPVirtualMouse"), Is.False);
                        Assert.That(run.RunnerResourcesReleased, Is.True, run.ReportError);
                        Assert.That(run.Steps.Take(ReleaseStepIndex + 1).All(step => step.Status == "passed"), Is.True, run.Error);
                        Assert.That(run.Status, Is.EqualTo(repaired ? "succeeded" : "timed_out"), run.Error);
                        Assert.That(run.Steps.Last().Status, Is.EqualTo(repaired ? "passed" : "timed_out"), run.Error);
                        Assert.That(inventory.OpenCount, Is.EqualTo(repaired ? 1 : 0));
                        if (repaired)
                            AssertUguiRoute(trace, pointScreen);
                        else
                        {
                            Assert.That(run.Failure?.Code, Is.EqualTo("condition_unmet"), run.Error);
                            Assert.That(run.Failure?.Target, Is.EqualTo(StateId));
                            Assert.That(run.Failure?.Actual, Is.EqualTo("false"));
                            AssertToolkitRoute(trace, pointPanel);
                        }
                    }
                }
            }
            finally
            {
                trace.Dispose();
                Object.DestroyImmediate(roots);
                Object.DestroyImmediate(canvasRoot);
                Object.DestroyImmediate(settings);
                Object.DestroyImmediate(point);
                Object.DestroyImmediate(press);
                Object.DestroyImmediate(asset);
                if (previous != null)
                    EventSystem.current = previous;
                input.TearDown();
            }
            yield return new ExitPlayMode();
        }

        [UnityTearDown]
        public IEnumerator RestoreEditMode()
        {
            if (EditorApplication.isPlaying)
                yield return new ExitPlayMode();
        }

        private static GameObject NewChild(GameObject parent, string name)
        {
            var child = new GameObject(name);
            child.transform.SetParent(parent.transform, false);
            return child;
        }

        private static bool Near(Vector2 a, Vector2 b) => Vector2.Distance(a, b) < 0.75f;

        private static JArray Coordinates(Vector2 point) => new JArray(point.x, point.y);

        private static JObject Sample(RoutedPointerSample sample) =>
            new JObject
            {
                ["backend"] = sample.Backend,
                ["event"] = sample.Event.ToString(),
                ["phase"] = sample.Phase,
                ["target"] = sample.Target,
                ["receiver"] = sample.Receiver,
                ["position"] = Coordinates(sample.Position),
                ["pointer_id"] = sample.PointerId,
                ["button"] = sample.Button,
                ["frame"] = sample.Frame,
            };

        private static PlayScenarioDefinition ClickAndCheck(Vector2 position) =>
            PlayScenarioDefinition.Parse(
                new JObject
                {
                    ["name"] = "mixed-ui-pointer",
                    ["poll_interval_ms"] = 100,
                    ["diagnostics"] = new JObject { ["record_timeline"] = true },
                    ["steps"] = new JArray(
                        ReadState(false),
                        Mouse("Move away from the shared target", "move", new Vector2(2, Screen.height - 2)),
                        Mouse("Move to the shared target", "move", position),
                        Mouse("Press the left pointer button", "press", position),
                        Mouse("Release the left pointer button", "release", position),
                        ReadState(true)
                    ),
                }
            );

        private static JObject ReadState(bool expected) =>
            new JObject
            {
                ["name"] = expected ? "Inventory opened" : "Inventory closed",
                ["action"] = "wait_state",
                ["state_id"] = StateId,
                ["state_equals"] = expected,
                ["timeout_seconds"] = 1,
            };

        private static JObject Mouse(string name, string state, Vector2 position)
        {
            var input = new JObject
            {
                ["action"] = "mouse",
                ["state"] = state,
                ["button"] = "left",
                ["position"] = Coordinates(position),
            };
            if (state == "press")
                input["frames"] = InputSimulationRequest.MaxFrames;
            return new JObject
            {
                ["name"] = name,
                ["action"] = "input",
                ["input"] = input,
                ["timeout_seconds"] = 2,
            };
        }

        private static void AssertUguiRoute(RoutedPointerTrace trace, Vector2 point)
        {
            Assert.That(
                trace.Samples.Select(sample => sample.Event),
                Is.EqualTo(new[] { RoutedPointerEvent.Down, RoutedPointerEvent.Up, RoutedPointerEvent.Click })
            );
            foreach (RoutedPointerSample sample in trace.Samples)
            {
                Assert.That(sample.Backend, Is.EqualTo("ugui"));
                Assert.That(sample.Phase, Is.EqualTo("handler"));
                Assert.That(sample.Target, Is.EqualTo(ButtonName));
                Assert.That(sample.Receiver, Is.EqualTo(ButtonName));
                Assert.That(sample.Button, Is.Zero);
                Assert.That(Near(sample.Position, point), Is.True, sample.Position.ToString());
            }
            Assert.That(trace.Samples.Select(sample => sample.PointerId).Distinct().Count(), Is.EqualTo(1));
        }

        private static void AssertToolkitRoute(RoutedPointerTrace trace, Vector2 point)
        {
            Assert.That(trace.Samples.Count, Is.EqualTo(9));
            int offset = 0;
            foreach (RoutedPointerEvent kind in new[] { RoutedPointerEvent.Down, RoutedPointerEvent.Up, RoutedPointerEvent.Click })
            {
                RoutedPointerSample[] route = trace.Samples.Skip(offset).Take(3).ToArray();
                Assert.That(route.Select(sample => sample.Phase), Is.EqualTo(new[] { "trickle_down", "target", "bubble_up" }));
                Assert.That(route.Select(sample => sample.Receiver), Is.EqualTo(new[] { ParentName, OverlayName, ParentName }));
                foreach (RoutedPointerSample sample in route)
                {
                    Assert.That(sample.Backend, Is.EqualTo("uitoolkit"));
                    Assert.That(sample.Event, Is.EqualTo(kind));
                    Assert.That(sample.Target, Is.EqualTo(OverlayName));
                    Assert.That(sample.Button, Is.Zero);
                    Assert.That(Near(sample.Position, point), Is.True, sample.Position.ToString());
                }
                offset += route.Length;
            }
            Assert.That(trace.Samples.Select(sample => sample.PointerId).Distinct().Count(), Is.EqualTo(1));
        }
    }
}
#endif
