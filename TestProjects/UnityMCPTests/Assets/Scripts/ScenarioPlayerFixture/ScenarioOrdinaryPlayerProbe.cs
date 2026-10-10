using System;
using MCPForUnity.Runtime;
using MCPForUnity.Runtime.PlayScenarios;
using UnityEngine;

namespace MCPForUnityTests.PlayScenarios.Player
{
    public sealed class ScenarioOrdinaryPlayerProbe : MonoBehaviour
    {
        private void Start()
        {
#if !UNITY_EDITOR && !MCP_FOR_UNITY_PLAY_SCENARIOS
            for (int index = 0; index < PlayScenarioResourceTracker.RegistrationLimit + 1; index++)
            {
                PlayScenarioResourceTracker.RegisterHandle().Dispose();
                PlayScenarioResourceTracker.RegisterSubscription().Dispose();
            }
            if (
                typeof(PlayScenarioPlayerHost).Assembly.GetType("MCPForUnity.Runtime.PlayScenarios.PlayScenarioPlayerRunner") != null
                || PlayScenarioPlayerInput.Backend != null
                || string.IsNullOrEmpty(PlayScenarioResourceTracker.Capture().Error)
            )
                throw new InvalidOperationException("Ordinary Player activated scenario tracking or bootstrap.");
            Debug.Log("ORDINARY_PLAYER_BOUNDARY_VERIFIED");
            Application.Quit(0);
#endif
        }
    }
}
