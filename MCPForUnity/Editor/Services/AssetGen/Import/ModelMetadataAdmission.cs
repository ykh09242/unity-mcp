using System;
using System.IO;
using System.Text;
using System.Threading;
using Newtonsoft.Json.Linq;

namespace MCPForUnity.Editor.Services.AssetGen.Import
{
    /// <summary>Admits glTF JSON and embedded image work before any installed importer can decode it.</summary>
    internal static class ModelMetadataAdmission
    {
        internal static void File(string path, ImageAdmission images = null, bool externalImagesAlreadyAdmitted = false)
        {
            string extension = Path.GetExtension(path).ToLowerInvariant();
            if (extension != ".gltf" && extension != ".glb")
                return;
            using var stream = System.IO.File.OpenRead(path);
            Inspect(
                stream,
                stream.Length,
                extension,
                images ?? new ImageAdmission(),
                relative => System.IO.File.OpenRead(MCPForUnity.Runtime.Helpers.SafePathUtility.ResolveWithinRoot(Path.GetDirectoryName(path), relative)),
                externalImagesAlreadyAdmitted
            );
        }

        internal static void Inspect(
            Stream stream,
            long length,
            string extension,
            ImageAdmission images,
            Func<string, Stream> openResource = null,
            bool externalImagesAlreadyAdmitted = false,
            CancellationToken cancellationToken = default
        )
        {
            cancellationToken.ThrowIfCancellationRequested();
            bool glb = extension.Equals(".glb", StringComparison.OrdinalIgnoreCase);
            long jsonOffset = 0,
                jsonLength = length,
                binaryOffset = 0,
                binaryLength = 0;
            if (glb)
            {
                using var reader = new BinaryReader(stream, Encoding.UTF8, true);
                if (length < 20 || reader.ReadUInt32() != 0x46546c67 || reader.ReadUInt32() != 2 || reader.ReadUInt32() != length)
                    throw new InvalidDataException("Model admission requires a valid GLB 2.0 header.");
                jsonLength = reader.ReadUInt32();
                if (reader.ReadUInt32() != 0x4e4f534a || jsonLength % 4 != 0 || jsonLength > length - 20)
                    throw new InvalidDataException("Model admission requires a bounded GLB JSON chunk.");
                jsonOffset = 20;
            }
            if (jsonLength <= 0 || jsonLength > 64 * 1024 * 1024)
                throw new InvalidDataException("Model JSON exceeds the 64 MiB metadata limit.");
            string text;
            using (var slice = new Slice(stream, jsonOffset, jsonLength))
            using (var reader = new StreamReader(slice, new UTF8Encoding(false, true), false, 4096))
                text = reader.ReadToEnd();
            JObject document = BoundedJson.ParseObject(text, BoundedJson.ModelLimits());
            if (glb)
            {
                long offset = jsonOffset + jsonLength;
                for (int chunks = 0; offset < length; chunks++)
                {
                    if (chunks > 65536 || length - offset < 8)
                        throw new InvalidDataException("Invalid GLB chunk table.");
                    stream.Position = offset;
                    using var reader = new BinaryReader(stream, Encoding.UTF8, true);
                    uint bytes = reader.ReadUInt32(),
                        type = reader.ReadUInt32();
                    if (bytes % 4 != 0 || bytes > length - offset - 8 || type == 0x4e4f534a)
                        throw new InvalidDataException("Invalid GLB chunk size/type.");
                    if (type == 0x004e4942)
                    {
                        if (binaryOffset != 0 || chunks != 0)
                            throw new InvalidDataException("Invalid GLB binary chunk order.");
                        binaryOffset = offset + 8;
                        binaryLength = bytes;
                    }
                    offset += bytes + 8L;
                }
            }
            if (!(document["images"] is JArray resources))
                return;
            if (resources.Count > 8192)
                throw new InvalidDataException("Model image count exceeds the dependency limit.");
            foreach (JToken image in resources)
            {
                cancellationToken.ThrowIfCancellationRequested();
                string uri = image["uri"]?.Value<string>();
                if (uri != null)
                {
                    if (uri.StartsWith("data:", StringComparison.Ordinal))
                    {
                        using var data = Data(uri, out string mime);
                        images.Inspect(data, data.Length, Extension(mime), token: cancellationToken);
                    }
                    else
                    {
                        string relative = LocalUri(uri);
                        string ext = Path.GetExtension(relative);
                        if (!ImageAdmission.IsImage(ext))
                            throw new InvalidDataException("External model image has an unsupported extension.");
                        if (!externalImagesAlreadyAdmitted)
                        {
                            using Stream external = openResource?.Invoke(relative);
                            if (external == null)
                                throw new InvalidDataException("Provider model contains external images; use a self-contained GLB or a complete archive.");
                            images.Inspect(external, external.Length, ext, token: cancellationToken);
                        }
                    }
                    continue;
                }
                int index = image["bufferView"]?.Value<int>() ?? -1;
                if (!(document["bufferViews"] is JArray views) || index < 0 || index >= views.Count)
                    throw new InvalidDataException("Model image has an invalid bufferView.");
                JToken view = views[index];
                int bufferIndex = view["buffer"]?.Value<int>() ?? -1;
                if (!(document["buffers"] is JArray buffers) || bufferIndex < 0 || bufferIndex >= buffers.Count)
                    throw new InvalidDataException("Model image has an invalid buffer index.");
                long start = view["byteOffset"]?.Value<long>() ?? 0,
                    bytesInView = view["byteLength"]?.Value<long>() ?? 0;
                long declaredBuffer = buffers[bufferIndex]["byteLength"]?.Value<long>() ?? 0;
                if (start < 0 || bytesInView <= 0 || start > declaredBuffer || bytesInView > declaredBuffer - start)
                    throw new InvalidDataException("Embedded image buffer range is invalid.");
                string bufferUri = buffers[bufferIndex]["uri"]?.Value<string>();
                string imageExtension = Extension(image["mimeType"]?.Value<string>());
                if (bufferUri == null && glb && bufferIndex == 0)
                {
                    using var slice = new Slice(stream, binaryOffset + start, bytesInView, binaryOffset + binaryLength);
                    images.Inspect(slice, slice.Length, imageExtension, token: cancellationToken);
                }
                else
                {
                    using Stream buffer =
                        bufferUri != null && bufferUri.StartsWith("data:", StringComparison.Ordinal)
                            ? Data(bufferUri, out _)
                            : openResource?.Invoke(LocalUri(bufferUri));
                    if (buffer == null || !buffer.CanSeek)
                        throw new InvalidDataException("Embedded image needs a bounded seekable local buffer.");
                    using var slice = new Slice(buffer, start, bytesInView);
                    images.Inspect(slice, slice.Length, imageExtension, token: cancellationToken);
                }
            }
        }

        internal static string LocalUri(string uri)
        {
            if (string.IsNullOrWhiteSpace(uri) || uri.IndexOf(':') >= 0 || uri.IndexOf('\\') >= 0 || uri.StartsWith("/", StringComparison.Ordinal))
                throw new InvalidDataException("Model resources must be contained local paths or bounded data URIs.");
            string decoded = Uri.UnescapeDataString(uri);
            if (
                decoded.StartsWith("/", StringComparison.Ordinal)
                || decoded.IndexOfAny(new[] { ':', '\\', '%', '?', '#' }) >= 0
                || Array.IndexOf(decoded.Split('/'), "..") >= 0
            )
                throw new InvalidDataException("Model resource path contains unsafe URI syntax.");
            string normalized = string.Join("/", Array.FindAll(decoded.Split('/'), segment => segment.Length != 0 && segment != "."));
            if (string.IsNullOrWhiteSpace(normalized))
                throw new InvalidDataException("Model resource path does not name a file.");
            return normalized;
        }

        private static string Extension(string mime) =>
            mime switch
            {
                "image/png" => ".png",
                "image/jpeg" => ".jpg",
                "image/webp" => ".webp",
                "image/ktx2" => ".ktx2",
                _ => throw new InvalidDataException("Embedded model image has an unsupported MIME type; export PNG, JPEG, WebP or KTX2."),
            };

        private static Stream Data(string uri, out string mime)
        {
            int comma = uri.IndexOf(',');
            if (comma < 12 || !uri.Substring(0, comma).EndsWith(";base64", StringComparison.Ordinal))
                throw new InvalidDataException("Embedded image/buffer requires a base64 data URI.");
            mime = uri.Substring(5, comma - 5 - 7);
            return new Base64Data(uri, comma + 1);
        }

        /// <summary>Seekable base64 view with constant memory, including the image-header probe/rewind.</summary>
        private sealed class Base64Data : Stream
        {
            private readonly string data;
            private readonly int start;
            private long position;
            private readonly long length;

            internal Base64Data(string data, int start)
            {
                this.data = data;
                this.start = start;
                int characters = data.Length - start;
                if (characters == 0 || characters % 4 != 0)
                    throw new InvalidDataException("Malformed base64 model resource.");
                int padding =
                    data.EndsWith("==", StringComparison.Ordinal) ? 2
                    : data.EndsWith("=", StringComparison.Ordinal) ? 1
                    : 0;
                for (int i = start; i < data.Length - padding; i++)
                    if (Value(data[i]) < 0)
                        throw new InvalidDataException("Malformed base64 model resource.");
                length = (long)characters / 4 * 3 - padding;
            }

            private static int Value(char c) =>
                c >= 'A' && c <= 'Z' ? c - 'A'
                : c >= 'a' && c <= 'z' ? c - 'a' + 26
                : c >= '0' && c <= '9' ? c - '0' + 52
                : c == '+' ? 62
                : c == '/' ? 63
                : -1;

            public override int Read(byte[] buffer, int offset, int count)
            {
                int n = (int)Math.Min(count, length - position);
                for (int i = 0; i < n; i++, position++)
                {
                    int block = start + (int)(position / 3 * 4),
                        component = (int)(position % 3);
                    int a = Value(data[block + component]),
                        b = Value(data[block + component + 1]);
                    buffer[offset + i] = (byte)((a << (2 + component * 2)) | (b >> (4 - component * 2)));
                }
                return n;
            }

            public override bool CanRead => true;
            public override bool CanSeek => true;
            public override bool CanWrite => false;
            public override long Length => length;
            public override long Position
            {
                get => position;
                set
                {
                    if (value < 0 || value > length)
                        throw new IOException("Invalid base64 offset.");
                    position = value;
                }
            }

            public override long Seek(long offset, SeekOrigin origin)
            {
                Position =
                    origin == SeekOrigin.Begin ? offset
                    : origin == SeekOrigin.Current ? position + offset
                    : length + offset;
                return position;
            }

            public override void Flush() { }

            public override void SetLength(long value) => throw new NotSupportedException();

            public override void Write(byte[] buffer, int offset, int count) => throw new NotSupportedException();
        }

        internal sealed class Slice : Stream
        {
            private readonly Stream source;
            private readonly long start,
                length;
            private long position;

            internal Slice(Stream source, long start, long length, long end = -1)
            {
                this.source = source;
                this.start = start;
                this.length = length;
                long limit = end < 0 ? source.Length : end;
                if (!source.CanSeek || start < 0 || length < 0 || start > limit || length > limit - start || limit > source.Length)
                    throw new InvalidDataException("Image/model slice exceeds its containing buffer.");
            }

            public override int Read(byte[] buffer, int offset, int count)
            {
                source.Position = start + position;
                int n = source.Read(buffer, offset, (int)Math.Min(count, length - position));
                position += n;
                return n;
            }

            public override bool CanRead => true;
            public override bool CanSeek => true;
            public override bool CanWrite => false;
            public override long Length => length;
            public override long Position
            {
                get => position;
                set
                {
                    if (value < 0 || value > length)
                        throw new IOException("Invalid slice offset.");
                    position = value;
                }
            }

            public override long Seek(long offset, SeekOrigin origin)
            {
                Position =
                    origin == SeekOrigin.Begin ? offset
                    : origin == SeekOrigin.Current ? position + offset
                    : length + offset;
                return position;
            }

            public override void Flush() { }

            public override void SetLength(long value) => throw new NotSupportedException();

            public override void Write(byte[] buffer, int offset, int count) => throw new NotSupportedException();
        }
    }
}
