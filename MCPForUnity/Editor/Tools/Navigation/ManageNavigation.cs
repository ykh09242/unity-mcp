using System;
using System.Reflection;
using MCPForUnity.Editor.Helpers;
using Newtonsoft.Json.Linq;

namespace MCPForUnity.Editor.Tools.Navigation
{
    [McpForUnityTool("manage_navigation", AutoRegister = false, Group = "core")]
    public static class ManageNavigation
    {
        public static object HandleCommand(JObject p)
        {
            if (p == null)
                return new ErrorResponse("Navigation parameters are required.");
#if !MCP_FOR_UNITY_AI_MODULE
            if ((string)p["action"] == "capabilities")
                return new SuccessResponse(
                    "Navigation module unavailable.",
                    new
                    {
                        module_available = false,
                        package_available = false,
                        package_version = (string)null,
                        package_name = "com.unity.ai.navigation",
                        native_bake_interruptible = false,
                        detail = "com.unity.modules.ai is not installed. No module or package was installed implicitly.",
                    }
                );
            return new ErrorResponse("ai_module_unavailable: com.unity.modules.ai is required for native navigation operations.");
#else
            try
            {
                switch ((string)p["action"])
                {
                    case "capabilities":
                        return NavigationAccess.Capabilities();
                    case "list":
                        return NavigationObjects.List(p);
                    case "read":
                        return new SuccessResponse("Navigation component.", NavigationObjects.Read(NavigationAccess.Component(p)));
                    case "add":
                        return NavigationObjects.Add(p);
                    case "configure":
                        return NavigationObjects.Configure(p);
                    case "bake":
                        return NavigationBakeJobs.Request(p);
                    case "bake_status":
                        return NavigationBakeJobs.Status(p);
                    case "cancel_bake":
                        return NavigationBakeJobs.Cancel(p);
                    case "sample_position":
                        return NavigationPaths.Query(p, false);
                    case "calculate_path":
                        return NavigationPaths.Query(p, true);
                    default:
                        return new ErrorResponse(
                            "Unknown navigation action. Use capabilities, list, read, add, configure, bake, bake_status, cancel_bake, sample_position or calculate_path."
                        );
                }
            }
            catch (TargetInvocationException ex)
            {
                return new ErrorResponse("Navigation native operation failed: " + (ex.InnerException?.Message ?? ex.Message));
            }
            catch (ArgumentException ex)
            {
                return new ErrorResponse(ex.Message);
            }
            catch (InvalidOperationException ex)
            {
                return new ErrorResponse(ex.Message);
            }
#endif
        }
    }
}
