using System;
using MCPForUnity.Runtime.PlayScenarios;
using UnityEngine;

namespace MCPForUnityTests.PlayScenarios.Player
{
    /// <summary>A static counter has no reset path: each suite child must start in a new process.</summary>
    public sealed class ScenarioPlayerSuiteProbe : MonoBehaviour, IPlayScenarioStateProvider
    {
        public string Label;
        private static int starts;
        private IDisposable registration;
        private bool observed;

        private void Awake()
        {
            if (!Application.isPlaying)
                return;
            registration = PlayScenarioStateRegistry.Register("suite-cold-process", this);
        }

        public bool TryRead(out PlayScenarioStateValue value)
        {
            if (!observed)
            {
                observed = true;
                starts++;
                Debug.Log("PLAYER_SUITE_COLD_PROCESS:" + Label + ":" + starts);
                if (Label == "second" && Environment.GetEnvironmentVariable("MCP_PLAYER_SUITE_FIXTURE_MODE") == "fail-second")
                    Debug.LogError("Player suite deliberate second-child failure.");
            }
            bool waiting = Label == "first" && Environment.GetEnvironmentVariable("MCP_PLAYER_SUITE_FIXTURE_MODE") == "cancel-first";
            value = PlayScenarioStateValue.FromInteger(waiting ? 0 : starts);
            return true;
        }

        private void OnDestroy() => registration?.Dispose();
    }
}
