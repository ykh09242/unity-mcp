using System;
using System.Collections.Generic;
using System.IO;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEngine;
using MCPForUnity.Editor.Helpers;

namespace MCPForUnity.Editor.Tools
{
    /// <summary>
    /// Handles procedural texture generation operations.
    /// Supports patterns (checkerboard, stripes, dots, grid, brick),
    /// gradients, noise, and direct pixel manipulation.
    /// </summary>
    [McpForUnityTool("manage_texture", AutoRegister = false, Group = "vfx")]
    public static class ManageTexture
    {
        // Allow 4K assets and typical 2K eight-octave / 4K two-octave noise.
        private const int MaxTextureDimension = 4096;
        private const int MaxTexturePixels = 4096 * 4096;
        private const int MaxNoiseWork = 32 * 1024 * 1024;
        // An incompressible 4K RGBA PNG needs about 64 MiB before format overhead.
        private const int MaxEncodedImageBytes = 96 * 1024 * 1024;
        private static readonly List<string> ValidActions = new List<string>
        {
            "create",
            "modify",
            "delete",
            "create_sprite",
            "apply_pattern",
            "apply_gradient",
            "apply_noise",
            "set_import_settings"
        };

        private static ErrorResponse ValidateDimensions(int width, int height, List<string> warnings)
        {
            if (width <= 0 || height <= 0)
                return new ErrorResponse($"Invalid dimensions: {width}x{height}. Must be positive.");
            if (width > MaxTextureDimension || height > MaxTextureDimension)
                return new ErrorResponse($"Dimensions exceed max {MaxTextureDimension} per side (got {width}x{height}).");
            long totalPixels = (long)width * height;
            if (totalPixels > MaxTexturePixels)
                return new ErrorResponse($"Total pixels exceed max {MaxTexturePixels} (got {width}x{height}).");
            return null;
        }

        // Bound the actual read, including a file that grows after its length is checked.
        private static byte[] ReadBoundedImage(string path)
        {
            using var stream = new FileStream(path, FileMode.Open, FileAccess.Read, FileShare.Read);
            long length = stream.Length;
            if (length <= 0 || length > MaxEncodedImageBytes)
                throw new ArgumentException($"Image must contain between 1 and {MaxEncodedImageBytes} encoded bytes.");
            byte[] bytes = new byte[(int)length];
            int offset = 0;
            while (offset < bytes.Length)
            {
                int read = stream.Read(bytes, offset, bytes.Length - offset);
                if (read == 0) throw new ArgumentException("Image changed or was truncated while reading.");
                offset += read;
            }
            if (stream.ReadByte() != -1)
                throw new ArgumentException("Image grew while reading.");
            return bytes;
        }

        private static uint ReadBigEndian(byte[] bytes, int offset)
        {
            return ((uint)bytes[offset] << 24) | ((uint)bytes[offset + 1] << 16)
                | ((uint)bytes[offset + 2] << 8) | bytes[offset + 3];
        }

        private static uint PngHeaderCrc(byte[] bytes, int offset)
        {
            uint crc = 0xffffffff;
            for (int i = offset; i < offset + 17; i++)
            {
                crc ^= bytes[i];
                for (int bit = 0; bit < 8; bit++)
                    crc = (crc >> 1) ^ ((crc & 1) != 0 ? 0xedb88320u : 0u);
            }
            return crc ^ 0xffffffff;
        }

        // Only documented PNG/JPEG inputs are decoded. Header lengths never drive allocations.
        private static void ReadImageDimensions(byte[] bytes, out int width, out int height)
        {
            width = height = 0;
            bool png = bytes.Length >= 8 && bytes[0] == 137 && bytes[1] == 80
                && bytes[2] == 78 && bytes[3] == 71 && bytes[4] == 13 && bytes[5] == 10
                && bytes[6] == 26 && bytes[7] == 10;
            if (png)
            {
                bool header = false, data = false, end = false;
                int offset = 8;
                while (offset <= bytes.Length - 12)
                {
                    uint length = ReadBigEndian(bytes, offset);
                    if (length > (uint)(bytes.Length - offset - 12))
                        throw new ArgumentException("Truncated PNG chunk.");
                    uint type = ReadBigEndian(bytes, offset + 4);
                    if (!header && type != 0x49484452)
                        throw new ArgumentException("PNG must start with IHDR.");
                    if (type == 0x49484452)
                    {
                        if (header || length != 13) throw new ArgumentException("Invalid or repeated PNG IHDR.");
                        if (PngHeaderCrc(bytes, offset + 4) != ReadBigEndian(bytes, offset + 21))
                            throw new ArgumentException("Invalid PNG IHDR checksum.");
                        uint w = ReadBigEndian(bytes, offset + 8), h = ReadBigEndian(bytes, offset + 12);
                        if (w == 0 || h == 0 || w > MaxTextureDimension || h > MaxTextureDimension)
                            throw new ArgumentException($"Image dimensions exceed max {MaxTextureDimension} per side or are invalid.");
                        width = (int)w; height = (int)h;
                        int depth = bytes[offset + 16], color = bytes[offset + 17];
                        bool validDepth = color == 0 ? (depth == 1 || depth == 2 || depth == 4 || depth == 8 || depth == 16)
                            : color == 3 ? (depth == 1 || depth == 2 || depth == 4 || depth == 8)
                            : (color == 2 || color == 4 || color == 6) && (depth == 8 || depth == 16);
                        if (!validDepth || bytes[offset + 18] != 0 || bytes[offset + 19] != 0 || bytes[offset + 20] > 1)
                            throw new ArgumentException("Invalid PNG image header.");
                        header = true;
                    }
                    else if (type == 0x49444154) data = true;
                    else if (type == 0x6163544c) throw new ArgumentException("Animated PNG inputs are not supported.");
                    else if (type == 0x49454e44)
                    {
                        if (length != 0 || !data || offset + 12 != bytes.Length)
                            throw new ArgumentException("Invalid PNG end or missing image data.");
                        end = true;
                        break;
                    }
                    offset += (int)length + 12;
                }
                if (!header || !end) throw new ArgumentException("Truncated PNG image.");
                return;
            }
            if (bytes.Length < 4 || bytes[0] != 255 || bytes[1] != 216)
                throw new ArgumentException("Image input must be PNG or JPEG.");
            bool frame = false, scan = false, inScan = false;
            int scanCount = 0;
            int position = 2;
            while (position < bytes.Length)
            {
                if (bytes[position++] != 255)
                {
                    if (inScan) continue;
                    throw new ArgumentException("Invalid JPEG marker.");
                }
                while (position < bytes.Length && bytes[position] == 255) position++;
                if (position >= bytes.Length) break;
                int marker = bytes[position++];
                if (inScan && (marker == 0 || (marker >= 208 && marker <= 215))) continue;
                inScan = false;
                if (marker == 217)
                {
                    if (!frame || !scan || position != bytes.Length) throw new ArgumentException("Invalid JPEG end.");
                    return;
                }
                if (marker == 0 || marker == 216 || marker == 1 || (marker >= 208 && marker <= 215)
                    || position > bytes.Length - 2) throw new ArgumentException("Invalid JPEG segment.");
                if (marker == 220 || marker == 222 || marker == 223)
                    throw new ArgumentException("JPEG dimension redefinition and hierarchical frames are not supported.");
                int length = (bytes[position] << 8) | bytes[position + 1];
                if (length < 2 || length > bytes.Length - position) throw new ArgumentException("Truncated JPEG segment.");
                bool sizeMarker = marker >= 192 && marker <= 207 && marker != 196 && marker != 200 && marker != 204;
                if (sizeMarker)
                {
                    if (frame || scan || (marker != 192 && marker != 193 && marker != 194) || length < 8)
                        throw new ArgumentException("Invalid or unsupported JPEG frame.");
                    int components = bytes[position + 7];
                    if (bytes[position + 2] != 8 || components < 1 || components > 4 || length != 8 + 3 * components)
                        throw new ArgumentException("Invalid JPEG frame header.");
                    for (int component = 0; component < components; component++)
                    {
                        int entry = position + 8 + 3 * component;
                        int sampling = bytes[entry + 1];
                        if ((sampling >> 4) < 1 || (sampling >> 4) > 4 || (sampling & 15) < 1
                            || (sampling & 15) > 4 || bytes[entry + 2] > 3)
                            throw new ArgumentException("Invalid JPEG sampling or quantization table.");
                        for (int previous = 0; previous < component; previous++)
                            if (bytes[entry] == bytes[position + 8 + 3 * previous])
                                throw new ArgumentException("Repeated JPEG component identifier.");
                    }
                    height = (bytes[position + 3] << 8) | bytes[position + 4];
                    width = (bytes[position + 5] << 8) | bytes[position + 6];
                    var error = ValidateDimensions(width, height, null);
                    if (error != null) throw new ArgumentException("JPEG dimensions exceed texture limits or are invalid.");
                    frame = true;
                }
                if (marker == 218)
                {
                    if (++scanCount > 64) throw new ArgumentException("JPEG contains too many scans.");
                    if (!frame || length < 6 || length != 6 + 2 * bytes[position + 2]
                        || bytes[position + 2] < 1 || bytes[position + 2] > 4)
                        throw new ArgumentException("Invalid JPEG scan header.");
                    scan = inScan = true;
                }
                position += length;
            }
            throw new ArgumentException("Truncated JPEG image.");
        }

        private static ErrorResponse ValidatePixelPayload(JToken pixels, int width, int height)
        {
            if (pixels?.Type != JTokenType.String) return null;
            string encoded = pixels.ToString();
            int prefix = encoded.StartsWith("base64:", StringComparison.Ordinal) ? 7 : 0;
            long maxCharacters = (((long)width * height * 4 + 2) / 3) * 4;
            if (encoded.Length - prefix > maxCharacters)
                return new ErrorResponse("Encoded pixel data exceeds the region's RGBA byte budget.");
            return null;
        }


        public static object HandleCommand(JObject @params)
        {
            string action = @params["action"]?.ToString()?.ToLowerInvariant();
            if (string.IsNullOrEmpty(action))
            {
                return new ErrorResponse("Action parameter is required.");
            }

            if (!ValidActions.Contains(action))
            {
                string validActionsList = string.Join(", ", ValidActions);
                return new ErrorResponse(
                    $"Unknown action: '{action}'. Valid actions are: {validActionsList}"
                );
            }

            string path = @params["path"]?.ToString();

            try
            {
                switch (action)
                {
                    case "create":
                    case "create_sprite":
                        return CreateTexture(@params, action == "create_sprite");
                    case "modify":
                        return ModifyTexture(@params);
                    case "delete":
                        return DeleteTexture(path);
                    case "apply_pattern":
                        return ApplyPattern(@params);
                    case "apply_gradient":
                        return ApplyGradient(@params);
                    case "apply_noise":
                        return ApplyNoise(@params);
                    case "set_import_settings":
                        return SetImportSettings(@params);
                    default:
                        return new ErrorResponse($"Unknown action: '{action}'");
                }
            }
            catch (Exception e)
            {
                McpLog.Error($"[ManageTexture] Action '{action}' failed: {e}");
                return new ErrorResponse($"Internal error processing action '{action}': {e.Message}");
            }
        }

        // --- Action Implementations ---

        private static object CreateTexture(JObject @params, bool asSprite)
        {
            string path = @params["path"]?.ToString();
            if (string.IsNullOrEmpty(path))
                return new ErrorResponse("'path' is required for create.");

            string imagePath = @params["imagePath"]?.ToString();
            bool hasImage = !string.IsNullOrEmpty(imagePath);

            int width = @params["width"]?.ReadScalar<int?>() ?? 64;
            int height = @params["height"]?.ReadScalar<int?>() ?? 64;
            List<string> warnings = new List<string>();

            // Validate dimensions
            if (!hasImage)
            {
                var dimensionError = ValidateDimensions(width, height, warnings);
                if (dimensionError != null)
                    return dimensionError;
            }

            string fullPath = AssetPathUtility.GetContainedAssetPath(path);

            Texture2D texture = null;
            try
            {
                var importSettingsToken = @params["importSettings"];
                var spriteSettingsToken = @params["spriteSettings"];
                Action<TextureImporter> applySettings = importSettingsToken != null
                    ? PrepareTextureImporterSettings(importSettingsToken)
                    : (asSprite || spriteSettingsToken != null ? PrepareSpriteSettings(spriteSettingsToken) : null);
                var fillColorToken = @params["fillColor"];
                var patternToken = @params["pattern"];
                var pixelsToken = @params["pixels"];
                if (!hasImage)
                {
                    var pixelError = ValidatePixelPayload(pixelsToken, width, height);
                    if (pixelError != null) return pixelError;
                }

                if (hasImage && (fillColorToken != null || patternToken != null || pixelsToken != null))
                {
                    return new ErrorResponse("imagePath cannot be combined with fillColor, pattern, or pixels.");
                }

                int patternSize = 8;
                if (!hasImage && patternToken != null)
                {
                    patternSize = @params["patternSize"]?.ReadScalar<int?>() ?? 8;
                    if (patternSize <= 0)
                        return new ErrorResponse("patternSize must be greater than 0.");
                }

                if (hasImage)
                {
                    string resolvedImagePath = ResolveImagePath(imagePath);
                    if (!File.Exists(resolvedImagePath))
                        return new ErrorResponse($"Image file not found at '{imagePath}'.");

                    byte[] imageBytes = ReadBoundedImage(resolvedImagePath);
                    ReadImageDimensions(imageBytes, out int imageWidth, out int imageHeight);
                    texture = new Texture2D(2, 2, TextureFormat.RGBA32, false);
                    if (!texture.LoadImage(imageBytes))
                    {
                        return new ErrorResponse($"Failed to load image from '{imagePath}'.");
                    }

                    width = texture.width;
                    height = texture.height;
                    var imageDimensionError = ValidateDimensions(width, height, warnings);
                    if (imageDimensionError != null)
                    {
                        return imageDimensionError;
                    }
                    if (width != imageWidth || height != imageHeight)
                        return new ErrorResponse("Decoded image dimensions do not match its header.");
                }
                else
                {
                    texture = new Texture2D(width, height, TextureFormat.RGBA32, false);

                    // Check for fill color
                    if (fillColorToken != null && fillColorToken.Type == JTokenType.Array)
                    {
                        Color32 fillColor = TextureOps.ParseColor32(fillColorToken as JArray);
                        TextureOps.FillTexture(texture, fillColor);
                    }

                    // Check for pattern
                    if (patternToken != null)
                    {
                        string pattern = patternToken.ToString();
                        var palette = TextureOps.ParsePalette(@params["palette"] as JArray);
                        ApplyPatternToTexture(texture, pattern, palette, patternSize);
                    }

                    // Check for direct pixel data
                    if (pixelsToken != null && pixelsToken.Type != JTokenType.Null)
                    {
                        TextureOps.ApplyPixelData(texture, pixelsToken, width, height);
                    }

                    // If nothing specified, create transparent texture
                    if (fillColorToken == null && patternToken == null && pixelsToken == null)
                    {
                        TextureOps.FillTexture(texture, new Color32(0, 0, 0, 0));
                    }
                }

                texture.Apply();

                // Save to disk
                byte[] imageData = TextureOps.EncodeTexture(texture, fullPath);
                if (imageData == null || imageData.Length == 0)
                {
                    return new ErrorResponse($"Failed to encode texture for '{fullPath}'");
                }
                EnsureDirectoryExists(fullPath);
                File.WriteAllBytes(GetAbsolutePath(fullPath), imageData);

                AssetDatabase.ImportAsset(fullPath, ImportAssetOptions.ForceUpdate);

                ApplyPreparedImportSettings(fullPath, applySettings);

                foreach (var warning in warnings)
                {
                    McpLog.Warn($"[ManageTexture] {warning}");
                }

                return new SuccessResponse(
                    $"Texture created at '{fullPath}' ({width}x{height})" + (asSprite ? " as sprite" : ""),
                    new
                    {
                        path = fullPath,
                        width,
                        height,
                        asSprite = asSprite || spriteSettingsToken != null || (importSettingsToken?["textureType"]?.ToString() == "Sprite"),
                        warnings = warnings.Count > 0 ? warnings : null
                    }
                );
            }
            catch (Exception e)
            {
                return new ErrorResponse($"Failed to create texture: {e.Message}");
            }
            finally
            {
                if (texture != null)
                    UnityEngine.Object.DestroyImmediate(texture);
            }
        }

        private static object ModifyTexture(JObject @params)
        {
            string path = @params["path"]?.ToString();
            if (string.IsNullOrEmpty(path))
                return new ErrorResponse("'path' is required for modify.");

            string fullPath = AssetPathUtility.GetContainedAssetPath(path);
            if (!AssetExists(fullPath))
                return new ErrorResponse($"Texture not found at path: {fullPath}");

            Texture2D editableTexture = null;
            try
            {
                var rawSetPixels = @params["setPixels"];
                if (rawSetPixels != null && rawSetPixels.Type != JTokenType.Null && rawSetPixels is not JObject)
                    return new ErrorResponse("setPixels must be an object.");
                var setPixelsToken = rawSetPixels as JObject;
                bool hasImportSettings = HasImportSettingsParams(@params);

                // Validate import settings before any writes
                if (hasImportSettings)
                {
                    var validationError = ValidateImportSettingsParams(@params);
                    if (validationError != null) return validationError;
                }

                Action<TextureImporter> applySettings = null;
                if (hasImportSettings)
                {
                    var preparationError = PrepareImportSettingsParams(@params, out applySettings);
                    if (preparationError != null) return preparationError;
                }

                // Fast path: only import settings, no pixel changes
                if (setPixelsToken == null && hasImportSettings)
                {
                    ApplyPreparedImportSettings(fullPath, applySettings);
                    return new SuccessResponse($"Texture modified: {fullPath}");
                }

                // Pixel modification path
                if (setPixelsToken != null)
                {
                    int x = setPixelsToken["x"]?.ReadScalar<int?>() ?? 0;
                    int y = setPixelsToken["y"]?.ReadScalar<int?>() ?? 0;
                    int w = setPixelsToken["width"]?.ReadScalar<int?>() ?? 1;
                    int h = setPixelsToken["height"]?.ReadScalar<int?>() ?? 1;
                    var regionError = ValidateDimensions(w, h, null);
                    if (regionError != null) return regionError;
                    var pixelsToken = setPixelsToken["pixels"];
                    var colorToken = setPixelsToken["color"];
                    var pixelError = ValidatePixelPayload(pixelsToken, w, h);
                    if (pixelError != null) return pixelError;
                    // Inspect the file before asking Unity to load a possibly uncached texture.
                    string absolutePath = GetAbsolutePath(fullPath);
                    byte[] fileData = ReadBoundedImage(absolutePath);
                    ReadImageDimensions(fileData, out int imageWidth, out int imageHeight);
                    Texture2D texture = AssetDatabase.LoadAssetAtPath<Texture2D>(fullPath);
                    if (texture == null)
                        return new ErrorResponse($"Failed to load texture at path: {fullPath}");
                    var existingDimensionError = ValidateDimensions(texture.width, texture.height, null);
                    if (existingDimensionError != null) return existingDimensionError;

                    editableTexture = new Texture2D(2, 2, TextureFormat.RGBA32, false);
                    if (!editableTexture.LoadImage(fileData))
                        return new ErrorResponse($"Failed to decode texture at path: {fullPath}");
                    var decodedDimensionError = ValidateDimensions(editableTexture.width, editableTexture.height, null);
                    if (decodedDimensionError != null) return decodedDimensionError;
                    if (editableTexture.width != imageWidth || editableTexture.height != imageHeight)
                        return new ErrorResponse("Decoded image dimensions do not match its header.");

                    if (pixelsToken != null)
                    {
                        TextureOps.ApplyPixelDataToRegion(editableTexture, pixelsToken, x, y, w, h);
                    }
                    else if (colorToken != null)
                    {
                        Color32 color = TextureOps.ParseRequiredColor32(colorToken as JArray);
                        int startX = Mathf.Max(0, x);
                        int startY = Mathf.Max(0, y);
                        int endX = (int)Math.Min((long)x + w, editableTexture.width);
                        int endY = (int)Math.Min((long)y + h, editableTexture.height);

                        for (int py = startY; py < endY; py++)
                        {
                            for (int px = startX; px < endX; px++)
                            {
                                editableTexture.SetPixel(px, py, color);
                            }
                        }
                    }
                    else
                    {
                        return new ErrorResponse("setPixels requires 'color' or 'pixels'.");
                    }

                    editableTexture.Apply();

                    byte[] imageData = TextureOps.EncodeTexture(editableTexture, fullPath);
                    if (imageData == null || imageData.Length == 0)
                    {
                        return new ErrorResponse($"Failed to encode texture for '{fullPath}'");
                    }
                    File.WriteAllBytes(absolutePath, imageData);
                    AssetDatabase.ImportAsset(fullPath, ImportAssetOptions.ForceUpdate);
                }

                if (hasImportSettings)
                {
                    ApplyPreparedImportSettings(fullPath, applySettings);
                }

                return new SuccessResponse($"Texture modified: {fullPath}");
            }
            catch (Exception e)
            {
                return new ErrorResponse($"Failed to modify texture: {e.Message}");
            }
            finally
            {
                if (editableTexture != null)
                    UnityEngine.Object.DestroyImmediate(editableTexture);
            }
        }

        private static object DeleteTexture(string path)
        {
            if (string.IsNullOrEmpty(path))
                return new ErrorResponse("'path' is required for delete.");

            string fullPath = AssetPathUtility.GetContainedAssetPath(path);
            if (!AssetExists(fullPath))
                return new ErrorResponse($"Texture not found at path: {fullPath}");

            try
            {
                bool success = AssetDatabase.DeleteAsset(fullPath);
                if (success)
                    return new SuccessResponse($"Texture deleted: {fullPath}");
                else
                    return new ErrorResponse($"Failed to delete texture: {fullPath}");
            }
            catch (Exception e)
            {
                return new ErrorResponse($"Error deleting texture: {e.Message}");
            }
        }

        private static object ApplyPattern(JObject @params)
        {
            // Reuse CreateTexture with pattern
            return CreateTexture(@params, false);
        }

        private static object ApplyGradient(JObject @params)
        {
            string path = @params["path"]?.ToString();
            if (string.IsNullOrEmpty(path))
                return new ErrorResponse("'path' is required for apply_gradient.");

            int width = @params["width"]?.ReadScalar<int?>() ?? 64;
            int height = @params["height"]?.ReadScalar<int?>() ?? 64;
            List<string> warnings = new List<string>();
            var dimensionError = ValidateDimensions(width, height, warnings);
            if (dimensionError != null)
                return dimensionError;
            string gradientType = @params["gradientType"]?.ToString() ?? "linear";
            float angle = @params["gradientAngle"]?.ReadScalar<float?>() ?? 0f;

            var palette = TextureOps.ParsePalette(@params["palette"] as JArray);
            if (palette == null || palette.Count < 2)
            {
                // Default gradient palette
                palette = new List<Color32> { new Color32(0, 0, 0, 255), new Color32(255, 255, 255, 255) };
            }

            string fullPath = AssetPathUtility.GetContainedAssetPath(path);
            Texture2D texture = null;
            try
            {
                var spriteSettingsToken = @params["spriteSettings"];
                var applySettings = spriteSettingsToken != null ? PrepareSpriteSettings(spriteSettingsToken) : null;
                texture = new Texture2D(width, height, TextureFormat.RGBA32, false);

                if (gradientType == "radial")
                {
                    ApplyRadialGradient(texture, palette);
                }
                else
                {
                    ApplyLinearGradient(texture, palette, angle);
                }

                texture.Apply();

                byte[] imageData = TextureOps.EncodeTexture(texture, fullPath);
                if (imageData == null || imageData.Length == 0)
                {
                    return new ErrorResponse($"Failed to encode texture for '{fullPath}'");
                }
                EnsureDirectoryExists(fullPath);
                File.WriteAllBytes(GetAbsolutePath(fullPath), imageData);

                AssetDatabase.ImportAsset(fullPath, ImportAssetOptions.ForceUpdate);

                // Configure as sprite if requested
                ApplyPreparedImportSettings(fullPath, applySettings);

                foreach (var warning in warnings)
                {
                    McpLog.Warn($"[ManageTexture] {warning}");
                }

                return new SuccessResponse(
                    $"Gradient texture created at '{fullPath}' ({width}x{height})",
                    new
                    {
                        path = fullPath,
                        width,
                        height,
                        gradientType,
                        warnings = warnings.Count > 0 ? warnings : null
                    }
                );
            }
            catch (Exception e)
            {
                return new ErrorResponse($"Failed to create gradient texture: {e.Message}");
            }
            finally
            {
                if (texture != null)
                    UnityEngine.Object.DestroyImmediate(texture);
            }
        }

        private static object ApplyNoise(JObject @params)
        {
            string path = @params["path"]?.ToString();
            if (string.IsNullOrEmpty(path))
                return new ErrorResponse("'path' is required for apply_noise.");

            int width = @params["width"]?.ReadScalar<int?>() ?? 64;
            int height = @params["height"]?.ReadScalar<int?>() ?? 64;
            List<string> warnings = new List<string>();
            var dimensionError = ValidateDimensions(width, height, warnings);
            if (dimensionError != null)
                return dimensionError;
            float scale = @params["noiseScale"]?.ReadScalar<float?>() ?? 0.1f;
            int octaves = @params["octaves"]?.ReadScalar<int?>() ?? 1;
            if (octaves <= 0)
                return new ErrorResponse("octaves must be greater than 0.");
            long totalPixels = (long)width * height;
            if (octaves > MaxNoiseWork / totalPixels)
                return new ErrorResponse($"Noise workload exceeds max {MaxNoiseWork} (got {width}x{height}x{octaves}).");

            var palette = TextureOps.ParsePalette(@params["palette"] as JArray);
            if (palette == null || palette.Count < 2)
            {
                palette = new List<Color32> { new Color32(0, 0, 0, 255), new Color32(255, 255, 255, 255) };
            }

            string fullPath = AssetPathUtility.GetContainedAssetPath(path);
            Texture2D texture = null;
            try
            {
                var spriteSettingsToken = @params["spriteSettings"];
                var applySettings = spriteSettingsToken != null ? PrepareSpriteSettings(spriteSettingsToken) : null;
                texture = new Texture2D(width, height, TextureFormat.RGBA32, false);
                ApplyPerlinNoise(texture, palette, scale, octaves);

                texture.Apply();

                byte[] imageData = TextureOps.EncodeTexture(texture, fullPath);
                if (imageData == null || imageData.Length == 0)
                {
                    return new ErrorResponse($"Failed to encode texture for '{fullPath}'");
                }
                EnsureDirectoryExists(fullPath);
                File.WriteAllBytes(GetAbsolutePath(fullPath), imageData);

                AssetDatabase.ImportAsset(fullPath, ImportAssetOptions.ForceUpdate);

                // Configure as sprite if requested
                ApplyPreparedImportSettings(fullPath, applySettings);

                foreach (var warning in warnings)
                {
                    McpLog.Warn($"[ManageTexture] {warning}");
                }

                return new SuccessResponse(
                    $"Noise texture created at '{fullPath}' ({width}x{height})",
                    new
                    {
                        path = fullPath,
                        width,
                        height,
                        noiseScale = scale,
                        octaves,
                        warnings = warnings.Count > 0 ? warnings : null
                    }
                );
            }
            catch (Exception e)
            {
                return new ErrorResponse($"Failed to create noise texture: {e.Message}");
            }
            finally
            {
                if (texture != null)
                    UnityEngine.Object.DestroyImmediate(texture);
            }
        }

        // --- Pattern Helpers ---

        private static void ApplyPatternToTexture(Texture2D texture, string pattern, List<Color32> palette, int patternSize)
        {
            if (palette == null || palette.Count == 0)
            {
                palette = new List<Color32> { new Color32(255, 255, 255, 255), new Color32(0, 0, 0, 255) };
            }

            int width = texture.width;
            int height = texture.height;

            for (int y = 0; y < height; y++)
            {
                for (int x = 0; x < width; x++)
                {
                    Color32 color = GetPatternColor(x, y, pattern, palette, patternSize, width, height);
                    texture.SetPixel(x, y, color);
                }
            }
        }

        private static Color32 GetPatternColor(int x, int y, string pattern, List<Color32> palette, int size, int width, int height)
        {
            int colorIndex = 0;

            switch (pattern.ToLower())
            {
                case "checkerboard":
                    colorIndex = ((x / size) + (y / size)) % 2;
                    break;

                case "stripes":
                case "stripes_v":
                    colorIndex = (x / size) % palette.Count;
                    break;

                case "stripes_h":
                    colorIndex = (y / size) % palette.Count;
                    break;

                case "stripes_diag":
                    colorIndex = ((x + y) / size) % palette.Count;
                    break;

                case "dots":
                    long cx = (x % ((long)size * 2)) - size;
                    long cy = (y % ((long)size * 2)) - size;
                    bool inDot = (cx * cx + cy * cy) < ((long)size * size / 4);
                    colorIndex = inDot ? 1 : 0;
                    break;

                case "grid":
                    bool onGridLine = (x % size == 0) || (y % size == 0);
                    colorIndex = onGridLine ? 1 : 0;
                    break;

                case "brick":
                    int row = y / size;
                    int offset = (row % 2) * (size / 2);
                    bool onBorder = ((x + offset) % size == 0) || (y % size == 0);
                    colorIndex = onBorder ? 1 : 0;
                    break;

                default:
                    colorIndex = 0;
                    break;
            }

            return palette[Mathf.Clamp(colorIndex, 0, palette.Count - 1)];
        }

        // --- Gradient Helpers ---

        private static void ApplyLinearGradient(Texture2D texture, List<Color32> palette, float angle)
        {
            int width = texture.width;
            int height = texture.height;
            float radians = angle * Mathf.Deg2Rad;
            Vector2 dir = new Vector2(Mathf.Cos(radians), Mathf.Sin(radians));
            float denomX = Mathf.Max(1, width - 1);
            float denomY = Mathf.Max(1, height - 1);

            for (int y = 0; y < height; y++)
            {
                for (int x = 0; x < width; x++)
                {
                    float nx = x / denomX;
                    float ny = y / denomY;
                    float t = Vector2.Dot(new Vector2(nx, ny), dir);
                    t = Mathf.Clamp01((t + 1f) / 2f);

                    Color32 color = LerpPalette(palette, t);
                    texture.SetPixel(x, y, color);
                }
            }
        }

        private static void ApplyRadialGradient(Texture2D texture, List<Color32> palette)
        {
            int width = texture.width;
            int height = texture.height;
            float cx = width / 2f;
            float cy = height / 2f;
            float maxDist = Mathf.Sqrt(cx * cx + cy * cy);

            for (int y = 0; y < height; y++)
            {
                for (int x = 0; x < width; x++)
                {
                    float dx = x - cx;
                    float dy = y - cy;
                    float dist = Mathf.Sqrt(dx * dx + dy * dy);
                    float t = Mathf.Clamp01(dist / maxDist);

                    Color32 color = LerpPalette(palette, t);
                    texture.SetPixel(x, y, color);
                }
            }
        }

        private static Color32 LerpPalette(List<Color32> palette, float t)
        {
            if (palette.Count == 1) return palette[0];
            if (t <= 0) return palette[0];
            if (t >= 1) return palette[palette.Count - 1];

            float scaledT = t * (palette.Count - 1);
            int index = Mathf.FloorToInt(scaledT);
            float localT = scaledT - index;

            if (index >= palette.Count - 1)
                return palette[palette.Count - 1];

            Color c1 = palette[index];
            Color c2 = palette[index + 1];
            return Color.Lerp(c1, c2, localT);
        }

        // --- Noise Helpers ---

        private static void ApplyPerlinNoise(Texture2D texture, List<Color32> palette, float scale, int octaves)
        {
            int width = texture.width;
            int height = texture.height;

            // Random offset to ensure different patterns
            float offsetX = UnityEngine.Random.Range(0f, 1000f);
            float offsetY = UnityEngine.Random.Range(0f, 1000f);

            for (int y = 0; y < height; y++)
            {
                for (int x = 0; x < width; x++)
                {
                    float noiseValue = 0f;
                    float amplitude = 1f;
                    float frequency = 1f;
                    float maxValue = 0f;

                    for (int o = 0; o < octaves; o++)
                    {
                        float sampleX = (x + offsetX) * scale * frequency;
                        float sampleY = (y + offsetY) * scale * frequency;
                        noiseValue += Mathf.PerlinNoise(sampleX, sampleY) * amplitude;
                        maxValue += amplitude;
                        amplitude *= 0.5f;
                        frequency *= 2f;
                    }

                    float t = Mathf.Clamp01(noiseValue / maxValue);
                    Color32 color = LerpPalette(palette, t);
                    texture.SetPixel(x, y, color);
                }
            }
        }

        private static bool HasImportSettingsParams(JObject @params)
        {
            JToken importSettingsToken = @params["import_settings"] ?? @params["importSettings"];
            JToken asSpriteToken = @params["as_sprite"] ?? @params["spriteSettings"];

            bool hasImportSettings = importSettingsToken is JObject importObject && importObject.HasValues;
            bool hasSpriteSettings = (asSpriteToken is JObject spriteObject && spriteObject.HasValues)
                || (asSpriteToken?.Type == JTokenType.Boolean && asSpriteToken.ReadScalar<bool>());

            return hasImportSettings || hasSpriteSettings;
        }

        private static object ValidateImportSettingsParams(JObject @params)
        {
            JToken importSettingsToken = @params["import_settings"] ?? @params["importSettings"];
            JToken asSpriteToken = @params["as_sprite"] ?? @params["spriteSettings"];

            if (importSettingsToken != null && asSpriteToken != null)
            {
                return new ErrorResponse("Cannot specify both 'import_settings' and 'as_sprite'.");
            }
            return null;
        }

        private static object PrepareImportSettingsParams(JObject @params, out Action<TextureImporter> apply)
        {
            apply = null;
            JToken importSettingsToken = @params["import_settings"] ?? @params["importSettings"];
            JToken asSpriteToken = @params["as_sprite"] ?? @params["spriteSettings"];

            if (importSettingsToken != null && asSpriteToken != null)
            {
                return new ErrorResponse(
                    "Cannot specify both 'import_settings' and 'as_sprite'. " +
                    "Use 'import_settings' with textureType='Sprite' instead.");
            }

            if (importSettingsToken != null)
            {
                apply = PrepareTextureImporterSettings(importSettingsToken);
            }
            else if (asSpriteToken != null &&
                     (asSpriteToken.Type == JTokenType.Boolean ? asSpriteToken.ReadScalar<bool>() : true))
            {
                apply = PrepareSpriteSettings(asSpriteToken.Type == JTokenType.Object ? asSpriteToken : null);
            }

            return null;
        }

        private static object SetImportSettings(JObject @params)
        {
            var toolParams = new MCPForUnity.Editor.Helpers.ToolParams(@params);
            var pathResult = toolParams.GetRequired("path", "'path' is required for set_import_settings.");
            if (!pathResult.IsSuccess)
                return new ErrorResponse(pathResult.ErrorMessage);
            string path = pathResult.Value;

            string fullPath = AssetPathUtility.GetContainedAssetPath(path);
            if (!AssetExists(fullPath))
                return new ErrorResponse($"Texture not found at path: {fullPath}");

            try
            {
                if (!HasImportSettingsParams(@params))
                {
                    return new ErrorResponse("Either 'import_settings' or 'as_sprite' is required.");
                }

                var error = PrepareImportSettingsParams(@params, out var applySettings);
                if (error != null) return error;
                ApplyPreparedImportSettings(fullPath, applySettings);

                return new SuccessResponse($"Import settings updated for: {fullPath}", new { path = fullPath });
            }
            catch (Exception e)
            {
                return new ErrorResponse($"Failed to set import settings: {e.Message}");
            }
        }

        // Prepare conversions before any file/importer changes. Native setter/import failures
        // can still leave applied changes; this is not a rollback transaction.
        private static void ApplyPreparedImportSettings(string path, Action<TextureImporter> apply)
        {
            if (apply == null) return;
            var importer = AssetImporter.GetAtPath(path) as TextureImporter;
            if (importer == null)
                throw new InvalidOperationException($"Could not get TextureImporter for {path}");
            apply(importer);
        }

        private static Action<TextureImporter> PrepareSpriteSettings(JToken spriteSettings)
        {
            var setters = new List<Action<TextureImporter>>
            {
                importer => importer.textureType = TextureImporterType.Sprite,
                importer => importer.spriteImportMode = SpriteImportMode.Single
            };

            if (spriteSettings != null && spriteSettings.Type == JTokenType.Object)
            {
                var settings = spriteSettings as JObject;

                // Pivot
                var pivotToken = settings["pivot"];
                if (pivotToken is JArray pivotArray && pivotArray.Count >= 2)
                {
                    var pivot = new Vector2(
                        pivotArray[0].ReadScalar<float>(),
                        pivotArray[1].ReadScalar<float>()
                    );
                    setters.Add(importer => importer.spritePivot = pivot);
                }

                // Pixels per unit
                var ppuToken = settings["pixelsPerUnit"];
                if (ppuToken != null)
                {
                    float pixelsPerUnit = ppuToken.ReadScalar<float>();
                    setters.Add(importer => importer.spritePixelsPerUnit = pixelsPerUnit);
                }
            }

            return importer =>
            {
                foreach (var set in setters) set(importer);
                importer.SaveAndReimport();
            };
        }

        private static Action<TextureImporter> PrepareTextureImporterSettings(JToken importSettings)
        {
            if (importSettings == null || importSettings.Type != JTokenType.Object)
            {
                return importer => { };
            }

            var settings = importSettings as JObject;
            var setters = new List<Action<TextureImporter>>();

            // Texture Type
            var textureTypeToken = settings["textureType"];
            if (textureTypeToken != null)
            {
                string typeStr = textureTypeToken.ToString();
                if (TryParseEnum<TextureImporterType>(typeStr, out var textureType))
                {
                    setters.Add(importer => importer.textureType = textureType);
                }
            }

            // Texture Shape
            var textureShapeToken = settings["textureShape"];
            if (textureShapeToken != null)
            {
                string shapeStr = textureShapeToken.ToString();
                if (TryParseEnum<TextureImporterShape>(shapeStr, out var textureShape))
                {
                    setters.Add(importer => importer.textureShape = textureShape);
                }
            }

            // sRGB
            var srgbToken = settings["sRGBTexture"];
            if (srgbToken != null)
            {
                bool sRGBTexture = srgbToken.ReadScalar<bool>();
                setters.Add(importer => importer.sRGBTexture = sRGBTexture);
            }

            // Alpha Source
            var alphaSourceToken = settings["alphaSource"];
            if (alphaSourceToken != null)
            {
                string alphaStr = alphaSourceToken.ToString();
                if (TryParseEnum<TextureImporterAlphaSource>(alphaStr, out var alphaSource))
                {
                    setters.Add(importer => importer.alphaSource = alphaSource);
                }
            }

            // Alpha Is Transparency
            var alphaTransToken = settings["alphaIsTransparency"];
            if (alphaTransToken != null)
            {
                bool alphaIsTransparency = alphaTransToken.ReadScalar<bool>();
                setters.Add(importer => importer.alphaIsTransparency = alphaIsTransparency);
            }

            // Readable
            var readableToken = settings["isReadable"];
            if (readableToken != null)
            {
                bool isReadable = readableToken.ReadScalar<bool>();
                setters.Add(importer => importer.isReadable = isReadable);
            }

            // Mipmaps
            var mipmapToken = settings["mipmapEnabled"];
            if (mipmapToken != null)
            {
                bool mipmapEnabled = mipmapToken.ReadScalar<bool>();
                setters.Add(importer => importer.mipmapEnabled = mipmapEnabled);
            }

            // Mipmap Filter
            var mipmapFilterToken = settings["mipmapFilter"];
            if (mipmapFilterToken != null)
            {
                string filterStr = mipmapFilterToken.ToString();
                if (TryParseEnum<TextureImporterMipFilter>(filterStr, out var mipmapFilter))
                {
                    setters.Add(importer => importer.mipmapFilter = mipmapFilter);
                }
            }

            // Wrap Mode
            var wrapModeToken = settings["wrapMode"];
            if (wrapModeToken != null)
            {
                string wrapStr = wrapModeToken.ToString();
                if (TryParseEnum<TextureWrapMode>(wrapStr, out var wrapMode))
                {
                    setters.Add(importer => importer.wrapMode = wrapMode);
                }
            }

            // Wrap Mode U
            var wrapModeUToken = settings["wrapModeU"];
            if (wrapModeUToken != null)
            {
                string wrapStr = wrapModeUToken.ToString();
                if (TryParseEnum<TextureWrapMode>(wrapStr, out var wrapMode))
                {
                    setters.Add(importer => importer.wrapModeU = wrapMode);
                }
            }

            // Wrap Mode V
            var wrapModeVToken = settings["wrapModeV"];
            if (wrapModeVToken != null)
            {
                string wrapStr = wrapModeVToken.ToString();
                if (TryParseEnum<TextureWrapMode>(wrapStr, out var wrapMode))
                {
                    setters.Add(importer => importer.wrapModeV = wrapMode);
                }
            }

            // Filter Mode
            var filterModeToken = settings["filterMode"];
            if (filterModeToken != null)
            {
                string filterStr = filterModeToken.ToString();
                if (TryParseEnum<FilterMode>(filterStr, out var filterMode))
                {
                    setters.Add(importer => importer.filterMode = filterMode);
                }
            }

            // Aniso Level
            var anisoToken = settings["anisoLevel"];
            if (anisoToken != null)
            {
                int anisoLevel = anisoToken.ReadScalar<int>();
                setters.Add(importer => importer.anisoLevel = anisoLevel);
            }

            // Max Texture Size
            var maxSizeToken = settings["maxTextureSize"];
            if (maxSizeToken != null)
            {
                int maxTextureSize = maxSizeToken.ReadScalar<int>();
                setters.Add(importer => importer.maxTextureSize = maxTextureSize);
            }

            // Compression
            var compressionToken = settings["textureCompression"];
            if (compressionToken != null)
            {
                string compStr = compressionToken.ToString();
                if (TryParseEnum<TextureImporterCompression>(compStr, out var compression))
                {
                    setters.Add(importer => importer.textureCompression = compression);
                }
            }

            // Crunched Compression
            var crunchedToken = settings["crunchedCompression"];
            if (crunchedToken != null)
            {
                bool crunchedCompression = crunchedToken.ReadScalar<bool>();
                setters.Add(importer => importer.crunchedCompression = crunchedCompression);
            }

            // Compression Quality
            var qualityToken = settings["compressionQuality"];
            if (qualityToken != null)
            {
                int compressionQuality = qualityToken.ReadScalar<int>();
                setters.Add(importer => importer.compressionQuality = compressionQuality);
            }

            // --- Sprite-specific settings ---

            // Sprite Import Mode
            var spriteModeToken = settings["spriteImportMode"];
            if (spriteModeToken != null)
            {
                string modeStr = spriteModeToken.ToString();
                if (TryParseEnum<SpriteImportMode>(modeStr, out var spriteMode))
                {
                    setters.Add(importer => importer.spriteImportMode = spriteMode);
                }
            }

            // Sprite Pixels Per Unit
            var ppuToken = settings["spritePixelsPerUnit"];
            if (ppuToken != null)
            {
                float spritePixelsPerUnit = ppuToken.ReadScalar<float>();
                setters.Add(importer => importer.spritePixelsPerUnit = spritePixelsPerUnit);
            }

            // Sprite Pivot
            var pivotToken = settings["spritePivot"];
            if (pivotToken is JArray pivotArray && pivotArray.Count >= 2)
            {
                var pivot = new Vector2(
                    pivotArray[0].ReadScalar<float>(),
                    pivotArray[1].ReadScalar<float>()
                );
                setters.Add(importer => importer.spritePivot = pivot);
            }

            SpriteMeshType? meshType = null;
            var meshTypeToken = settings["spriteMeshType"];
            if (meshTypeToken != null && TryParseEnum<SpriteMeshType>(meshTypeToken.ToString(), out var parsedMeshType))
                meshType = parsedMeshType;

            uint? extrude = null;
            var extrudeToken = settings["spriteExtrude"];
            if (extrudeToken != null)
                extrude = (uint)extrudeToken.ReadScalar<int>();

            return importer =>
            {
                foreach (var set in setters) set(importer);

                var importerSettings = new TextureImporterSettings();
                importer.ReadTextureSettings(importerSettings);
                if (meshType.HasValue) importerSettings.spriteMeshType = meshType.Value;
                if (extrude.HasValue) importerSettings.spriteExtrude = extrude.Value;
                if (meshType.HasValue || extrude.HasValue)
                    importer.SetTextureSettings(importerSettings);

                importer.SaveAndReimport();
            };
        }

        private static bool TryParseEnum<T>(string value, out T result) where T : struct
        {
            // Try exact match first
            if (Enum.TryParse<T>(value, true, out result))
            {
                return true;
            }

            // Try without common prefixes/suffixes
            string cleanValue = value.Replace("_", "").Replace("-", "");
            if (Enum.TryParse<T>(cleanValue, true, out result))
            {
                return true;
            }

            result = default;
            return false;
        }

        private static bool AssetExists(string path)
        {
            return !string.IsNullOrEmpty(AssetDatabase.AssetPathToGUID(path));
        }

        private static void EnsureDirectoryExists(string assetPath)
        {
            string directory = Path.GetDirectoryName(assetPath);
            if (!string.IsNullOrEmpty(directory) && !Directory.Exists(GetAbsolutePath(directory)))
            {
                Directory.CreateDirectory(GetAbsolutePath(directory));
                AssetDatabase.Refresh(ImportAssetOptions.ForceSynchronousImport);
            }
        }

        private static string GetAbsolutePath(string assetPath)
        {
            return AssetPathUtility.GetFullAssetPath(assetPath);
        }

        private static string ResolveImagePath(string imagePath)
        {
            if (!AssetGenPaths.TryGetAssetsRelativePath(imagePath, out string relative))
                throw new ArgumentException("Image paths must resolve safely inside Assets.");
            return AssetPathUtility.GetFullAssetPath(relative);
        }
    }
}
