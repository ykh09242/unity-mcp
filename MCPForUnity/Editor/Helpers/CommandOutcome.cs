using System;
using System.Collections.Generic;
using Newtonsoft.Json.Linq;

namespace MCPForUnity.Editor.Helpers
{
    internal enum CommandOutcome
    {
        Unknown,
        Success,
        Error,
    }

    internal static class CommandOutcomeMetadata
    {
        internal static CommandOutcome FromResult(object result)
        {
            try
            {
                return ReadResult(result);
            }
            catch
            {
                return CommandOutcome.Unknown;
            }
        }

        private static CommandOutcome ReadResult(object result)
        {
            if (result is IMcpResponse response)
                return response.Success ? CommandOutcome.Success : CommandOutcome.Error;
            if (result is JObject json)
                return
                    json["success"]?.Type == JTokenType.Boolean && !json.Value<bool>("success")
                    || json["status"]?.Type == JTokenType.String && string.Equals(json.Value<string>("status"), "error", StringComparison.OrdinalIgnoreCase)
                    ? CommandOutcome.Error
                    : CommandOutcome.Success;
            if (result is IDictionary<string, object> values)
                return values.TryGetValue("success", out object success) && success is bool flag && !flag ? CommandOutcome.Error : CommandOutcome.Success;
            // Legacy anonymous results expose only this scalar. Never walk or serialize their payload.
            var property = result?.GetType().GetProperty("success") ?? result?.GetType().GetProperty("Success");
            if (property?.PropertyType == typeof(bool) && property.GetIndexParameters().Length == 0)
                return (bool)property.GetValue(result) ? CommandOutcome.Success : CommandOutcome.Error;
            return CommandOutcome.Success;
        }
    }
}
