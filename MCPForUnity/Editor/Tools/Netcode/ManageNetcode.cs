using System;
using System.Reflection;
using MCPForUnity.Editor.Helpers;
using Newtonsoft.Json.Linq;

namespace MCPForUnity.Editor.Tools.Netcode
{
    [McpForUnityTool("manage_netcode", AutoRegister = false, Group = "core")]
    public static class ManageNetcode
    {
        public static object HandleCommand(JObject p)
        {
            try
            {
                if (p?["action"]?.Type != JTokenType.String)
                    return new ErrorResponse("action must be capabilities or preflight.");
                switch ((string)p["action"])
                {
                    case "capabilities":
                        return new SuccessResponse("Netcode optional-package capabilities.", NetcodeAccess.Capabilities());
                    case "preflight":
                        return NetcodePreflight.Inspect(p);
                    default:
                        return new ErrorResponse("Unknown Netcode action. Use capabilities or preflight; this tool is read-only.");
                }
            }
            catch (ArgumentException ex)
            {
                return new ErrorResponse(ex.Message);
            }
            catch (InvalidOperationException ex)
            {
                return new ErrorResponse(ex.Message);
            }
            catch (TargetInvocationException ex)
            {
                return new ErrorResponse("netcode_api_unsupported: " + (ex.InnerException?.Message ?? ex.Message));
            }
        }
    }
}
