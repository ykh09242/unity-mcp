using System;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Editor.Services;
using Newtonsoft.Json.Linq;

namespace MCPForUnity.Editor.Tools
{
    [McpForUnityTool("cancel_test_job", AutoRegister = false, Group = "testing")]
    public static class CancelTestJob
    {
        public static object HandleCommand(JObject parameters)
        {
            if (parameters?["job_id"]?.Type != JTokenType.String || !Guid.TryParseExact((string)parameters["job_id"], "N", out _))
                return new ErrorResponse("Expected a job_id returned by run_tests.");
            try
            {
                return new SuccessResponse(
                    "Cancellation request evaluated; inspect request_state and termination before retrying.",
                    TestJobManager.CancelJob((string)parameters["job_id"])
                );
            }
            catch (NotSupportedException)
            {
                return new ErrorResponse("unsupported_native_test_control");
            }
            catch (ArgumentException)
            {
                return new ErrorResponse("Test job not found.");
            }
            catch (Exception)
            {
                return new ErrorResponse("Native test cancellation is unavailable.");
            }
        }
    }
}
