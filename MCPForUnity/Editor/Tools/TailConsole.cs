using System;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Editor.Services;
using Newtonsoft.Json.Linq;

namespace MCPForUnity.Editor.Tools
{
    // Backend alias only. The Python surface remains read_console.
    [McpForUnityTool("tail_console", AutoRegister = false)]
    public static class TailConsole
    {
        internal static readonly ConsoleTailCursor Cursors = new ConsoleTailCursor();

        public static object HandleCommand(JObject parameters)
        {
            try
            {
                if (!MCPServiceLocator.ToolDiscovery.IsToolEnabled("read_console") || !MCPServiceLocator.ToolDiscovery.IsToolEnabled("tail_console"))
                    return ConsoleTailCursor.Failure("tool_disabled");
                if (parameters?["verification_window"] != null)
                {
                    string StringOption(string name) => parameters[name]?.Type == JTokenType.String ? (string)parameters[name] : null;
                    string id = StringOption("window_id");
                    switch (StringOption("verification_window"))
                    {
                        case "begin":
                            return VerificationConsoleWindow.Begin();
                        case "read":
                            return TestJobManager.ReadConsoleWindow(StringOption("job_id"), id);
                        case "discard":
                            VerificationConsoleWindow.Discard(id);
                            return new SuccessResponse("Verification console observation released.");
                        default:
                            return ConsoleTailCursor.Failure("invalid_verification_window");
                    }
                }
                ConsoleTailQuery query = ConsoleTailQuery.Parse(parameters);
                using var snapshot = ReadConsole.OpenTailSnapshot();
                return Cursors.Read(query, snapshot);
            }
            catch (ArgumentException)
            {
                return ConsoleTailCursor.Failure("invalid_tail_options");
            }
            catch (Exception)
            {
                // No native console payload or reflection exception text enters diagnostics.
                Cursors.Invalidate();
                return ConsoleTailCursor.Failure("tail_unsupported");
            }
        }
    }
}
