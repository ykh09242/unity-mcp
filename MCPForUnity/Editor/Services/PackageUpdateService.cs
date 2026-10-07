using System;
using System.Net;
using System.Text.RegularExpressions;
using MCPForUnity.Editor.Constants;
using MCPForUnity.Editor.Helpers;
using Newtonsoft.Json.Linq;
using UnityEditor;
using PackageInfo = UnityEditor.PackageManager.PackageInfo;

namespace MCPForUnity.Editor.Services
{
    /// <summary>
    /// Service for checking this fork's Git package updates.
    /// </summary>
    public class PackageUpdateService : IPackageUpdateService
    {
        private const int DefaultRequestTimeoutMs = 3000;
        private const string LastCheckDateKey = EditorPrefKeys.LastUpdateCheck + ".ykh09242";
        private const string CachedVersionKey = EditorPrefKeys.LatestKnownVersion + ".ykh09242";
        private const string LastBetaCheckDateKey = LastCheckDateKey + ".beta";
        private const string CachedBetaVersionKey = CachedVersionKey + ".beta";
        private const string LastAssetStoreCheckDateKey = EditorPrefKeys.LastAssetStoreUpdateCheck + ".ykh09242";
        private const string CachedAssetStoreVersionKey = EditorPrefKeys.LatestKnownAssetStoreVersion + ".ykh09242";
        private const string MainPackageJsonUrl = "https://raw.githubusercontent.com/ykh09242/unity-mcp/main/MCPForUnity/package.json";
        private const string BetaPackageJsonUrl = "https://raw.githubusercontent.com/ykh09242/unity-mcp/beta/MCPForUnity/package.json";

        /// <inheritdoc/>
        public UpdateCheckResult CheckForUpdate(string currentVersion)
        {
            bool isGitInstallation = IsGitInstallation();
            if (!isGitInstallation)
                return UnsupportedLocalUpdate();
            string gitBranch = GetGitUpdateBranch(currentVersion);
            bool useBetaChannel = string.Equals(gitBranch, "beta", StringComparison.OrdinalIgnoreCase);
            string lastCheckKey = useBetaChannel ? LastBetaCheckDateKey : LastCheckDateKey;
            string cachedVersionKey = useBetaChannel ? CachedBetaVersionKey : CachedVersionKey;

            string lastCheckDate = EditorPrefs.GetString(lastCheckKey, "");
            string cachedLatestVersion = EditorPrefs.GetString(cachedVersionKey, "");

            if (lastCheckDate == DateTime.Now.ToString("yyyy-MM-dd") && TryParseVersion(cachedLatestVersion, out _))
            {
                return new UpdateCheckResult
                {
                    CheckSucceeded = true,
                    LatestVersion = cachedLatestVersion,
                    UpdateAvailable = IsNewerVersion(cachedLatestVersion, currentVersion),
                    Message = "Using cached version check",
                };
            }

            string latestVersion = FetchLatestVersionFromGitHub(gitBranch);

            if (TryParseVersion(latestVersion, out _))
            {
                // Cache the result
                EditorPrefs.SetString(lastCheckKey, DateTime.Now.ToString("yyyy-MM-dd"));
                EditorPrefs.SetString(cachedVersionKey, latestVersion);

                return new UpdateCheckResult
                {
                    CheckSucceeded = true,
                    LatestVersion = latestVersion,
                    UpdateAvailable = IsNewerVersion(latestVersion, currentVersion),
                    Message = "Successfully checked for updates",
                };
            }

            return new UpdateCheckResult
            {
                CheckSucceeded = false,
                UpdateAvailable = false,
                Message = !string.IsNullOrEmpty(latestVersion)
                    ? "Failed to check for updates (invalid version metadata)"
                    : "Failed to check for updates (network issue or offline)",
            };
        }

        /// <inheritdoc/>
        public UpdateCheckResult TryGetCachedResult(string currentVersion)
        {
            bool isGitInstallation = IsGitInstallation();
            if (!isGitInstallation)
                return UnsupportedLocalUpdate();
            string gitBranch = GetGitUpdateBranch(currentVersion);
            bool useBetaChannel = string.Equals(gitBranch, "beta", StringComparison.OrdinalIgnoreCase);
            string lastCheckKey = useBetaChannel ? LastBetaCheckDateKey : LastCheckDateKey;
            string cachedVersionKey = useBetaChannel ? CachedBetaVersionKey : CachedVersionKey;

            string lastCheckDate = EditorPrefs.GetString(lastCheckKey, "");
            string cachedLatestVersion = EditorPrefs.GetString(cachedVersionKey, "");

            if (lastCheckDate == DateTime.Now.ToString("yyyy-MM-dd") && TryParseVersion(cachedLatestVersion, out _))
            {
                return new UpdateCheckResult
                {
                    CheckSucceeded = true,
                    LatestVersion = cachedLatestVersion,
                    UpdateAvailable = IsNewerVersion(cachedLatestVersion, currentVersion),
                    Message = "Using cached version check",
                };
            }

            return null;
        }

        /// <inheritdoc/>
        public UpdateCheckResult FetchAndCompare(string currentVersion)
        {
            bool isGitInstallation = IsGitInstallation();
            string gitBranch = isGitInstallation ? GetGitUpdateBranch(currentVersion) : "main";
            return FetchAndCompare(currentVersion, isGitInstallation, gitBranch);
        }

        /// <inheritdoc/>
        public UpdateCheckResult FetchAndCompare(string currentVersion, bool isGitInstallation, string gitBranch)
        {
            if (!isGitInstallation)
                return UnsupportedLocalUpdate();
            string latestVersion = FetchLatestVersionFromGitHub(gitBranch);

            if (TryParseVersion(latestVersion, out _))
            {
                return new UpdateCheckResult
                {
                    CheckSucceeded = true,
                    LatestVersion = latestVersion,
                    UpdateAvailable = IsNewerVersion(latestVersion, currentVersion),
                    Message = "Successfully checked for updates",
                };
            }

            return new UpdateCheckResult
            {
                CheckSucceeded = false,
                UpdateAvailable = false,
                Message = !string.IsNullOrEmpty(latestVersion)
                    ? "Failed to check for updates (invalid version metadata)"
                    : "Failed to check for updates (network issue or offline)",
            };
        }

        /// <inheritdoc/>
        public void CacheFetchResult(string currentVersion, string fetchedVersion)
        {
            if (!TryParseVersion(fetchedVersion, out _))
                return;

            bool isGitInstallation = IsGitInstallation();
            if (!isGitInstallation)
                return;
            string gitBranch = GetGitUpdateBranch(currentVersion);
            bool useBetaChannel = string.Equals(gitBranch, "beta", StringComparison.OrdinalIgnoreCase);
            string lastCheckKey = useBetaChannel ? LastBetaCheckDateKey : LastCheckDateKey;
            string cachedVersionKey = useBetaChannel ? CachedBetaVersionKey : CachedVersionKey;

            EditorPrefs.SetString(lastCheckKey, DateTime.Now.ToString("yyyy-MM-dd"));
            EditorPrefs.SetString(cachedVersionKey, fetchedVersion);
        }

        /// <inheritdoc/>
        public bool IsNewerVersion(string version1, string version2)
        {
            if (!TryParseVersion(version1, out var left) || !TryParseVersion(version2, out var right))
            {
                return false;
            }

            return CompareVersions(left, right) > 0;
        }

        private static int CompareVersions(ParsedVersion left, ParsedVersion right)
        {
            int cmp = left.Major.CompareTo(right.Major);
            if (cmp != 0)
                return cmp;

            cmp = left.Minor.CompareTo(right.Minor);
            if (cmp != 0)
                return cmp;

            cmp = left.Patch.CompareTo(right.Patch);
            if (cmp != 0)
                return cmp;

            // Stable is newer than prerelease when core version matches.
            if (!left.IsPrerelease && right.IsPrerelease)
                return 1;
            if (left.IsPrerelease && !right.IsPrerelease)
                return -1;
            if (!left.IsPrerelease && !right.IsPrerelease)
                return 0;

            cmp = GetPrereleaseRank(left.PrereleaseLabel).CompareTo(GetPrereleaseRank(right.PrereleaseLabel));
            if (cmp != 0)
                return cmp;

            cmp = left.PrereleaseNumber.CompareTo(right.PrereleaseNumber);
            if (cmp != 0)
                return cmp;

            return string.Compare(left.PrereleaseLabel, right.PrereleaseLabel, StringComparison.OrdinalIgnoreCase);
        }

        private static int GetPrereleaseRank(string label)
        {
            if (string.IsNullOrEmpty(label))
            {
                return 0;
            }

            switch (label.ToLowerInvariant())
            {
                case "a":
                case "alpha":
                    return 1;
                case "b":
                case "beta":
                    return 2;
                case "rc":
                    return 3;
                case "preview":
                case "pre":
                    return 4;
                default:
                    return 5;
            }
        }

        private static bool TryParseVersion(string version, out ParsedVersion parsed)
        {
            parsed = default;
            if (string.IsNullOrWhiteSpace(version))
            {
                return false;
            }

            string normalized = version.Trim().TrimStart('v', 'V');
            var match = Regex.Match(normalized, @"^(?<major>\d+)\.(?<minor>\d+)\.(?<patch>\d+)(?:-(?<label>[A-Za-z]+)(?:\.(?<number>\d+))?)?$");

            if (!match.Success)
            {
                return false;
            }

            if (
                !int.TryParse(match.Groups["major"].Value, out int major)
                || !int.TryParse(match.Groups["minor"].Value, out int minor)
                || !int.TryParse(match.Groups["patch"].Value, out int patch)
            )
            {
                return false;
            }

            string prereleaseLabel = match.Groups["label"].Success ? match.Groups["label"].Value : string.Empty;
            int prereleaseNumber = 0;
            if (match.Groups["number"].Success && !int.TryParse(match.Groups["number"].Value, out prereleaseNumber))
            {
                return false;
            }

            parsed = new ParsedVersion
            {
                Major = major,
                Minor = minor,
                Patch = patch,
                PrereleaseLabel = prereleaseLabel,
                PrereleaseNumber = prereleaseNumber,
                IsPrerelease = !string.IsNullOrEmpty(prereleaseLabel),
            };
            return true;
        }

        /// <inheritdoc/>
        public virtual string GetGitUpdateBranch(string currentVersion)
        {
            try
            {
                var packageInfo = PackageInfo.FindForAssembly(typeof(PackageUpdateService).Assembly);
                return GetGitUpdateBranchForPackageId(packageInfo?.packageId);
            }
            catch
            {
                // Use the fork's default branch when installation metadata is unavailable.
            }

            return "beta";
        }

        internal static string GetGitUpdateBranchForPackageId(string packageId)
        {
            int revisionStart = packageId?.IndexOf('#') ?? -1;
            string revision = revisionStart >= 0 ? packageId.Substring(revisionStart + 1) : string.Empty;
            if (string.Equals(revision, "main", StringComparison.OrdinalIgnoreCase))
                return "main";

            // Tags and commits follow this fork's default branch, including stable releases.
            return "beta";
        }

        /// <inheritdoc/>
        public virtual bool IsGitInstallation()
        {
            try
            {
                var packageInfo = PackageInfo.FindForAssembly(typeof(PackageUpdateService).Assembly);
                return packageInfo != null && packageInfo.source == UnityEditor.PackageManager.PackageSource.Git;
            }
            catch
            {
                return false;
            }
        }

        private static UpdateCheckResult UnsupportedLocalUpdate() =>
            new UpdateCheckResult
            {
                CheckSucceeded = false,
                UpdateAvailable = false,
                Message =
                    "Automatic updates for Unity MCP (ykh09242) require a Git Package Manager installation. Update local copies manually from https://github.com/ykh09242/unity-mcp.",
            };

        /// <inheritdoc/>
        public void ClearCache()
        {
            EditorPrefs.DeleteKey(LastCheckDateKey);
            EditorPrefs.DeleteKey(CachedVersionKey);
            EditorPrefs.DeleteKey(LastBetaCheckDateKey);
            EditorPrefs.DeleteKey(CachedBetaVersionKey);
            EditorPrefs.DeleteKey(LastAssetStoreCheckDateKey);
            EditorPrefs.DeleteKey(CachedAssetStoreVersionKey);
        }

        /// <summary>
        /// Fetches the latest version from GitHub package.json for the requested branch.
        /// </summary>
        protected virtual string FetchLatestVersionFromGitHub(string branch)
        {
            try
            {
                // GitHub API endpoint (Option 1 - has rate limits):
                // https://api.github.com/repos/ykh09242/unity-mcp/releases/latest
                //
                // We use Option 2 (package.json directly) because:
                // - No API rate limits (GitHub serves raw files freely)
                // - Simpler - just parse JSON for version field
                // - More reliable - doesn't require releases to be published
                // - Direct source of truth from the selected fork branch

                using (var client = CreateWebClient())
                {
                    client.Headers.Add("User-Agent", "Unity-MCPForUnity-UpdateChecker");
                    string packageJsonUrl = string.Equals(branch, "beta", StringComparison.OrdinalIgnoreCase) ? BetaPackageJsonUrl : MainPackageJsonUrl;
                    string jsonContent = client.DownloadString(packageJsonUrl);

                    var packageJson = JObject.Parse(jsonContent);
                    string version = packageJson["version"]?.ToString();

                    return string.IsNullOrEmpty(version) ? null : version;
                }
            }
            catch (Exception ex)
            {
                // Silent fail - don't interrupt the user if network is unavailable
                McpLog.Info($"Update check failed (this is normal if offline): {ex.Message}");
                return null;
            }
        }

        private struct ParsedVersion
        {
            public int Major;
            public int Minor;
            public int Patch;
            public string PrereleaseLabel;
            public int PrereleaseNumber;
            public bool IsPrerelease;
        }

        /// <summary>
        /// Retained for compatibility; this fork has no Asset Store update channel.
        /// </summary>
        protected virtual string FetchLatestVersionFromAssetStoreJson()
        {
            return null;
        }

        protected virtual WebClient CreateWebClient()
        {
            return new TimeoutWebClient(GetRequestTimeoutMs());
        }

        protected virtual int GetRequestTimeoutMs()
        {
            return DefaultRequestTimeoutMs;
        }

        private sealed class TimeoutWebClient : WebClient
        {
            private readonly int _timeoutMs;

            public TimeoutWebClient(int timeoutMs)
            {
                _timeoutMs = timeoutMs;
            }

            protected override WebRequest GetWebRequest(Uri address)
            {
                var request = base.GetWebRequest(address);
                if (request != null)
                {
                    request.Timeout = _timeoutMs;

                    if (request is HttpWebRequest httpRequest)
                    {
                        httpRequest.ReadWriteTimeout = _timeoutMs;
                    }
                }

                return request;
            }
        }
    }
}
