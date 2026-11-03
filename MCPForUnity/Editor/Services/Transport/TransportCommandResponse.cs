using System;
using System.Threading.Tasks;
using MCPForUnity.Editor.Models;
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
        private JObject resume;
        internal JToken BusinessPayload => payload as JToken;
        public object Payload => Wrap(payload);

        private object Wrap(object business)
        {
            object inner = admission == null ? business : new { native_arbitration = admission, payload = business };
            return resume == null ? inner : new { command_resume = resume, payload = inner };
        }

        internal TransportCommandResponse WithResume(JObject receipt)
        {
            resume = receipt;
            return this;
        }

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

        public string ToJson() => Serialize(null);

        internal static string ToWebSocketJson(string id, object result) =>
            (result as TransportCommandResponse ?? new TransportCommandResponse(result)).Serialize(id);

        private string Serialize(string id)
        {
            object Wire(object business)
            {
                var result = Wrap(business);
                return id == null
                    ? result
                    : new
                    {
                        type = "command_result",
                        id,
                        result,
                    };
            }
            try
            {
                return BoundedResponseJson.Serialize(Wire(payload));
            }
            catch (ResponseLimitException)
            {
                // Only replace the business result. These proofs were established by the
                // transport, and cannot be reconstructed from a handler's JSON.
                return BoundedResponseJson.Serialize(
                    Wire(id == null ? LimitPayload() : new { status = "error", error = "Command result exceeds the transport size limit" })
                );
            }
        }

        internal static TransportCommandResponse Interrupted(Command command, string detail, bool mayHaveStarted)
        {
            var bound = IsBoundRequest(command?.arbitration);
            var request = command?.resume;
            string invocation = request?["invocation_id"]?.Type == JTokenType.String ? (string)request["invocation_id"] : null;
            bool resumeBound =
                CommandResumeJournal.IsIdentity(invocation)
                && request?["version"]?.Type == JTokenType.Integer
                && request["version"].ToString(Formatting.None) == "1"
                && request["editor_session_id"]?.Type == JTokenType.String
                && (string)request["editor_session_id"] == CommandResumeState.SessionId;
            // An earlier attempt of a resumable invocation may have run even if this
            // transport did not dispatch. Never certify its completion or permit replay.
            bool unknown = mayHaveStarted || resumeBound;
            if (detail?.Length > 512)
                detail = detail.Substring(0, 512);
            if (unknown && (bound || resumeBound))
                detail += ". The command outcome is unknown. Inspect the target state before retrying.";
            var error = new JObject { ["status"] = "error", ["error"] = detail };
            if (unknown && bound && !resumeBound)
            {
                // An ordinary business failure passes through both legacy result adapters.
                error = new JObject
                {
                    ["status"] = "success",
                    ["result"] = new JObject
                    {
                        ["success"] = false,
                        ["error"] = detail,
                        ["hint"] = "inspect_state_before_retry",
                        ["data"] = new JObject { ["reason"] = "outcome_unknown" },
                    },
                };
            }
            var response = new TransportCommandResponse(error).WithAdmission(
                command?.arbitration,
                !unknown,
                new NativeAdmissionBlocker("command_interrupted", false)
            );
            if (resumeBound)
                response.WithResume(
                    new JObject
                    {
                        ["version"] = 1,
                        ["invocation_id"] = invocation,
                        ["editor_session_id"] = CommandResumeState.SessionId,
                        ["state"] = "unknown",
                    }
                );
            return response;
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
