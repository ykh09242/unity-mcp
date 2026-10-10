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
        private IDisposable setupRegistration;
        private IDisposable cleanupRegistration;
        private StageProbe setupProbe;
        private StageProbe cleanupProbe;
        public bool IsResetComplete => true;

        private void Awake()
        {
            registration = PlayScenarioResetRegistry.Register("fixture-state", this);
            setupProbe = new StageProbe(this, "setup");
            cleanupProbe = new StageProbe(this, "cleanup");
            setupRegistration = PlayScenarioStateRegistry.Register("fixture-setup", setupProbe);
            cleanupRegistration = PlayScenarioStateRegistry.Register("fixture-cleanup", cleanupProbe);
        }

        public void BeginReset()
        {
            ClicksInIteration = 0;
            ResetCount++;
            Debug.Log("Player fixture reset:" + ResetCount);
        }

        private void OnDestroy()
        {
            registration?.Dispose();
            setupRegistration?.Dispose();
            cleanupRegistration?.Dispose();
        }

        private sealed class StageProbe : IPlayScenarioStateProvider
        {
            private readonly ScenarioPlayerFixtureState owner;
            private readonly string stage;

            internal StageProbe(ScenarioPlayerFixtureState owner, string stage)
            {
                this.owner = owner;
                this.stage = stage;
            }

            public bool TryRead(out PlayScenarioStateValue value)
            {
                if (owner.ResetCount == 2 && ScenarioPlayerFixture.Mode == "iteration-" + stage + "-failure")
                    throw new InvalidOperationException("Player fixture iteration 2 " + stage + " failure.");
                value = PlayScenarioStateValue.FromBoolean(true);
                return true;
            }
        }
    }
}
