using System;
using System.Threading;
using System.Threading.Tasks;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Editor.Services;
using Newtonsoft.Json.Linq;
using UnityEngine;

namespace MCPForUnity.Editor.Resources.Scene
{
    [McpForUnityResource("get_gameobject_by_handle_v2")]
    public static class GameObjectHandleResource
    {
        public static object HandleCommand(JObject parameters) => Handle(parameters, CancellationToken.None);

        public static Task<object> HandleCommand(JObject parameters, CancellationToken cancellationToken) =>
            Task.FromResult(Handle(parameters, cancellationToken));

        private static object Handle(JObject parameters, CancellationToken token)
        {
            if (!MCPServiceLocator.ResourceDiscovery.IsResourceEnabled("get_gameobject"))
                return new ObjectHandles.Failure("resource_disabled", "The original GameObject resource is disabled.").Response();
            try
            {
                token.ThrowIfCancellationRequested();
                if (parameters == null || parameters.Count != 1 || parameters["handle"]?.Type != JTokenType.String)
                    throw new ObjectHandles.Failure("invalid_parameters", "Supply only the opaque handle string.");
                var resolved = ObjectHandles.Resolve((string)parameters["handle"], token);
                if (!(resolved.Object is GameObject go))
                    throw new ObjectHandles.Failure("object_kind_mismatch", "This resource requires a GameObject handle.");
                token.ThrowIfCancellationRequested();
                object metadata = ObjectHandles.Operation(
                    "resource_metadata",
                    () =>
                    {
                        ObjectHandles.Validate(resolved, token);
                        return GameObjectResource.SerializeGameObject(go);
                    },
                    token
                );
                ObjectHandles.Validate(resolved, token);
                return new SuccessResponse("GameObject session metadata", new { identity = resolved.Descriptor, gameObject = metadata });
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
                return new ObjectHandles.Failure("session_identity_unavailable", "Unity could not complete this object's session reference read.").Response();
            }
        }
    }
}
