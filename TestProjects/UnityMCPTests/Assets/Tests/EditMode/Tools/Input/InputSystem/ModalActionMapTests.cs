#if MCP_INPUT_SYSTEM && ENABLE_INPUT_SYSTEM
using System.Collections;
using System.Collections.Generic;
using System.Linq;
using MCPForUnity.Editor.Tools.Input;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEngine;
using UnityEngine.InputSystem;
using UnityEngine.TestTools;
using InputApi = UnityEngine.InputSystem.InputSystem;

namespace MCPForUnityTests.EditMode.Tools.Input
{
    public class ModalActionMapTests
    {
        [UnityTest]
        public IEnumerator KeyboardSubmitWhileMenuOpen() => InPlayMode("Keyboard");

        [UnityTest]
        public IEnumerator GamepadSubmitWhileMenuOpen() => InPlayMode("Gamepad");

        private static IEnumerator InPlayMode(string layout)
        {
            yield return new EnterPlayMode();
            var input = new InputTestFixture();
            input.Setup();
            try
            {
                Exercise(layout);
            }
            finally
            {
                input.TearDown();
            }
            yield return new ExitPlayMode();
        }

        [UnityTearDown]
        public IEnumerator ReturnToEditMode()
        {
            if (Application.isPlaying)
                yield return new ExitPlayMode();
        }

        private static void Exercise(string layout)
        {
            InputApi.settings.updateMode = InputSettings.UpdateMode.ProcessEventsInDynamicUpdate;
            var simulation = new InputSystemSimulationBackend();
            using var gameplay = new InputActionMap("Gameplay");
            using var ui = new InputActionMap("UI");
            InputAction fire = gameplay.AddAction("Fire", InputActionType.Button, "<Keyboard>/enter");
            InputAction submit = ui.AddAction("Submit", InputActionType.Button, "<Keyboard>/enter");
            fire.AddBinding("<Gamepad>/buttonSouth");
            submit.AddBinding("<Gamepad>/buttonSouth");
            var events = new List<string>();
            var controls = new List<string>();
            var devices = new List<InputDevice>();
            int ammo = 3;
            int confirmations = 0;
            void Record(InputAction.CallbackContext context)
            {
                events.Add(context.action.actionMap.name + "/" + context.action.name);
                controls.Add(context.control.path);
                devices.Add(context.control.device);
            }
            fire.performed += context =>
            {
                Record(context);
                ammo--;
            };
            submit.performed += context =>
            {
                Record(context);
                confirmations++;
            };
            try
            {
                foreach (bool repaired in new[] { false, true })
                {
                    ammo = 3;
                    confirmations = 0;
                    gameplay.Enable();
                    if (repaired)
                        gameplay.Disable();
                    ui.Enable();
                    Press("menu-open");
                    Assert.AreEqual(1, confirmations);
                    Assert.AreEqual(repaired ? 3 : 2, ammo);
                    CollectionAssert.AreEquivalent(repaired ? new[] { "UI/Submit" } : new[] { "Gameplay/Fire", "UI/Submit" }, events);
                    ui.Disable();
                    gameplay.Enable();
                    Press("menu-closed");
                    CollectionAssert.AreEqual(new[] { "Gameplay/Fire" }, events);
                    Assert.AreEqual(1, confirmations);
                    Assert.AreEqual(repaired ? 2 : 1, ammo);

                    void Press(string state)
                    {
                        events.Clear();
                        controls.Clear();
                        devices.Clear();
                        string[] maps = new[] { gameplay, ui }.Where(map => map.enabled).Select(map => map.name).ToArray();
                        string packet =
                            layout == "Keyboard"
                                ? "{\"action\":\"key\",\"key\":\"Enter\",\"frames\":1}"
                                : "{\"action\":\"gamepad\",\"control\":\"buttonSouth\",\"frames\":1}";
                        simulation.Apply(InputSimulationRequest.Parse(JObject.Parse(packet)));
                        InputDevice active = layout == "Keyboard" ? (InputDevice)Keyboard.current : Gamepad.current;
                        InputApi.Update();
                        InputApi.Update();
                        TestContext.WriteLine(
                            JsonConvert.SerializeObject(
                                new
                                {
                                    repaired,
                                    state,
                                    maps,
                                    events,
                                    controls,
                                    ammo,
                                    confirmations,
                                    devices = devices.Select(d => new { d.layout, d.deviceId }),
                                }
                            )
                        );
                        Assert.IsNotEmpty(devices);
                        Assert.IsTrue(devices.All(device => device == active && device.layout == layout));
                    }
                }
            }
            finally
            {
                simulation.ReleaseAll();
            }
        }
    }
}
#endif
