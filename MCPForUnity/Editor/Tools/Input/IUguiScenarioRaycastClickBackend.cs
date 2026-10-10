using UnityEngine;

namespace MCPForUnity.Editor.Tools.Input
{
    /// <summary>Raycast-verified scenario input waits only before sending any pointer event.</summary>
    public interface IUguiScenarioRaycastClickBackend
    {
        bool TryRaycastClick(GameObject target, out object result, out string detail);
    }
}
