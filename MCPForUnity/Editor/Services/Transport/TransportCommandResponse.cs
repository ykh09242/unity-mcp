using System;
using System.Threading.Tasks;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;

namespace MCPForUnity.Editor.Services.Transport
{
    /// <summary>A response envelope retained as structured data until the transport writes it.</summary>
    internal sealed class TransportCommandResponse
    {
        private TransportCommandResponse(object payload) => this.payload = payload;

        private object payload;
        private JObject admission;
        public object Payload => admission == null ? payload : new { native_arbitration = admission, payload };

        // Only the dispatcher attaches provenance, after checking the transport-owned request binding.
        internal TransportCommandResponse WithAdmission(JObject request, bool notStarted = false, NativeAdmissionBlocker blocker = default)
        {
            if (!IsBoundRequest(request))
                return this;
            admission = new JObject
            {
                ["version"] = 1,
                ["request_nonce"] = request.Value<string>("request_nonce"),
                ["instance_generation"] = NativeCommandArbitrator.Shared.Generation,
                ["disposition"] = notStarted ? "not_started" : "started",
                ["retryable"] = notStarted && blocker.Retryable,
            };
            if (notStarted)
            {
                admission["reason"] = blocker.Reason;
                // Keep the negotiated proof schema unchanged for older servers.
                // Diagnostics belong to the refusal payload, not replay authority.
                if (blocker.RecoveryAction != null && payload is JObject result)
                {
                    var data = result["data"] as JObject;
                    if (data == null)
                        result["data"] = data = new JObject();
                    var coordination = new JObject
                    {
                        ["blocked"] = true,
                        ["reason"] = blocker.Reason,
                        ["retryable"] = blocker.Retryable,
                        ["recovery_action"] = blocker.RecoveryAction,
                    };
                    if (blocker.Recovery != null)
                        coordination["recovery"] = JObject.FromObject(blocker.Recovery);
                    data["native_coordination"] = coordination;
                }
            }
            return this;
        }

        internal static bool IsBoundRequest(JObject request) =>
            request?["version"]?.Type == JTokenType.Integer
            && request["version"].ToString(Formatting.None) == "1"
            && request["request_nonce"]?.Type == JTokenType.String
            && request.Value<string>("request_nonce").Length >= 16
            && request.Value<string>("request_nonce").Length <= 128
            && request["instance_generation"]?.Type == JTokenType.String
            && request.Value<string>("instance_generation") == NativeCommandArbitrator.Shared.Generation;

        public string ToJson()
        {
            try
            {
                return BoundedResponseJson.Serialize(Payload);
            }
            catch (ResponseLimitException)
            {
                return BoundedResponseJson.Serialize(LimitPayload());
            }
        }

        private static object LimitPayload() => new { status = "error", error = "Command result exceeds the transport size or complexity limit" };

        // Freeze Unity-facing values on the dispatcher thread without an intermediate JSON string.
        public static TransportCommandResponse FromObject(object payload)
        {
            try
            {
                return new TransportCommandResponse(BoundedResponseJson.Freeze(payload));
            }
            catch (ResponseLimitException)
            {
                return new TransportCommandResponse(JObject.FromObject(LimitPayload()));
            }
        }

        // Existing asynchronous handlers complete a string TCS. Parse only that compatibility path.
        public static TransportCommandResponse FromJson(string json)
        {
            try
            {
                return new TransportCommandResponse(BoundedResponseJson.Parse(json));
            }
            catch (ResponseLimitException)
            {
                return FromObject(LimitPayload());
            }
            catch (JsonException)
            {
                return FromObject(new { status = "error", error = "Invalid response payload" });
            }
            catch (ArgumentNullException)
            {
                return FromObject(new { status = "error", error = "Invalid response payload" });
            }
        }
    }

    internal sealed class TransportCommandOperation
    {
        public TransportCommandOperation(Task<TransportCommandResponse> response, Task completion, Task<string> jsonResponse)
        {
            Response = response;
            Completion = completion;
            JsonResponse = jsonResponse;
        }

        public Task<TransportCommandResponse> Response { get; }

        // A deadline can cancel the response while an existing Unity handler is still changing state.
        public Task Completion { get; }
        public Task<string> JsonResponse { get; }
    }
}
