using UnityEngine;

namespace MCPForUnity.Editor.Tools.Input
{
    /// <summary>Optional Input System routing boundary without a core package reference.</summary>
    public interface IPlayerInputSimulationBackend
    {
        object DiagnosePlayer(GameObject target);
        object PairPlayer(GameObject target, string device);
        object ReleasePlayer(GameObject target);
    }
}
