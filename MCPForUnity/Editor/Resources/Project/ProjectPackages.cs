using System;
using System.Linq;
using MCPForUnity.Editor.Helpers;
using Newtonsoft.Json.Linq;
using UnityEditor.PackageManager;

namespace MCPForUnity.Editor.Resources.Project
{
    [McpForUnityResource("get_project_packages", Description = "Read installed package versions and registered package metadata.")]
    public static class ProjectPackages
    {
        public static object HandleCommand(JObject @params)
        {
            JToken requested = @params?["package"];
            if (requested != null && (requested.Type != JTokenType.String || string.IsNullOrWhiteSpace((string)requested)))
                return new ErrorResponse("Supply the installed package name.");

            try
            {
                var packages = PackageInfo.GetAllRegisteredPackages();
                if (packages == null || packages.Any(package => package == null))
                    return new ErrorResponse("The Editor's registered package inventory is unavailable.");

                if (requested == null)
                {
                    var inventory = packages
                        .OrderBy(package => package.name, StringComparer.Ordinal)
                        .Select(package => new { name = package.name, version = package.version })
                        .ToArray();
                    return PackageSourceSecurity.RedactResponse(
                        new SuccessResponse("Registered packages in the selected Editor.", new { count = inventory.Length, packages = inventory })
                    );
                }

                var info = packages.FirstOrDefault(package => string.Equals(package.name, (string)requested, StringComparison.OrdinalIgnoreCase));
                if (info == null)
                    return new ErrorResponse("The requested package is not registered in the selected Editor.");
                return PackageSourceSecurity.RedactResponse(
                    new SuccessResponse(
                        "Registered metadata for the installed package version.",
                        new
                        {
                            name = info.name,
                            version = info.version,
                            display_name = info.displayName,
                            description = info.description,
                            author = string.IsNullOrWhiteSpace(info.author?.name) ? null : info.author.name,
                            source = info.source.ToString(),
                            registry = PackageSourceSecurity.RegistryMetadata(info.source, info.registry),
                            is_direct_dependency = info.isDirectDependency,
                        }
                    )
                );
            }
            catch (Exception)
            {
                return new ErrorResponse("Could not read the Editor's registered package metadata.");
            }
        }
    }
}
