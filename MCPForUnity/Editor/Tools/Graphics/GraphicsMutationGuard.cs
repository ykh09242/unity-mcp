using System;
using System.IO;
using MCPForUnity.Editor.Helpers;
using UnityEditor;
using UnityEngine;

namespace MCPForUnity.Editor.Tools.Graphics
{
    // References may come from packages, GUIDs or native object IDs. Validate the actual
    // persistent owner, including subassets, before any property or structural mutation.
    internal static class GraphicsMutationGuard
    {
        internal static ErrorResponse Validate(object target, bool requirePersistent = false)
        {
            const string message =
                "Graphics mutations require an editable asset under Assets with no linked paths. Create a copy under Assets and assign that copy before editing.";
            if (!(target is UnityEngine.Object owner) || owner == null)
                return new ErrorResponse(message);

            if (requirePersistent && !EditorUtility.IsPersistent(owner))
                return new ErrorResponse(message);

            string path = AssetDatabase.GetAssetPath(owner);
            if (string.IsNullOrEmpty(path))
                return !requirePersistent && !EditorUtility.IsPersistent(owner) && !AssetDatabase.Contains(owner) ? null : new ErrorResponse(message);

            // Never reinterpret a persistent built-in/package path as a relative Assets path.
            if (!path.StartsWith("Assets/", StringComparison.Ordinal))
                return new ErrorResponse(message);
            try
            {
                AssetPathUtility.GetFullAssetPath(path);
                AssetPathUtility.GetFullAssetPath(path + ".meta");
                return null;
            }
            catch (Exception error)
                when (error is ArgumentException || error is InvalidOperationException || error is IOException || error is UnauthorizedAccessException)
            {
                return new ErrorResponse(message);
            }
        }
    }
}
