using System;
using System.Collections.Generic;
using System.IO;
using MCPForUnity.Editor.Helpers;

namespace MCPForUnity.Editor.Services.AssetGen.Providers
{
    /// <summary>
    /// Helpers for feeding a LOCAL on-disk image to a provider: resolve+verify the path, and encode
    /// it as a base64 <c>data:</c> URI — the inline form fal, Meshy, and OpenRouter accept for image
    /// input (no hosting/upload needed). Tripo does NOT accept data URIs and is handled separately.
    /// </summary>
    internal static class LocalImage
    {
        internal const int MaxImageBytes = 32 * 1024 * 1024;
        internal const int MaxDataUriBytes = 48 * 1024 * 1024 - 1024;
        private const string SizeError = "Source image exceeds the 32 MiB local image input limit.";

        // Extensions that can be inlined as a data URI for provider image input.
        private static readonly HashSet<string> SupportedExtensions = new(StringComparer.OrdinalIgnoreCase) { ".png", ".jpg", ".jpeg", ".webp", ".gif" };

        /// <summary>
        /// Resolve an image path under the project's Assets folder to an existing absolute file of
        /// a supported image type. Returns false with a user-facing <paramref name="error"/> for
        /// an unsafe path, a missing file, or an unsupported extension, so the handler can fail
        /// fast before any provider request is made.
        /// </summary>
        public static bool ResolveExisting(string path, out string absPath, out string error)
        {
            absPath = null;
            error = null;
            if (string.IsNullOrWhiteSpace(path))
            {
                error = "image_path is empty.";
                return false;
            }
            if (!AssetGenPaths.TryGetAssetsRelativePath(path, out string rel))
            {
                error = "image_path must point to a file under the project's Assets folder.";
                return false;
            }
            string abs = AssetGenPaths.ToAbsolute(rel);
            if (!File.Exists(abs))
            {
                error = $"Source image not found: {path}";
                return false;
            }
            if (!SupportedExtensions.Contains(Path.GetExtension(abs)))
            {
                error = $"Unsupported image type '{Path.GetExtension(abs)}'. Use .png, .jpg, .jpeg, .webp, or .gif.";
                return false;
            }
            try
            {
                RequireSize(new FileInfo(abs).Length, MimeFromExtension(Path.GetExtension(abs)));
            }
            catch (IOException)
            {
                error = "Source image could not be read within the 32 MiB local image input limit.";
                return false;
            }
            catch (UnauthorizedAccessException)
            {
                error = "Source image could not be inspected.";
                return false;
            }
            absPath = abs;
            return true;
        }

        /// <summary>
        /// Read a local image and return a "data:image/&lt;mime&gt;;base64,..." URI. Throws
        /// <see cref="NotSupportedException"/> for an unsupported extension.
        /// </summary>
        public static string ToDataUri(string absPath)
        {
            if (!AssetGenPaths.TryGetAssetsRelativePath(absPath, out string rel))
                throw new UnauthorizedAccessException("image_path must point to a file under the project's Assets folder.");
            absPath = AssetGenPaths.ToAbsolute(rel);
            string mime = MimeFromExtension(Path.GetExtension(absPath));
            RequireSize(new FileInfo(absPath).Length, mime);
            // Recheck the opened file: it may have changed after handler validation. FileShare.Read
            // also excludes writers on platforms that enforce sharing; the bounded read is still
            // required for platforms that allow an existing writer to grow the file.
            using var stream = new FileStream(absPath, FileMode.Open, FileAccess.Read, FileShare.Read);
            return ReadDataUri(stream, mime);
        }

        private static string ReadDataUri(Stream stream, string mime)
        {
            long length = stream.Length;
            RequireSize(length, mime);
            byte[] bytes = new byte[(int)length]; // RequireSize bounds the conversion and allocation.
            int offset = 0;
            while (offset < bytes.Length)
            {
                int read = stream.Read(bytes, offset, bytes.Length - offset);
                if (read == 0)
                    throw new IOException("Source image changed while being read.");
                offset += read;
            }
            // Probe only one byte instead of reading a growing file into an expanding buffer.
            if (stream.ReadByte() != -1)
                throw new IOException("Source image changed while being read.");
            return "data:" + mime + ";base64," + Convert.ToBase64String(bytes);
        }

        private static void RequireSize(long length, string mime)
        {
            if (length < 0 || length > MaxImageBytes)
                throw new IOException(SizeError);
            long encodedLength = ((length + 2) / 3) * 4 + "data:".Length + mime.Length + ";base64,".Length;
            if (encodedLength > MaxDataUriBytes)
                throw new IOException(SizeError);
        }

        private static string MimeFromExtension(string ext)
        {
            switch ((ext ?? string.Empty).ToLowerInvariant())
            {
                case ".png":
                    return "image/png";
                case ".jpg":
                case ".jpeg":
                    return "image/jpeg";
                case ".webp":
                    return "image/webp";
                case ".gif":
                    return "image/gif";
                default:
                    throw new NotSupportedException($"Unsupported image type '{ext}' for image input. Use .png, .jpg, .jpeg, .webp, or .gif.");
            }
        }
    }
}
