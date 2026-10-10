#if MCP_INPUT_UGUI
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Runtime.PlayScenarios;
using UnityEditor;
using UnityEngine;

namespace MCPForUnity.Editor.Tools.Input
{
    [InitializeOnLoad]
    public sealed class UguiInputSimulationBackend : IUguiInputSimulationBackend, IUguiScenarioClickBackend, IUguiScenarioRaycastClickBackend
    {
        private readonly UguiScenarioDispatch dispatch = new UguiScenarioDispatch();

        static UguiInputSimulationBackend() => ManageInput.UguiBackend = new UguiInputSimulationBackend();

        public object Click(GameObject target) => Wrap(dispatch.Click(target));

        public bool TryClick(GameObject target, out object result, out string detail)
        {
            bool ready = dispatch.TryClick(target, out object value, out detail);
            result = ready ? Wrap(value) : null;
            return ready;
        }

        public bool TryRaycastClick(GameObject target, out object result, out string detail)
        {
            bool ready = dispatch.TryRaycastClick(target, out object value, out detail);
            result = ready ? Wrap(value) : null;
            return ready;
        }

        private static object Wrap(object value)
        {
            var click = (PlayScenarioClickResult)value;
            return new SuccessResponse(click.Message, click.Data);
        }
    }
}
#endif
