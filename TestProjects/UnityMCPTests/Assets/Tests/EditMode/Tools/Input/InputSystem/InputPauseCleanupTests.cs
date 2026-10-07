#if MCP_INPUT_SYSTEM && ENABLE_INPUT_SYSTEM
using System.Collections;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Editor.Tools.Input;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEditor;
using UnityEngine.InputSystem;
using UnityEngine.InputSystem.LowLevel;
using UnityEngine.TestTools;
using InputApi = UnityEngine.InputSystem.InputSystem;

namespace MCPForUnityTests.EditMode.Tools.Input
{
    public class InputPauseCleanupTests
    {
        [UnityTest]
        public IEnumerator PausingPlayModeImmediatelyReleasesOwnedInputAndPreservesUnrelatedDevices()
        {
            yield return new EnterPlayMode();

            var input = new InputTestFixture();
            input.Setup();
            IInputSimulationBackend previousBackend = ManageInput.InputBackend;
            var backend = new InputSystemSimulationBackend();
            ManageInput.InputBackend = backend;
            try
            {
                InputApi.settings.updateMode = InputSettings.UpdateMode.ProcessEventsInDynamicUpdate;
                // Given: an unrelated held key and MCP keyboard/touch holds that have not reached their release update.
                Keyboard unrelated = InputApi.AddDevice<Keyboard>("UnrelatedKeyboard");
                InputApi.QueueStateEvent(unrelated, new KeyboardState(Key.B));
                InputApi.Update();
                using (var action = new InputAction(type: InputActionType.Button, binding: "<Keyboard>/a"))
                {
                    int canceled = 0;
                    action.canceled += context => canceled++;
                    action.Enable();
                    Assert.That(
                        ManageInput.HandleCommand(
                            new JObject
                            {
                                ["action"] = "key",
                                ["key"] = "A",
                                ["frames"] = 600,
                            }
                        ),
                        Is.TypeOf<SuccessResponse>()
                    );
                    Assert.That(
                        ManageInput.HandleCommand(
                            new JObject
                            {
                                ["action"] = "touch",
                                ["touch_id"] = 1,
                                ["position"] = new JArray(10, 20),
                                ["frames"] = 600,
                            }
                        ),
                        Is.TypeOf<SuccessResponse>()
                    );
                    Keyboard simulatedKeyboard = Keyboard.current;
                    Touchscreen simulatedTouchscreen = Touchscreen.current;
                    InputApi.Update();
                    Assert.That(simulatedKeyboard.aKey.isPressed, Is.True);
                    Assert.That(simulatedTouchscreen.primaryTouch.press.isPressed, Is.True);
                    Assert.That(action.phase, Is.EqualTo(InputActionPhase.Performed));

                    // When: the real editor pauses, before another game input update or the 30-second timeout.
                    EditorApplication.isPaused = true;
                    yield return null; // Let the editor deliver pauseStateChanged; gameplay remains paused.

                    // Then: the lifecycle callback removes all owned held input immediately and cancels its action.
                    Assert.That(EditorApplication.isPaused, Is.True);
                    Assert.That(simulatedKeyboard.added, Is.False);
                    Assert.That(simulatedTouchscreen.added, Is.False);
                    Assert.That(canceled, Is.EqualTo(1));
                    Assert.That(action.phase, Is.EqualTo(InputActionPhase.Waiting));
                    var status = JObject.FromObject(backend.Status);
                    Assert.That(((JArray)status["held_keys"]).Count, Is.Zero);
                    Assert.That(((JArray)status["active_touch_ids"]).Count, Is.Zero);
                    Assert.That(unrelated.added, Is.True);
                    Assert.That(Keyboard.current, Is.SameAs(unrelated));

                    // Paused Editor updates select a separate state buffer. Resume and select the
                    // player buffer without queuing B again, proving cleanup preserved its held state.
                    EditorApplication.isPaused = false;
                    yield return null; // Let the resumed player loop restore its input state buffer.
                    InputApi.Update();
                    Assert.That(unrelated.bKey.isPressed, Is.True);
                }
            }
            finally
            {
                backend.ReleaseAll();
                ManageInput.InputBackend = previousBackend;
                EditorApplication.isPaused = false;
                input.TearDown();
            }

            yield return new ExitPlayMode();
        }

        [UnityTearDown]
        public IEnumerator RestoreEditModeAfterFailure()
        {
            EditorApplication.isPaused = false;
            if (EditorApplication.isPlaying)
                yield return new ExitPlayMode();
        }
    }
}
#endif
