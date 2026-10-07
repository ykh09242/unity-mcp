using System;
using System.IO;
using UnityEngine;

namespace MCPForUnity.Editor.Helpers
{
    /// <summary>
    /// Project-path conversions shared by the asset-gen import/write code: project-relative
    /// ("Assets/...") ↔ absolute on-disk paths, with forward-slash normalization for
    /// cross-platform consistency.
    /// </summary>
    public static class AssetGenPaths
    {
        /// <summary>Resolve a project-relative ("Assets/...") path to an absolute, forward-slashed path.</summary>
        public static string ToAbsolute(string projectRelative)
        {
            if (string.IsNullOrWhiteSpace(projectRelative))
                return projectRelative;
            string p = projectRelative.Replace('\\', '/');
            string abs = Path.IsPathRooted(p) ? p : Path.Combine(ProjectRoot(), p);
            return MCPForUnity.Runtime.Helpers.SafePathUtility.ResolveWithinRoot(Application.dataPath, abs).Replace('\\', '/');
        }

        /// <summary>Convert an absolute (or already-relative) path to a project-relative ("Assets/...") path.</summary>
        public static string ToProjectRelative(string path)
        {
            if (TryGetAssetsRelativePath(path, out string rel))
                return rel;
            return path?.Replace('\\', '/');
        }

        /// <summary>
        /// Normalize an absolute or "Assets/..." path and verify it resolves inside the project's
        /// Assets directory. Rejects traversal such as "Assets/../ProjectSettings".
        /// </summary>
        public static bool TryGetAssetsRelativePath(string path, out string projectRelative)
        {
            projectRelative = null;
            if (string.IsNullOrWhiteSpace(path))
                return false;

            try
            {
                string p = path.Replace('\\', '/');
                string abs;
                if (p == "Assets" || p.StartsWith("Assets/", StringComparison.Ordinal))
                {
                    abs = Path.Combine(ProjectRoot(), p);
                }
                else if (Path.IsPathRooted(p))
                {
                    abs = p;
                }
                else
                {
                    return false;
                }

                string full = MCPForUnity.Runtime.Helpers.SafePathUtility.ResolveWithinRoot(Application.dataPath, abs).Replace('\\', '/');
                string dataPath = Path.GetFullPath(Application.dataPath).Replace('\\', '/').TrimEnd('/');
                if (string.Equals(full, dataPath, StringComparison.Ordinal))
                {
                    projectRelative = "Assets";
                    return true;
                }

                string prefix = dataPath + "/";
                if (!full.StartsWith(prefix, StringComparison.Ordinal))
                    return false;

                projectRelative = "Assets/" + full.Substring(prefix.Length);
                return true;
            }
            catch
            {
                return false;
            }
        }

        /// <summary>Normalize an Assets folder path without creating it; reject existing files at any folder component.</summary>
        public static bool TryGetAssetsFolder(string path, out string projectRelative)
        {
            if (!TryGetAssetsRelativePath(path, out projectRelative))
                return false;
            projectRelative = projectRelative.TrimEnd('/');
            string current = ToAbsolute(projectRelative);
            string assetsRoot = Path.GetFullPath(Application.dataPath).Replace('\\', '/').TrimEnd('/');
            while (!string.IsNullOrEmpty(current))
            {
                if (File.Exists(current))
                {
                    projectRelative = null;
                    return false;
                }
                if (string.Equals(current, assetsRoot, StringComparison.Ordinal))
                    break;
                current = Path.GetDirectoryName(current)?.Replace('\\', '/');
            }
            return true;
        }

        /// <summary>
        /// Validate an optional caller-supplied output folder. Empty is allowed (the tool picks a
        /// default); a non-empty value must resolve under the project's Assets folder. Shared by the
        /// generate_* tools.
        /// </summary>
        public static bool NormalizeOutputFolder(string outputFolder, out string normalized, out string error)
        {
            normalized = outputFolder;
            error = null;
            if (string.IsNullOrWhiteSpace(outputFolder))
                return true;
            if (TryGetAssetsFolder(outputFolder, out normalized))
                return true;
            error = "'output_folder' must resolve to a folder under the project's Assets folder, without an existing file at the destination or an ancestor.";
            return false;
        }

        /// <summary>Resolve the effective destination before allocating work, preserving the legacy fallback for invalid configured roots.</summary>
        internal static string ResolveOutputFolder(string outputFolder, string subfolder)
        {
            string requested = !string.IsNullOrWhiteSpace(outputFolder) ? outputFolder : AssetGenPrefs.OutputRoot + "/" + subfolder;
            if (string.IsNullOrWhiteSpace(outputFolder) && !TryGetAssetsRelativePath(requested, out _))
                requested = AssetGenPrefs.DefaultOutputRoot + "/" + subfolder;
            if (!NormalizeOutputFolder(requested, out string normalized, out string error))
                throw new ArgumentException(error);
            return normalized;
        }

        private static string ProjectRoot()
        {
            string dataPath = Path.GetFullPath(Application.dataPath).Replace('\\', '/');
            return dataPath.Substring(0, dataPath.Length - "Assets".Length);
        }
    }
}
