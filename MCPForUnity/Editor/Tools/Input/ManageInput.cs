using System;
using MCPForUnity.Editor.Helpers;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEngine;

namespace MCPForUnity.Editor.Tools.Input
{
    public interface IInputSimulationBackend
    {
        bool Available { get; }
        string UnavailableReason { get; }
        object Status { get; }
        object Apply(InputSimulationRequest request);
        void ReleaseAll();
    }

    public interface IUguiInputSimulationBackend
    {
        object Click(GameObject target);
    }

    /// <summary>Scenario clicks may wait before dispatch, never after an event has been sent.</summary>
    public interface IUguiScenarioClickBackend
    {
        bool TryClick(GameObject target, out object result, out string detail);
    }

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
                if (!EditorApplication.isPlaying || !EditorApplication.isPlayingOrWillChangePlaymode)
                    return new ErrorResponse("Input simulation requires Play Mode. Start play with manage_editor first.");
                if (EditorApplication.isPaused && request.Action != "release_all")
                    return new ErrorResponse("Resume Play Mode before simulating input; release_all remains available while paused.");
                switch (request.Action)
                {
                    case "release_all":
                        Cleanup();
                        return new SuccessResponse("Released simulated controls and removed MCP virtual devices.");
                    case "ui_click":
                        if (UguiBackend == null)
                            return new ErrorResponse("uGUI simulation is unavailable. Install/enable com.unity.ugui and allow scripts to recompile.");
                        return UguiBackend.Click(InputTargetResolver.Resolve(request.Target));
                    default:
                        if (InputBackend == null || !InputBackend.Available)
                            return new ErrorResponse(
                                InputBackend?.UnavailableReason
                                    ?? "Raw input simulation requires com.unity.inputsystem (1.7+), with Active Input Handling set to Input System Package or Both. Legacy UnityEngine.Input cannot be injected. ui_click can work independently with uGUI."
                            );
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
                    ui_click = UguiBackend != null,
                    input_system = InputBackend?.Available ?? false,
                    keyboard = InputBackend?.Available ?? false,
                    mouse = InputBackend?.Available ?? false,
                    touch = InputBackend?.Available ?? false,
                    multi_touch = InputBackend?.Available ?? false,
                    max_touch_contacts = 10,
                    legacy_raw_input = false,
                    max_frames = InputSimulationRequest.MaxFrames,
                    device_timeout_seconds = InputSimulationRequest.DeviceTimeoutSeconds,
                    raw_input_unavailable_reason = InputBackend?.Available == true
                        ? null
                        : InputBackend?.UnavailableReason
                            ?? "Install com.unity.inputsystem and enable Input System Package or Both under Active Input Handling. Legacy UnityEngine.Input is unsupported.",
                    simulation = InputBackend?.Status,
                    notes = "Virtual devices affect Input System bindings that accept newly added devices. PlayerInput/device-paired actions may require pairing. frames counts game Input System updates (dynamic or fixed), not Editor updates. No OS input is sent.",
                }
            );

        private static void Cleanup() => InputBackend?.ReleaseAll();
    }
}
