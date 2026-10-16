using System;
using System.Threading;
using System.Threading.Tasks;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Editor.Tools.Graphics;
using Newtonsoft.Json.Linq;

namespace MCPForUnity.Editor.Resources.Scene
{
    [McpForUnityResource("get_rendering_stats")]
    public static class RenderingStatsResource
    {
        public static Task<object> HandleCommand(JObject @params, CancellationToken cancellationToken) =>
            RenderingStatsOps.GetStatsAsync(@params ?? new JObject(), cancellationToken);

        public static object HandleCommand(JObject @params)
        {
            try
            {
                return RenderingStatsOps.GetStats(@params ?? new JObject());
            }
            catch (Exception e)
            {
                McpLog.Error($"[RenderingStatsResource] Error: {e}");
                return new ErrorResponse($"Error getting rendering stats: {e.Message}");
            }
        }
    }
}
