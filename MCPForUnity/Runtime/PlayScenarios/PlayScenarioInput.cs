using UnityEngine;

namespace MCPForUnity.Editor.Tools.Input
{
    public interface IUguiInputSimulationBackend
    {
        object Click(GameObject target);
    }

    /// <summary>Scenario clicks may wait before dispatch, never after an event has been sent.</summary>
    public interface IUguiScenarioClickBackend
    {
        bool TryClick(GameObject target, out object result, out string detail);
    }

    public interface IUguiScenarioRaycastClickBackend
    {
        bool TryRaycastClick(GameObject target, out object result, out string detail);
    }
}

namespace MCPForUnity.Runtime.PlayScenarios
{
    public static class PlayScenarioPlayerInput
    {
        public static MCPForUnity.Editor.Tools.Input.IUguiInputSimulationBackend Backend { get; set; }
    }

    public sealed class PlayScenarioClickResult
    {
        public string Message { get; }
        public object Data { get; }

        public PlayScenarioClickResult(string message, object data)
        {
            Message = message;
            Data = data;
        }
    }
}
