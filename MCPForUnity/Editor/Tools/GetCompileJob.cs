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
            var response = JObject.FromObject(new SuccessResponse("Compilation request evidence.", evidence));
            // Older servers strictly validate data as compile_job@1. Keep diagnostics
            // outside that ledger so a new native peer preserves their compiler result.
            response["native_coordination"] = JObject.FromObject(CompilationJobTracker.CoordinationStatus(token.Value<string>()));
            response["last_successful_compilation"] = CompilationJobTracker.Ledger.LastSuccessfulCompilation();
            return response;
        }
    }
}
