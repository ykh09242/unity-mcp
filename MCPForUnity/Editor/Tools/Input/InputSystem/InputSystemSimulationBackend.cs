#if MCP_INPUT_SYSTEM
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Runtime.PlayScenarios;
using UnityEditor;

namespace MCPForUnity.Editor.Tools.Input
{
    /// <summary>Editor lifetime adapter over the shared virtual device backend.</summary>
    [InitializeOnLoad]
    public sealed class InputSystemSimulationBackend : RuntimeInputSystemSimulationBackend
    {
        static InputSystemSimulationBackend()
        {
            ManageInput.InputBackend = new InputSystemSimulationBackend();
            PlayScenarioPlayerInput.CreateInputBackend = () => new InputSystemSimulationBackend();
        }

        protected override double Clock => EditorApplication.timeSinceStartup;

        protected override void SubscribeLifetime()
        {
            EditorApplication.update += CheckTimeout;
            AssemblyReloadEvents.beforeAssemblyReload += ReleaseAll;
            EditorApplication.quitting += ReleaseAll;
            EditorApplication.playModeStateChanged += PlayModeChanged;
            EditorApplication.pauseStateChanged += PauseChanged;
        }

        protected override void UnsubscribeLifetime()
        {
            EditorApplication.update -= CheckTimeout;
            AssemblyReloadEvents.beforeAssemblyReload -= ReleaseAll;
            EditorApplication.quitting -= ReleaseAll;
            EditorApplication.playModeStateChanged -= PlayModeChanged;
            EditorApplication.pauseStateChanged -= PauseChanged;
        }

        private void PlayModeChanged(PlayModeStateChange state)
        {
            if (state == PlayModeStateChange.ExitingPlayMode || state == PlayModeStateChange.EnteredEditMode)
                ReleaseAll();
        }

        private void PauseChanged(PauseState state)
        {
            if (state == PauseState.Paused)
                ReleaseAll();
        }

        private void CheckTimeout() => MaintainLifetime(Clock, EditorApplication.isPlaying && !EditorApplication.isPaused);

        public override object Apply(InputSimulationRequest request) =>
            new SuccessResponse("Queued simulated input for the next game input update.", base.Apply(request));
    }
}
#endif
