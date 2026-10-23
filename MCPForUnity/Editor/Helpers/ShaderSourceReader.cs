using System;
using System.IO;
using System.Text;

namespace MCPForUnity.Editor.Helpers
{
    /// <summary>Strict bounded Unicode reads; offsets count source bytes after the initial BOM.</summary>
    internal static class ShaderSourceReader
    {
        internal const int DefaultLimit = 65536;
        internal const int MaximumLimit = 1048576;

        internal sealed class Page
        {
            internal string Text;
            internal string EncodingName;
            internal long Size;
            internal long Next;
        }

        internal static Page Read(string path, long offset, int limit)
        {
            if (offset < 0 || limit < 4 || limit > MaximumLimit)
                throw new ArgumentOutOfRangeException(nameof(limit), "Use a nonnegative offset and a byte limit from 4 to 1048576.");
            using var stream = new FileStream(path, FileMode.Open, FileAccess.Read, FileShare.Read);
            long length = stream.Length;
            byte[] header = new byte[4];
            int headerCount = Fill(stream, header, Math.Min(header.Length, (int)Math.Min(length, 4)));
            Encoding encoding = new UTF8Encoding(false, true);
            int preamble = 0;
            int alignment = 1;
            if (headerCount >= 4 && header[0] == 0xff && header[1] == 0xfe && header[2] == 0 && header[3] == 0)
            {
                encoding = new UTF32Encoding(false, false, true);
                preamble = alignment = 4;
            }
            else if (headerCount >= 4 && header[0] == 0 && header[1] == 0 && header[2] == 0xfe && header[3] == 0xff)
            {
                encoding = new UTF32Encoding(true, false, true);
                preamble = alignment = 4;
            }
            else if (headerCount >= 3 && header[0] == 0xef && header[1] == 0xbb && header[2] == 0xbf)
                preamble = 3;
            else if (headerCount >= 2 && header[0] == 0xff && header[1] == 0xfe)
            {
                encoding = new UnicodeEncoding(false, false, true);
                preamble = alignment = 2;
            }
            else if (headerCount >= 2 && header[0] == 0xfe && header[1] == 0xff)
            {
                encoding = new UnicodeEncoding(true, false, true);
                preamble = alignment = 2;
            }
            long size = length - preamble;
            if (offset > size || offset % alignment != 0)
                throw new ArgumentOutOfRangeException(nameof(offset), "Offset must be a source character boundary at or before EOF.");
            stream.Position = preamble + offset;
            int requested = (int)Math.Min(limit, size - offset);
            byte[] bytes = new byte[requested];
            int count = Fill(stream, bytes, requested);
            if (count != requested || stream.Length != length)
                throw new IOException("Shader changed while reading; restart the read.");
            char[] chars = new char[encoding.GetMaxCharCount(count)];
            encoding.GetDecoder().Convert(bytes, 0, count, chars, 0, chars.Length, offset + count == size, out _, out int usedChars, out _);
            // A partial trailing code point stays in the decoder. Re-read those bytes
            // next time instead of returning a split surrogate or dropping source bytes.
            long next = offset + encoding.GetByteCount(chars, 0, usedChars);
            if (next == offset && offset != size)
                throw new IOException("Read made no progress; choose a character boundary and a limit of at least four bytes.");
            return new Page
            {
                Text = new string(chars, 0, usedChars),
                EncodingName = encoding.WebName,
                Size = size,
                Next = next,
            };
        }

        private static int Fill(Stream stream, byte[] bytes, int count)
        {
            int used = 0;
            while (used < count)
            {
                int read = stream.Read(bytes, used, count - used);
                if (read == 0)
                    break;
                used += read;
            }
            return used;
        }
    }
}
