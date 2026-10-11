#if MCP_INPUT_SYSTEM && !UNITY_EDITOR
using MCPForUnity.Editor.Tools.Input;
using UnityEngine;

namespace MCPForUnity.Runtime.PlayScenarios
{
    internal static class PlayScenarioInputSystemRegistration
    {
        [RuntimeInitializeOnLoadMethod(RuntimeInitializeLoadType.BeforeSceneLoad)]
        private static void Register() => PlayScenarioPlayerInput.CreateInputBackend = () => new RuntimeInputSystemSimulationBackend();
    }
}
#endif
