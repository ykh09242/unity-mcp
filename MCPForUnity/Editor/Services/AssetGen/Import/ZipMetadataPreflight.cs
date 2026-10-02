using System;
using System.IO;
using System.Text;
using System.Threading;

namespace MCPForUnity.Editor.Services.AssetGen.Import
{
    /// <summary>Bounds ZIP metadata before ZipArchive allocates its entry collection.</summary>
    internal static class ZipMetadataPreflight
    {
        internal const long MaxDirectoryBytes = 16 * 1024 * 1024;

        // PKWARE APPNOTE 4.3.12-4.3.16: little-endian central directory and end records.
        internal static void Validate(Stream stream, int maxEntries, CancellationToken cancellationToken)
        {
            if (!stream.CanSeek || stream.Length < 22) throw new IOException("Invalid ZIP end record.");
            using var reader = new BinaryReader(stream, Encoding.UTF8, true);
            int tailLength = (int)Math.Min(stream.Length, 22 + ushort.MaxValue);
            stream.Position = stream.Length - tailLength;
            byte[] tail = reader.ReadBytes(tailLength);
            int end = -1;
            for (int i = tail.Length - 22; i >= 0; i--)
            {
                cancellationToken.ThrowIfCancellationRequested();
                if (U32(tail, i) == 0x06054b50 && i + 22 + U16(tail, i + 20) == tail.Length)
                {
                    end = i;
                    break;
                }
            }
            if (end < 0) throw new IOException("Invalid ZIP end record.");
            long endOffset = stream.Length - tailLength + end;
            if (U16(tail, end + 4) != 0 || U16(tail, end + 6) != 0)
                throw new IOException("Split ZIP archives are not supported.");
            ulong count = U16(tail, end + 10);
            ulong directorySize = U32(tail, end + 12);
            ulong directoryOffset = U32(tail, end + 16);
            ulong directoryEnd = (ulong)endOffset;
            bool zip64Required = count == ushort.MaxValue || U16(tail, end + 8) == ushort.MaxValue ||
                directorySize == uint.MaxValue || directoryOffset == uint.MaxValue;
            bool hasLocator = false;
            if (endOffset >= 20)
            {
                stream.Position = endOffset - 20;
                hasLocator = reader.ReadUInt32() == 0x07064b50;
            }
            if (hasLocator)
            {
                if (reader.ReadUInt32() != 0) throw new IOException("Split ZIP64 archives are not supported.");
                ulong zip64Offset = reader.ReadUInt64();
                if (reader.ReadUInt32() != 1 || zip64Offset > (ulong)(endOffset - 20) ||
                    (ulong)(endOffset - 20) - zip64Offset < 56)
                    throw new IOException("Invalid ZIP64 locator.");
                stream.Position = (long)zip64Offset;
                if (reader.ReadUInt32() != 0x06064b50) throw new IOException("Invalid ZIP64 end record.");
                ulong recordSize = reader.ReadUInt64();
                if (recordSize < 44 || recordSize > MaxDirectoryBytes ||
                    recordSize != (ulong)(endOffset - 20) - zip64Offset - 12)
                    throw new IOException("ZIP64 metadata exceeds its boundary.");
                reader.ReadUInt16(); // version made by
                reader.ReadUInt16(); // version needed
                if (reader.ReadUInt32() != 0 || reader.ReadUInt32() != 0)
                    throw new IOException("Split ZIP64 archives are not supported.");
                ulong diskCount = reader.ReadUInt64();
                ulong wideCount = reader.ReadUInt64();
                ulong wideSize = reader.ReadUInt64();
                ulong wideOffset = reader.ReadUInt64();
                if (diskCount != wideCount ||
                    (count != ushort.MaxValue && count != wideCount) ||
                    (U16(tail, end + 8) != ushort.MaxValue && U16(tail, end + 8) != wideCount) ||
                    (directorySize != uint.MaxValue && directorySize != wideSize) ||
                    (directoryOffset != uint.MaxValue && directoryOffset != wideOffset))
                    throw new IOException("Inconsistent ZIP64 metadata.");
                count = wideCount;
                directorySize = wideSize;
                directoryOffset = wideOffset;
                directoryEnd = zip64Offset;
            }
            else if (zip64Required || U16(tail, end + 8) != count)
                throw new IOException("Missing or inconsistent ZIP metadata.");

            if (count > (ulong)maxEntries || directorySize > MaxDirectoryBytes)
                throw new IOException("Archive exceeds the entry count or metadata size limit.");
            if (directoryOffset > directoryEnd || directorySize != directoryEnd - directoryOffset)
                throw new IOException("Invalid central directory boundary.");

            // Count actual headers too: a forged EOCD count must not bypass the allocation limit.
            stream.Position = (long)directoryOffset;
            int actual = 0;
            while ((ulong)stream.Position < directoryEnd)
            {
                cancellationToken.ThrowIfCancellationRequested();
                long start = stream.Position;
                if (directoryEnd - (ulong)start < 46 || reader.ReadUInt32() != 0x02014b50)
                    throw new IOException("Invalid central directory header.");
                if (++actual > maxEntries || (ulong)actual > count)
                    throw new IOException("Archive exceeds the entry count limit.");
                stream.Position = start + 28;
                long variableBytes = (long)reader.ReadUInt16() + reader.ReadUInt16() + reader.ReadUInt16();
                if (reader.ReadUInt16() != 0) throw new IOException("Split ZIP entries are not supported.");
                long next = start + 46 + variableBytes;
                if ((ulong)next > directoryEnd) throw new IOException("Truncated central directory entry.");
                stream.Position = next;
            }
            if ((ulong)actual != count) throw new IOException("ZIP entry count does not match its metadata.");
            stream.Position = 0;
        }

        private static uint U16(byte[] bytes, int offset)
            => (uint)(bytes[offset] | bytes[offset + 1] << 8);
        private static uint U32(byte[] bytes, int offset)
            => U16(bytes, offset) | U16(bytes, offset + 2) << 16;
    }
}
