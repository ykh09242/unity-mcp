using System;
using MCPForUnity.Editor.Helpers;
using Newtonsoft.Json.Linq;

namespace MCPForUnity.Editor.Tools.ProjectAuditor
{
    [McpForUnityTool("manage_project_auditor", AutoRegister = false, Group = "profiling")]
    public static class ManageProjectAuditor
    {
        public static object HandleCommand(JObject parameters)
        {
            try
            {
                if (parameters == null || parameters["action"]?.Type != JTokenType.String)
                    throw new ArgumentException("action is required.");
                string action = (string)parameters["action"];
                foreach (var parameter in parameters.Properties())
                    if (parameter.Name != "action" && parameter.Name != "audit_id" && parameter.Name != "page_size" && parameter.Name != "cursor")
                        throw new ArgumentException("Unknown Project Auditor parameter: " + parameter.Name);
                if (action != "issues" && (parameters["page_size"] != null || parameters["cursor"] != null))
                    throw new ArgumentException("page_size and cursor are only valid for issues.");
                if (action == "availability")
                {
                    if (parameters["audit_id"] != null)
                        throw new ArgumentException("audit_id is not used by availability.");
                    return new SuccessResponse("Observed optional Project Auditor capability.", ProjectAuditorJobs.Availability());
                }
                string id = parameters["audit_id"]?.Type == JTokenType.String ? (string)parameters["audit_id"] : null;
                if (!ProjectAuditorJobs.ValidId(id))
                    throw new ArgumentException("audit_id must be 32 lower-case hexadecimal characters.");
                switch (action)
                {
                    case "start":
                        return ProjectAuditorJobs.Start(id);
                    case "status":
                        return ProjectAuditorJobs.Status(id);
                    case "issues":
                        int size = 50;
                        if (parameters["page_size"] != null)
                        {
                            if (
                                parameters["page_size"].Type != JTokenType.Integer
                                || !int.TryParse(parameters["page_size"].ToString(), out size)
                                || size < 1
                                || size > 100
                            )
                                throw new ArgumentException("page_size must be an integer from 1 to 100.");
                        }
                        string cursor = null;
                        if (parameters["cursor"] != null)
                        {
                            if (
                                parameters["cursor"].Type != JTokenType.String
                                || string.IsNullOrEmpty((string)parameters["cursor"])
                                || ((string)parameters["cursor"]).Length > 128
                            )
                                throw new ArgumentException("cursor must be a nonempty string of at most 128 characters.");
                            cursor = (string)parameters["cursor"];
                        }
                        return ProjectAuditorJobs.Issues(id, size, cursor);
                    default:
                        throw new ArgumentException("Unsupported Project Auditor action.");
                }
            }
            catch (ArgumentException error)
            {
                return new ErrorResponse("invalid_project_auditor_request", new { message = error.Message });
            }
        }
    }
}
