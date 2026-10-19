using System;
using MCPForUnity.Editor.Helpers;
using Newtonsoft.Json.Linq;

namespace MCPForUnity.Editor.Tools.Addressables
{
    [McpForUnityTool("manage_addressables", Group = "core")]
    public static class ManageAddressables
    {
        public static object HandleCommand(JObject request)
        {
            try
            {
                if (request == null)
                    return new ErrorResponse("invalid_addressables_request");
                string action = AddressablesState.Text(request["action"], "action", 32).ToLowerInvariant();
                request = (JObject)request.DeepClone();
                request["action"] = action;
                AddressablesState.Validate(request, action);
                if (action == "analyze_layout")
                    return AddressablesLayout.Read(request);
                AddressablesState state = AddressablesState.Capture(request);
                if (action == "inspect")
                    return state.Page(request, state.Rows(request.Value<string>("section") ?? "entries"));
                if (action == "preflight")
                    return state.Page(request, AddressablesPreflight.Read(state));
                if (action == "set_address" || action == "move_entry" || action == "set_profile_value")
                    return AddressablesEdit.Apply(state, request);
                return new ErrorResponse("invalid_addressables_action");
            }
            catch (AddressablesException ex)
            {
                return new ErrorResponse(ex.Code, new { message = ex.Message });
            }
            catch (Exception)
            {
                return new ErrorResponse(
                    "addressables_operation_failed",
                    new { message = "Optional Addressables operation failed; no content build or retry was started." }
                );
            }
        }
    }
}
