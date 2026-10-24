using System;
using System.Collections.Generic;
using System.IO;
using System.Reflection;

namespace MCPForUnity.Editor.Setup
{
    internal static class RoslynAssemblyCompatibility
    {
        private static readonly Dictionary<string, (Version version, string token)> Required = new Dictionary<string, (Version, string)>(StringComparer.Ordinal)
        {
            ["Microsoft.CodeAnalysis"] = (new Version(4, 12, 0, 0), "31bf3856ad364e35"),
            ["Microsoft.CodeAnalysis.CSharp"] = (new Version(4, 12, 0, 0), "31bf3856ad364e35"),
            ["System.Collections.Immutable"] = (new Version(8, 0, 0, 0), "b03f5f7f11d50a3a"),
            ["System.Reflection.Metadata"] = (new Version(8, 0, 0, 0), "b03f5f7f11d50a3a"),
            ["System.Runtime.CompilerServices.Unsafe"] = (new Version(6, 0, 0, 0), "b03f5f7f11d50a3a"),
        };

        internal static bool IsCompatible(AssemblyName identity)
        {
            if (!Required.TryGetValue(identity.Name ?? "", out var expected))
                return false;
            string token = BitConverter.ToString(identity.GetPublicKeyToken() ?? Array.Empty<byte>()).Replace("-", "").ToLowerInvariant();
            return identity.Version == expected.version && token == expected.token && string.IsNullOrEmpty(identity.CultureName);
        }

        internal static HashSet<string> GetProvided(string folder, IEnumerable<(AssemblyName identity, string location)> loaded)
        {
            var provided = new HashSet<string>(StringComparer.Ordinal);
            foreach (var assembly in loaded)
            {
                if (!Required.ContainsKey(assembly.identity.Name ?? ""))
                    continue;
                string name = assembly.identity.Name + ".dll";
                if (!IsCompatible(assembly.identity) || string.IsNullOrEmpty(assembly.location))
                    throw new IOException(
                        $"Loaded Roslyn dependency conflict: {assembly.identity.FullName} at '{assembly.location ?? "unknown location"}'. "
                            + $"Required version: {Required[assembly.identity.Name].version}. Update or remove the package providing this assembly and restart Unity before retrying. "
                            + "The installer will not overwrite another package or replace an already loaded assembly."
                    );
                string local = Path.GetFullPath(Path.Combine(folder, name));
                string origin = Path.GetFullPath(assembly.location);
                var comparison = Environment.OSVersion.Platform == PlatformID.Win32NT ? StringComparison.OrdinalIgnoreCase : StringComparison.Ordinal;
                if (string.Equals(local, origin, comparison))
                    continue;
                if (File.Exists(local))
                    throw new IOException(
                        $"Compatible dependency '{name}' is already loaded from '{origin}', but '{local}' also exists. "
                            + "Uninstall the MCP-owned Roslyn files or explicitly move an unowned duplicate, then restart Unity and retry. Other packages are preserved."
                    );
                provided.Add(name);
            }
            return provided;
        }
    }
}
