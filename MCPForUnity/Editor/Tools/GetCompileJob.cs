using MCPForUnity.Editor.Helpers;
using MCPForUnity.Editor.Services;
using Newtonsoft.Json.Linq;

namespace MCPForUnity.Editor.Tools
{
    /// <summary>Read compilation evidence without requesting work, including on legacy peers.</summary>
    [McpForUnityTool("get_compile_job", AutoRegister = false)]
    public static class GetCompileJob
    {
        public static object HandleCommand(JObject parameters)
        {
            var token = parameters?["compile_id"];
            if (token?.Type != JTokenType.String || !CompilationLedger.ValidId(token.Value<string>()))
                return new ErrorResponse("compile_id must contain 32 lowercase hexadecimal characters.");
            var evidence = CompilationJobTracker.Ledger.Query(token.Value<string>());
            evidence["native_coordination"] = JObject.FromObject(CompilationJobTracker.CoordinationStatus(token.Value<string>()));
            return new SuccessResponse("Compilation request evidence.", evidence);
        }
    }
}
