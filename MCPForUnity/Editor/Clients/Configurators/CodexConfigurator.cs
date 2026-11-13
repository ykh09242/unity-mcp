using System.IO;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Editor.Models;

namespace MCPForUnity.Editor.Clients.Configurators
{
    public class CodexConfigurator : CodexMcpConfigurator
    {
        private readonly string home;

        public CodexConfigurator()
            : this(CodexHome.Resolve()) { }

        internal CodexConfigurator(string resolvedHome)
            : base(
                new McpClient
                {
                    name = "Codex",
                    windowsConfigPath = Path.Combine(resolvedHome, "config.toml"),
                    macConfigPath = Path.Combine(resolvedHome, "config.toml"),
                    linuxConfigPath = Path.Combine(resolvedHome, "config.toml"),
                }
            )
        {
            home = resolvedHome;
        }

        public override bool SupportsSkills => true;

        public override string GetSkillInstallPath() => CodexHome.SkillInstallPath(home);
    }
}
