using UnityEngine;

namespace MCPForUnityTests.PlayScenarios.Player
{
    public enum EncounterState
    {
        Idle,
        Combat,
        Reward,
    }

    public sealed class SavedEnumProbe : MonoBehaviour
    {
        public EncounterState State;
    }
}
