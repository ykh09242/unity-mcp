using System;
using MCPForUnity.Editor.Services.PlayScenarios;
using MCPForUnity.Runtime.Helpers;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEngine;

namespace MCPForUnity.Editor.Tools.Input
{
    /// <summary>Old Input Handling observation only. Never changes Active Input Handling, focus or native input state.</summary>
    [InitializeOnLoad]
    public sealed class LegacyInputScenarioRecordingBackend : IPlayScenarioInputRecordingBackend, IPlayScenarioInputRecordingCapabilities
    {
        public const string RawReplayUnavailableReason =
            "UnityEngine.Input has no public raw-state injection API. Old Input Handling supports uGUI ui_click and legacy observation, but recorded raw input cannot replay into UnityEngine.Input. Review the draft and use target-based click_ui for uGUI; Input System virtual-device replay only affects Input System consumers.";

        static LegacyInputScenarioRecordingBackend()
        {
#if ENABLE_LEGACY_INPUT_MANAGER && !ENABLE_INPUT_SYSTEM
            PlayScenarioRecordingService.InputBackend = new LegacyInputScenarioRecordingBackend();
#endif
        }

        public JObject Capabilities => DescribeCapabilities();

        public static JObject DescribeCapabilities() =>
            new JObject
            {
                ["backend"] = "legacy_input_manager",
                ["source"] = "UnityEngine.Input",
                ["frame_clock"] = "game_update",
                ["keyboard"] = true,
                ["mouse"] = true,
                ["touch"] = true,
                ["wheel"] = true,
                ["gamepad"] = false,
                ["raw_replay_available"] = false,
                ["raw_replay_unavailable_reason"] = RawReplayUnavailableReason,
                ["limitations"] = new JArray(
                    "Only observed game-frame edges are recorded; keys held before recording, IME text, Input Manager axes/buttons and platform-specific joystick mappings are not inferred.",
                    "Legacy wheel values retain UnityEngine.Input units. Cross-backend timing, keyboard layout and wheel scaling require review.",
                    "Unity focus/background policy still determines delivered physical input. Recording neither focuses windows nor changes runInBackground or input settings."
                ),
            };

        public IPlayScenarioInputRecordingSession Begin(Action<JObject, long> observed, Action<string> diagnose)
        {
#if ENABLE_LEGACY_INPUT_MANAGER && !ENABLE_INPUT_SYSTEM
            if (!EditorApplication.isPlaying || EditorApplication.isPaused)
                throw new InvalidOperationException("Legacy input recording requires running, unpaused Play Mode.");
            diagnose(RawReplayUnavailableReason);
            return new Session(observed, diagnose);
#else
            throw new InvalidOperationException("The legacy observer is selected only for Old Input Handling; New and Both retain the Input System observer.");
#endif
        }

#if ENABLE_LEGACY_INPUT_MANAGER && !ENABLE_INPUT_SYSTEM
        private sealed class Source : ILegacyInputRecordingSource
        {
            public int FrameCount => Time.frameCount;
            public bool AnyKey => UnityEngine.Input.anyKey;
            public bool AnyKeyDown => UnityEngine.Input.anyKeyDown;

            public bool GetKeyDown(KeyCode key) => UnityEngine.Input.GetKeyDown(key);

            public bool GetKeyUp(KeyCode key) => UnityEngine.Input.GetKeyUp(key);

            public bool MousePresent => UnityEngine.Input.mousePresent;
            public Vector2 MousePosition => UnityEngine.Input.mousePosition;
            public Vector2 MouseScrollDelta => UnityEngine.Input.mouseScrollDelta;

            public bool GetMouseButtonDown(int button) => UnityEngine.Input.GetMouseButtonDown(button);

            public bool GetMouseButtonUp(int button) => UnityEngine.Input.GetMouseButtonUp(button);

            public bool SimulateMouseWithTouches => UnityEngine.Input.simulateMouseWithTouches;
            public int TouchCount => UnityEngine.Input.touchCount;

            public LegacyInputTouch GetTouch(int index)
            {
                Touch touch = UnityEngine.Input.GetTouch(index);
                return new LegacyInputTouch(touch.fingerId, touch.position, touch.phase);
            }
        }

        private sealed class Session : IPlayScenarioInputRecordingSession
        {
            private readonly LegacyInputObservation observation;
            private LegacyInputRecordingPump pump;
            public long UpdateCount => observation.UpdateCount;

            public Session(Action<JObject, long> observed, Action<string> diagnose)
            {
                observation = new LegacyInputObservation(new Source(), observed, diagnose);
                try
                {
                    pump = LegacyInputRecordingPump.Begin(observation.Sample, Dispose);
                }
                catch
                {
                    observation.Dispose();
                    throw;
                }
            }

            public void Dispose()
            {
                observation.Dispose();
                var owned = pump;
                pump = null;
                owned?.Cancel();
            }
        }
#endif
    }
}
