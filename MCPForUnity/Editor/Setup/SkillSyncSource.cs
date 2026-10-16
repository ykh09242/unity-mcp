using System;
using System.Linq;
using System.Security.Cryptography;
using System.Text;
using MCPForUnity.Editor.Helpers;
using Newtonsoft.Json.Linq;
using UnityEditor.PackageManager;

namespace MCPForUnity.Editor.Setup
{
    /// <summary>A declared package skill revision or an explicitly selected floating branch.</summary>
    public sealed class SkillSyncSource
    {
        internal const string SkillSubdirectory = ".claude/skills/unity-mcp-skill";
        internal const string ForkRepository = "https://github.com/ykh09242/unity-mcp";
        private readonly string _packageIdentityFingerprint;
        public string Repository { get; }
        public string RepoUrl => "https://github.com/" + Repository;
        public string Revision { get; }
        public string Branch { get; }
        public string PackageIdentity { get; }
        public string PackageName { get; }
        public string PackageVersion { get; }
        public bool IsPackagePinned => Revision != null;
        internal string Reference => Revision ?? Branch;
        public string Description =>
            IsPackagePinned
                ? $"Installed package {PackageName} {PackageVersion}; installation identity SHA256 {_packageIdentityFingerprint}; skill source {Repository}/{SkillSubdirectory}; requested commit {Revision}"
                : $"Latest skill source {Repository}/{SkillSubdirectory} branch {Branch}; package compatibility is not verified";

        private SkillSyncSource(string repository, string branch)
        {
            Repository = repository;
            Branch = branch;
        }

        private SkillSyncSource(JObject package, string identity, string revision)
        {
            Repository = "ykh09242/unity-mcp";
            Revision = revision;
            PackageIdentity = identity;
            using var sha256 = SHA256.Create();
            _packageIdentityFingerprint = BitConverter.ToString(sha256.ComputeHash(Encoding.UTF8.GetBytes(identity))).Replace("-", "").ToLowerInvariant();
            PackageName = package.Value<string>("name");
            PackageVersion = package.Value<string>("version");
        }

        public static SkillSyncSource FromPackage(JObject package, string identity)
        {
            string version = package?["version"]?.Type == JTokenType.String ? package.Value<string>("version") : "unknown";
            var pin = package?["mcpSkillRevision"];
            string revision = pin?.Type == JTokenType.String ? pin.Value<string>() : null;
            if (!IsCommit(revision))
                throw new InvalidOperationException(
                    $"Installed package version {version}: mcpSkillRevision must be a nonzero full 40-hex commit. Reinstall a package with a verified skill pin or explicitly choose Latest from branch."
                );
            if (
                package?["name"]?.Type != JTokenType.String
                || package.Value<string>("name") != "com.ykh09242.unity-mcp"
                || package["version"]?.Type != JTokenType.String
                || string.IsNullOrWhiteSpace(package.Value<string>("version"))
                || string.IsNullOrWhiteSpace(identity)
            )
                throw new InvalidOperationException("The installed Unity package identity/version cannot be established for skill selection.");
            return new SkillSyncSource(package, identity, revision.ToLowerInvariant());
        }

        // Package discovery must run on the Editor main thread; Apply re-reads it through the service dispatcher.
        public static SkillSyncSource CaptureInstalled()
        {
            var info = PackageInfo.FindForAssembly(typeof(SkillSyncSource).Assembly);
            string identity = info?.packageId ?? AssetPathUtility.GetMcpPackageRootPath();
            return FromPackage(AssetPathUtility.GetPackageJson(), identity);
        }

        public static SkillSyncSource Latest(string repoUrl, string branch)
        {
            if (!SkillSyncService.TryParseGitHubRepository(repoUrl, out var repository) || string.IsNullOrWhiteSpace(branch))
                throw new InvalidOperationException("An explicit GitHub repository and branch are required for Latest skill selection.");
            return new SkillSyncSource(repository.Owner.ToLowerInvariant() + "/" + repository.Repo.ToLowerInvariant(), branch);
        }

        internal void RequireCurrentSelection(SkillSyncSource current)
        {
            if (
                IsPackagePinned
                && (
                    current == null
                    || !current.IsPackagePinned
                    || current.PackageIdentity != PackageIdentity
                    || current.PackageName != PackageName
                    || current.PackageVersion != PackageVersion
                    || current.Revision != Revision
                )
            )
                throw new InvalidOperationException("The installed package skill selection changed after preview. Preview again before applying.");
        }

        internal static bool IsCommit(string value) =>
            value != null
            && value.Length == 40
            && value.Any(character => character != '0')
            && value.All(character => character >= '0' && character <= '9' || character >= 'a' && character <= 'f' || character >= 'A' && character <= 'F');
    }
}
