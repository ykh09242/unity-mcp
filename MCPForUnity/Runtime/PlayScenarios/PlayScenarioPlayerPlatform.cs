using System;

namespace MCPForUnity.Runtime.PlayScenarios
{
    /// <summary>Explicit standalone build identities; legacy bundles retain Windows Mono defaults.</summary>
    public static class PlayScenarioPlayerPlatform
    {
        public static void Validate(string target, string backend)
        {
            Executable(target);
            if (backend != "mono" && backend != "il2cpp")
                throw new ArgumentException("Player backend must be mono or il2cpp.");
        }

        public static string Executable(string target)
        {
            switch (target)
            {
                case "windows64":
                    return "MCPScenarioPlayer.exe";
                case "linux64":
                    return "MCPScenarioPlayer.x86_64";
                case "macos":
                    return "MCPScenarioPlayer.app/Contents/MacOS/MCPScenarioPlayer";
                default:
                    throw new ArgumentException("Player target must be windows64, linux64 or macos.");
            }
        }

        public static string BundleLocation(string target) => target == "macos" ? "MCPScenarioPlayer.app" : Executable(target);

        public static string TargetForExecutable(string executable)
        {
            foreach (string target in new[] { "windows64", "linux64", "macos" })
                if (Executable(target) == executable)
                    return target;
            throw new ArgumentException("Unsupported Player executable.");
        }
    }
}
