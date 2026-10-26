using System;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Editor.Services.PlayScenarios;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEngine;

namespace MCPForUnity.Editor.Tools.Input
{
    /// <summary>Optional assemblies register strongly typed adapters without adding package dependencies.</summary>
    [InitializeOnLoad]
    [McpForUnityTool(
        "manage_input",
        AutoRegister = false,
        Group = "testing",
        Description = "Bounded Play Mode input: uGUI events and optional Input System virtual devices."
    )]
    public static class ManageInput
    {
        public static IInputSimulationBackend InputBackend { get; set; }
        public static IUguiInputSimulationBackend UguiBackend { get; set; }

        static ManageInput()
        {
            AssemblyReloadEvents.beforeAssemblyReload += Cleanup;
            EditorApplication.quitting += Cleanup;
            EditorApplication.pauseStateChanged += state =>
            {
                if (state == PauseState.Paused)
                    Cleanup();
            };
            EditorApplication.playModeStateChanged += state =>
            {
                if (state == PlayModeStateChange.ExitingPlayMode || state == PlayModeStateChange.EnteredEditMode)
                    Cleanup();
            };
        }

        public static object HandleCommand(JObject parameters)
        {
            try
            {
                InputSimulationRequest request = InputSimulationRequest.Parse(parameters);
                if (request.Action == "status")
                    return Status();
                if (request.Action == "player_status")
                {
                    if (!(InputBackend is IPlayerInputSimulationBackend observer))
                        return new ErrorResponse("PlayerInput diagnostics require the optional Input System backend.");
                    return new SuccessResponse(
                        "Selected PlayerInput routing diagnostics.",
                        observer.DiagnosePlayer(InputTargetResolver.Resolve(request.Target))
                    );
                }
                if (!EditorApplication.isPlaying || !EditorApplication.isPlayingOrWillChangePlaymode)
                    return new ErrorResponse("Input simulation requires Play Mode. Start play with manage_editor first.");
                if (EditorApplication.isPaused && request.Action != "release_all" && request.Action != "release_player")
                    return new ErrorResponse("Resume Play Mode before simulating input; release_all and release_player remain available while paused.");
                switch (request.Action)
                {
                    case "release_all":
                        Cleanup();
                        return new SuccessResponse("Released simulated controls and removed MCP virtual devices.");
                    case "pair_player":
                    case "release_player":
                        if (!(InputBackend is IPlayerInputSimulationBackend routing))
                            return new ErrorResponse("Explicit PlayerInput pairing requires the optional Input System backend.");
                        GameObject player = InputTargetResolver.Resolve(request.Target);
                        object detail = request.Action == "pair_player" ? routing.PairPlayer(player, request.Device) : routing.ReleasePlayer(player);
                        return new SuccessResponse("Selected PlayerInput pairing lease updated.", detail);
                    case "ui_click":
                        if (UguiBackend == null)
                            return new ErrorResponse("uGUI simulation is unavailable. Install/enable com.unity.ugui and allow scripts to recompile.");
                        return UguiBackend.Click(InputTargetResolver.Resolve(request.Target));
                    default:
                        if (InputBackend == null || !InputBackend.Available)
                            return new ErrorResponse(InputBackend?.UnavailableReason ?? RawInputUnavailableReason);
                        return InputBackend.Apply(request);
                }
            }
            catch (ArgumentException exception)
            {
                return new ErrorResponse(exception.Message);
            }
            catch (InvalidOperationException exception)
            {
                Cleanup();
                return new ErrorResponse("Input simulation failed: " + exception.Message);
            }
        }

        private static object Status() =>
            new SuccessResponse(
                "Input simulation capabilities.",
                new
                {
                    playing = EditorApplication.isPlaying,
                    paused = EditorApplication.isPaused,
                    active_input_handling = ActiveInputHandling,
                    ui_click = UguiBackend != null,
                    input_system = InputBackend?.Available ?? false,
                    keyboard = InputBackend?.Available ?? false,
                    mouse = InputBackend?.Available ?? false,
                    touch = InputBackend?.Available ?? false,
                    gamepad = InputBackend?.Available ?? false,
                    wheel = InputBackend?.Available ?? false,
                    player_input_diagnostics = InputBackend is IPlayerInputSimulationBackend,
                    reversible_gamepad_pairing = InputBackend is IPlayerInputSimulationBackend && InputBackend.Available,
                    multi_touch = InputBackend?.Available ?? false,
                    max_touch_contacts = 10,
                    legacy_raw_input = false,
                    legacy_raw_input_unavailable_reason = LegacyInputScenarioRecordingBackend.RawReplayUnavailableReason,
                    legacy_input_observation = LegacyInputObservationAvailable,
                    legacy_input_observation_unavailable_reason = LegacyInputObservationUnavailableReason,
                    input_observation = (PlayScenarioRecordingService.InputBackend as IPlayScenarioInputRecordingCapabilities)?.Capabilities,
                    ui_dispatch_changes_raw_input = false,
                    max_frames = InputSimulationRequest.MaxFrames,
                    device_timeout_seconds = InputSimulationRequest.DeviceTimeoutSeconds,
                    raw_input_unavailable_reason = InputBackend?.Available == true ? null : InputBackend?.UnavailableReason ?? RawInputUnavailableReason,
                    simulation = InputBackend?.Status,
                    notes = "ui_click dispatches uGUI events independently of Old/New/Both input handling and does not change UnityEngine.Input. Old mode supports legacy observation, not raw injection. Virtual devices affect Input System consumers only, including in Both mode. Use player_status for actual action/device resolution and pair_player for explicit reversible routing to one selected PlayerInput. release_player ends the owned device lease and restores prior membership. frames counts game Input System updates (dynamic or fixed), not Editor updates. No OS input is sent.",
                }
            );

        public static string RawInputUnavailableReason =>
            LegacyInputObservationAvailable
                ? LegacyInputScenarioRecordingBackend.RawReplayUnavailableReason
                : "Raw virtual-device replay requires an enabled com.unity.inputsystem 1.7+ backend, Input System Package/Both handling and an Input System consumer. Changing handling alone does not redirect UnityEngine.Input. Use ui_click or scenario click_ui for uGUI targets; a project-defined input adapter requires explicit project-code integration. MCP does not change input settings or send OS input.";

        public static bool LegacyInputObservationAvailable =>
#if ENABLE_LEGACY_INPUT_MANAGER && !ENABLE_INPUT_SYSTEM
            true;
#else
            false;
#endif

        public static string LegacyInputObservationUnavailableReason =>
            LegacyInputObservationAvailable
                ? null
                : "Legacy input observation is selected only for Old Input Handling. New and Both retain the Input System observer; inspect input_observation for its current capabilities.";

        public static string ActiveInputHandling =>
#if ENABLE_LEGACY_INPUT_MANAGER && ENABLE_INPUT_SYSTEM
            "Both";
#elif ENABLE_LEGACY_INPUT_MANAGER
            "Old";
#elif ENABLE_INPUT_SYSTEM
            "Input System Package";
#else
            "unknown";
#endif

        private static void Cleanup() => InputBackend?.ReleaseAll();
    }
}
