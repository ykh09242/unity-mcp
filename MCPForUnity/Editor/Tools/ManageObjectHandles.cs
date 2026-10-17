using System;
using System.Linq;
using System.Numerics;
using System.Threading;
using System.Threading.Tasks;
using MCPForUnity.Editor.Helpers;
using Newtonsoft.Json.Linq;

namespace MCPForUnity.Editor.Tools
{
    [McpForUnityTool("manage_object_handles_v2", AutoRegister = false)]
    public static class ManageObjectHandles
    {
        public static object HandleCommand(JObject parameters) => Handle(parameters, CancellationToken.None);

        public static Task<object> HandleCommand(JObject parameters, CancellationToken cancellationToken) =>
            Task.FromResult(Handle(parameters, cancellationToken));

        private static object Handle(JObject parameters, CancellationToken token)
        {
            try
            {
                token.ThrowIfCancellationRequested();
                string action = parameters?["action"]?.Type == JTokenType.String ? (string)parameters["action"] : null;
                string[] allowed =
                    action == "capture" ? new[] { "action", "instance_id" }
                    : action == "resolve" ? new[] { "action", "handle" }
                    : null;
                if (allowed == null || parameters.Properties().Any(property => !allowed.Contains(property.Name)))
                    throw Invalid("Use capture or resolve with only that action's exact fields.");
                if (action == "capture")
                {
                    JToken id = parameters["instance_id"];
                    if (
                        id?.Type != JTokenType.Integer
                        || !BigInteger.TryParse(id.ToString(), out BigInteger value)
                        || value == 0
                        || value < int.MinValue
                        || value > int.MaxValue
                    )
                        throw Invalid("instance_id must be a nonzero Int32 integer.");
                    return new SuccessResponse("Object session reference captured", ObjectHandles.Capture(ObjectHandles.Lookup((int)value, token), token));
                }
                if (parameters["handle"]?.Type != JTokenType.String)
                    throw Invalid("handle must be an opaque string.");
                var resolved = ObjectHandles.Resolve((string)parameters["handle"], token);
                return new SuccessResponse("Object session reference resolved", resolved.Descriptor);
            }
            catch (ObjectHandles.Failure failure)
            {
                return failure.Response();
            }
            catch (OperationCanceledException)
            {
                throw;
            }
            catch (Exception)
            {
                return new ObjectHandles.Failure(
                    "session_identity_unavailable",
                    "Unity could not complete this object's session reference operation."
                ).Response();
            }
        }

        private static ObjectHandles.Failure Invalid(string message) => new ObjectHandles.Failure("invalid_parameters", message);
    }
}
