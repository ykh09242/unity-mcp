using System;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Editor.Services;
using Newtonsoft.Json.Linq;

namespace MCPForUnity.Editor.Tools
{
    /// <summary>
    /// Poll a previously started async test job by job_id.
    /// </summary>
    [McpForUnityTool("get_test_job", AutoRegister = false, Group = "testing")]
    public static class GetTestJob
    {
        public static object HandleCommand(JObject @params)
        {
            string jobId = @params?["job_id"]?.ToString() ?? @params?["jobId"]?.ToString();
            if (string.IsNullOrWhiteSpace(jobId))
            {
                return new ErrorResponse("Missing required parameter 'job_id'.");
            }

            var p = new ToolParams(@params);
            bool includeDetails = p.GetBool("includeDetails");
            bool includeFailedTests = p.GetBool("includeFailedTests");

            var job = TestJobManager.GetJob(jobId);
            if (job == null)
            {
                return new ErrorResponse("Unknown job_id.");
            }

            object payload;
            if (@params?["page_size"] != null || @params?["cursor"] != null)
            {
                if (
                    @params?["page_size"]?.Type != JTokenType.Integer
                    || (long)@params["page_size"] < 1
                    || (long)@params["page_size"] > 100
                    || @params?["cursor"] != null && @params["cursor"].Type != JTokenType.String
                    || !includeDetails && !includeFailedTests
                )
                    return new ErrorResponse("Paging requires page_size 1..100, optional string cursor and explicit detail selection.");
                try
                {
                    var value = JObject.FromObject(TestJobManager.ToSerializable(job, false, false));
                    // Paging is also used while polling. Running jobs and interrupted
                    // jobs without a checkpoint retain their progress/error instead
                    // of turning an ordinary status read into an invalid-cursor error.
                    // An explicit continuation must still be validated against results.
                    value["result_page"] =
                        string.IsNullOrEmpty((string)@params["cursor"]) && !TestJobManager.HasResultPages(job)
                            ? JValue.CreateNull()
                            : JObject.FromObject(
                                TestJobManager.Page(job, (int)@params["page_size"], (string)@params["cursor"], !includeDetails && includeFailedTests)
                            );
                    payload = value;
                }
                catch (Exception)
                {
                    return new ErrorResponse("Test result page is unavailable or the cursor is invalid.");
                }
            }
            else
                payload = TestJobManager.ToSerializable(job, includeDetails, includeFailedTests);
            return new SuccessResponse("Test job status retrieved.", payload);
        }
    }
}
