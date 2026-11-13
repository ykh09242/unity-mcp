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
                var job = TestJobManager.GetCompletedJobForExport((string)parameters["job_id"]);
                if (job == null)
                    return new ErrorResponse("Test result details are unavailable.");
                // Lifecycle fields belong to the typed job, not optional persisted attribution.
                string format = (string)parameters["format"];
                string status = job.Status.ToString().ToLowerInvariant();
                if (
                    !JToken.DeepEquals(job.ResultArtifact["status"], (JToken)status)
                    || !JToken.DeepEquals(job.ResultArtifact["started_unix_ms"], (JToken)job.StartedUnixMs)
                    || !JToken.DeepEquals(job.ResultArtifact["finished_unix_ms"], (JToken)job.FinishedUnixMs)
                )
                {
                    job.ResultArtifact["status"] = status;
                    job.ResultArtifact["started_unix_ms"] = job.StartedUnixMs;
                    job.ResultArtifact["finished_unix_ms"] = job.FinishedUnixMs;
                    job.ExportMetadataPending = true;
                }
                if (job.ResultArtifact["exports"]?[format] == null)
                    job.ExportMetadataPending = true;
                JObject chunk = TestJobResultExport.Read(TestJobManager.ResultStore, job.JobId, job.ResultArtifact, format, (string)parameters["cursor"]);
                // No successful chunk may escape until its original digest is durable across reload.
                if (job.ExportMetadataPending)
                {
                    if (!TestJobManager.PersistResultArtifactMetadata())
                        return new ErrorResponse("Test result export metadata could not be retained.");
                    job.ExportMetadataPending = false;
                }
                return new SuccessResponse("Test result export chunk.", chunk);
            }
            catch (Exception)
            {
                return new ErrorResponse("Test result export is unavailable or the cursor is invalid.");
            }
        }
    }
}
