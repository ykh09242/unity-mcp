using System;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Editor.Services;
using Newtonsoft.Json.Linq;

namespace MCPForUnity.Editor.Resources.Editor
{
    /// <summary>
    /// Provides dynamic editor state information that changes frequently.
    /// </summary>
    [McpForUnityResource("get_editor_state")]
    public static class EditorState
    {
        public static object HandleCommand(JObject @params)
        {
            try
            {
                JObject target = null;
                if (@params?["expected_editor_generation"] is JToken expected)
                {
                    target = ProjectIdentityUtility.CaptureEditorTarget();
                    if (expected.Type != JTokenType.String || !JToken.DeepEquals(expected, target["generation"]))
                        return new ErrorResponse("editor_generation_mismatch");
                }
                var snapshot = EditorStateCache.GetSnapshot();
                if (target != null)
                    snapshot["editor_target"] = target;
                if (
                    @params?["_editor_clock_request"]?.Type == JTokenType.String
                    && Guid.TryParseExact((string)@params["_editor_clock_request"], "D", out var requestId)
                )
                {
                    // GetSnapshot returns a detached clone. Sample when answering this
                    // request, independently of when the cached state was observed.
                    snapshot["_editor_clock"] = new JObject
                    {
                        ["request_id"] = requestId.ToString("D"),
                        ["unix_ms"] = DateTimeOffset.UtcNow.ToUnixTimeMilliseconds(),
                    };
                }
                return new SuccessResponse("Retrieved editor state.", snapshot);
            }
            catch (Exception e)
            {
                return new ErrorResponse($"Error getting editor state: {e.Message}");
            }
        }
    }
}
