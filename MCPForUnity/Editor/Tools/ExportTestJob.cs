using System;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Editor.Services;
using Newtonsoft.Json.Linq;

namespace MCPForUnity.Editor.Tools
{
    [McpForUnityTool("export_test_job", AutoRegister = false, Group = "testing")]
    public static class ExportTestJob
    {
        public static object HandleCommand(JObject parameters)
        {
            try
            {
                if (
                    parameters?["job_id"]?.Type != JTokenType.String
                    || parameters["format"]?.Type != JTokenType.String
                    || parameters["cursor"] != null && parameters["cursor"].Type != JTokenType.String
                )
                    return new ErrorResponse("Expected job_id/format strings and optional cursor string.");
                var job = TestJobManager.GetJob((string)parameters["job_id"]);
                if (job?.CompletionKind != "completed" || job.ResultArtifact == null)
                    return new ErrorResponse("Test result details are unavailable.");
                // Lifecycle fields belong to the typed job, not optional persisted attribution.
                job.ResultArtifact["status"] = job.Status.ToString().ToLowerInvariant();
                job.ResultArtifact["started_unix_ms"] = job.StartedUnixMs;
                job.ResultArtifact["finished_unix_ms"] = job.FinishedUnixMs;
                JObject chunk = TestJobResultExport.Read(
                    TestJobManager.ResultStore,
                    job.JobId,
                    job.ResultArtifact,
                    (string)parameters["format"],
                    (string)parameters["cursor"]
                );
                // No successful chunk may escape until its original digest is durable across reload.
                if (!TestJobManager.PersistResultArtifactMetadata())
                    return new ErrorResponse("Test result export metadata could not be retained.");
                return new SuccessResponse("Test result export chunk.", chunk);
            }
            catch (Exception)
            {
                return new ErrorResponse("Test result export is unavailable or the cursor is invalid.");
            }
        }
    }
}
