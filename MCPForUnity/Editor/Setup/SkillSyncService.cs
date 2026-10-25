using System;
using System.Collections.Generic;
using System.Collections.ObjectModel;
using System.IO;
using System.Linq;
using System.Net.Http;
using System.Security.Cryptography;
using System.Text;
using System.Threading;
using System.Threading.Tasks;
using MCPForUnity.Runtime.Helpers;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEngine;

namespace MCPForUnity.Editor.Setup
{
    public static class SkillSyncService
    {
        private const string DefaultRepoUrl = "https://github.com/ykh09242/unity-mcp";
        private const string SkillSubdir = SkillSyncSource.SkillSubdirectory;
        private const string SyncOwnershipMarker = ".unity-mcp-skill-sync";
        private const string LastSyncedCommitKeyPrefix = "UnityMcpSkillSync.LastSyncedCommit";
        private const string BaselineFile = ".unity-mcp-skill-sync-baseline.json";
        private const string MarkerContents = "managed-by-unity-mcp-skill-sync";
        private const int MaxBaselineBytes = 4 * 1024 * 1024;
        private static readonly object ApplyGate = new();
        internal static Action<string, string> BeforeFileOperation;
        internal static Func<SkillSyncSource> InstalledSelectionReader = null;
        private const int MaxLocalEntries = 8192;
        private const int MaxLocalDepth = 64;

        public sealed class SyncResult
        {
            public bool Success { get; set; }
            public int Added { get; set; }
            public int Updated { get; set; }
            public int Deleted { get; set; }
            public string CommitSha { get; set; }
            public string SourceDescription { get; set; }
            public string ReviewSourceUrl { get; set; }
            public string Error { get; set; }
            public string[] Conflicts { get; set; } = Array.Empty<string>();
            public string[] UnknownBaselineConflicts { get; set; } = Array.Empty<string>();
            public string[] Preserved { get; set; } = Array.Empty<string>();
            public bool PartialApplication { get; set; }
            public bool RolledBack { get; set; }
            public string RecoveryPath { get; set; }
        }

        public sealed class SyncPreview
        {
            internal readonly SyncPlan Plan;
            internal readonly string Repository;
            internal readonly string Branch;
            internal readonly SkillSyncSource Source;
            internal readonly Dictionary<string, string> RemoteFiles;
            public string InstallPath { get; }
            public string CommitSha { get; }
            public string SourceDescription => Source?.Description;
            public string ReviewSourceUrl => $"https://github.com/{Repository}/tree/{CommitSha}/{SkillSubdir}";
            public string Error { get; }
            public bool Success => Error == null;
            public ReadOnlyCollection<string> Added { get; }
            public ReadOnlyCollection<string> Updated { get; }
            public ReadOnlyCollection<string> Deleted { get; }
            public ReadOnlyCollection<string> Conflicts { get; }
            public ReadOnlyCollection<string> UnknownBaselineConflicts { get; }
            public ReadOnlyCollection<string> Preserved { get; }

            internal SyncPreview(string error)
            {
                Error = error;
            }

            internal SyncPreview(SkillSyncSource source, string installPath, RemoteSnapshot remote, SyncPlan plan)
            {
                Source = source;
                Repository = source.Repository;
                Branch = source.Reference;
                InstallPath = installPath;
                CommitSha = remote.CommitSha;
                RemoteFiles = new Dictionary<string, string>(remote.Files, StringComparer.Ordinal);
                Plan = plan;
                Added = Array.AsReadOnly(plan.Added.ToArray());
                Updated = Array.AsReadOnly(plan.Updated.ToArray());
                Deleted = Array.AsReadOnly(plan.Deleted.ToArray());
                Conflicts = Array.AsReadOnly(plan.Conflicts.ToArray());
                UnknownBaselineConflicts = Array.AsReadOnly(plan.UnknownBaselineConflicts.ToArray());
                Preserved = Array.AsReadOnly(plan.Preserved.ToArray());
            }
        }

        public static void SyncAsync(string installDir, string branch, Action<string> log, Action<SyncResult> onComplete) =>
            SyncAsync(DefaultRepoUrl, installDir, branch, log, onComplete);

        public static void SyncAsync(string repoUrl, string installDir, string branch, Action<string> log, Action<SyncResult> onComplete)
        {
            PreviewAsync(
                repoUrl,
                installDir,
                branch,
                log,
                preview =>
                {
                    if (!preview.Success)
                        onComplete?.Invoke(new SyncResult { Error = preview.Error });
                    else
                        ApplyAsync(preview, log, onComplete);
                }
            );
        }

        public static void SyncSourceAsync(SkillSyncSource source, string installDir, Action<string> log, Action<SyncResult> onComplete)
        {
            PreviewSourceAsync(
                source,
                installDir,
                log,
                preview =>
                {
                    if (!preview.Success)
                        onComplete?.Invoke(new SyncResult { Error = preview.Error });
                    else
                        ApplyAsync(preview, log, onComplete);
                }
            );
        }

        public static void PreviewAsync(string repoUrl, string installDir, string branch, Action<string> log, Action<SyncPreview> onComplete)
        {
            try
            {
                PreviewSourceAsync(SkillSyncSource.Latest(repoUrl, branch), installDir, log, onComplete);
            }
            catch (Exception error)
            {
                EditorApplication.delayCall += () => onComplete?.Invoke(new SyncPreview(error.Message));
            }
        }

        public static void PreviewSourceAsync(SkillSyncSource source, string installDir, Action<string> log, Action<SyncPreview> onComplete) =>
            PreviewSourceAsync(source, installDir, log, onComplete, CancellationToken.None);

        public static void PreviewSourceAsync(
            SkillSyncSource source,
            string installDir,
            Action<string> log,
            Action<SyncPreview> onComplete,
            CancellationToken cancellationToken,
            double? editorWaitTimeoutSeconds = null
        )
        {
            Task.Run(() =>
            {
                SyncPreview preview;
                try
                {
                    var budget = new SkillSyncWaitBudget(editorWaitTimeoutSeconds ?? SkillSyncWaitBudget.DefaultSeconds, cancellationToken);
                    budget.ThrowIfCancellationRequested();
                    if (source == null)
                        throw new InvalidOperationException("An explicit skill source is required.");
                    if (source.IsPackagePinned)
                        source.RequireCurrentSelection(ReadInstalledWithBudget(budget));
                    var installPath = ResolveAndValidateInstallPath(installDir);
                    ListFiles(installPath); // Reject linked roots before any remote request.
                    using var client = CreateGitHubClient();
                    budget.ThrowIfCancellationRequested();
                    var snapshot = FetchSourceSnapshot(client, source, log);
                    budget.ThrowIfCancellationRequested();
                    preview = CreateSourcePreview(source, installPath, snapshot);
                    budget.ThrowIfCancellationRequested();
                    log?.Invoke(preview.SourceDescription + "; resolved commit " + preview.CommitSha);
                    LogPlanDetails(preview.Plan, log);
                    if (preview.Conflicts.Count > 0)
                        log?.Invoke("Review the resolved source: " + preview.ReviewSourceUrl);
                }
                catch (Exception error)
                {
                    preview = new SyncPreview(error.Message);
                }
                EditorApplication.delayCall += () => onComplete?.Invoke(preview);
            });
        }

        public static void ApplyAsync(SyncPreview preview, Action<string> log, Action<SyncResult> onComplete) =>
            ApplyAsync(preview, log, onComplete, CancellationToken.None);

        public static void ApplyAsync(
            SyncPreview preview,
            Action<string> log,
            Action<SyncResult> onComplete,
            CancellationToken cancellationToken,
            double? editorWaitTimeoutSeconds = null
        )
        {
            Task.Run(() =>
            {
                SyncResult result;
                try
                {
                    var budget = new SkillSyncWaitBudget(editorWaitTimeoutSeconds ?? SkillSyncWaitBudget.DefaultSeconds, cancellationToken);
                    budget.ThrowIfCancellationRequested();
                    if (preview == null || !preview.Success || !TryParseGitHubRepository("https://github.com/" + preview.Repository, out var repository))
                        throw new InvalidOperationException("A successful skill preview is required.");
                    using var client = CreateGitHubClient();
                    result = ApplySourcePreviewWithBudget(
                        preview,
                        () => ReadInstalledWithBudget(budget),
                        () =>
                        {
                            budget.ThrowIfCancellationRequested();
                            return FetchSourceSnapshot(client, preview.Source, log);
                        },
                        (path, limit) =>
                        {
                            budget.ThrowIfCancellationRequested();
                            return DownloadBytes(client, BuildRawFileUrl(repository, preview.CommitSha, CombineRemotePath(SkillSubdir, path)), limit);
                        },
                        log,
                        budget
                    );
                }
                catch (Exception error)
                {
                    result = FailureResult(error);
                }
                EditorApplication.delayCall += () =>
                {
                    if (result.Success)
                        EditorPrefs.SetString(GetLastSyncedCommitKey(preview.Repository, preview.Branch), result.CommitSha);
                    onComplete?.Invoke(result);
                };
            });
        }

        internal static SyncPreview CreatePreview(string repoUrl, string installDir, string branch, RemoteSnapshot remote)
        {
            return CreateSourcePreview(SkillSyncSource.Latest(repoUrl, branch), installDir, remote);
        }

        internal static SyncPreview CreateSourcePreview(SkillSyncSource selection, string installDir, RemoteSnapshot remote)
        {
            if (selection == null || selection.IsPackagePinned && selection.Revision != remote.CommitSha)
                throw new InvalidOperationException("The skill snapshot does not match the selected package revision.");
            string source = selection.Repository;
            string branch = selection.Reference;
            string root = ResolveAndValidateInstallPath(installDir);
            var local = ListFiles(root);
            if (!IsBlobHash(remote.CommitSha) || remote.SubdirPath != SkillSubdir)
                throw new InvalidOperationException("The remote skill snapshot identity is invalid.");
            ValidateRemoteFiles(remote.Files);
            var baseline = ReadBaseline(root, source, branch);
            bool markerExists = File.Exists(ResolvePathUnderRoot(root, SyncOwnershipMarker, StringComparison.OrdinalIgnoreCase));
            if (markerExists && Encoding.UTF8.GetString(ReadMetadataBytes(root, SyncOwnershipMarker)) != MarkerContents)
                throw new InvalidOperationException("The skill ownership marker is invalid.");
            if (baseline == null && !markerExists && local.Count > 0 && !local.Keys.Contains("SKILL.md", StringComparer.OrdinalIgnoreCase))
                throw new InvalidOperationException("Install Dir is not an empty or existing skill folder.");
            var previous =
                baseline?.files.ToDictionary(file => file.path, file => file.hash, StringComparer.OrdinalIgnoreCase)
                ?? new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);
            var plan = BuildPlanWithBaseline(remote.Files, local, previous, StringComparer.OrdinalIgnoreCase);
            plan.ExpectedDirectories = DirectorySnapshot(root);
            RejectCaseParentChanges(plan, local.Keys.Concat(plan.ExpectedDirectories), previous);
            plan.ExpectedMetadata = MetadataSnapshot(root);
            var next = new BaselineDocument
            {
                version = 2,
                repository = source,
                branch = branch,
                selection = selection.IsPackagePinned ? "package" : "latest",
                packageVersion = selection.PackageVersion,
                subdir = SkillSubdir,
                commit = remote.CommitSha,
                files = plan
                    .NextBaseline.OrderBy(file => file.Key, StringComparer.Ordinal)
                    .Select(file => new BaselineEntry { path = file.Key, hash = file.Value })
                    .ToArray(),
            };
            plan.Metadata[BaselineFile] = Encoding.UTF8.GetBytes(JsonUtility.ToJson(next, true));
            if (!markerExists)
                plan.Metadata[SyncOwnershipMarker] = Encoding.UTF8.GetBytes(MarkerContents);
            return new SyncPreview(selection, root, remote, plan);
        }

        internal static SyncResult ApplySourcePreview(
            SyncPreview preview,
            Func<SkillSyncSource> readInstalled,
            Func<RemoteSnapshot> fetchSnapshot,
            Func<string, long, byte[]> download,
            Action<string> log
        ) => ApplySourcePreviewWithBudget(preview, readInstalled, fetchSnapshot, download, log, null);

        private static SyncResult ApplySourcePreviewWithBudget(
            SyncPreview preview,
            Func<SkillSyncSource> readInstalled,
            Func<RemoteSnapshot> fetchSnapshot,
            Func<string, long, byte[]> download,
            Action<string> log,
            SkillSyncWaitBudget budget
        ) =>
            ApplyPreview(
                preview,
                fetchSnapshot,
                download,
                log,
                () =>
                {
                    budget?.ThrowIfCancellationRequested();
                    if (preview?.Source?.IsPackagePinned == true)
                    {
                        SkillSyncSource current;
                        try
                        {
                            current = readInstalled();
                        }
                        catch (Exception error)
                        {
                            throw new InvalidOperationException(
                                "The installed package selection could not be re-read. Preview again before applying. " + error.Message,
                                error
                            );
                        }
                        preview.Source.RequireCurrentSelection(current);
                    }
                }
            );

        private static SkillSyncSource ReadInstalledWithBudget(SkillSyncWaitBudget budget) =>
            budget.Read(() =>
            {
                var reader = InstalledSelectionReader;
                return reader != null ? reader() : SkillSyncSource.CaptureInstalled();
            });

        internal static SyncResult ApplyPreview(
            SyncPreview preview,
            Func<RemoteSnapshot> fetchSnapshot,
            Func<string, long, byte[]> download,
            Action<string> log,
            Action validateSelection = null
        )
        {
            lock (ApplyGate)
            {
                try
                {
                    if (preview == null || !preview.Success)
                        throw new InvalidOperationException("A successful skill preview is required.");
                    validateSelection?.Invoke();
                    ValidateRemotePreview(preview, fetchSnapshot());
                    ApplyVerifiedPlan(
                        preview.InstallPath,
                        preview.Plan,
                        preview.RemoteFiles,
                        StringComparison.OrdinalIgnoreCase,
                        download,
                        log,
                        () =>
                        {
                            validateSelection?.Invoke();
                            ValidateRemotePreview(preview, fetchSnapshot());
                        },
                        validateSelection
                    );
                    return new SyncResult
                    {
                        Success = true,
                        Added = preview.Added.Count,
                        Updated = preview.Updated.Count,
                        Deleted = preview.Deleted.Count,
                        CommitSha = preview.CommitSha,
                        SourceDescription = preview.SourceDescription,
                        ReviewSourceUrl = preview.ReviewSourceUrl,
                        Conflicts = preview.Conflicts.ToArray(),
                        UnknownBaselineConflicts = preview.UnknownBaselineConflicts.ToArray(),
                        Preserved = preview.Preserved.ToArray(),
                        PartialApplication = preview.Conflicts.Count > 0,
                        RecoveryPath = preview.Plan.RecoveryPath,
                    };
                }
                catch (Exception error)
                {
                    var result = FailureResult(error);
                    result.CommitSha = preview?.CommitSha;
                    result.SourceDescription = preview?.SourceDescription;
                    result.ReviewSourceUrl = preview?.ReviewSourceUrl;
                    result.Conflicts = preview?.Conflicts?.ToArray() ?? Array.Empty<string>();
                    result.UnknownBaselineConflicts = preview?.UnknownBaselineConflicts?.ToArray() ?? Array.Empty<string>();
                    result.Preserved = preview?.Preserved?.ToArray() ?? Array.Empty<string>();
                    return result;
                }
            }
        }

        private static void ValidateRemotePreview(SyncPreview preview, RemoteSnapshot current)
        {
            ValidateRemoteFiles(current.Files);
            if (current.CommitSha != preview.CommitSha || current.SubdirPath != SkillSubdir || !HashesEqual(current.Files, preview.RemoteFiles))
                throw new InvalidOperationException("The remote skill changed after preview. Preview again before applying.");
        }

        private static SyncResult FailureResult(Exception error) =>
            new SyncResult
            {
                Error = error.Message,
                RolledBack = error is SyncApplyException failure && failure.RolledBack,
                PartialApplication = error is SyncApplyException partial && !partial.RolledBack,
                RecoveryPath = (error as SyncApplyException)?.RecoveryPath,
            };

        internal static string DescribeResult(SyncResult result)
        {
            string summary;
            if (!result.Success)
                summary = result.Error + (result.PartialApplication ? "\nSome changes remain; recovery is required." : "");
            else
            {
                bool unchanged = result.Added + result.Updated + result.Deleted == 0;
                summary =
                    unchanged && result.Preserved.Length == 0 && result.Conflicts.Length == 0
                        ? "Skills are already up to date."
                        : (unchanged ? "No skill files changed." : $"Applied: +{result.Added} ~{result.Updated} -{result.Deleted}.")
                            + $" Preserved locally: {result.Preserved.Length + result.Conflicts.Length} (including conflicts).";
                if (result.Conflicts.Length > 0)
                {
                    summary += $"\nReview required ({result.Conflicts.Length}); local files or deletions kept:\n" + string.Join("\n", result.Conflicts);
                    if (result.UnknownBaselineConflicts.Length > 0)
                        summary +=
                            "\nNo recorded baseline for these differing files; previous upstream content and local edits cannot be distinguished:\n"
                            + string.Join("\n", result.UnknownBaselineConflicts);
                    if (!string.IsNullOrEmpty(result.ReviewSourceUrl))
                        summary += "\nResolved source for comparison: " + result.ReviewSourceUrl;
                    summary +=
                        "\nBack up the local skill, compare the listed paths with the resolved source, merge desired changes manually, then preview again. "
                        + "Files kept different will continue to require review; sync does not overwrite them or adopt an unknown baseline.";
                }
            }
            if (!string.IsNullOrEmpty(result.RecoveryPath))
                summary += "\nRecovery files retained at: " + result.RecoveryPath;
            if (!string.IsNullOrEmpty(result.SourceDescription))
                summary +=
                    "\n" + result.SourceDescription + "\nResolved commit: " + result.CommitSha + ". Local edits and conflicts can differ from this source.";
            return summary;
        }

        private static string GetLastSyncedCommitKey(string repoUrl, string branch)
        {
            var scope = $"{repoUrl}|{branch}|{NormalizeRemotePath(SkillSubdir)}";
            using var sha256 = SHA256.Create();
            var hash = sha256.ComputeHash(Encoding.UTF8.GetBytes(scope));
            var suffix = BitConverter.ToString(hash).Replace("-", string.Empty).ToLowerInvariant();
            return $"{LastSyncedCommitKeyPrefix}.{suffix}";
        }

        internal static bool TryParseGitHubRepository(string url, out GitHubRepoInfo repoInfo)
        {
            repoInfo = default;
            if (string.IsNullOrWhiteSpace(url))
            {
                return false;
            }

            var trimmed = url.Trim();
            if (trimmed.StartsWith("git@github.com:", StringComparison.OrdinalIgnoreCase))
            {
                var repoPath = trimmed.Substring("git@github.com:".Length).Trim('/');
                return TryParseOwnerAndRepo(repoPath, out repoInfo);
            }

            if (!Uri.TryCreate(trimmed, UriKind.Absolute, out var uri))
            {
                return false;
            }

            if (!string.Equals(uri.Host, "github.com", StringComparison.OrdinalIgnoreCase))
            {
                return false;
            }

            var repoPathFromUri = uri.AbsolutePath.Trim('/');
            return TryParseOwnerAndRepo(repoPathFromUri, out repoInfo);
        }

        private static bool TryParseOwnerAndRepo(string path, out GitHubRepoInfo repoInfo)
        {
            repoInfo = default;
            var segments = path.Split(new[] { '/' }, StringSplitOptions.RemoveEmptyEntries);
            if (segments.Length < 2)
            {
                return false;
            }

            var owner = segments[0].Trim();
            var repo = segments[1].Trim();
            if (repo.EndsWith(".git", StringComparison.OrdinalIgnoreCase))
            {
                repo = repo.Substring(0, repo.Length - 4);
            }

            if (string.IsNullOrWhiteSpace(owner) || string.IsNullOrWhiteSpace(repo))
            {
                return false;
            }

            repoInfo = new GitHubRepoInfo(owner, repo);
            return true;
        }

        internal static RemoteSnapshot FetchSourceSnapshot(HttpClient client, SkillSyncSource source, Action<string> log)
        {
            if (source == null || !TryParseGitHubRepository(source.RepoUrl, out var repoInfo))
                throw new InvalidOperationException("A valid skill source is required.");
            string commitSha = source.Revision ?? FetchBranchHeadCommitSha(client, repoInfo, source.Branch, log);
            if (!SkillSyncSource.IsCommit(commitSha))
                throw new InvalidOperationException("GitHub did not return a full skill commit identity.");
            commitSha = commitSha.ToLowerInvariant();
            string commitUrl = $"https://api.github.com/repos/{repoInfo.Owner}/{repoInfo.Repo}/git/commits/{commitSha}";
            JObject commit;
            try
            {
                commit = JObject.Parse(DownloadString(client, commitUrl, SkillSyncDownload.MaxBranchBytes));
            }
            catch (Exception error)
            {
                throw new InvalidOperationException($"Skill commit {commitSha} is unavailable or invalid for {source.Description}: {error.Message}", error);
            }
            string treeSha = commit["tree"]?["sha"]?.Type == JTokenType.String ? commit["tree"]["sha"].Value<string>() : null;
            if (commit["sha"]?.Type != JTokenType.String || commit.Value<string>("sha") != commitSha || !SkillSyncSource.IsCommit(treeSha))
                throw new InvalidOperationException("GitHub returned a different or invalid skill commit/tree identity.");
            var treeApiUrl = BuildTreeApiUrl(repoInfo, treeSha);
            log?.Invoke($"Fetching remote directory tree at commit {ShortCommit(commitSha)}...");
            var json = DownloadString(client, treeApiUrl);
            SkillSyncDownload.ValidateTreeJson(json);
            var treeResponse = JsonUtility.FromJson<GitHubTreeResponse>(json);
            if (treeResponse == null || treeResponse.tree == null || treeResponse.sha != treeSha)
            {
                throw new InvalidOperationException("GitHub returned a different or invalid skill directory tree identity.");
            }

            if (treeResponse.truncated)
            {
                throw new InvalidOperationException(
                    "GitHub returned a truncated directory tree (incomplete snapshot). "
                        + "Sync was aborted to prevent accidental deletion of valid local files."
                );
            }

            var normalizedSubdir = NormalizeRemotePath(SkillSubdir);
            var subdirPrefix = string.IsNullOrEmpty(normalizedSubdir) ? string.Empty : $"{normalizedSubdir}/";
            var remoteFiles = new Dictionary<string, string>(StringComparer.Ordinal);
            var remoteBudget = new SkillSyncDownload.ByteBudget();

            foreach (var entry in treeResponse.tree)
            {
                if (!string.Equals(entry.type, "blob", StringComparison.Ordinal))
                {
                    continue;
                }

                var remotePath = NormalizeRemotePath(entry.path);
                if (string.IsNullOrEmpty(remotePath))
                {
                    continue;
                }

                if (!string.IsNullOrEmpty(subdirPrefix) && !remotePath.StartsWith(subdirPrefix, StringComparison.Ordinal))
                {
                    continue;
                }

                var relativePath = string.IsNullOrEmpty(subdirPrefix) ? remotePath : remotePath.Substring(subdirPrefix.Length);
                if (string.IsNullOrWhiteSpace(relativePath) || string.IsNullOrWhiteSpace(entry.sha))
                {
                    continue;
                }

                if (!TryNormalizeRelativePath(relativePath, out var safeRelativePath))
                {
                    log?.Invoke($"Skip unsafe remote path: {remotePath}");
                    continue;
                }

                if (remoteFiles.ContainsKey(safeRelativePath))
                    throw new InvalidOperationException("GitHub tree contains duplicate skill file paths.");
                SkillSyncDownload.RequireFileCount((long)remoteFiles.Count + 1);
                remoteBudget.Admit(entry.size);
                remoteFiles[safeRelativePath] = entry.sha.Trim().ToLowerInvariant();
            }

            if (!remoteFiles.ContainsKey("SKILL.md"))
            {
                throw new InvalidOperationException($"Remote skill directory is missing SKILL.md: {normalizedSubdir} at {commitSha}");
            }

            log?.Invoke($"Remote file count: {remoteFiles.Count}");
            return new RemoteSnapshot(commitSha, normalizedSubdir, remoteFiles);
        }

        private static string FetchBranchHeadCommitSha(HttpClient client, GitHubRepoInfo repoInfo, string branch, Action<string> log)
        {
            var branchApiUrl = BuildBranchApiUrl(repoInfo, branch);
            log?.Invoke($"Fetching branch head commit...");
            var branchJson = DownloadString(client, branchApiUrl, SkillSyncDownload.MaxBranchBytes);
            var branchResponse = JsonUtility.FromJson<GitHubBranchResponse>(branchJson);
            var commitSha = branchResponse?.commit?.sha?.Trim();
            if (string.IsNullOrWhiteSpace(commitSha))
            {
                throw new InvalidOperationException($"Failed to resolve branch head commit SHA for: {branch}");
            }

            return commitSha;
        }

        private static string BuildBranchApiUrl(GitHubRepoInfo repoInfo, string branch)
        {
            return $"https://api.github.com/repos/{Uri.EscapeDataString(repoInfo.Owner)}/{Uri.EscapeDataString(repoInfo.Repo)}/branches/{Uri.EscapeDataString(branch)}";
        }

        private static string BuildTreeApiUrl(GitHubRepoInfo repoInfo, string reference)
        {
            return $"https://api.github.com/repos/{Uri.EscapeDataString(repoInfo.Owner)}/{Uri.EscapeDataString(repoInfo.Repo)}/git/trees/{Uri.EscapeDataString(reference)}?recursive=1";
        }

        private static string BuildRawFileUrl(GitHubRepoInfo repoInfo, string commitSha, string remoteFilePath)
        {
            var encodedPath = string.Join(
                "/",
                NormalizeRemotePath(remoteFilePath).Split(new[] { '/' }, StringSplitOptions.RemoveEmptyEntries).Select(Uri.EscapeDataString)
            );
            return $"https://raw.githubusercontent.com/{Uri.EscapeDataString(repoInfo.Owner)}/{Uri.EscapeDataString(repoInfo.Repo)}/{Uri.EscapeDataString(commitSha)}/{encodedPath}";
        }

        internal static HttpClient CreateGitHubClient()
        {
            var client = new HttpClient { Timeout = TimeSpan.FromSeconds(60) };
            client.DefaultRequestHeaders.UserAgent.ParseAdd("UnityMcpSkillSync/1.0");
            client.DefaultRequestHeaders.Accept.ParseAdd("application/vnd.github+json");
            return client;
        }

        internal static string DownloadString(HttpClient client, string url, int maxBytes = SkillSyncDownload.MaxTreeBytes)
        {
            byte[] bytes = SkillSyncDownload.ReadBytes(client, url, maxBytes);
            using var stream = new MemoryStream(bytes, false);
            using var reader = new StreamReader(stream, Encoding.UTF8, true);
            return reader.ReadToEnd();
        }

        private static byte[] DownloadBytes(HttpClient client, string url, long maxBytes)
        {
            return SkillSyncDownload.ReadBytes(client, url, maxBytes);
        }

        internal static string NormalizeRemotePath(string path)
        {
            if (string.IsNullOrWhiteSpace(path))
            {
                return string.Empty;
            }

            return path.Replace('\\', '/').Trim().Trim('/');
        }

        private static string CombineRemotePath(string left, string right)
        {
            var normalizedLeft = NormalizeRemotePath(left);
            var normalizedRight = NormalizeRemotePath(right);
            if (string.IsNullOrEmpty(normalizedLeft))
            {
                return normalizedRight;
            }

            if (string.IsNullOrEmpty(normalizedRight))
            {
                return normalizedLeft;
            }

            return $"{normalizedLeft}/{normalizedRight}";
        }

        internal static bool TryNormalizeRelativePath(string relativePath, out string normalizedPath)
        {
            normalizedPath = NormalizeRemotePath(relativePath);
            if (string.IsNullOrWhiteSpace(normalizedPath) || Path.IsPathRooted(normalizedPath))
            {
                return false;
            }

            var segments = normalizedPath.Split('/');
            if (segments.Length == 0)
            {
                return false;
            }

            foreach (var segment in segments)
            {
                if (
                    string.IsNullOrWhiteSpace(segment)
                    || string.Equals(segment, ".", StringComparison.Ordinal)
                    || string.Equals(segment, "..", StringComparison.Ordinal)
                    || segment.IndexOf(':') >= 0
                )
                {
                    return false;
                }
            }

            normalizedPath = string.Join("/", segments);
            return true;
        }

        internal static string ResolvePathUnderRoot(string root, string relativePath, StringComparison pathComparison)
        {
            if (!TryNormalizeRelativePath(relativePath, out var safeRelativePath))
            {
                throw new InvalidOperationException($"Unsafe relative path: {relativePath}");
            }

            var normalizedRoot = EnsureTrailingDirectorySeparator(Path.GetFullPath(root));
            var combinedPath = Path.Combine(normalizedRoot, safeRelativePath.Replace('/', Path.DirectorySeparatorChar));
            var fullPath = Path.GetFullPath(combinedPath);
            if (!fullPath.StartsWith(normalizedRoot, pathComparison))
            {
                throw new InvalidOperationException($"Path escapes install root: {relativePath}");
            }

            ValidateUnlinkedPath(root);
            return SafePathUtility.ResolveWithinRoot(root, fullPath);
        }

        private static string ValidateUnlinkedPath(string path)
        {
            var fullPath = Path.GetFullPath(path);
            return SafePathUtility.ResolveWithinRoot(Path.GetPathRoot(fullPath), fullPath);
        }

        private static string EnsureTrailingDirectorySeparator(string path)
        {
            return path.TrimEnd(Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar) + Path.DirectorySeparatorChar;
        }

        internal static SyncPlan BuildPlan(Dictionary<string, string> remoteFiles, Dictionary<string, string> localFiles, StringComparer pathComparer) =>
            BuildPlanWithBaseline(remoteFiles, localFiles, new Dictionary<string, string>(), pathComparer);

        internal static SyncPlan BuildPlanWithBaseline(
            Dictionary<string, string> remoteFiles,
            Dictionary<string, string> localFiles,
            Dictionary<string, string> baseline,
            StringComparer pathComparer
        )
        {
            // Deliberately conservative on every volume: case-distinct entries and case-only
            // renames need manual resolution, without creating a filesystem probe during preview.
            pathComparer = StringComparer.OrdinalIgnoreCase;
            ValidateRemoteFiles(remoteFiles);
            var localHashes = new Dictionary<string, string>(pathComparer);
            foreach (var file in localFiles)
            {
                if (localHashes.ContainsKey(file.Key))
                    throw new InvalidOperationException("Local skill paths collide when compared without case.");
                localHashes.Add(file.Key, ComputeGitBlobSha1(ValidateUnlinkedPath(file.Value)));
            }
            var remote = new Dictionary<string, string>(remoteFiles, pathComparer);
            var previous = new Dictionary<string, string>(baseline, pathComparer);
            var plan = new SyncPlan { LocalHashes = localHashes };
            foreach (var file in previous)
                plan.NextBaseline.Add(file.Key, file.Value);
            var paths = new HashSet<string>(previous.Keys, pathComparer);
            paths.UnionWith(localHashes.Keys);
            paths.UnionWith(remote.Keys);
            foreach (string path in paths.OrderBy(path => path, StringComparer.Ordinal))
            {
                localHashes.TryGetValue(path, out var localHash);
                remote.TryGetValue(path, out var remoteHash);
                previous.TryGetValue(path, out var baselineHash);
                string localName = localHashes.Keys.FirstOrDefault(key => pathComparer.Equals(key, path));
                string remoteName = remote.Keys.FirstOrDefault(key => pathComparer.Equals(key, path));
                string baselineName = previous.Keys.FirstOrDefault(key => pathComparer.Equals(key, path));
                string name = remoteName ?? localName ?? baselineName;
                if (remoteName != null && ((localName != null && localName != remoteName) || (baselineName != null && baselineName != remoteName)))
                {
                    plan.Conflicts.Add(name);
                    continue;
                }
                if (localHash == remoteHash)
                {
                    if (remoteHash == null)
                        plan.NextBaseline.Remove(name);
                    else
                        plan.NextBaseline[name] = remoteHash;
                }
                else if (baselineHash != null && localHash == baselineHash)
                {
                    if (remoteHash == null)
                    {
                        plan.Deleted.Add(name);
                        plan.NextBaseline.Remove(name);
                    }
                    else
                    {
                        plan.Updated.Add(name);
                        plan.NextBaseline[name] = remoteHash;
                    }
                }
                else if (baselineHash != null && remoteHash == baselineHash || remoteHash == null && baselineHash == null)
                    plan.Preserved.Add(name);
                else if (localHash == null && baselineHash == null)
                {
                    plan.Added.Add(name);
                    plan.NextBaseline[name] = remoteHash;
                }
                else
                {
                    plan.Conflicts.Add(name);
                    if (baselineHash == null)
                        plan.UnknownBaselineConflicts.Add(name);
                }
            }
            foreach (string path in plan.Added.Concat(plan.Updated).ToArray())
            {
                bool blocked = localHashes.Keys.Any(local =>
                    !plan.Deleted.Contains(local, pathComparer)
                    && (local.StartsWith(path + "/", StringComparison.OrdinalIgnoreCase) || path.StartsWith(local + "/", StringComparison.OrdinalIgnoreCase))
                );
                if (!blocked)
                    continue;
                plan.Added.Remove(path);
                plan.Updated.Remove(path);
                plan.Conflicts.Add(path);
                if (previous.TryGetValue(path, out var previousHash))
                    plan.NextBaseline[path] = previousHash;
                else
                    plan.NextBaseline.Remove(path);
            }
            RejectCaseParentChanges(plan, localHashes.Keys, previous);
            foreach (var list in new[] { plan.Added, plan.Updated, plan.Deleted, plan.Conflicts, plan.UnknownBaselineConflicts, plan.Preserved })
                list.Sort(StringComparer.Ordinal);
            return plan;
        }

        private static Dictionary<string, string> PathSpellings(IEnumerable<string> paths)
        {
            var spellings = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);
            foreach (string path in paths)
                for (string current = path; !string.IsNullOrEmpty(current); current = Path.GetDirectoryName(current)?.Replace('\\', '/'))
                {
                    if (spellings.TryGetValue(current, out var previous) && previous != current)
                        throw new InvalidOperationException("Skill paths differ only in case; resolve these names before synchronizing.");
                    spellings[current] = current;
                }
            return spellings;
        }

        private static void RejectCaseParentChanges(SyncPlan plan, IEnumerable<string> localPaths, Dictionary<string, string> baseline)
        {
            var spellings = PathSpellings(localPaths);
            foreach (string path in plan.Added.Concat(plan.Updated).ToArray())
            {
                bool conflict = false;
                for (string current = path; !string.IsNullOrEmpty(current); current = Path.GetDirectoryName(current)?.Replace('\\', '/'))
                    conflict |= spellings.TryGetValue(current, out var existing) && existing != current;
                if (!conflict)
                    continue;
                plan.Added.Remove(path);
                plan.Updated.Remove(path);
                plan.Conflicts.Add(path);
                if (baseline.TryGetValue(path, out var hash))
                    plan.NextBaseline[path] = hash;
                else
                    plan.NextBaseline.Remove(path);
            }
        }

        private static void ValidateRemoteFiles(Dictionary<string, string> files)
        {
            SkillSyncDownload.RequireFileCount(files.Count);
            PathSpellings(files.Keys);
            var seen = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
            foreach (var file in files)
            {
                if (
                    !TryNormalizeRelativePath(file.Key, out var normalized)
                    || normalized != file.Key
                    || IsMetadataPath(file.Key)
                    || !seen.Add(file.Key)
                    || !IsBlobHash(file.Value)
                )
                    throw new InvalidOperationException("The remote skill contains an unsafe, reserved, duplicate or invalid file entry.");
            }
            foreach (var path in seen)
                for (
                    string parent = Path.GetDirectoryName(path)?.Replace('\\', '/');
                    !string.IsNullOrEmpty(parent);
                    parent = Path.GetDirectoryName(parent)?.Replace('\\', '/')
                )
                    if (seen.Contains(parent))
                        throw new InvalidOperationException("The remote skill contains conflicting file and directory paths.");
        }

        private static bool IsBlobHash(string hash) =>
            hash != null && hash.Length == 40 && hash.All(character => character >= '0' && character <= '9' || character >= 'a' && character <= 'f');

        private static bool IsMetadataPath(string path) =>
            path.Equals(SyncOwnershipMarker, StringComparison.OrdinalIgnoreCase)
            || path.Equals(BaselineFile, StringComparison.OrdinalIgnoreCase)
            || path.StartsWith(SyncOwnershipMarker + "/", StringComparison.OrdinalIgnoreCase)
            || path.StartsWith(BaselineFile + "/", StringComparison.OrdinalIgnoreCase);

        internal static void ApplyPlan(
            string targetRoot,
            SyncPlan plan,
            Dictionary<string, string> remoteFiles,
            StringComparison pathComparison,
            Func<string, byte[]> download,
            Action<string> log
        )
        {
            ApplyBoundedPlan(targetRoot, plan, remoteFiles, pathComparison, (path, limit) => download(path), log);
        }

        internal static void ApplyBoundedPlan(
            string targetRoot,
            SyncPlan plan,
            Dictionary<string, string> remoteFiles,
            StringComparison pathComparison,
            Func<string, long, byte[]> download,
            Action<string> log
        ) => ApplyVerifiedPlan(targetRoot, plan, remoteFiles, pathComparison, download, log, null);

        private static void ApplyVerifiedPlan(
            string targetRoot,
            SyncPlan plan,
            Dictionary<string, string> remoteFiles,
            StringComparison pathComparison,
            Func<string, long, byte[]> download,
            Action<string> log,
            Action revalidateRemote,
            Action validateSelection = null
        )
        {
            pathComparison = StringComparison.OrdinalIgnoreCase;
            SkillSyncDownload.RequireFileCount((long)plan.Added.Count + plan.Updated.Count);
            ValidateRemoteFiles(remoteFiles);
            ValidatePlanPaths(targetRoot, plan, pathComparison);
            ValidateLocalSnapshot(targetRoot, plan);
            var writes = new Dictionary<string, byte[]>(StringComparer.OrdinalIgnoreCase);
            var budget = new SkillSyncDownload.ByteBudget();
            foreach (string path in plan.Added.Concat(plan.Updated))
            {
                ResolvePathUnderRoot(targetRoot, path, pathComparison);
                log?.Invoke($"Download: {path}");
                var bytes = download(path, budget.NextBlobLimit);
                budget.Admit(bytes.LongLength);
                if (ComputeGitBlobSha1(bytes) != remoteFiles[path])
                    throw new InvalidOperationException($"File hash mismatch: {path}");
                writes.Add(path, bytes);
            }
            foreach (var metadata in plan.Metadata)
            {
                string current = ResolvePathUnderRoot(targetRoot, metadata.Key, pathComparison);
                if (!File.Exists(current) || ComputeGitBlobSha1(current) != ComputeGitBlobSha1(metadata.Value))
                    writes.Add(metadata.Key, metadata.Value);
            }
            revalidateRemote?.Invoke();
            ValidatePlanPaths(targetRoot, plan, pathComparison);
            ValidateLocalSnapshot(targetRoot, plan);
            if (writes.Count == 0 && plan.Deleted.Count == 0)
                return;

            string parent = Path.GetDirectoryName(Path.GetFullPath(targetRoot));
            string transactionRoot = Path.Combine(parent, ".unity-mcp-sync-" + Guid.NewGuid().ToString("N"));
            var createdDirectories = new List<string>();
            var removedDirectories = new List<string>();
            var originalDirectories = new HashSet<string>(
                DirectorySnapshot(targetRoot).Select(path => ResolvePathUnderRoot(targetRoot, path, pathComparison)),
                StringComparer.OrdinalIgnoreCase
            );
            var backups = new Dictionary<string, (string path, string hash)>(StringComparer.OrdinalIgnoreCase);
            var staged = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);
            var installed = new List<string>();
            bool committed = false;
            try
            {
                EnsureDirectory(parent, createdDirectories);
                ValidateUnlinkedPath(transactionRoot);
                if (Directory.Exists(transactionRoot) || File.Exists(transactionRoot))
                    throw new IOException("The skill transaction directory is already occupied.");
                Directory.CreateDirectory(transactionRoot);
                int index = 0;
                foreach (var file in writes)
                {
                    BeforeFileOperation?.Invoke("stage", file.Key);
                    string stagePath = ResolvePathUnderRoot(transactionRoot, "new-" + index++, pathComparison);
                    using (var stream = new FileStream(stagePath, FileMode.CreateNew, FileAccess.Write, FileShare.None))
                    {
                        staged.Add(file.Key, stagePath);
                        stream.Write(file.Value, 0, file.Value.Length);
                    }
                }
                ValidateLocalSnapshot(targetRoot, plan);
                validateSelection?.Invoke();
                EnsureDirectory(targetRoot, createdDirectories);
                foreach (string path in plan.Updated.Concat(plan.Deleted))
                    Backup(path, plan.LocalHashes[path]);
                foreach (var file in writes.Where(file => !IsMetadataPath(file.Key)))
                    Install(file.Key);
                var expected = new Dictionary<string, string>(plan.LocalHashes, StringComparer.OrdinalIgnoreCase);
                foreach (string path in plan.Deleted)
                    expected.Remove(path);
                foreach (var file in writes.Where(file => !IsMetadataPath(file.Key)))
                    expected[file.Key] = ComputeGitBlobSha1(file.Value);
                if (!HashesEqual(expected, HashLocalFiles(targetRoot)))
                    throw new IOException("Local skill files changed during apply.");
                // Metadata is committed last; a failed metadata write still restores user files.
                foreach (var file in writes.Where(file => IsMetadataPath(file.Key)).OrderBy(file => file.Key == BaselineFile ? 1 : 0))
                {
                    BeforeFileOperation?.Invoke("metadata", file.Key);
                    string metadata = ResolvePathUnderRoot(targetRoot, file.Key, pathComparison);
                    string previous = plan.ExpectedMetadata?[file.Key];
                    if (previous != null)
                        Backup(file.Key, previous);
                    else if (File.Exists(metadata) || Directory.Exists(metadata))
                        throw new IOException("Skill sync metadata changed during apply.");
                    Install(file.Key);
                }
                committed = true;
            }
            catch (Exception error)
            {
                var rollbackErrors = new List<string>();
                foreach (string path in installed.AsEnumerable().Reverse())
                {
                    try
                    {
                        BeforeFileOperation?.Invoke("rollback_remove", path);
                        string destination = ResolvePathUnderRoot(targetRoot, path, pathComparison);
                        if (!File.Exists(destination) || ComputeGitBlobSha1(destination) != ComputeGitBlobSha1(writes[path]))
                            throw new IOException($"Changed destination retained: {path}");
                        File.Delete(ResolvePathUnderRoot(targetRoot, path, pathComparison));
                    }
                    catch (Exception rollback)
                    {
                        rollbackErrors.Add(rollback.Message);
                    }
                }
                foreach (var backup in backups.Reverse())
                {
                    try
                    {
                        BeforeFileOperation?.Invoke("rollback_restore", backup.Key);
                        string destination = ResolvePathUnderRoot(targetRoot, backup.Key, pathComparison);
                        RemoveEmptyDestinationDirectories(
                            destination,
                            removedDirectories,
                            new HashSet<string>(createdDirectories, StringComparer.OrdinalIgnoreCase)
                        );
                        if (File.Exists(destination) || Directory.Exists(destination))
                            throw new IOException($"Occupied recovery destination retained: {backup.Key}");
                        EnsureDirectory(Path.GetDirectoryName(destination), createdDirectories);
                        string saved = SafePathUtility.ResolveWithinRoot(ValidateUnlinkedPath(transactionRoot), backup.Value.path);
                        if (ComputeGitBlobSha1(saved) != backup.Value.hash)
                            throw new IOException($"Changed recovery backup retained: {backup.Key}");
                        File.Move(saved, ResolvePathUnderRoot(targetRoot, backup.Key, pathComparison));
                    }
                    catch (Exception rollback)
                    {
                        rollbackErrors.Add(rollback.Message);
                    }
                }
                foreach (string directory in removedDirectories.Where(originalDirectories.Contains).OrderBy(path => path.Length))
                {
                    try
                    {
                        ValidateUnlinkedPath(directory);
                        if (!Directory.Exists(directory) && !File.Exists(directory))
                            Directory.CreateDirectory(directory);
                    }
                    catch (Exception rollback)
                    {
                        rollbackErrors.Add(rollback.Message);
                    }
                }
                CleanupDirectories(createdDirectories.Where(path => !originalDirectories.Contains(path)));
                bool rolledBack = rollbackErrors.Count == 0;
                if (rolledBack)
                    CleanupTransaction(transactionRoot, staged.Values, backups.Values.Select(backup => backup.path), log);
                else
                    WriteRecoveryManifest(transactionRoot, targetRoot, backups, rollbackErrors.Add);
                throw new SyncApplyException(
                    error.Message + (rolledBack ? " Changes rolled back." : " Recovery required: " + string.Join(" ", rollbackErrors)),
                    rolledBack,
                    Directory.Exists(transactionRoot) ? transactionRoot : null
                );
            }
            finally
            {
                if (committed)
                {
                    CleanupTransaction(transactionRoot, staged.Values, backups.Values.Select(backup => backup.path), log);
                    if (Directory.Exists(transactionRoot))
                    {
                        plan.RecoveryPath = transactionRoot;
                        WriteRecoveryManifest(transactionRoot, targetRoot, backups, log);
                    }
                }
            }

            void Backup(string path, string expectedHash)
            {
                BeforeFileOperation?.Invoke("backup", path);
                validateSelection?.Invoke();
                string current = ResolvePathUnderRoot(targetRoot, path, pathComparison);
                if (!File.Exists(current) || ComputeGitBlobSha1(current) != expectedHash)
                    throw new IOException($"Local file changed before replacement: {path}");
                string saved = ResolvePathUnderRoot(transactionRoot, "old-" + backups.Count, pathComparison);
                File.Move(ResolvePathUnderRoot(targetRoot, path, pathComparison), saved);
                backups.Add(path, (saved, expectedHash));
            }

            void Install(string path)
            {
                BeforeFileOperation?.Invoke("install", path);
                validateSelection?.Invoke();
                string destination = ResolvePathUnderRoot(targetRoot, path, pathComparison);
                RemoveEmptyDestinationDirectories(
                    destination,
                    removedDirectories,
                    new HashSet<string>(originalDirectories.Concat(createdDirectories), StringComparer.OrdinalIgnoreCase)
                );
                EnsureDirectory(Path.GetDirectoryName(destination), createdDirectories);
                if (File.Exists(ResolvePathUnderRoot(targetRoot, path, pathComparison)))
                    throw new IOException($"Destination changed before installation: {path}");
                string source = SafePathUtility.ResolveWithinRoot(ValidateUnlinkedPath(transactionRoot), staged[path]);
                File.Move(source, ResolvePathUnderRoot(targetRoot, path, pathComparison));
                installed.Add(path);
                if (ComputeGitBlobSha1(ResolvePathUnderRoot(targetRoot, path, pathComparison)) != ComputeGitBlobSha1(writes[path]))
                    throw new IOException($"Installed file failed verification: {path}");
            }
        }

        private static void EnsureDirectory(string path, List<string> created)
        {
            path = ValidateUnlinkedPath(path);
            if (Directory.Exists(path))
                return;
            if (File.Exists(path))
                throw new IOException("A required skill directory is occupied by a file.");
            string parent = Path.GetDirectoryName(path);
            if (!string.IsNullOrEmpty(parent))
                EnsureDirectory(parent, created);
            ValidateUnlinkedPath(path);
            Directory.CreateDirectory(path);
            created.Add(path);
        }

        private static void RemoveEmptyDestinationDirectories(string path, List<string> removed, HashSet<string> expectedDirectories)
        {
            ValidateUnlinkedPath(path);
            if (!Directory.Exists(path))
                return;
            var directories = EnumerateContainedEntries(path)
                .Where(entry => entry.directory)
                .Select(entry => entry.path)
                .Append(path)
                .OrderByDescending(directory => directory.Length)
                .ToArray();
            if (directories.Any(directory => !expectedDirectories.Contains(directory)))
                throw new IOException("A destination directory changed during apply; it was retained.");
            foreach (string directory in directories)
            {
                ValidateUnlinkedPath(directory);
                if (Directory.EnumerateFileSystemEntries(directory).Any())
                    continue;
                Directory.Delete(ValidateUnlinkedPath(directory), false);
                removed.Add(directory);
            }
        }

        private static void CleanupDirectories(IEnumerable<string> directories)
        {
            foreach (string directory in directories.OrderByDescending(path => path.Length))
            {
                try
                {
                    ValidateUnlinkedPath(directory);
                    if (Directory.Exists(directory) && !Directory.EnumerateFileSystemEntries(directory).Any())
                        Directory.Delete(ValidateUnlinkedPath(directory), false);
                }
                catch (Exception) { /* Retain any replaced or occupied directory. */ }
            }
        }

        private static void WriteRecoveryManifest(
            string root,
            string installRoot,
            Dictionary<string, (string path, string hash)> backups,
            Action<string> report
        )
        {
            try
            {
                ValidateUnlinkedPath(root);
                var files = backups
                    .Where(backup => File.Exists(SafePathUtility.ResolveWithinRoot(root, backup.Value.path)))
                    .Select(backup => new RecoveryEntry
                    {
                        path = backup.Key,
                        backup = Path.GetFileName(backup.Value.path),
                        hash = backup.Value.hash,
                    })
                    .ToArray();
                var bytes = Encoding.UTF8.GetBytes(
                    JsonUtility.ToJson(new RecoveryDocument { installRoot = Path.GetFullPath(installRoot), files = files }, true)
                );
                string manifest = ResolvePathUnderRoot(root, "recovery.json", StringComparison.OrdinalIgnoreCase);
                using var stream = new FileStream(manifest, FileMode.CreateNew, FileAccess.Write, FileShare.None);
                stream.Write(bytes, 0, bytes.Length);
            }
            catch (Exception error)
            {
                report?.Invoke("Recovery mapping could not be written; backups remain at '" + root + "': " + error.Message);
            }
        }

        private static void CleanupTransaction(string root, IEnumerable<string> staged, IEnumerable<string> backups, Action<string> log)
        {
            try
            {
                ValidateUnlinkedPath(root);
                foreach (string path in staged.Concat(backups))
                {
                    string safePath = SafePathUtility.ResolveWithinRoot(root, path);
                    if (File.Exists(safePath))
                        File.Delete(safePath);
                }
                if (Directory.Exists(root) && !Directory.EnumerateFileSystemEntries(root).Any())
                    Directory.Delete(ValidateUnlinkedPath(root), false);
            }
            catch (Exception error)
            {
                log?.Invoke($"Skill transaction retained at '{root}': {error.Message}");
            }
        }

        private static void ValidatePlanPaths(string root, SyncPlan plan, StringComparison comparison)
        {
            ListFiles(root);
            foreach (var path in plan.Added.Concat(plan.Updated).Concat(plan.Deleted).Concat(plan.Metadata.Keys))
                ResolvePathUnderRoot(root, path, comparison);
        }

        private static Dictionary<string, string> HashLocalFiles(string root) =>
            ListFiles(root)
                .ToDictionary(
                    file => file.Key,
                    file => ComputeGitBlobSha1(ResolvePathUnderRoot(root, file.Key, StringComparison.OrdinalIgnoreCase)),
                    StringComparer.OrdinalIgnoreCase
                );

        private static bool HashesEqual(Dictionary<string, string> left, Dictionary<string, string> right) =>
            left.Count == right.Count
            && left.All(file => right.Keys.Contains(file.Key, StringComparer.Ordinal) && right.TryGetValue(file.Key, out var hash) && hash == file.Value);

        private static void ValidateLocalSnapshot(string root, SyncPlan plan)
        {
            if (
                !HashesEqual(plan.LocalHashes, HashLocalFiles(root))
                || plan.ExpectedMetadata != null && !HashesEqual(plan.ExpectedMetadata, MetadataSnapshot(root))
                || plan.ExpectedDirectories != null && !plan.ExpectedDirectories.SequenceEqual(DirectorySnapshot(root), StringComparer.Ordinal)
            )
                throw new InvalidOperationException("Local skill files or baseline changed after preview. Preview again before applying.");
        }

        internal static string ComputeGitBlobSha1(string filePath)
        {
            using var stream = new FileStream(filePath, FileMode.Open, FileAccess.Read, FileShare.Read);
            var headerBytes = Encoding.UTF8.GetBytes($"blob {stream.Length}\0");
            using var sha1 = SHA1.Create();
            sha1.TransformBlock(headerBytes, 0, headerBytes.Length, null, 0);
            byte[] buffer = new byte[64 * 1024];
            int read;
            while ((read = stream.Read(buffer, 0, buffer.Length)) != 0)
                sha1.TransformBlock(buffer, 0, read, null, 0);
            sha1.TransformFinalBlock(Array.Empty<byte>(), 0, 0);
            return BitConverter.ToString(sha1.Hash ?? Array.Empty<byte>()).Replace("-", string.Empty).ToLowerInvariant();
        }

        internal static string ComputeGitBlobSha1(byte[] bytes)
        {
            var headerBytes = Encoding.UTF8.GetBytes($"blob {bytes.Length}\0");
            using var sha1 = SHA1.Create();
            sha1.TransformBlock(headerBytes, 0, headerBytes.Length, null, 0);
            sha1.TransformBlock(bytes, 0, bytes.Length, null, 0);
            sha1.TransformFinalBlock(Array.Empty<byte>(), 0, 0);
            return BitConverter.ToString(sha1.Hash ?? Array.Empty<byte>()).Replace("-", string.Empty).ToLowerInvariant();
        }

        internal static Dictionary<string, string> ListFiles(string root)
        {
            var map = new Dictionary<string, string>(StringComparer.Ordinal);
            var normalizedRoot = ValidateUnlinkedPath(root);
            foreach (var entry in EnumerateContainedEntries(normalizedRoot))
            {
                if (entry.directory)
                    continue;
                var relativePath = Path.GetRelativePath(normalizedRoot, entry.path).Replace('\\', '/');
                if (IsMetadataPath(relativePath))
                {
                    continue;
                }

                SkillSyncDownload.RequireFileCount((long)map.Count + 1);
                map[relativePath] = entry.path;
            }

            return map;
        }

        private static IEnumerable<(string path, bool directory)> EnumerateContainedEntries(string root)
        {
            root = ValidateUnlinkedPath(root);
            if (!Directory.Exists(root))
                yield break;
            var pending = new Stack<(string path, int depth)>();
            pending.Push((root, 0));
            int count = 0;
            while (pending.Count > 0)
            {
                var directory = pending.Pop();
                ValidateUnlinkedPath(root);
                var safeDirectory = SafePathUtility.ResolveWithinRoot(root, directory.path);
                // Top-level enumeration never descends automatically through a link.
                foreach (var entry in Directory.EnumerateFileSystemEntries(safeDirectory))
                {
                    if (++count > MaxLocalEntries)
                        throw new InvalidOperationException("Local skill tree exceeds the entry limit.");
                    ValidateUnlinkedPath(root);
                    var safeEntry = SafePathUtility.ResolveWithinRoot(root, entry);
                    var attributes = File.GetAttributes(safeEntry);
                    if ((attributes & FileAttributes.ReparsePoint) != 0)
                        throw new InvalidOperationException("Linked skill entries are not permitted.");
                    bool isDirectory = (attributes & FileAttributes.Directory) != 0;
                    if (isDirectory)
                    {
                        if (directory.depth >= MaxLocalDepth)
                            throw new InvalidOperationException("Local skill tree exceeds the directory depth limit.");
                        pending.Push((safeEntry, directory.depth + 1));
                    }
                    yield return (safeEntry, isDirectory);
                }
            }
        }

        private static byte[] ReadMetadataBytes(string root, string path)
        {
            using var stream = new FileStream(
                ResolvePathUnderRoot(root, path, StringComparison.OrdinalIgnoreCase),
                FileMode.Open,
                FileAccess.Read,
                FileShare.Read
            );
            return SkillSyncDownload.ReadBounded(stream, MaxBaselineBytes, CancellationToken.None);
        }

        private static BaselineDocument ReadBaseline(string root, string repository, string branch)
        {
            string path = ResolvePathUnderRoot(root, BaselineFile, StringComparison.OrdinalIgnoreCase);
            if (Directory.Exists(path))
                throw new InvalidOperationException("The skill baseline path is a directory.");
            if (!File.Exists(path))
                return null;
            BaselineDocument baseline;
            try
            {
                baseline = JsonUtility.FromJson<BaselineDocument>(Encoding.UTF8.GetString(ReadMetadataBytes(root, BaselineFile)));
            }
            catch (Exception error)
            {
                throw new InvalidOperationException("The skill baseline cannot be read safely.", error);
            }
            if (
                baseline == null
                || baseline.version != 1 && baseline.version != 2
                || baseline.repository != repository
                || baseline.version == 1 && string.IsNullOrWhiteSpace(baseline.branch)
                || baseline.subdir != SkillSubdir
                || !IsBlobHash(baseline.commit)
                || baseline.files == null
            )
                throw new InvalidOperationException("The skill baseline is invalid or belongs to a different source.");
            SkillSyncDownload.RequireFileCount(baseline.files.Length);
            var seen = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
            foreach (var file in baseline.files)
                if (
                    file == null
                    || !TryNormalizeRelativePath(file.path, out var normalized)
                    || normalized != file.path
                    || IsMetadataPath(file.path)
                    || !seen.Add(file.path)
                    || !IsBlobHash(file.hash)
                )
                    throw new InvalidOperationException("The skill baseline contains invalid or colliding entries.");
            ValidateRemoteFiles(baseline.files.ToDictionary(file => file.path, file => file.hash, StringComparer.Ordinal));
            return baseline;
        }

        private static Dictionary<string, string> MetadataSnapshot(string root)
        {
            var hashes = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);
            foreach (string name in new[] { SyncOwnershipMarker, BaselineFile })
            {
                string path = ResolvePathUnderRoot(root, name, StringComparison.OrdinalIgnoreCase);
                if (Directory.Exists(path))
                    throw new InvalidOperationException("A reserved skill metadata path is a directory.");
                hashes[name] = File.Exists(path) ? ComputeGitBlobSha1(path) : null;
            }
            return hashes;
        }

        private static string[] DirectorySnapshot(string root) =>
            EnumerateContainedEntries(root)
                .Where(entry => entry.directory)
                .Select(entry => Path.GetRelativePath(root, entry.path).Replace('\\', '/'))
                .OrderBy(path => path, StringComparer.Ordinal)
                .ToArray();

        internal static StringComparison GetPathComparison(string root)
        {
            ListFiles(root);
            return StringComparison.OrdinalIgnoreCase;
        }

        internal static StringComparer GetPathComparer(StringComparison pathComparison)
        {
            return pathComparison == StringComparison.Ordinal ? StringComparer.Ordinal : StringComparer.OrdinalIgnoreCase;
        }

        internal static string ResolveAndValidateInstallPath(string installDir)
        {
            if (string.IsNullOrWhiteSpace(installDir))
            {
                throw new InvalidOperationException("Install Dir is empty.");
            }

            var trimmed = installDir.Trim();
            if (trimmed.IndexOfAny(Path.GetInvalidPathChars()) >= 0)
            {
                throw new InvalidOperationException($"Install Dir contains invalid path characters: {installDir}");
            }

            string expandedPath;
            try
            {
                expandedPath = ExpandPath(trimmed);
            }
            catch (Exception ex)
            {
                throw new InvalidOperationException($"Install Dir is invalid and cannot be resolved: {installDir}", ex);
            }

            if (string.IsNullOrWhiteSpace(expandedPath))
            {
                throw new InvalidOperationException("Install Dir resolved to an empty path.");
            }

            return ValidateUnlinkedPath(expandedPath);
        }

        internal static string ExpandPath(string path)
        {
            if (string.IsNullOrWhiteSpace(path))
            {
                return string.Empty;
            }

            var expanded = path.Trim();
            if (expanded.StartsWith("~", StringComparison.Ordinal))
            {
                var userHome = Environment.GetFolderPath(Environment.SpecialFolder.UserProfile);
                expanded = Path.Combine(userHome, expanded.Substring(1).TrimStart('/', '\\'));
            }

            return Path.GetFullPath(expanded);
        }

        private static string ShortCommit(string commit)
        {
            if (string.IsNullOrWhiteSpace(commit))
            {
                return "(none)";
            }

            return commit.Length <= 8 ? commit : commit.Substring(0, 8);
        }

        private static string ShortHash(string hash)
        {
            if (string.IsNullOrWhiteSpace(hash))
            {
                return "(none)";
            }

            return hash.Length <= 6 ? hash : hash.Substring(0, 6);
        }

        private static void LogPlanDetails(SyncPlan plan, Action<string> log)
        {
            log?.Invoke(
                $"Preview: +{plan.Added.Count} ~{plan.Updated.Count} -{plan.Deleted.Count}; preserved {plan.Preserved.Count}; conflicts {plan.Conflicts.Count}."
            );
            foreach (var path in plan.Added)
                log?.Invoke($"+ {path}");
            foreach (var path in plan.Updated)
                log?.Invoke($"~ {path}");
            foreach (var path in plan.Deleted)
                log?.Invoke($"- {path}");
            foreach (var path in plan.Preserved)
                log?.Invoke($"Preserve local: {path}");
            foreach (var path in plan.Conflicts)
                log?.Invoke(
                    plan.UnknownBaselineConflicts.Contains(path)
                        ? $"Review required, no recorded baseline; local file kept: {path}"
                        : $"Review required, local file or deletion kept: {path}"
                );
        }

        internal readonly struct GitHubRepoInfo
        {
            public GitHubRepoInfo(string owner, string repo)
            {
                Owner = owner;
                Repo = repo;
            }

            public string Owner { get; }
            public string Repo { get; }
        }

        internal readonly struct RemoteSnapshot
        {
            public RemoteSnapshot(string commitSha, string subdirPath, Dictionary<string, string> files)
            {
                CommitSha = commitSha;
                SubdirPath = subdirPath;
                Files = files;
            }

            public string CommitSha { get; }
            public string SubdirPath { get; }
            public Dictionary<string, string> Files { get; }
        }

        [Serializable]
        internal sealed class GitHubTreeResponse
        {
            public string sha;
            public GitHubTreeEntry[] tree;
            public bool truncated;
        }

        [Serializable]
        internal sealed class GitHubBranchResponse
        {
            public GitHubBranchCommit commit;
        }

        [Serializable]
        internal sealed class GitHubBranchCommit
        {
            public string sha;
        }

        [Serializable]
        internal sealed class GitHubTreeEntry
        {
            public string path;
            public string type;
            public string sha;
            public long size;
        }

        [Serializable]
        private sealed class BaselineDocument
        {
            public int version;
            public string repository;
            public string branch;
            public string selection;
            public string packageVersion;
            public string subdir;
            public string commit;
            public BaselineEntry[] files;
        }

        [Serializable]
        private sealed class BaselineEntry
        {
            public string path;
            public string hash;
        }

        [Serializable]
        private sealed class RecoveryDocument
        {
            public string installRoot;
            public RecoveryEntry[] files;
        }

        [Serializable]
        private sealed class RecoveryEntry
        {
            public string path;
            public string backup;
            public string hash;
        }

        private sealed class SyncApplyException : IOException
        {
            public bool RolledBack { get; }
            public string RecoveryPath { get; }

            public SyncApplyException(string message, bool rolledBack, string recoveryPath)
                : base(message)
            {
                RolledBack = rolledBack;
                RecoveryPath = recoveryPath;
            }
        }

        internal sealed class SyncPlan
        {
            public List<string> Added { get; } = new();
            public List<string> Updated { get; } = new();
            public List<string> Deleted { get; } = new();
            public List<string> Conflicts { get; } = new();
            public List<string> UnknownBaselineConflicts { get; } = new();
            public List<string> Preserved { get; } = new();
            internal Dictionary<string, string> LocalHashes = new(StringComparer.OrdinalIgnoreCase);
            internal Dictionary<string, string> NextBaseline = new(StringComparer.OrdinalIgnoreCase);
            internal Dictionary<string, string> ExpectedMetadata;
            internal string[] ExpectedDirectories;
            internal Dictionary<string, byte[]> Metadata = new(StringComparer.OrdinalIgnoreCase);
            internal string RecoveryPath;
        }
    }
}
