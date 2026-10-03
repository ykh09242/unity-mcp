using System;
using System.IO;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Editor.Security;
using UnityEditor;
using UnityEngine;

namespace MCPForUnity.Editor.Services.AssetGen.Import
{
    /// <summary>
    /// Imports a generated 2D image (PNG, already under Assets/) and applies TextureImporter
    /// settings: Sprite vs Default, alpha-is-transparency, and sRGB (color) vs linear (data maps).
    /// </summary>
    public static class ImageImportPipeline
    {
        public static AssetGenJob ImportInto(AssetGenJob job, string localFilePath, bool asSprite, bool transparent, bool isColor)
        {
            if (job == null) return null;
            try
            {
                if (string.IsNullOrEmpty(localFilePath))
                    return Fail(job, "No file to import.");

                if (!AssetGenPaths.TryGetAssetsRelativePath(localFilePath, out string rel))
                    return Fail(job, "Generated file is not under the Assets folder.");

                // Defense-in-depth: never import a non-image file even if one slipped past WriteFile.
                if (!AssetGenJobManager.IsAllowedResultExtension("image", Path.GetExtension(rel)))
                    return Fail(job, "Refusing to import a non-image file type.");

                AssetDatabase.ImportAsset(rel, ImportAssetOptions.ForceUpdate);

                var importer = AssetImporter.GetAtPath(rel) as TextureImporter;
                if (importer == null)
                    return Fail(job, "Unity did not create a TextureImporter for the generated image.");

                importer.textureType = asSprite ? TextureImporterType.Sprite : TextureImporterType.Default;
                importer.alphaIsTransparency = transparent;
                importer.sRGBTexture = isColor; // color maps sRGB; normal/roughness/metallic would be linear
                if (asSprite)
                {
                    importer.spriteImportMode = SpriteImportMode.Single;
                    importer.mipmapEnabled = false;
                }
                importer.SaveAndReimport();

                if (AssetDatabase.LoadAssetAtPath<Texture2D>(rel) == null)
                    return Fail(job, "Unity did not produce a usable Texture2D from the generated image after reimport.");
                if (asSprite && AssetDatabase.LoadAssetAtPath<Sprite>(rel) == null)
                    return Fail(job, "Unity did not produce the requested Sprite from the generated image after reimport.");

                job.AssetPath = rel;
                job.AssetGuid = AssetDatabase.AssetPathToGUID(rel);
                if (string.IsNullOrEmpty(job.AssetGuid))
                    return Fail(job, "Imported the image but Unity did not register it as an asset.");

                if (job.State != AssetGenJobState.Failed)
                    job.State = AssetGenJobState.Done;
                return job;
            }
            catch (Exception e)
            {
                return Fail(job, SecretRedactor.Scrub(e.Message));
            }
        }

        private static AssetGenJob Fail(AssetGenJob job, string message)
        {
            job.State = AssetGenJobState.Failed;
            job.Error = message;
            return job;
        }
    }
}
