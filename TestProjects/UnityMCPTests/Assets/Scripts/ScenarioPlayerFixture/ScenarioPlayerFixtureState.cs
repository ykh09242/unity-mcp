using System;
using MCPForUnity.Runtime.PlayScenarios;
using UnityEngine;

namespace MCPForUnityTests.PlayScenarios.Player
{
    public sealed class ScenarioPlayerFixtureState : MonoBehaviour, IPlayScenarioResetParticipant
    {
        public int ClicksInIteration;
        public int TotalClicks;
        public int ResetCount;
        private IDisposable registration;
        public bool IsResetComplete => true;

        private void Awake() => registration = PlayScenarioResetRegistry.Register("fixture-state", this);

        public void BeginReset()
        {
            ClicksInIteration = 0;
            ResetCount++;
            Debug.Log("Player fixture reset:" + ResetCount);
        }

        private void OnDestroy() => registration?.Dispose();
    }
}
