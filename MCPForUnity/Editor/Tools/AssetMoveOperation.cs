using System;
using MCPForUnity.Editor.Helpers;
using UnityEditor;

namespace MCPForUnity.Editor.Tools
{
    internal static class AssetMoveOperation
    {
        internal static object Apply(AssetMoveRequest request)
        {
            if (string.IsNullOrEmpty(request.Path))
                return new ErrorResponse("'path' is required for move/rename.");
            if (string.IsNullOrEmpty(request.Destination))
                return new ErrorResponse("'destination' path is required for move/rename.");

            string sourcePath = AssetPathUtility.GetContainedAssetPath(request.Path);
            string destPath = AssetPathUtility.GetContainedAssetPath(request.Destination);

            if (!ManageAsset.AssetExists(sourcePath))
                return new ErrorResponse($"Source asset not found at path: {sourcePath}");
            if (ManageAsset.AssetExists(destPath))
                return new ErrorResponse($"An asset already exists at the destination path: {destPath}");

            try
            {
                var consentError = ManageAsset.RequireScriptConsent(sourcePath, destPath);
                if (consentError != null)
                    return consentError;
                using var folders = new AssetFolderScope();
                folders.EnsureParentDirectory(destPath);
                // Validate will return an error string if failed, null if successful
                AssetPathUtility.GetFullAssetPath(sourcePath);
                AssetPathUtility.GetFullAssetPath(destPath);
                string error = AssetDatabase.ValidateMoveAsset(sourcePath, destPath);
                if (!string.IsNullOrEmpty(error))
                {
                    return new ErrorResponse($"Failed to move/rename asset from '{sourcePath}' to '{destPath}': {error}");
                }

                AssetPathUtility.GetFullAssetPath(sourcePath);
                AssetPathUtility.GetFullAssetPath(destPath);
                AssetSearchSnapshots.Invalidate();
                string moveError = AssetDatabase.MoveAsset(sourcePath, destPath);
                if (string.IsNullOrEmpty(moveError))
                {
                    var response = ManageAsset.SavedAssetResponse(destPath, $"Asset moved/renamed from '{sourcePath}' to '{destPath}'.");
                    folders.Complete();
                    return response;
                }
                else
                {
                    return new ErrorResponse($"Failed to move/rename asset from '{sourcePath}' to '{destPath}': {moveError}");
                }
            }
            catch (Exception e)
            {
                return new ErrorResponse($"Error moving/renaming asset '{sourcePath}': {e.Message}");
            }
        }
    }
}
