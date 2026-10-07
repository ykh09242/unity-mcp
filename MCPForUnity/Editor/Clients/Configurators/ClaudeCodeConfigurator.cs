using System;
using System.IO;
using MCPForUnity.Editor.Models;

namespace MCPForUnity.Editor.Clients.Configurators
{
    /// <summary>
    /// Claude Code configurator using the CLI-based registration (claude mcp add/remove).
    /// This integrates with Claude Code's native MCP management.
    /// </summary>
    public class ClaudeCodeConfigurator : ClaudeCliMcpConfigurator
    {
        public ClaudeCodeConfigurator()
            : base(new McpClient { name = "Claude Code", SupportsHttpTransport = true }) { }

        public override bool SupportsSkills => true;

        public override string GetSkillInstallPath()
        {
            var userHome = Environment.GetFolderPath(Environment.SpecialFolder.UserProfile);
            return Path.Combine(userHome, ".claude", "skills", "unity-mcp-skill");
        }
    }
}
