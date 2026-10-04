using System;
using System.Collections.Generic;
using System.IO;
using Newtonsoft.Json.Linq;
using UnityEngine;

namespace MCPForUnity.Editor.Helpers
{
    public static class TextureOps
    {
        public static byte[] EncodeTexture(Texture2D texture, string assetPath)
        {
            if (texture == null)
                return null;

            string extension = Path.GetExtension(assetPath);
            if (string.IsNullOrEmpty(extension))
            {
                McpLog.Warn($"[TextureOps] No file extension for '{assetPath}', defaulting to PNG.");
                return texture.EncodeToPNG();
            }

            switch (extension.ToLowerInvariant())
            {
                case ".png":
                    return texture.EncodeToPNG();
                case ".jpg":
                case ".jpeg":
                    return texture.EncodeToJPG();
                default:
                    McpLog.Warn($"[TextureOps] Unsupported extension '{extension}' for '{assetPath}', defaulting to PNG.");
                    return texture.EncodeToPNG();
            }
        }

        public static void FillTexture(Texture2D texture, Color32 color)
        {
            if (texture == null)
                return;

            Color32[] pixels = new Color32[texture.width * texture.height];
            for (int i = 0; i < pixels.Length; i++)
            {
                pixels[i] = color;
            }
            texture.SetPixels32(pixels);
        }

        public static Color32 ParseColor32(JArray colorArray)
        {
            if (colorArray == null || colorArray.Count < 3)
                return new Color32(255, 255, 255, 255);

            byte r = (byte)Mathf.Clamp(colorArray[0].ToObject<int>(), 0, 255);
            byte g = (byte)Mathf.Clamp(colorArray[1].ToObject<int>(), 0, 255);
            byte b = (byte)Mathf.Clamp(colorArray[2].ToObject<int>(), 0, 255);
            byte a = colorArray.Count > 3 ? (byte)Mathf.Clamp(colorArray[3].ToObject<int>(), 0, 255) : (byte)255;

            return new Color32(r, g, b, a);
        }

        internal static Color32 ParseRequiredColor32(JArray colorArray)
        {
            if (colorArray == null || colorArray.Count < 3)
                throw new ArgumentException("Pixel colors must contain at least RGB components.");
            for (int i = 0; i < Math.Min(colorArray.Count, 4); i++)
            {
                var type = colorArray[i].Type;
                if (type != JTokenType.Integer && type != JTokenType.Float && type != JTokenType.String)
                    throw new ArgumentException("Pixel color components must be numeric.");
            }
            return ParseColor32(colorArray);
        }

        public static List<Color32> ParsePalette(JArray paletteArray)
        {
            if (paletteArray == null)
                return null;

            List<Color32> palette = new List<Color32>();
            foreach (var item in paletteArray)
            {
                if (item is JArray colorArray)
                {
                    palette.Add(ParseColor32(colorArray));
                }
            }
            return palette.Count > 0 ? palette : null;
        }

        public static void ApplyPixelData(Texture2D texture, JToken pixelsToken, int width, int height)
        {
            ApplyPixelDataToRegion(texture, pixelsToken, 0, 0, width, height);
        }

        public static void ApplyPixelDataToRegion(Texture2D texture, JToken pixelsToken, int offsetX, int offsetY, int regionWidth, int regionHeight)
        {
            if (texture == null || pixelsToken == null)
                return;

            if (regionWidth <= 0 || regionHeight <= 0)
                throw new ArgumentException("Pixel region width and height must be positive.");
            long expectedCount = (long)regionWidth * regionHeight;
            byte[] rawData = null;
            var pixelArray = pixelsToken as JArray;

            // Validate the complete payload before changing even an in-memory pixel.
            if (pixelArray != null)
            {
                if (pixelArray.Count != expectedCount)
                    throw new ArgumentException($"Pixel array size mismatch: expected {expectedCount} entries, got {pixelArray.Count}.");
                foreach (var pixel in pixelArray)
                    ParseRequiredColor32(pixel as JArray);
            }
            else if (pixelsToken.Type == JTokenType.String)
            {
                string pixelString = pixelsToken.ToString();
                string base64 = pixelString.StartsWith("base64:") ? pixelString.Substring(7) : pixelString;
                if (!pixelString.StartsWith("base64:"))
                {
                    McpLog.Warn("[TextureOps] Base64 pixel data missing 'base64:' prefix; attempting to decode.");
                }

                rawData = Convert.FromBase64String(base64);

                // Assume RGBA32 format: 4 bytes per pixel
                // Compare pixels after divisibility to avoid overflowing count * 4.
                if (rawData.Length % 4 != 0 || rawData.Length / 4 != expectedCount)
                    throw new ArgumentException($"Base64 data size mismatch: expected {expectedCount} RGBA32 pixels, got {rawData.Length} bytes.");
            }
            else
                throw new ArgumentException("Pixels must be an array of colors or a base64 RGBA32 string.");

            int startX = Math.Max(0, offsetX);
            int startY = Math.Max(0, offsetY);
            int endX = (int)Math.Min((long)offsetX + regionWidth, texture.width);
            int endY = (int)Math.Min((long)offsetY + regionHeight, texture.height);
            if (startX >= endX || startY >= endY)
                return;
            int clippedWidth = endX - startX;
            int clippedHeight = endY - startY;
            var colors = new Color32[clippedWidth * clippedHeight];
            // Iterate only the intersection; source indices still address the requested region.
            for (int py = startY; py < endY; py++)
            {
                for (int px = startX; px < endX; px++)
                {
                    int index = (int)(((long)py - offsetY) * regionWidth + ((long)px - offsetX));
                    Color32 color;
                    if (pixelArray != null)
                        color = ParseRequiredColor32((JArray)pixelArray[index]);
                    else
                    {
                        int byteIndex = index * 4;
                        color = new Color32(rawData[byteIndex], rawData[byteIndex + 1], rawData[byteIndex + 2], rawData[byteIndex + 3]);
                    }
                    colors[(py - startY) * clippedWidth + px - startX] = color;
                }
            }
            texture.SetPixels32(startX, startY, clippedWidth, clippedHeight, colors);
        }
    }
}
