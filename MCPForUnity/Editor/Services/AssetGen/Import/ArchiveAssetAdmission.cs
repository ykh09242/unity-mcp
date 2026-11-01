using System;
using System.Collections.Generic;
using System.IO;
using System.IO.Compression;
using System.Threading;

namespace MCPForUnity.Editor.Services.AssetGen.Import
{
    internal static class ArchiveAssetAdmission
    {
        // Admission runs on the editor thread. Even the 8 GiB archive size allowance
        // must not authorize many GiB of repeated metadata decompression in one tick.
        internal const long MaxInspectionReadBytes = 512L * 1024 * 1024;

        internal static void Validate(ZipArchive archive, ISet<string> allowed, CancellationToken cancellationToken = default, long? maxReadBytes = null)
        {
            var images = new ImageAdmission();
            var entries = new Dictionary<string, ZipArchiveEntry>(StringComparer.Ordinal);
            long totalBytes = 0;
            foreach (ZipArchiveEntry entry in archive.Entries)
            {
                cancellationToken.ThrowIfCancellationRequested();
                string name = entry.FullName.Replace('\\', '/');
                if (entry.Name.Length == 0 || !allowed.Contains(Path.GetExtension(name)))
                    continue;
                if (!entries.TryAdd(name, entry))
                    throw new InvalidDataException("Duplicate archive asset path.");
                if (entry.Length > SafeZipExtractor.MaxTotalBytes - totalBytes)
                    throw new InvalidDataException("Archive exceeds the uncompressed size limit.");
                totalBytes += entry.Length;
            }
            // Three passes cover header probing and a normal metadata scan, with 64 MiB
            // slack for small shared buffers. Reopens share this allowance; image count
            // must never multiply the archive's decompression work. Extraction has its own
            // one-pass size limit. Large mesh-only buffers require no inspection reads;
            // large image-data layouts may need re-exporting to fit the fixed scan cap.
            // The admitted 8 GiB size bounds this arithmetic.
            long readLimit = Math.Min(totalBytes * 3 + 64L * 1024 * 1024, MaxInspectionReadBytes);
            var budget = new ReadBudget(maxReadBytes.HasValue ? Math.Min(readLimit, maxReadBytes.Value) : readLimit, cancellationToken);
            // Every extracted texture is admitted, including files not referenced by the selected model.
            foreach (var item in entries)
            {
                cancellationToken.ThrowIfCancellationRequested();
                string extension = Path.GetExtension(item.Key).ToLowerInvariant();
                if (!ImageAdmission.IsImage(extension))
                    continue;
                using var input = new EntryStream(item.Value, budget);
                images.Inspect(input, input.Length, extension, token: cancellationToken);
            }
            foreach (var item in entries)
            {
                cancellationToken.ThrowIfCancellationRequested();
                string extension = Path.GetExtension(item.Key).ToLowerInvariant();
                if (extension != ".gltf" && extension != ".glb")
                    continue;
                using var input = new EntryStream(item.Value, budget);
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
                        return new EntryStream(entry, budget);
                    },
                    externalImagesAlreadyAdmitted: true,
                    cancellationToken: cancellationToken
                );
            }
        }

        private sealed class ReadBudget
        {
            private long remaining;
            private readonly CancellationToken cancellationToken;

            internal ReadBudget(long bytes, CancellationToken cancellationToken)
            {
                if (bytes < 0)
                    throw new ArgumentOutOfRangeException(nameof(bytes));
                remaining = bytes;
                this.cancellationToken = cancellationToken;
            }

            internal void Require(long bytes)
            {
                cancellationToken.ThrowIfCancellationRequested();
                if (bytes > remaining)
                    throw new InvalidDataException(
                        "Archive metadata exceeds the aggregate read budget; export metadata before image data or reduce shared-buffer rescanning."
                    );
            }

            internal void Consume(int bytes) => remaining -= bytes;
        }

        /// <summary>Seekable metadata view without buffering the uncompressed entry. Limits all decompression/rescanning work.</summary>
        private sealed class EntryStream : Stream
        {
            private readonly ZipArchiveEntry entry;
            private readonly ReadBudget budget;
            private Stream source;
            private long position,
                read;
            private readonly byte[] scratch = new byte[8192];

            internal EntryStream(ZipArchiveEntry entry, ReadBudget budget)
            {
                this.entry = entry;
                this.budget = budget;
                budget.Require(0);
                source = entry.Open();
            }

            public override int Read(byte[] buffer, int offset, int count)
            {
                int requested = (int)Math.Min(count, Length - position);
                budget.Require(requested);
                int n = source.Read(buffer, offset, requested);
                budget.Consume(n);
                budget.Require(0);
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
                    // Reject unaffordable prefixes before starting decompression, including
                    // the full prefix required by an image-signature rewind.
                    budget.Require(value < position ? value : value - position);
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
