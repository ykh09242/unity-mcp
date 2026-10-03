using System;
using UnityEngine;

namespace MCPForUnity.Runtime.Helpers
{
    /// <summary>Physical containment for caller-selected Unity asset reads and references.</summary>
    public static class UnityAssetPath
    {
        public static string Resolve(string path, bool allowPackages = false, bool allowBuiltIn = false)
        {
            if (string.IsNullOrWhiteSpace(path)) throw new ArgumentException("An asset path is required.");
            string normalized = path.Replace('\\', '/');
            if (normalized == "Resources/unity_builtin_extra" || normalized == "Library/unity default resources")
            {
                if (!allowBuiltIn) throw new ArgumentException("Built-in assets are not permitted for this operation.");
                return normalized;
            }
            if (normalized.StartsWith("/", StringComparison.Ordinal) || normalized.IndexOf(':') >= 0)
                throw new ArgumentException("Rooted asset paths are not permitted.");
            foreach (string part in normalized.Split('/'))
                if (part.Length == 0 || part == "." || part == ".." ||
                    part.IndexOfAny(new[] { '\0', '*', '?', '"', '<', '>', '|', '\r', '\n' }) >= 0)
                    throw new ArgumentException("Invalid asset path segment.");
            if (normalized.StartsWith("Packages/", StringComparison.OrdinalIgnoreCase))
            {
#if UNITY_EDITOR
                if (!allowPackages) throw new ArgumentException("Package assets are not permitted for this operation.");
                normalized = "Packages/" + normalized.Substring(9);
                var package = UnityEditor.PackageManager.PackageInfo.FindForAssetPath(normalized);
                if (package == null || string.IsNullOrEmpty(package.assetPath) || string.IsNullOrEmpty(package.resolvedPath) ||
                    !normalized.StartsWith(package.assetPath + "/", StringComparison.Ordinal))
                    throw new ArgumentException("Asset path must identify a registered package asset.");
                // A registered package is an explicit read root, but its ancestry may not hide a link.
                string packageRoot = System.IO.Path.GetFullPath(package.resolvedPath);
                SafePathUtility.ResolveWithinRoot(System.IO.Path.GetPathRoot(packageRoot), packageRoot);
                SafePathUtility.ResolveWithinRoot(package.resolvedPath, normalized.Substring(package.assetPath.Length + 1));
                return normalized;
#else
                throw new ArgumentException("Package asset resolution requires the Editor.");
#endif
            }
            if (normalized.Equals("Assets", StringComparison.OrdinalIgnoreCase)) normalized = "Assets";
            else if (normalized.StartsWith("Assets/", StringComparison.OrdinalIgnoreCase)) normalized = "Assets/" + normalized.Substring(7);
            else normalized = "Assets/" + normalized;
            SafePathUtility.ResolveWithinRoot(Application.dataPath, normalized.Length == 6 ? "." : normalized.Substring(7));
            return normalized;
        }
    }
}
