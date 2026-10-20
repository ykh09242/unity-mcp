using System;
using System.IO;
using System.Security.Cryptography;
using System.Text;
using MCPForUnity.Editor.Constants;
using MCPForUnity.Editor.Helpers;
using UnityEditor;
using UnityEngine;
using PackageInfo = UnityEditor.PackageManager.PackageInfo;

namespace MCPForUnity.Editor.Services
{
    /// <summary>
    /// Handles copying a local MCPForUnity folder into the current project's package location with backup/restore support.
    /// </summary>
    public class PackageDeploymentService : IPackageDeploymentService
    {
        private const string BackupRootFolderName = "MCPForUnityDeployBackups";
        private readonly string projectRoot;
        private readonly Func<string> targetOverride;
        private readonly Func<string, string> readPreference;
        private readonly Action<string, string> writePreference;
        private readonly Action<string> deletePreference;

        public PackageDeploymentService()
            : this(GetProjectRoot(), null, key => EditorPrefs.GetString(key, string.Empty), EditorPrefs.SetString, EditorPrefs.DeleteKey) { }

        internal PackageDeploymentService(
            string projectRoot,
            Func<string> targetPath,
            Func<string, string> readPreference,
            Action<string, string> writePreference,
            Action<string> deletePreference
        )
        {
            this.projectRoot = CanonicalPath(projectRoot);
            targetOverride = targetPath;
            this.readPreference = readPreference;
            this.writePreference = writePreference;
            this.deletePreference = deletePreference;
        }

        private string PreferenceKey(string key)
        {
            string identity = Path.DirectorySeparatorChar == '\\' ? projectRoot.ToUpperInvariant() : projectRoot;
            using (var hash = SHA256.Create())
                return key + ".Project." + BitConverter.ToString(hash.ComputeHash(Encoding.UTF8.GetBytes(identity))).Replace("-", "");
        }

        private string Read(string key) => readPreference(PreferenceKey(key)) ?? string.Empty;

        private void Write(string key, string value) => writePreference(PreferenceKey(key), value);

        private static string CanonicalPath(string path) => Path.GetFullPath(path).TrimEnd(Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar);

        private static bool SamePath(string a, string b) =>
            string.Equals(
                CanonicalPath(a),
                CanonicalPath(b),
                Path.DirectorySeparatorChar == '\\' ? StringComparison.OrdinalIgnoreCase : StringComparison.Ordinal
            );

        public string GetStoredSourcePath()
        {
            return Read(EditorPrefKeys.PackageDeploySourcePath);
        }

        public void SetStoredSourcePath(string path)
        {
            ValidateSource(path);
            Write(EditorPrefKeys.PackageDeploySourcePath, Path.GetFullPath(path));
        }

        public void ClearStoredSourcePath()
        {
            deletePreference(PreferenceKey(EditorPrefKeys.PackageDeploySourcePath));
        }

        public string GetTargetPath()
        {
            if (targetOverride != null)
                return targetOverride();

            // Prefer Package Manager resolved path for the installed package
            var packageInfo = PackageInfo.FindForAssembly(typeof(PackageDeploymentService).Assembly);
            if (packageInfo != null)
            {
                if (!string.IsNullOrEmpty(packageInfo.resolvedPath) && Directory.Exists(packageInfo.resolvedPath))
                {
                    return packageInfo.resolvedPath;
                }

                if (!string.IsNullOrEmpty(packageInfo.assetPath))
                {
                    string absoluteFromAsset = MakeAbsolute(packageInfo.assetPath);
                    if (Directory.Exists(absoluteFromAsset))
                    {
                        return absoluteFromAsset;
                    }
                }
            }

            // Fallback to computed package root
            string packageRoot = AssetPathUtility.GetMcpPackageRootPath();
            if (!string.IsNullOrEmpty(packageRoot))
            {
                string absolutePath = MakeAbsolute(packageRoot);
                if (Directory.Exists(absolutePath))
                {
                    return absolutePath;
                }
            }

            return null;
        }

        public string GetTargetDisplayPath()
        {
            string target = GetTargetPath();
            if (string.IsNullOrEmpty(target))
                return "Not found (check Packages/manifest.json)";
            // Use forward slashes to avoid backslash escape sequence issues in UI text
            return target.Replace('\\', '/');
        }

        public string GetLastBackupPath()
        {
            return TryGetRestorePaths(out string backup, out _) ? backup : string.Empty;
        }

        public bool HasBackup()
        {
            string path = GetLastBackupPath();
            return !string.IsNullOrEmpty(path) && Directory.Exists(path);
        }

        public PackageDeploymentResult DeployFromStoredSource()
        {
            string sourcePath = GetStoredSourcePath();
            if (string.IsNullOrEmpty(sourcePath))
            {
                return Fail("Select a MCPForUnity folder first.");
            }

            string validationError = ValidateSource(sourcePath, throwOnError: false);
            if (!string.IsNullOrEmpty(validationError))
            {
                return Fail(validationError);
            }

            string targetPath = GetTargetPath();
            if (string.IsNullOrEmpty(targetPath))
            {
                return Fail("Could not locate the installed MCP package. Check Packages/manifest.json.");
            }

            if (PathsOverlap(sourcePath, targetPath))
            {
                return Fail("Source and target folders must not be the same or contain one another.");
            }

            try
            {
                EditorUtility.DisplayProgressBar("Deploy MCP for Unity", "Creating backup...", 0.25f);
                string backupRoot = Path.Combine(projectRoot, "Library", BackupRootFolderName);
                string backupPath = CreateBackup(targetPath, backupRoot);

                // Publish the completed backup before any destructive replacement, so a failed copy can be recovered.
                Write(EditorPrefKeys.PackageDeployLastBackupPath, backupPath);
                Write(EditorPrefKeys.PackageDeployLastTargetPath, targetPath);
                Write(EditorPrefKeys.PackageDeployLastSourcePath, sourcePath);

                EditorUtility.DisplayProgressBar("Deploy MCP for Unity", "Replacing package contents...", 0.7f);
                CopyCoreFolders(sourcePath, targetPath);

                AssetDatabase.Refresh(ImportAssetOptions.ForceUpdate);
                return Success("Deployment completed.", sourcePath, targetPath, backupPath);
            }
            catch (Exception ex)
            {
                McpLog.Error($"Deployment failed: {ex.Message}");
                return Fail($"Deployment failed: {ex.Message}");
            }
            finally
            {
                EditorUtility.ClearProgressBar();
            }
        }

        public PackageDeploymentResult RestoreLastBackup()
        {
            if (!TryGetRestorePaths(out string backupPath, out string targetPath))
                return Fail("No backup matches this project and its currently installed package.");

            if (PathsOverlap(backupPath, targetPath))
            {
                return Fail("Backup and target folders must not be the same or contain one another.");
            }

            try
            {
                EditorUtility.DisplayProgressBar("Restore MCP for Unity", "Restoring backup...", 0.5f);
                ReplaceDirectory(backupPath, targetPath);

                AssetDatabase.Refresh(ImportAssetOptions.ForceUpdate);
                return Success("Restore completed.", null, targetPath, backupPath);
            }
            catch (Exception ex)
            {
                McpLog.Error($"Restore failed: {ex.Message}");
                return Fail($"Restore failed: {ex.Message}");
            }
            finally
            {
                EditorUtility.ClearProgressBar();
            }
        }

        private bool TryGetRestorePaths(out string backup, out string target)
        {
            backup = Read(EditorPrefKeys.PackageDeployLastBackupPath);
            target = GetTargetPath();
            string recordedTarget = Read(EditorPrefKeys.PackageDeployLastTargetPath);
            if (string.IsNullOrEmpty(backup) || string.IsNullOrEmpty(target) || string.IsNullOrEmpty(recordedTarget))
                return false;
            try
            {
                string backupRoot = CanonicalPath(Path.Combine(projectRoot, "Library", BackupRootFolderName));
                string canonicalBackup = CanonicalPath(backup);
                var comparison = Path.DirectorySeparatorChar == '\\' ? StringComparison.OrdinalIgnoreCase : StringComparison.Ordinal;
                return SamePath(target, recordedTarget)
                    && canonicalBackup.StartsWith(backupRoot + Path.DirectorySeparatorChar, comparison)
                    && Directory.Exists(canonicalBackup);
            }
            catch (Exception ex) when (ex is ArgumentException || ex is IOException || ex is NotSupportedException)
            {
                return false;
            }
        }

        private void CopyCoreFolders(string sourceRoot, string targetRoot)
        {
            string sourceEditor = Path.Combine(sourceRoot, "Editor");
            string sourceRuntime = Path.Combine(sourceRoot, "Runtime");

            ReplaceDirectory(sourceEditor, Path.Combine(targetRoot, "Editor"));
            ReplaceDirectory(sourceRuntime, Path.Combine(targetRoot, "Runtime"));
        }

        private static void ReplaceDirectory(string source, string destination)
        {
            if (Directory.Exists(destination))
            {
                FileUtil.DeleteFileOrDirectory(destination);
            }

            FileUtil.CopyFileOrDirectory(source, destination);
        }

        private string CreateBackup(string targetPath, string backupRoot)
        {
            Directory.CreateDirectory(backupRoot);

            string stamp = DateTime.Now.ToString("yyyyMMdd_HHmmss");
            string backupPath = Path.Combine(backupRoot, $"backup_{stamp}_{Guid.NewGuid():N}");

            FileUtil.CopyFileOrDirectory(targetPath, backupPath);
            return backupPath;
        }

        private static string ValidateSource(string sourcePath, bool throwOnError = true)
        {
            if (string.IsNullOrEmpty(sourcePath))
            {
                if (throwOnError)
                {
                    throw new ArgumentException("Source path cannot be empty.");
                }

                return "Source path is empty.";
            }

            if (!Directory.Exists(sourcePath))
            {
                if (throwOnError)
                {
                    throw new ArgumentException("Selected folder does not exist.");
                }

                return "Selected folder does not exist.";
            }

            bool hasEditor = Directory.Exists(Path.Combine(sourcePath, "Editor"));
            bool hasRuntime = Directory.Exists(Path.Combine(sourcePath, "Runtime"));

            if (!hasEditor || !hasRuntime)
            {
                string message = "Folder must contain Editor and Runtime subfolders.";
                if (throwOnError)
                {
                    throw new ArgumentException(message);
                }

                return message;
            }

            return null;
        }

        private static string MakeAbsolute(string assetPath)
        {
            assetPath = assetPath.Replace('\\', '/');

            if (assetPath.StartsWith("Assets/", StringComparison.OrdinalIgnoreCase))
            {
                return Path.GetFullPath(Path.Combine(Application.dataPath, "..", assetPath));
            }

            if (assetPath.StartsWith("Packages/", StringComparison.OrdinalIgnoreCase))
            {
                return Path.GetFullPath(Path.Combine(Application.dataPath, "..", assetPath));
            }

            return Path.GetFullPath(assetPath);
        }

        private static string GetProjectRoot()
        {
            return Path.GetFullPath(Path.Combine(Application.dataPath, ".."));
        }

        private static bool PathsOverlap(string a, string b)
        {
            string normA = Path.GetFullPath(a).TrimEnd(Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar);
            string normB = Path.GetFullPath(b).TrimEnd(Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar);
            return string.Equals(normA, normB, StringComparison.OrdinalIgnoreCase)
                || normA.StartsWith(normB + Path.DirectorySeparatorChar, StringComparison.OrdinalIgnoreCase)
                || normB.StartsWith(normA + Path.DirectorySeparatorChar, StringComparison.OrdinalIgnoreCase);
        }

        private static PackageDeploymentResult Success(string message, string source, string target, string backup)
        {
            return new PackageDeploymentResult
            {
                Success = true,
                Message = message,
                SourcePath = source,
                TargetPath = target,
                BackupPath = backup,
            };
        }

        private static PackageDeploymentResult Fail(string message)
        {
            return new PackageDeploymentResult { Success = false, Message = message };
        }
    }
}
