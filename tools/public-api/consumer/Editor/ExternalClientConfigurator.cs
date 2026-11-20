using System.Collections.Generic;
using MCPForUnity.Editor.Models;

namespace MCPForUnity.Editor.Clients.Configurators
{
    public sealed class ExternalClientConfigurator : JsonFileMcpConfigurator
    {
        public ExternalClientConfigurator()
            : base(
                new McpClient
                {
                    name = "External client",
                    windowsConfigPath = "external.json",
                    macConfigPath = "external.json",
                    linuxConfigPath = "external.json",
                }
            ) { }

        public override IList<string> GetInstallationSteps() => new[] { "Select the external client." };
    }
}
