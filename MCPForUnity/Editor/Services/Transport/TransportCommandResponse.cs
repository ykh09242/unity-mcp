using System;
using System.Threading.Tasks;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;

namespace MCPForUnity.Editor.Services.Transport
{
    /// <summary>A response envelope retained as structured data until the transport writes it.</summary>
    internal sealed class TransportCommandResponse
    {
        private TransportCommandResponse(object payload) => Payload = payload;

        public object Payload { get; }
        public string ToJson() => JsonConvert.SerializeObject(Payload);
        // Freeze Unity-facing values on the dispatcher thread without an intermediate JSON string.
        public static TransportCommandResponse FromObject(object payload)
            => new TransportCommandResponse(payload is JToken ? payload : JToken.FromObject(payload));

        // Existing asynchronous handlers complete a string TCS. Parse only that compatibility path.
        public static TransportCommandResponse FromJson(string json)
        {
            try { return FromObject(JToken.Parse(json)); }
            catch (JsonException) { return FromObject(new { status = "error", error = "Invalid response payload" }); }
            catch (ArgumentNullException) { return FromObject(new { status = "error", error = "Invalid response payload" }); }
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
