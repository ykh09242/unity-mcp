using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using MCPForUnity.Editor.Helpers;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEngine;

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
            "set_import_settings",
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
                if (read == 0)
                    throw new ArgumentException("Image changed or was truncated while reading.");
                offset += read;
            }
            if (stream.ReadByte() != -1)
                throw new ArgumentException("Image grew while reading.");
            return bytes;
        }

        private static uint ReadBigEndian(byte[] bytes, int offset)
        {
            return ((uint)bytes[offset] << 24) | ((uint)bytes[offset + 1] << 16) | ((uint)bytes[offset + 2] << 8) | bytes[offset + 3];
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
            bool png =
                bytes.Length >= 8
                && bytes[0] == 137
                && bytes[1] == 80
                && bytes[2] == 78
                && bytes[3] == 71
                && bytes[4] == 13
                && bytes[5] == 10
                && bytes[6] == 26
                && bytes[7] == 10;
            if (png)
            {
                bool header = false,
                    data = false,
                    end = false;
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
                        if (header || length != 13)
                            throw new ArgumentException("Invalid or repeated PNG IHDR.");
                        if (PngHeaderCrc(bytes, offset + 4) != ReadBigEndian(bytes, offset + 21))
                            throw new ArgumentException("Invalid PNG IHDR checksum.");
                        uint w = ReadBigEndian(bytes, offset + 8),
                            h = ReadBigEndian(bytes, offset + 12);
                        if (w == 0 || h == 0 || w > MaxTextureDimension || h > MaxTextureDimension)
                            throw new ArgumentException($"Image dimensions exceed max {MaxTextureDimension} per side or are invalid.");
                        width = (int)w;
                        height = (int)h;
                        int depth = bytes[offset + 16],
                            color = bytes[offset + 17];
                        bool validDepth =
                            color == 0 ? (depth == 1 || depth == 2 || depth == 4 || depth == 8 || depth == 16)
                            : color == 3 ? (depth == 1 || depth == 2 || depth == 4 || depth == 8)
                            : (color == 2 || color == 4 || color == 6) && (depth == 8 || depth == 16);
                        if (!validDepth || bytes[offset + 18] != 0 || bytes[offset + 19] != 0 || bytes[offset + 20] > 1)
                            throw new ArgumentException("Invalid PNG image header.");
                        header = true;
                    }
                    else if (type == 0x49444154)
                        data = true;
                    else if (type == 0x6163544c)
                        throw new ArgumentException("Animated PNG inputs are not supported.");
                    else if (type == 0x49454e44)
                    {
                        if (length != 0 || !data || offset + 12 != bytes.Length)
                            throw new ArgumentException("Invalid PNG end or missing image data.");
                        end = true;
                        break;
                    }
                    offset += (int)length + 12;
                }
                if (!header || !end)
                    throw new ArgumentException("Truncated PNG image.");
                return;
            }
            if (bytes.Length < 4 || bytes[0] != 255 || bytes[1] != 216)
                throw new ArgumentException("Image input must be PNG or JPEG.");
            bool frame = false,
                scan = false,
                inScan = false;
            int scanCount = 0;
            int position = 2;
            while (position < bytes.Length)
            {
                if (bytes[position++] != 255)
                {
                    if (inScan)
                        continue;
                    throw new ArgumentException("Invalid JPEG marker.");
                }
                while (position < bytes.Length && bytes[position] == 255)
                    position++;
                if (position >= bytes.Length)
                    break;
                int marker = bytes[position++];
                if (inScan && (marker == 0 || (marker >= 208 && marker <= 215)))
                    continue;
                inScan = false;
                if (marker == 217)
                {
                    if (!frame || !scan || position != bytes.Length)
                        throw new ArgumentException("Invalid JPEG end.");
                    return;
                }
                if (marker == 0 || marker == 216 || marker == 1 || (marker >= 208 && marker <= 215) || position > bytes.Length - 2)
                    throw new ArgumentException("Invalid JPEG segment.");
                if (marker == 220 || marker == 222 || marker == 223)
                    throw new ArgumentException("JPEG dimension redefinition and hierarchical frames are not supported.");
                int length = (bytes[position] << 8) | bytes[position + 1];
                if (length < 2 || length > bytes.Length - position)
                    throw new ArgumentException("Truncated JPEG segment.");
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
                        if ((sampling >> 4) < 1 || (sampling >> 4) > 4 || (sampling & 15) < 1 || (sampling & 15) > 4 || bytes[entry + 2] > 3)
                            throw new ArgumentException("Invalid JPEG sampling or quantization table.");
                        for (int previous = 0; previous < component; previous++)
                            if (bytes[entry] == bytes[position + 8 + 3 * previous])
                                throw new ArgumentException("Repeated JPEG component identifier.");
                    }
                    height = (bytes[position + 3] << 8) | bytes[position + 4];
                    width = (bytes[position + 5] << 8) | bytes[position + 6];
                    var error = ValidateDimensions(width, height, null);
                    if (error != null)
                        throw new ArgumentException("JPEG dimensions exceed texture limits or are invalid.");
                    frame = true;
                }
                if (marker == 218)
                {
                    if (++scanCount > 64)
                        throw new ArgumentException("JPEG contains too many scans.");
                    if (!frame || length < 6 || length != 6 + 2 * bytes[position + 2] || bytes[position + 2] < 1 || bytes[position + 2] > 4)
                        throw new ArgumentException("Invalid JPEG scan header.");
                    scan = inScan = true;
                }
                position += length;
            }
            throw new ArgumentException("Truncated JPEG image.");
        }

        private static ErrorResponse ValidatePixelPayload(JToken pixels, int width, int height)
        {
            if (pixels?.Type != JTokenType.String)
                return null;
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
                return new ErrorResponse($"Unknown action: '{action}'. Valid actions are: {validActionsList}");
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
                PreparedTextureImport applySettings =
                    importSettingsToken != null
                        ? PrepareTextureImporterSettings(importSettingsToken)
                        : (asSprite || spriteSettingsToken != null ? PrepareSpriteSettings(spriteSettingsToken) : null);
                var fillColorToken = @params["fillColor"];
                var patternToken = @params["pattern"];
                var pixelsToken = @params["pixels"];
                if (!hasImage)
                {
                    var pixelError = ValidatePixelPayload(pixelsToken, width, height);
                    if (pixelError != null)
                        return pixelError;
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
                var applyContents = !hasImage ? PrepareTextureContents(@params, width, height, patternSize) : null;
                using var folders = new AssetFolderScope();

                if (hasImage)
                {
                    string resolvedImagePath = ResolveImagePath(imagePath);
                    if (!File.Exists(resolvedImagePath))
                        return new ErrorResponse($"Image file not found at '{imagePath}'.");

                    byte[] imageBytes = ReadBoundedImage(resolvedImagePath);
                    ReadImageDimensions(imageBytes, out int imageWidth, out int imageHeight);
                    folders.EnsureParentDirectory(fullPath);
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
                    folders.EnsureParentDirectory(fullPath);
                    texture = new Texture2D(width, height, TextureFormat.RGBA32, false);
                    applyContents(texture);
                }

                texture.Apply();

                // Save to disk
                byte[] imageData = TextureOps.EncodeTexture(texture, fullPath);
                if (imageData == null || imageData.Length == 0)
                {
                    return new ErrorResponse($"Failed to encode texture for '{fullPath}'");
                }
                File.WriteAllBytes(GetAbsolutePath(fullPath), imageData);

                AssetDatabase.ImportAsset(fullPath, ImportAssetOptions.ForceUpdate);

                var importReport = ApplyPreparedImportSettings(fullPath, applySettings);
                if (importReport?.Value<string>("error") != null)
                    return ImportSettingsResponse(fullPath, importReport, true);

                foreach (var warning in warnings)
                {
                    McpLog.Warn($"[ManageTexture] {warning}");
                }

                var response = new SuccessResponse(
                    $"Texture created at '{fullPath}' ({width}x{height})"
                        + (asSprite ? " as sprite" : "")
                        + (importReport?["rejected"]?.HasValues == true ? ". Some importer settings were rejected; see import_settings." : ""),
                    new
                    {
                        path = fullPath,
                        source_saved = true,
                        width,
                        height,
                        asSprite = (AssetImporter.GetAtPath(fullPath) as TextureImporter)?.textureType == TextureImporterType.Sprite,
                        warnings = warnings.Count > 0 ? warnings : null,
                        import_settings = importReport,
                    }
                );
                folders.Complete();
                return response;
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

        private static Action<Texture2D> PrepareTextureContents(JObject @params, int width, int height, int patternSize)
        {
            var fillColorToken = @params["fillColor"];
            var patternToken = @params["pattern"];
            var pixelsToken = @params["pixels"];
            Color32? fillColor = fillColorToken is JArray fill ? TextureOps.ParseColor32(fill) : null;
            string pattern = patternToken?.ToString();
            var palette = patternToken != null ? TextureOps.ParsePalette(@params["palette"] as JArray) : null;
            var pixels = pixelsToken != null && pixelsToken.Type != JTokenType.Null ? TextureOps.PreparePixelData(pixelsToken, width, height) : null;
            return texture =>
            {
                if (fillColor.HasValue)
                    TextureOps.FillTexture(texture, fillColor.Value);
                if (patternToken != null)
                    ApplyPatternToTexture(texture, pattern, palette, patternSize);
                if (pixels != null)
                    TextureOps.ApplyPreparedPixelDataToRegion(texture, pixels, 0, 0, width, height);
                if (fillColorToken == null && patternToken == null && pixelsToken == null)
                    TextureOps.FillTexture(texture, new Color32(0, 0, 0, 0));
            };
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
                    if (validationError != null)
                        return validationError;
                }

                PreparedTextureImport applySettings = null;
                if (hasImportSettings)
                {
                    var preparationError = PrepareImportSettingsParams(@params, out applySettings);
                    if (preparationError != null)
                        return preparationError;
                }

                // Fast path: only import settings, no pixel changes
                if (setPixelsToken == null && hasImportSettings)
                {
                    var report = ApplyPreparedImportSettings(fullPath, applySettings);
                    return ImportSettingsResponse(fullPath, report, false);
                }

                // Pixel modification path
                if (setPixelsToken != null)
                {
                    int x = setPixelsToken["x"]?.ReadScalar<int?>() ?? 0;
                    int y = setPixelsToken["y"]?.ReadScalar<int?>() ?? 0;
                    int w = setPixelsToken["width"]?.ReadScalar<int?>() ?? 1;
                    int h = setPixelsToken["height"]?.ReadScalar<int?>() ?? 1;
                    var regionError = ValidateDimensions(w, h, null);
                    if (regionError != null)
                        return regionError;
                    var pixelsToken = setPixelsToken["pixels"];
                    var colorToken = setPixelsToken["color"];
                    var pixelError = ValidatePixelPayload(pixelsToken, w, h);
                    if (pixelError != null)
                        return pixelError;
                    Color32[] pixels = null;
                    Color32 color = default;
                    if (pixelsToken != null)
                        pixels = TextureOps.PreparePixelData(pixelsToken, w, h);
                    else if (colorToken != null)
                        color = TextureOps.ParseRequiredColor32(colorToken as JArray);
                    else
                        return new ErrorResponse("setPixels requires 'color' or 'pixels'.");
                    // Inspect the file before asking Unity to load a possibly uncached texture.
                    string absolutePath = GetAbsolutePath(fullPath);
                    byte[] fileData = ReadBoundedImage(absolutePath);
                    ReadImageDimensions(fileData, out int imageWidth, out int imageHeight);
                    Texture2D texture = AssetDatabase.LoadAssetAtPath<Texture2D>(fullPath);
                    if (texture == null)
                        return new ErrorResponse($"Failed to load texture at path: {fullPath}");
                    var existingDimensionError = ValidateDimensions(texture.width, texture.height, null);
                    if (existingDimensionError != null)
                        return existingDimensionError;

                    editableTexture = new Texture2D(2, 2, TextureFormat.RGBA32, false);
                    if (!editableTexture.LoadImage(fileData))
                        return new ErrorResponse($"Failed to decode texture at path: {fullPath}");
                    var decodedDimensionError = ValidateDimensions(editableTexture.width, editableTexture.height, null);
                    if (decodedDimensionError != null)
                        return decodedDimensionError;
                    if (editableTexture.width != imageWidth || editableTexture.height != imageHeight)
                        return new ErrorResponse("Decoded image dimensions do not match its header.");

                    if (pixelsToken != null)
                    {
                        TextureOps.ApplyPreparedPixelDataToRegion(editableTexture, pixels, x, y, w, h);
                    }
                    else if (colorToken != null)
                    {
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

                    editableTexture.Apply();

                    byte[] imageData = TextureOps.EncodeTexture(editableTexture, fullPath);
                    if (imageData == null || imageData.Length == 0)
                    {
                        return new ErrorResponse($"Failed to encode texture for '{fullPath}'");
                    }
                    File.WriteAllBytes(absolutePath, imageData);
                    AssetDatabase.ImportAsset(fullPath, ImportAssetOptions.ForceUpdate);
                }

                var importReport = hasImportSettings ? ApplyPreparedImportSettings(fullPath, applySettings) : null;
                return ImportSettingsResponse(fullPath, importReport, setPixelsToken != null);
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

            List<Color32> palette;
            try
            {
                palette = TextureOps.ParsePalette(@params["palette"] as JArray);
            }
            catch (ArgumentException ex)
            {
                return new ErrorResponse($"Invalid gradient palette: {ex.Message}");
            }
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
                using var folders = new AssetFolderScope();
                folders.EnsureParentDirectory(fullPath);
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
                File.WriteAllBytes(GetAbsolutePath(fullPath), imageData);

                AssetDatabase.ImportAsset(fullPath, ImportAssetOptions.ForceUpdate);

                // Configure as sprite if requested
                var importReport = ApplyPreparedImportSettings(fullPath, applySettings);
                if (importReport?.Value<string>("error") != null)
                    return ImportSettingsResponse(fullPath, importReport, true);

                foreach (var warning in warnings)
                {
                    McpLog.Warn($"[ManageTexture] {warning}");
                }

                var response = new SuccessResponse(
                    $"Gradient texture created at '{fullPath}' ({width}x{height})",
                    new
                    {
                        path = fullPath,
                        width,
                        height,
                        import_settings = importReport,
                        gradientType,
                        source_saved = true,
                        warnings = warnings.Count > 0 ? warnings : null,
                    }
                );
                folders.Complete();
                return response;
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

            List<Color32> palette;
            try
            {
                palette = TextureOps.ParsePalette(@params["palette"] as JArray);
            }
            catch (ArgumentException ex)
            {
                return new ErrorResponse($"Invalid noise palette: {ex.Message}");
            }
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
                using var folders = new AssetFolderScope();
                folders.EnsureParentDirectory(fullPath);
                texture = new Texture2D(width, height, TextureFormat.RGBA32, false);
                ApplyPerlinNoise(texture, palette, scale, octaves);

                texture.Apply();

                byte[] imageData = TextureOps.EncodeTexture(texture, fullPath);
                if (imageData == null || imageData.Length == 0)
                {
                    return new ErrorResponse($"Failed to encode texture for '{fullPath}'");
                }
                File.WriteAllBytes(GetAbsolutePath(fullPath), imageData);

                AssetDatabase.ImportAsset(fullPath, ImportAssetOptions.ForceUpdate);

                // Configure as sprite if requested
                var importReport = ApplyPreparedImportSettings(fullPath, applySettings);
                if (importReport?.Value<string>("error") != null)
                    return ImportSettingsResponse(fullPath, importReport, true);

                foreach (var warning in warnings)
                {
                    McpLog.Warn($"[ManageTexture] {warning}");
                }

                var response = new SuccessResponse(
                    $"Noise texture created at '{fullPath}' ({width}x{height})",
                    new
                    {
                        path = fullPath,
                        width,
                        height,
                        import_settings = importReport,
                        noiseScale = scale,
                        source_saved = true,
                        octaves,
                        warnings = warnings.Count > 0 ? warnings : null,
                    }
                );
                folders.Complete();
                return response;
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
            if (width > 0 && height > 0)
                pattern = pattern.ToLower();

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

            switch (pattern)
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
            if (palette.Count == 1)
                return palette[0];
            if (t <= 0)
                return palette[0];
            if (t >= 1)
                return palette[palette.Count - 1];

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
            bool hasSpriteSettings =
                (asSpriteToken is JObject spriteObject && spriteObject.HasValues)
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

        private static object PrepareImportSettingsParams(JObject @params, out PreparedTextureImport apply)
        {
            apply = null;
            JToken importSettingsToken = @params["import_settings"] ?? @params["importSettings"];
            JToken asSpriteToken = @params["as_sprite"] ?? @params["spriteSettings"];

            if (importSettingsToken != null && asSpriteToken != null)
            {
                return new ErrorResponse(
                    "Cannot specify both 'import_settings' and 'as_sprite'. " + "Use 'import_settings' with textureType='Sprite' instead."
                );
            }

            if (importSettingsToken != null)
            {
                apply = PrepareTextureImporterSettings(importSettingsToken);
            }
            else if (asSpriteToken != null && (asSpriteToken.Type == JTokenType.Boolean ? asSpriteToken.ReadScalar<bool>() : true))
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
                if (error != null)
                    return error;
                var report = ApplyPreparedImportSettings(fullPath, applySettings);
                return ImportSettingsResponse(fullPath, report, false);
            }
            catch (Exception e)
            {
                return new ErrorResponse($"Failed to set import settings: {e.Message}");
            }
        }

        // Prepare conversions before any file/importer changes. Native setter/import failures
        // can still leave applied changes; this is not a rollback transaction.
        private static JObject ApplyPreparedImportSettings(string path, PreparedTextureImport apply)
        {
            if (apply == null)
                return null;
            var importer = AssetImporter.GetAtPath(path) as TextureImporter;
            if (importer == null)
                throw new InvalidOperationException($"Could not get TextureImporter for {path}");
            return apply.Apply(importer, path);
        }

        private static PreparedTextureImport PrepareSpriteSettings(JToken spriteSettings)
        {
            var settings = new JObject { ["textureType"] = "Sprite", ["spriteImportMode"] = "Single" };
            if (spriteSettings is JObject sprite)
                foreach (var property in sprite.Properties())
                    settings[
                        property.Name == "pivot" ? "spritePivot"
                        : property.Name == "pixelsPerUnit" ? "spritePixelsPerUnit"
                        : property.Name
                    ] = property.Value.DeepClone();
            return PrepareTextureImporterSettings(settings);
        }

        private static Action<TextureImporter> PrepareTextureImporterSetters(JToken importSettings)
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
                var pivot = new Vector2(pivotArray[0].ReadScalar<float>(), pivotArray[1].ReadScalar<float>());
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
                foreach (var set in setters)
                    set(importer);

                if (meshType.HasValue || extrude.HasValue)
                {
                    var importerSettings = new TextureImporterSettings();
                    importer.ReadTextureSettings(importerSettings);
                    if (meshType.HasValue)
                        importerSettings.spriteMeshType = meshType.Value;
                    if (extrude.HasValue)
                        importerSettings.spriteExtrude = extrude.Value;
                    importer.SetTextureSettings(importerSettings);
                }
            };
        }

        private static bool TryParseEnum<T>(string value, out T result)
            where T : struct
        {
            // Try exact match first
            if (Enum.TryParse<T>(value, true, out result) && Enum.IsDefined(typeof(T), result))
            {
                return true;
            }

            // Try without common prefixes/suffixes
            string cleanValue = value.Replace("_", "").Replace("-", "");
            if (value.Any(char.IsLetter) && Enum.TryParse<T>(cleanValue, true, out result) && Enum.IsDefined(typeof(T), result))
            {
                return true;
            }

            result = default;
            return false;
        }

        private static object ImportSettingsResponse(string path, JObject report, bool sourceSaved)
        {
            var data = new
            {
                path,
                source_saved = sourceSaved,
                import_settings = report,
            };
            if (report?.Value<string>("error") is string error)
                return new ErrorResponse($"Texture importer update failed: {error}", data);
            if (report?.Value<string>("status") == "rejected" && !sourceSaved && !(report.Value<bool?>("legacy_no_op") ?? false))
                return new ErrorResponse("No requested importer settings were accepted.", data);
            return new SuccessResponse(
                $"Texture processed: {path}" + (report?["rejected"]?.HasValues == true ? ". Some importer settings were rejected; see import_settings." : ""),
                data
            );
        }

        private static PreparedTextureImport PrepareTextureImporterSettings(JToken token)
        {
            if (!(token is JObject settings))
                throw new ArgumentException("Import settings must be an object.");
            // Preserve the established all-or-nothing scalar conversion preflight.
            PrepareTextureImporterSetters(settings);
            return new PreparedTextureImport(settings);
        }

        private sealed class PreparedTextureImport
        {
            private static readonly HashSet<string> GlobalFields = new HashSet<string>(StringComparer.Ordinal)
            {
                "textureType",
                "textureShape",
                "sRGBTexture",
                "alphaSource",
                "alphaIsTransparency",
                "isReadable",
                "mipmapEnabled",
                "mipmapFilter",
                "wrapMode",
                "wrapModeU",
                "wrapModeV",
                "filterMode",
                "anisoLevel",
                "maxTextureSize",
                "textureCompression",
                "crunchedCompression",
                "compressionQuality",
                "spriteImportMode",
                "spritePixelsPerUnit",
                "spritePivot",
                "spriteMeshType",
                "spriteExtrude",
            };
            private static readonly Dictionary<string, BuildTarget> Platforms = new Dictionary<string, BuildTarget>(StringComparer.Ordinal)
            {
                ["Standalone"] = BuildTarget.StandaloneWindows64,
                ["Android"] = BuildTarget.Android,
                ["iPhone"] = BuildTarget.iOS,
                ["WebGL"] = BuildTarget.WebGL,
            };
            private static readonly HashSet<string> PlatformFields = new HashSet<string>(StringComparer.Ordinal)
            {
                "overridden",
                "maxTextureSize",
                "format",
                "textureCompression",
                "compressionQuality",
                "crunchedCompression",
                "allowsAlphaSplitting",
            };
            private readonly JObject requested;
            private readonly JObject accepted = new JObject();
            private readonly JObject rejected = new JObject();
            private readonly Dictionary<string, JObject> platformSettings = new Dictionary<string, JObject>(StringComparer.Ordinal);
            private readonly Action<TextureImporter> setGlobal;
            private bool legacyNoOp;

            internal PreparedTextureImport(JObject settings)
            {
                requested = (JObject)settings.DeepClone();
                foreach (var property in settings.Properties())
                {
                    if (property.Name == "platformSettings")
                    {
                        PreparePlatforms(property.Value);
                        continue;
                    }
                    if (!GlobalFields.Contains(property.Name))
                    {
                        rejected[property.Name] = "Unknown or unsupported texture importer setting.";
                        continue;
                    }
                    if (property.Name == "spritePivot" && property.Value is JArray shortPivot && shortPivot.Count < 2)
                    {
                        rejected[property.Name] = "Incomplete pivot was retained as a legacy no-op; no pivot was applied.";
                        legacyNoOp = true;
                        continue;
                    }
                    var owner =
                        property.Name == "spriteMeshType" || property.Name == "spriteExtrude" ? typeof(TextureImporterSettings) : typeof(TextureImporter);
                    var member = owner.GetProperty(property.Name);
                    if (member == null || !member.CanRead || !member.CanWrite)
                    {
                        rejected[property.Name] = "This Editor does not expose the requested setting.";
                        continue;
                    }
                    var value = ConvertSetting(property.Value, member.PropertyType, property.Name, out string reason);
                    if (reason != null)
                        rejected[property.Name] = reason;
                    else
                        accepted[property.Name] = value;
                }
                setGlobal = PrepareTextureImporterSetters(accepted);
            }

            private void PreparePlatforms(JToken token)
            {
                if (!(token is JObject platforms))
                    throw new ArgumentException("platformSettings must be an object keyed by platform name.");
                foreach (var platform in platforms.Properties())
                {
                    string prefix = "platformSettings." + platform.Name;
                    if (!Platforms.ContainsKey(platform.Name))
                    {
                        rejected[prefix] = "Unsupported platform name. Supported importer profiles: Standalone, Android, iPhone, WebGL.";
                        continue;
                    }
                    if (!(platform.Value is JObject values))
                        throw new ArgumentException(prefix + " must be an object.");
                    var prepared = new JObject();
                    platformSettings.Add(platform.Name, prepared);
                    foreach (var property in values.Properties())
                    {
                        string key = prefix + "." + property.Name;
                        var member = typeof(TextureImporterPlatformSettings).GetProperty(property.Name);
                        if (!PlatformFields.Contains(property.Name) || member == null || !member.CanRead || !member.CanWrite)
                        {
                            rejected[key] = "Unknown platform setting or unsupported by this Editor.";
                            continue;
                        }
                        var value = ConvertSetting(property.Value, member.PropertyType, property.Name, out string reason);
                        if (reason != null)
                            rejected[key] = reason;
                        else
                            prepared[property.Name] = value;
                    }
                }
            }

            private static JToken ConvertSetting(JToken token, Type type, string key, out string reason)
            {
                reason = null;
                if (token == null || token.Type == JTokenType.Null)
                    throw new ArgumentException($"'{key}' cannot be null.");
                if (type.IsEnum)
                {
                    string text = token.Type == JTokenType.String || token.Type == JTokenType.Integer ? token.ToString() : "";
                    bool parsed = Enum.TryParse(type, text, true, out object value) && Enum.IsDefined(type, value);
                    if (!parsed && token.Type == JTokenType.String && text.Any(char.IsLetter))
                        parsed = Enum.TryParse(type, text.Replace("_", "").Replace("-", ""), true, out value) && Enum.IsDefined(type, value);
                    if (!parsed)
                    {
                        reason = "Unknown or unsupported enum value for this Editor.";
                        return null;
                    }
                    return new JValue(value.ToString());
                }
                if (type == typeof(bool))
                    return new JValue(token.ReadScalar<bool>());
                if (type == typeof(int) || type == typeof(uint))
                {
                    int value = token.ReadScalar<int>();
                    bool valid =
                        key == "anisoLevel" ? value >= 0 && value <= 16
                        : key == "compressionQuality" ? value >= 0 && value <= 100
                        : key == "maxTextureSize" ? value >= 32 && value <= 16384 && (value & (value - 1)) == 0
                        : key == "spriteExtrude" ? value >= 0 && value <= 32
                        : true;
                    if (!valid)
                        throw new ArgumentException($"'{key}' is outside its supported range.");
                    return new JValue(value);
                }
                if (type == typeof(float))
                {
                    float value = token.ReadScalar<float>();
                    if (float.IsNaN(value) || float.IsInfinity(value) || (key == "spritePixelsPerUnit" && value <= 0))
                        throw new ArgumentException($"'{key}' must be a finite positive number.");
                    return new JValue(value);
                }
                if (type == typeof(Vector2))
                {
                    if (!(token is JArray array) || array.Count < 2)
                        throw new ArgumentException($"'{key}' must contain two normalized coordinates.");
                    float x = array[0].ReadScalar<float>(),
                        y = array[1].ReadScalar<float>();
                    if (float.IsNaN(x) || float.IsNaN(y) || x < 0 || x > 1 || y < 0 || y > 1)
                        throw new ArgumentException($"'{key}' coordinates must be finite values between zero and one.");
                    return new JArray(x, y);
                }
                reason = "Unsupported setting type in this Editor.";
                return null;
            }

            private static JToken ReadValue(object owner, string key)
            {
                var member = owner.GetType().GetProperty(key);
                if (member == null)
                    return JValue.CreateNull();
                object value = member.GetValue(owner);
                if (value is Enum)
                    return new JValue(value.ToString());
                if (value is Vector2 vector)
                    return new JArray(vector.x, vector.y);
                return value == null ? JValue.CreateNull() : JToken.FromObject(value);
            }

            private JObject Snapshot(TextureImporter importer, bool effective)
            {
                var snapshot = new JObject();
                var sprite = new TextureImporterSettings();
                importer.ReadTextureSettings(sprite);
                foreach (string key in GlobalFields)
                    snapshot[key] = ReadValue(key == "spriteMeshType" || key == "spriteExtrude" ? (object)sprite : importer, key);
                var platforms = new JObject();
                foreach (string name in platformSettings.Keys)
                {
                    var stored = importer.GetPlatformTextureSettings(name);
                    var source = effective && !stored.overridden ? importer.GetDefaultPlatformTextureSettings() : stored;
                    var values = new JObject();
                    foreach (string key in PlatformFields)
                        values[key] = key == "overridden" ? new JValue(stored.overridden) : ReadValue(source, key);
                    platforms[name] = values;
                }
                snapshot["platformSettings"] = platforms;
                return snapshot;
            }

            internal JObject Apply(TextureImporter importer, string path)
            {
                // Validate format/type compatibility before assigning any native importer values.
                var type =
                    accepted["textureType"] != null
                        ? (TextureImporterType)Enum.Parse(typeof(TextureImporterType), accepted.Value<string>("textureType"))
                        : importer.textureType;
                foreach (var platform in platformSettings)
                {
                    if (platform.Value["format"] == null)
                        continue;
                    var format = (TextureImporterFormat)Enum.Parse(typeof(TextureImporterFormat), platform.Value.Value<string>("format"));
                    if (!TextureImporter.IsPlatformTextureFormatValid(type, Platforms[platform.Key], format))
                    {
                        rejected["platformSettings." + platform.Key + ".format"] = "The format is not supported for this texture type and platform.";
                        platform.Value.Remove("format");
                    }
                }
                var before = Snapshot(importer, false);
                bool changes =
                    accepted.Properties().Any(property => !JToken.DeepEquals(before[property.Name], property.Value))
                    || platformSettings.Any(platform =>
                        platform.Value.Properties().Any(property => !JToken.DeepEquals(before["platformSettings"][platform.Key][property.Name], property.Value))
                    );
                string failure = null;
                bool reimported = false;
                try
                {
                    if (changes)
                    {
                        setGlobal(importer);
                        foreach (var platform in platformSettings)
                        {
                            if (!platform.Value.HasValues)
                                continue;
                            var native = importer.GetPlatformTextureSettings(platform.Key);
                            foreach (var property in platform.Value.Properties())
                            {
                                var member = typeof(TextureImporterPlatformSettings).GetProperty(property.Name);
                                object value = member.PropertyType.IsEnum
                                    ? Enum.Parse(member.PropertyType, property.Value.ToString())
                                    : property.Value.ToObject(member.PropertyType);
                                member.SetValue(native, value);
                            }
                            importer.SetPlatformTextureSettings(native);
                        }
                        importer.SaveAndReimport();
                        reimported = true;
                    }
                }
                catch (Exception ex)
                {
                    failure = ex.GetBaseException().Message;
                }
                var fresh = AssetImporter.GetAtPath(path) as TextureImporter;
                if (fresh == null)
                    throw new InvalidOperationException("Texture importer is unavailable after applying settings.");
                var storedAfter = Snapshot(fresh, false);
                var effectiveAfter = Snapshot(fresh, true);
                var applied = new JObject();
                foreach (var property in accepted.Properties())
                    applied[property.Name] = storedAfter[property.Name].DeepClone();
                var appliedPlatforms = new JObject();
                foreach (var platform in platformSettings)
                {
                    var values = new JObject();
                    foreach (var property in platform.Value.Properties())
                        values[property.Name] = storedAfter["platformSettings"][platform.Key][property.Name].DeepClone();
                    appliedPlatforms[platform.Key] = values;
                }
                applied["platformSettings"] = appliedPlatforms;
                var overrides = new JObject();
                foreach (string name in platformSettings.Keys)
                    overrides[name] = new JObject
                    {
                        ["stored"] = storedAfter["platformSettings"][name].DeepClone(),
                        ["effective"] = effectiveAfter["platformSettings"][name].DeepClone(),
                        ["automatic_format"] = fresh.GetAutomaticFormat(name).ToString(),
                    };
                bool changed = !JToken.DeepEquals(before, storedAfter);
                var outcomes = new JObject();
                foreach (var property in accepted.Properties())
                    outcomes[property.Name] = new JObject
                    {
                        ["status"] =
                            JToken.DeepEquals(before[property.Name], storedAfter[property.Name]) ? "unchanged"
                            : JToken.DeepEquals(property.Value, storedAfter[property.Name]) ? "applied"
                            : "adjusted",
                        ["requested"] = requested[property.Name]?.DeepClone(),
                        ["effective"] = effectiveAfter[property.Name].DeepClone(),
                    };
                foreach (var property in rejected.Properties())
                    outcomes[property.Name] = new JObject { ["status"] = "rejected", ["reason"] = property.Value.DeepClone() };
                foreach (var platform in platformSettings)
                foreach (var property in platform.Value.Properties())
                {
                    var beforeValue = before["platformSettings"][platform.Key][property.Name];
                    var storedValue = storedAfter["platformSettings"][platform.Key][property.Name];
                    var effectiveValue = effectiveAfter["platformSettings"][platform.Key][property.Name];
                    outcomes["platformSettings." + platform.Key + "." + property.Name] = new JObject
                    {
                        ["status"] =
                            !JToken.DeepEquals(storedValue, effectiveValue) ? "inactive_override"
                            : JToken.DeepEquals(beforeValue, storedValue) ? "unchanged"
                            : JToken.DeepEquals(property.Value, storedValue) ? "applied"
                            : "adjusted",
                        ["requested"] = property.Value.DeepClone(),
                        ["stored"] = storedValue.DeepClone(),
                        ["effective"] = effectiveValue.DeepClone(),
                    };
                }
                int acceptedCount = accepted.Count + platformSettings.Sum(platform => platform.Value.Count);
                return new JObject
                {
                    ["requested"] = requested.DeepClone(),
                    ["applied"] = applied,
                    ["effective"] = effectiveAfter,
                    ["rejected"] = rejected.DeepClone(),
                    ["changed"] = changed,
                    ["reimported"] = reimported,
                    ["persisted"] = failure == null,
                    ["per_setting"] = outcomes,
                    ["platform_overrides"] = overrides,
                    ["legacy_no_op"] = legacyNoOp,
                    ["status"] =
                        failure != null ? "failed"
                        : acceptedCount == 0 && rejected.HasValues ? "rejected"
                        : rejected.HasValues ? "partial"
                        : changed ? "applied"
                        : "unchanged",
                    ["error"] = failure,
                    ["limitations"] = "Effective values are importer readbacks; platform output has not been built or validated on a target device.",
                };
            }
        }

        private static bool AssetExists(string path)
        {
            return !string.IsNullOrEmpty(AssetDatabase.AssetPathToGUID(path));
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
