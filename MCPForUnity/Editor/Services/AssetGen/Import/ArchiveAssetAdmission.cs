using System;
using System.Collections.Generic;
using System.IO;
using System.IO.Compression;

namespace MCPForUnity.Editor.Services.AssetGen.Import
{
    internal static class ArchiveAssetAdmission
    {
        internal static void Validate(ZipArchive archive, ISet<string> allowed)
        {
            var images = new ImageAdmission();
            var entries = new Dictionary<string, ZipArchiveEntry>(StringComparer.Ordinal);
            foreach (ZipArchiveEntry entry in archive.Entries)
            {
                string name = entry.FullName.Replace('\\', '/');
                if (entry.Name.Length == 0 || !allowed.Contains(Path.GetExtension(name)))
                    continue;
                if (!entries.TryAdd(name, entry))
                    throw new InvalidDataException("Duplicate archive asset path.");
            }
            // Every extracted texture is admitted, including files not referenced by the selected model.
            foreach (var item in entries)
            {
                string extension = Path.GetExtension(item.Key).ToLowerInvariant();
                if (!ImageAdmission.IsImage(extension))
                    continue;
                using var input = new EntryStream(item.Value);
                images.Inspect(input, input.Length, extension);
            }
            foreach (var item in entries)
            {
                string extension = Path.GetExtension(item.Key).ToLowerInvariant();
                if (extension != ".gltf" && extension != ".glb")
                    continue;
                using var input = new EntryStream(item.Value);
                ModelMetadataAdmission.Inspect(
                    input,
                    input.Length,
                    extension,
                    images,
                    relative =>
                    {
                        string parent = Path.GetDirectoryName(item.Key)?.Replace('\\', '/');
                        string key = string.IsNullOrEmpty(parent) ? relative : parent + "/" + relative;
                        if (!entries.TryGetValue(key, out var entry))
                            throw new InvalidDataException("Embedded model buffer is absent from the archive.");
                        return new EntryStream(entry);
                    },
                    externalImagesAlreadyAdmitted: true
                );
            }
        }

        /// <summary>Seekable metadata view without buffering the uncompressed entry. Limits all decompression/rescanning work.</summary>
        private sealed class EntryStream : Stream
        {
            private readonly ZipArchiveEntry entry;
            private Stream source;
            private long position,
                read;
            private readonly byte[] scratch = new byte[8192];

            internal EntryStream(ZipArchiveEntry entry)
            {
                this.entry = entry;
                source = entry.Open();
            }

            public override int Read(byte[] buffer, int offset, int count)
            {
                int n = source.Read(buffer, offset, (int)Math.Min(count, Length - position));
                position += n;
                read += n;
                if (read > Length * 3 + 64 * 1024 * 1024)
                    throw new InvalidDataException("Archive image metadata requires excessive rescanning; export metadata before image data.");
                return n;
            }

            public override bool CanRead => true;
            public override bool CanSeek => true;
            public override bool CanWrite => false;
            public override long Length => entry.Length;
            public override long Position
            {
                get => position;
                set
                {
                    if (value < 0 || value > Length)
                        throw new InvalidDataException("Archive metadata offset exceeds entry length.");
                    if (value < position)
                    {
                        source.Dispose();
                        source = entry.Open();
                        position = 0;
                    }
                    while (position < value)
                        if (Read(scratch, 0, (int)Math.Min(scratch.Length, value - position)) == 0)
                            throw new EndOfStreamException();
                }
            }

            public override long Seek(long offset, SeekOrigin origin)
            {
                Position =
                    origin == SeekOrigin.Begin ? offset
                    : origin == SeekOrigin.Current ? position + offset
                    : Length + offset;
                return position;
            }

            protected override void Dispose(bool disposing)
            {
                if (disposing)
                    source.Dispose();
                base.Dispose(disposing);
            }

            public override void Flush() { }

            public override void SetLength(long value) => throw new NotSupportedException();

            public override void Write(byte[] buffer, int offset, int count) => throw new NotSupportedException();
        }
    }
}
