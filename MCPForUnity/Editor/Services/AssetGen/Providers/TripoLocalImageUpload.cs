using System;
using System.IO;
using System.Text;
using System.Threading;
using MCPForUnity.Editor.Helpers;

namespace MCPForUnity.Editor.Services.AssetGen.Providers
{
    /// <summary>Tripo-only, bounded PNG/JPEG multipart preparation. Never decodes or rewrites images.</summary>
    internal static class TripoLocalImageUpload
    {
        internal const int MaxImageBytes = 20_000_000;
        internal const int MaxEnvelopeBytes = 4096;
        private const int MaxPathCharacters = 2048;
        private const string InputError = "Tripo local image must be an existing PNG/JPEG file under Assets, within the 20 MB upload limit.";
        private const string ReadError = "Tripo local image could not be read safely within the upload limit.";

        internal sealed class PreparedUpload : IDisposable
        {
            internal byte[] Body;
            internal readonly string ContentType;

            internal PreparedUpload(byte[] body, string contentType)
            {
                Body = body;
                ContentType = contentType;
            }

            public void Dispose() => Body = null;
        }

        internal static string RequirePath(string path)
        {
            if (string.IsNullOrWhiteSpace(path) || path.Length > MaxPathCharacters)
                throw new InvalidDataException(InputError);
            foreach (char value in path)
                if (char.IsControl(value))
                    throw new InvalidDataException(InputError);
            foreach (string segment in path.Replace('\\', '/').Split('/'))
                if (segment == "..")
                    throw new InvalidDataException(InputError);
            if (!AssetGenPaths.TryGetAssetsRelativePath(path, out string relative))
                throw new InvalidDataException(InputError);
            try
            {
                string absolute = AssetGenPaths.ToAbsolute(relative);
                if (absolute.Length > MaxPathCharacters || !File.Exists(absolute))
                    throw new InvalidDataException(InputError);
                Extension(absolute);
                RequireLength(new FileInfo(absolute).Length);
                return absolute;
            }
            catch (Exception error)
                when (error is IOException || error is UnauthorizedAccessException || error is ArgumentException || error is NotSupportedException)
            {
                throw new InvalidDataException(InputError);
            }
        }

        internal static PreparedUpload Prepare(string path, CancellationToken token)
        {
            token.ThrowIfCancellationRequested();
            string absolute = RequirePath(path);
            try
            {
                // Re-resolve immediately before opening. Existing path checks are not an atomic
                // identity lock against a hostile concurrent ancestor replacement.
                absolute = RequirePath(absolute);
                using var stream = new FileStream(absolute, FileMode.Open, FileAccess.Read, FileShare.Read);
                PreparedUpload result = ReadMultipart(stream, Extension(absolute), token);
                try
                {
                    token.ThrowIfCancellationRequested();
                    if (!string.Equals(RequirePath(absolute), absolute, StringComparison.Ordinal))
                        throw new InvalidDataException(ReadError);
                    return result;
                }
                catch
                {
                    result.Dispose();
                    throw;
                }
            }
            catch (Exception error)
                when (error is IOException || error is UnauthorizedAccessException || error is ArgumentException || error is NotSupportedException)
            {
                throw new InvalidDataException(ReadError);
            }
        }

        // Stream seam exercises exact-length, truncation/growth and cancellation without a race
        // or a filesystem mutation. One pre-sized buffer holds both envelope and original bytes.
        internal static PreparedUpload ReadMultipart(Stream stream, string extension, CancellationToken token)
        {
            token.ThrowIfCancellationRequested();
            long length = stream.Length;
            RequireLength(length);
            bool png = extension == ".png";
            if (!png && extension != ".jpg" && extension != ".jpeg")
                throw new InvalidDataException(InputError);
            string boundary = "unitymcp_" + Guid.NewGuid().ToString("N");
            string mime = png ? "image/png" : "image/jpeg";
            string filename = png ? "image.png" : "image.jpg";
            byte[] prefix = Encoding.ASCII.GetBytes(
                "--" + boundary + "\r\nContent-Disposition: form-data; name=\"file\"; filename=\"" + filename + "\"\r\nContent-Type: " + mime + "\r\n\r\n"
            );
            byte[] suffix = Encoding.ASCII.GetBytes("\r\n--" + boundary + "--\r\n");
            if (prefix.Length + suffix.Length > MaxEnvelopeBytes)
                throw new InvalidDataException(ReadError);
            byte[] body = new byte[checked(prefix.Length + (int)length + suffix.Length)];
            Buffer.BlockCopy(prefix, 0, body, 0, prefix.Length);
            int offset = 0;
            while (offset < length)
            {
                token.ThrowIfCancellationRequested();
                int read = stream.Read(body, prefix.Length + offset, Math.Min(64 * 1024, (int)length - offset));
                if (read == 0)
                    throw new InvalidDataException(ReadError);
                offset += read;
            }
            token.ThrowIfCancellationRequested();
            if (stream.ReadByte() != -1)
                throw new InvalidDataException(ReadError);
            if (png)
            {
                byte[] signature = { 137, 80, 78, 71, 13, 10, 26, 10 };
                if (length < signature.Length)
                    throw new InvalidDataException(InputError);
                for (int index = 0; index < signature.Length; index++)
                    if (body[prefix.Length + index] != signature[index])
                        throw new InvalidDataException(InputError);
            }
            else if (length < 3 || body[prefix.Length] != 0xff || body[prefix.Length + 1] != 0xd8 || body[prefix.Length + 2] != 0xff)
                throw new InvalidDataException(InputError);
            Buffer.BlockCopy(suffix, 0, body, prefix.Length + (int)length, suffix.Length);
            token.ThrowIfCancellationRequested();
            return new PreparedUpload(body, "multipart/form-data; boundary=" + boundary);
        }

        private static void RequireLength(long length)
        {
            if (length <= 0 || length > MaxImageBytes)
                throw new InvalidDataException(InputError);
        }

        private static string Extension(string path)
        {
            string extension = Path.GetExtension(path).ToLowerInvariant();
            if (extension != ".png" && extension != ".jpg" && extension != ".jpeg")
                throw new InvalidDataException(InputError);
            return extension;
        }
    }
}
