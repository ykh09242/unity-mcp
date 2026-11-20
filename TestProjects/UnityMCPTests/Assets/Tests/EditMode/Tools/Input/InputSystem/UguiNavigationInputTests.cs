#if MCP_INPUT_SYSTEM && MCP_INPUT_UGUI && ENABLE_INPUT_SYSTEM
using System.Collections;
using MCPForUnity.Editor.Services.PlayScenarios;
using MCPForUnity.Editor.Tools.Input;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEditor;
using UnityEngine;
using UnityEngine.EventSystems;
using UnityEngine.InputSystem;
using UnityEngine.InputSystem.UI;
using UnityEngine.SceneManagement;
using UnityEngine.TestTools;
using UnityEngine.UI;
using InputApi = UnityEngine.InputSystem.InputSystem;

namespace MCPForUnityTests.EditMode.Tools.Input
{
    public sealed class UguiNavigationInputTests
    {
        [UnityTest]
        public IEnumerator PointerReachableButtonRequiresRepairedDirectionBeforeKeyboardAndGamepadSubmit()
        {
            yield return new EnterPlayMode();
            var input = new InputTestFixture();
            input.Setup();
            var host = new UnityPlayScenarioHost();
            var previousEvents = EventSystem.current;
            GameObject eventsRoot = null;
            GameObject canvasRoot = null;
            InputActionAsset asset = null;
            InputActionReference moveReference = null;
            InputActionReference submitReference = null;
            try
            {
                InputApi.settings.updateMode = InputSettings.UpdateMode.ProcessEventsInDynamicUpdate;
                InputApi.settings.backgroundBehavior = InputSettings.BackgroundBehavior.IgnoreFocus;
                canvasRoot = new GameObject("NativeNavigationFixture", typeof(RectTransform), typeof(Canvas), typeof(GraphicRaycaster));
                var canvas = canvasRoot.GetComponent<Canvas>();
                canvas.renderMode = RenderMode.ScreenSpaceOverlay;
                canvas.sortingOrder = short.MaxValue;
                Button start = CreateButton(canvasRoot.transform, "Start", -80);
                Button confirm = CreateButton(canvasRoot.transform, "Confirm", 80);
                int confirmations = 0;
                confirm.onClick.AddListener(() => confirmations++);

                asset = ScriptableObject.CreateInstance<InputActionAsset>();
                var map = asset.AddActionMap("FixtureUI");
                var move = map.AddAction("Move", InputActionType.PassThrough, expectedControlLayout: "Vector2");
                move.AddCompositeBinding("2DVector").With("Right", "<Keyboard>/rightArrow");
                move.AddBinding("<Gamepad>/dpad");
                var submit = map.AddAction("Submit", InputActionType.Button, "<Keyboard>/enter");
                submit.AddBinding("<Gamepad>/buttonSouth");
                int rightInputs = 0;
                int submitInputs = 0;
                move.performed += context =>
                {
                    if (context.ReadValue<Vector2>() == Vector2.right)
                        rightInputs++;
                };
                submit.performed += _ => submitInputs++;
                moveReference = InputActionReference.Create(move);
                submitReference = InputActionReference.Create(submit);

                eventsRoot = new GameObject("NativeNavigationEvents");
                eventsRoot.SetActive(false);
                var events = eventsRoot.AddComponent<EventSystem>();
                var module = eventsRoot.AddComponent<InputSystemUIInputModule>();
                events.firstSelectedGameObject = start.gameObject;
                module.actionsAsset = asset;
                module.move = moveReference;
                module.submit = submitReference;
                eventsRoot.SetActive(true);
                EventSystem.current = events;

                string scenePath = SceneManager.GetActiveScene().path;
                var focus = new PlayScenarioStep
                {
                    Name = "Confirm has focus",
                    Action = "wait_ui_focus",
                    Target = canvasRoot.name + "/Confirm",
                };
                var pointer = new PlayScenarioStep
                {
                    Name = "Click Confirm",
                    Action = "click_ui",
                    ClickMode = "raycast",
                    Target = focus.Target,
                };
                yield return null;
                Canvas.ForceUpdateCanvases();
                Assert.That(events.currentInputModule, Is.SameAs(module));
                Assert.That(confirm.GetComponent<Image>().depth, Is.GreaterThanOrEqualTo(0), "The fixture must render a native raycast target.");
                Assert.That(move.enabled && submit.enabled, Is.True);
                PlayScenarioObservation click = host.Evaluate(pointer, true);
                Assert.That(click.Ready, Is.True, click.Detail);
                Assert.That(confirmations, Is.EqualTo(1), "Pointer dispatch must reach the same button before the navigation repair.");

                var inputs = new[] { (Move: KeyStep("RightArrow"), Submit: KeyStep("Enter")), (Move: PadStep("dpadRight"), Submit: PadStep("buttonSouth")) };
                foreach (var route in inputs)
                {
                    confirmations = 0;
                    start.navigation = new Navigation { mode = Navigation.Mode.Explicit };
                    events.SetSelectedGameObject(start.gameObject);
                    int previousRight = rightInputs;
                    int previousSubmit = submitInputs;
                    Send(host, module, route.Move);
                    PlayScenarioObservation blocked = host.Evaluate(focus, true);
                    Assert.That(blocked.Ready, Is.False, blocked.Detail);
                    Assert.That(blocked.Failure.Actual, Does.Contain("'Start'"));
                    Assert.That(events.currentSelectedGameObject, Is.SameAs(start.gameObject));
                    Send(host, module, route.Submit);
                    Assert.That(rightInputs, Is.EqualTo(previousRight + 1), "The missing link must be exercised by a delivered direction.");
                    Assert.That(submitInputs, Is.EqualTo(previousSubmit + 1));
                    Assert.That(confirmations, Is.Zero);

                    start.navigation = new Navigation { mode = Navigation.Mode.Explicit, selectOnRight = confirm };
                    Assert.That(events.currentSelectedGameObject, Is.SameAs(start.gameObject), "Repairing a link must not itself change focus.");
                    Send(host, module, route.Move);
                    PlayScenarioObservation reached = host.Evaluate(focus, true);
                    Assert.That(reached.Ready, Is.True, reached.Detail);
                    Assert.That(reached.Detail, Does.Contain("'Confirm'").And.Contain("in scene '" + scenePath + "'"));
                    Assert.That(events.currentSelectedGameObject, Is.SameAs(confirm.gameObject));
                    Assert.That(confirmations, Is.Zero, "Navigation moves focus before the independent submit.");
                    Send(host, module, route.Submit);
                    Assert.That(rightInputs, Is.EqualTo(previousRight + 2));
                    Assert.That(submitInputs, Is.EqualTo(previousSubmit + 2));
                    Assert.That(confirmations, Is.EqualTo(1));
                }
            }
            finally
            {
                host.Release();
                Object.DestroyImmediate(eventsRoot);
                Object.DestroyImmediate(canvasRoot);
                Object.DestroyImmediate(moveReference);
                Object.DestroyImmediate(submitReference);
                Object.DestroyImmediate(asset);
                if (previousEvents != null)
                    EventSystem.current = previousEvents;
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

        private static Button CreateButton(Transform canvas, string name, float x)
        {
            var root = new GameObject(name, typeof(RectTransform), typeof(Image), typeof(Button));
            root.transform.SetParent(canvas, false);
            var rect = (RectTransform)root.transform;
            rect.anchoredPosition = new Vector2(x, 0);
            rect.sizeDelta = new Vector2(80, 40);
            var button = root.GetComponent<Button>();
            button.navigation = new Navigation { mode = Navigation.Mode.Explicit };
            return button;
        }

        private static PlayScenarioStep KeyStep(string key) =>
            new PlayScenarioStep
            {
                Name = key,
                Action = "input",
                Input = new JObject
                {
                    ["action"] = "key",
                    ["key"] = key,
                    ["frames"] = 1,
                },
            };

        private static PlayScenarioStep PadStep(string control) =>
            new PlayScenarioStep
            {
                Name = control,
                Action = "input",
                Input = new JObject
                {
                    ["action"] = "gamepad",
                    ["control"] = control,
                    ["frames"] = 1,
                },
            };

        private static void Send(UnityPlayScenarioHost host, InputSystemUIInputModule module, PlayScenarioStep step)
        {
            Assert.That(host.Evaluate(step, true).Ready, Is.False);
            // Drive the fixture's isolated Input System and real uGUI module in native update order.
            InputApi.Update();
            module.Process();
            Assert.That(host.Evaluate(step, false).Ready, Is.True);
            InputApi.Update();
            module.Process();
        }
    }
}
#endif
