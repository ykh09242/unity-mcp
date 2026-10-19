using System;
using System.Threading;
using System.Threading.Tasks;
using MCPForUnity.Editor.Helpers;
using Newtonsoft.Json.Linq;

namespace MCPForUnity.Editor.Tools.Terrain
{
    [McpForUnityTool("manage_terrain", Group = "core")]
    public static class ManageTerrain
    {
        public static object HandleCommand(JObject request) => Handle(request, CancellationToken.None);

        public static Task<object> HandleCommand(JObject request, CancellationToken cancellationToken) => Task.FromResult(Handle(request, cancellationToken));

        private static object Handle(JObject request, CancellationToken token)
        {
#if MCP_FOR_UNITY_TERRAIN
            try
            {
                return TerrainEdit.Handle(request, token);
            }
            catch (TerrainRequestException ex)
            {
                return new ErrorResponse(ex.Code, new { message = ex.Message });
            }
            catch (OperationCanceledException)
            {
                return new ErrorResponse("terrain_cancelled");
            }
            catch (Exception)
            {
                return new ErrorResponse("terrain_operation_failed", new { message = "Native Terrain operation failed; no automatic retry was performed." });
            }
#else
            return new ErrorResponse("terrain_unavailable", new { message = "The built-in Terrain module and its supported version define are required." });
#endif
        }
    }
}
