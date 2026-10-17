using System;
using System.Threading;
using System.Threading.Tasks;
using MCPForUnity.Editor.Helpers;
using Newtonsoft.Json.Linq;

namespace MCPForUnity.Editor.Tools
{
    [McpForUnityTool(
        "inspect_serialized_object_v1",
        AutoRegister = false,
        Group = "scripting_ext",
        Description = "Bounded serialized inspection. Instance-ID lookup may load assets and invoke callbacks before admission."
    )]
    public static class InspectSerializedObject
    {
        public static object HandleCommand(JObject parameters) => Handle(parameters, CancellationToken.None);

        public static Task<object> HandleCommand(JObject parameters, CancellationToken cancellationToken) =>
            Task.FromResult(Handle(parameters, cancellationToken));

        private static object Handle(JObject parameters, CancellationToken token)
        {
            try
            {
                token.ThrowIfCancellationRequested();
                return SerializedObjectInspection.Inspect(SerializedObjectInspection.Options.Parse(parameters), token);
            }
            catch (SerializedObjectInspection.Failure failure)
            {
                return failure.Response();
            }
            catch (ComponentSelection.Failure failure)
            {
                return failure.Response();
            }
            catch (OperationCanceledException)
            {
                throw;
            }
            catch (Exception)
            {
                return new SerializedObjectInspection.Failure(
                    "inspection_unavailable",
                    "Unity could not complete the retained object's serialized projection."
                ).Response();
            }
        }
    }
}
