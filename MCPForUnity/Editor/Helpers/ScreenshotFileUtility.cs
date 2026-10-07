using MCPForUnity.Runtime.Helpers;

namespace MCPForUnity.Editor.Helpers
{
    internal static class ScreenshotFileUtility
    {
        public static void WriteCaptureBytes(string fullPath, byte[] bytes, bool ensureUniqueFileName = true)
        {
            if (bytes == null)
                throw new System.ArgumentNullException(nameof(bytes));
            // Validate before registering any Assets folders; the runtime writer rechecks at open.
            string folder = ScreenshotUtility.ResolveFolderAbsolute(System.IO.Path.GetDirectoryName(fullPath));
            fullPath = SafePathUtility.ResolveWithinRoot(folder, fullPath);
            string assetPath = ScreenshotUtility.ToProjectRelativePath(fullPath);
            using (var folders = new AssetFolderScope())
            {
                if (ScreenshotUtility.IsUnderAssets(assetPath))
                    folders.EnsureParentDirectory(assetPath);
                ScreenshotUtility.WriteCaptureBytes(fullPath, bytes, ensureUniqueFileName);
                folders.Complete();
            }
        }
    }
}
