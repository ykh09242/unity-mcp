using System;
using System.Threading;
using System.Threading.Tasks;
using Newtonsoft.Json.Linq;

namespace MCPForUnity.Editor.Tools.Capabilities
{
    internal static class ToolCapabilityDispatch
    {
        private static bool Admits(JObject parameters, string[] actions)
        {
            var token = parameters?["action"];
            if (token?.Type != JTokenType.String)
                return false;
            return Array.IndexOf(actions, ((string)token).ToLowerInvariant()) >= 0;
        }

        private static object Rejected() =>
            new
            {
                success = false,
                error = "action_outside_capability",
                message = "This action belongs to a different feature tool.",
            };

        internal static BatchActionValidation ValidateBatch(JObject parameters, string[] actions, Func<JObject, BatchActionValidation> validator) =>
            Admits(parameters, actions) ? validator(parameters) : BatchActionValidation.Rejected("unsupported_action");

        internal static object Invoke(JObject parameters, string[] actions, Func<JObject, object> handler) =>
            Admits(parameters, actions) ? handler(parameters) : Rejected();

        internal static Task<object> InvokeAsync(
            JObject parameters,
            string[] actions,
            Func<JObject, CancellationToken, Task<object>> handler,
            CancellationToken cancellationToken
        )
        {
            cancellationToken.ThrowIfCancellationRequested();
            return Admits(parameters, actions) ? handler(parameters, cancellationToken) : Task.FromResult(Rejected());
        }
    }
}
