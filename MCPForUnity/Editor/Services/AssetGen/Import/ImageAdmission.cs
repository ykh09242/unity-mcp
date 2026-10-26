using System;
using System.Collections.Generic;
using System.IO;
using System.Text;
using System.Threading;

namespace MCPForUnity.Editor.Services.AssetGen.Import
{
    /// <summary>
    /// Bounded metadata/framing inspection, never a pixel decoder. The estimate includes two buffers
    /// of at least RGBA8 per pixel (and source precision/channels when larger). It is admission policy,
    /// not a guarantee about allocations or codec correctness inside an installed native importer.
    /// </summary>
    internal sealed class ImageAdmission
    {
        internal sealed class Limits
        {
            internal int MaxSide = 32768;
            internal int MaxFrames = 4096;
            internal long MaxImageBytes = Setting("MCPFORUNITY_ASSETGEN_IMAGE_WORK_GIB", 2);
            internal long MaxOperationBytes = Setting("MCPFORUNITY_ASSETGEN_IMAGE_TOTAL_GIB", 8);

            private static long Setting(string key, int fallback) =>
                (long.TryParse(Environment.GetEnvironmentVariable(key), out long value) && value >= 1 && value <= 1024 ? value : fallback) * 1024 * 1024 * 1024;
        }

        private readonly Limits limits;
        private long total;
        internal long WorkBytes => total;

        internal ImageAdmission(Limits limits = null) => this.limits = limits ?? new Limits();

        internal static bool IsImage(string extension) =>
            Array.IndexOf(
                new[] { ".png", ".jpg", ".jpeg", ".tga", ".bmp", ".tif", ".tiff", ".webp", ".exr", ".hdr", ".ktx", ".ktx2", ".basis", ".dds" },
                extension.ToLowerInvariant()
            ) >= 0;

        internal void File(string path, CancellationToken token = default)
        {
            if (Directory.Exists(path))
                throw Bad("image path is a directory; provide an image file");
            using var stream = new FileStream(path, FileMode.Open, FileAccess.Read, FileShare.Read);
            Inspect(stream, stream.Length, Path.GetExtension(path), null, token);
        }

        internal string Inspect(Stream stream, long length, string extension, Func<Stream> reopen = null, CancellationToken token = default)
        {
            using var reader = new Input(stream, length, reopen, token);
            try
            {
                byte[] signature = reader.Bytes((int)Math.Min(12, length));
                reader.At(0);
                string actual = ImageResultFormat.FromBytes(signature);
                string ext = extension?.TrimStart('.').ToLowerInvariant();
                if (ext == "jpeg")
                    ext = "jpg";
                if (ext == "tiff")
                    ext = "tif";
                if (actual != null && actual != ext)
                    throw Bad("signature does not match the declared image extension");
                long work;
                switch (ext)
                {
                    case "png":
                        work = Png(reader);
                        break;
                    case "jpg":
                        work = Jpeg(reader);
                        break;
                    case "webp":
                        work = Webp(reader);
                        break;
                    case "bmp":
                        work = Bmp(reader);
                        break;
                    case "tga":
                        work = Tga(reader);
                        break;
                    case "hdr":
                        work = Hdr(reader);
                        break;
                    case "exr":
                        work = Exr(reader);
                        break;
                    case "tif":
                        work = Tiff(reader);
                        break;
                    case "ktx":
                        work = Ktx(reader, false);
                        break;
                    case "ktx2":
                        work = Ktx(reader, true);
                        break;
                    case "dds":
                        work = Dds(reader);
                        break;
                    case "basis":
                        work = Basis(reader);
                        break;
                    default:
                        throw Bad("unsupported image metadata format; export PNG, JPEG, TIFF, TGA, HDR or EXR");
                }
                if (work <= 0 || work > limits.MaxImageBytes)
                    throw Bad(
                        "decoded work exceeds the per-image budget; reduce dimensions/frames or configure MCPFORUNITY_ASSETGEN_IMAGE_WORK_GIB in the Editor environment"
                    );
                if (work > limits.MaxOperationBytes - total)
                    throw Bad(
                        "aggregate texture work exceeds the operation budget; split the asset or configure MCPFORUNITY_ASSETGEN_IMAGE_TOTAL_GIB in the Editor environment"
                    );
                total += work;
                return ext;
            }
            catch (OverflowException)
            {
                throw Bad("image dimensions or work estimate overflow");
            }
            catch (EndOfStreamException)
            {
                throw Bad("truncated image header or container");
            }
        }

        private static InvalidDataException Bad(string reason) => new("Image admission rejected: " + reason + ".");

        private static void Require(bool condition, string reason)
        {
            if (!condition)
                throw Bad(reason);
        }

        private long Pixels(long width, long height, long bytes = 4, long frames = 1)
        {
            Require(width > 0 && height > 0 && width <= limits.MaxSide && height <= limits.MaxSide, "invalid dimensions or side exceeds 32768 pixels");
            Require(frames > 0 && frames <= limits.MaxFrames && bytes > 0 && bytes <= 256, "invalid frame count or sample precision");
            long work = checked(width * height * Math.Max(4, bytes) * frames * 2);
            Require(work <= limits.MaxImageBytes, "declared pixel work exceeds the per-image budget (MCPFORUNITY_ASSETGEN_IMAGE_WORK_GIB)");
            return work;
        }

        private long Png(Input r)
        {
            Require(Convert.ToBase64String(r.Bytes(8)) == "iVBORw0KGgo=", "invalid PNG signature");
            long width = 0,
                height = 0,
                bytes = 0,
                frames = 1;
            int controls = 0;
            bool data = false;
            bool header = false;
            bool animated = false;
            for (int chunks = 0; chunks < 65536; chunks++)
            {
                uint size = r.U32(true);
                string kind = r.Text(4);
                long end = r.Range(size + 4L);
                Require(header || kind == "IHDR", "PNG must start with IHDR");
                if (kind == "IHDR")
                {
                    Require(!header && size == 13, "invalid or duplicate PNG IHDR");
                    width = r.U32(true);
                    height = r.U32(true);
                    int depth = r.Byte();
                    int color = r.Byte();
                    bool valid =
                        color == 0 ? depth == 1 || depth == 2 || depth == 4 || depth == 8 || depth == 16
                        : color == 3 ? depth == 1 || depth == 2 || depth == 4 || depth == 8
                        : (color == 2 || color == 4 || color == 6) && (depth == 8 || depth == 16);
                    Require(valid && r.Byte() == 0 && r.Byte() == 0 && r.Byte() <= 1, "unsupported PNG sample/header fields");
                    bytes = 4 * Math.Max(1, depth / 8);
                    Pixels(width, height, bytes);
                    header = true;
                }
                else if (kind == "acTL")
                {
                    Require(size == 8 && !data && !animated, "invalid APNG frame header");
                    animated = true;
                    frames = r.U32(true);
                    r.U32(true);
                    Pixels(width, height, bytes, frames);
                }
                else if (kind == "fcTL")
                {
                    Require(animated && size == 26 && ++controls <= frames, "invalid APNG frame count");
                    r.U32(true);
                    long w = r.U32(true),
                        h = r.U32(true),
                        x = r.U32(true),
                        y = r.U32(true);
                    Require(w > 0 && h > 0 && w <= width - x && h <= height - y, "APNG frame exceeds its canvas");
                }
                else if (kind == "IDAT")
                {
                    Require(size > 0 || data, "empty PNG image data");
                    data = true;
                }
                else if (kind == "IEND")
                {
                    Require(size == 0 && data && (!animated || controls == frames), "missing PNG data or APNG frames");
                    r.At(end);
                    Require(r.Position == r.Length, "content after PNG IEND");
                    return Pixels(width, height, bytes, frames);
                }
                r.At(end);
            }
            throw Bad("PNG exceeds the chunk count limit");
        }

        private long Jpeg(Input r)
        {
            Require(r.U16(true) == 0xffd8, "invalid JPEG signature");
            long work = 0;
            int scans = 0;
            int marker = -1;
            for (int segments = 0; segments < 65536; segments++)
            {
                if (marker < 0)
                {
                    Require(r.Byte() == 255, "invalid JPEG marker");
                    do
                    {
                        marker = r.Byte();
                    } while (marker == 255);
                }
                if (marker == 0xd9)
                {
                    Require(work > 0 && scans > 0 && r.Position == r.Length, "JPEG is incomplete or contains multiple images");
                    return work;
                }
                Require(marker != 0xd8 && marker != 0 && !(marker >= 0xd0 && marker <= 0xd7), "unexpected JPEG marker");
                int size = r.U16(true);
                Require(size >= 2, "invalid JPEG segment length");
                long end = r.Range(size - 2);
                if (marker >= 0xc0 && marker <= 0xcf && marker != 0xc4 && marker != 0xc8 && marker != 0xcc)
                {
                    Require(
                        work == 0 && (marker == 0xc0 || marker == 0xc1 || marker == 0xc2),
                        "unsupported hierarchical/lossless JPEG; export baseline or progressive JPEG"
                    );
                    int precision = r.Byte();
                    int height = r.U16(true),
                        width = r.U16(true),
                        channels = r.Byte();
                    Require((precision == 8 || precision == 12) && channels >= 1 && channels <= 4 && size == 8 + 3 * channels, "invalid JPEG frame metadata");
                    work = Pixels(width, height, 4 * ((precision + 7) / 8));
                }
                if (marker == 0xe2 && size >= 6)
                    Require(r.Text(4) != "MPF\0", "multi-picture JPEG requires export to individual images");
                bool scan = marker == 0xda;
                r.At(end);
                marker = -1;
                if (scan)
                {
                    Require(work > 0 && ++scans <= 256, "invalid JPEG scan count");
                    while (true)
                    {
                        if (r.Byte() != 255)
                            continue;
                        do
                        {
                            marker = r.Byte();
                        } while (marker == 255);
                        if (marker == 0 || marker >= 0xd0 && marker <= 0xd7)
                        {
                            marker = -1;
                            continue;
                        }
                        break;
                    }
                }
            }
            throw Bad("JPEG exceeds the segment count limit");
        }

        private long Webp(Input r)
        {
            Require(r.Text(4) == "RIFF" && r.U32() + 8L == r.Length && r.Text(4) == "WEBP", "invalid WebP RIFF header");
            long width = 0,
                height = 0;
            int frames = 0;
            bool image = false,
                animation = false;
            for (int chunks = 0; r.Position < r.Length && chunks < 65536; chunks++)
            {
                string type = r.Text(4);
                uint size = r.U32();
                long end = r.Range(size + (size & 1));
                if (type == "VP8X")
                {
                    Require(size == 10 && width == 0, "invalid WebP extended header");
                    int flags = r.Byte();
                    animation = (flags & 2) != 0;
                    r.Skip(3);
                    width = r.U24() + 1L;
                    height = r.U24() + 1L;
                    Pixels(width, height);
                }
                else if (type == "ANMF")
                {
                    Require(animation && size >= 24 && ++frames <= limits.MaxFrames, "invalid WebP animation frame");
                    long x = r.U24() * 2L,
                        y = r.U24() * 2L,
                        w = r.U24() + 1L,
                        h = r.U24() + 1L;
                    Require(w <= width - x && h <= height - y, "WebP animation frame exceeds canvas");
                    Pixels(width, height, 4, frames);
                }
                else if (type == "VP8 " || type == "VP8L")
                {
                    Require(!image && !animation, "unexpected WebP image chunk");
                    image = true;
                    long w,
                        h;
                    if (type == "VP8 ")
                    {
                        Require(size >= 10 && (r.Byte() & 1) == 0, "invalid WebP keyframe");
                        r.Skip(2);
                        Require(r.U24() == 0x2a019d, "invalid WebP frame signature");
                        w = r.U16() & 0x3fff;
                        h = r.U16() & 0x3fff;
                    }
                    else
                    {
                        Require(size >= 5 && r.Byte() == 0x2f, "invalid WebP lossless header");
                        uint bits = r.U32();
                        Require(bits >> 29 == 0, "unsupported WebP version");
                        w = (bits & 0x3fff) + 1;
                        h = ((bits >> 14) & 0x3fff) + 1;
                    }
                    Require(width == 0 || width == w && height == h, "WebP canvas/frame mismatch");
                    width = w;
                    height = h;
                }
                r.At(end);
            }
            Require(r.Position == r.Length && (animation ? frames > 0 : image), "missing WebP image/frame data");
            return Pixels(width, height, 4, Math.Max(1, frames));
        }

        private long Bmp(Input r)
        {
            Require(r.Text(2) == "BM", "invalid BMP signature");
            uint size = r.U32();
            r.Skip(4);
            uint offset = r.U32();
            uint dib = r.U32();
            Require(size == r.Length && (dib == 12 || dib >= 40 && dib <= 124), "invalid BMP file/header size");
            long width,
                height;
            int bits;
            if (dib == 12)
            {
                width = r.U16();
                height = r.U16();
                Require(r.U16() == 1, "invalid BMP planes");
                bits = r.U16();
            }
            else
            {
                width = r.I32();
                height = Math.Abs((long)r.I32());
                Require(r.U16() == 1, "invalid BMP planes");
                bits = r.U16();
                uint compression = r.U32();
                Require(compression <= 3 || compression == 6, "embedded JPEG/PNG BMP is unsupported; export its image directly");
                uint imageSize = r.U32();
                r.Bounds(offset, imageSize);
                if (compression == 0 || compression == 3 || compression == 6)
                    r.Bounds(offset, checked(((width * bits + 31) / 32) * 4 * height));
            }
            Require(bits == 1 || bits == 4 || bits == 8 || bits == 16 || bits == 24 || bits == 32, "unsupported BMP bit depth");
            Require(offset >= 14 + dib && offset < r.Length, "invalid BMP pixel offset");
            return Pixels(width, height);
        }

        private long Tga(Input r)
        {
            byte[] h = r.Bytes(18);
            int type = h[2];
            int bits = h[16];
            Require(type == 1 || type == 2 || type == 3 || type == 9 || type == 10 || type == 11, "unsupported TGA image type");
            Require(h[1] <= 1 && (bits == 8 || bits == 16 || bits == 24 || bits == 32), "invalid TGA sample metadata");
            int width = U16(h, 12),
                height = U16(h, 14);
            long work = Pixels(width, height);
            r.Skip(h[0] + (long)h[1] * U16(h, 5) * ((h[7] + 7) / 8));
            if (type < 9)
                r.Skip((long)width * height * ((bits + 7) / 8));
            else
            {
                long remaining = (long)width * height;
                while (remaining > 0)
                {
                    int packet = r.Byte();
                    int count = (packet & 127) + 1;
                    Require(count <= remaining, "TGA run exceeds pixel count");
                    r.Skip(((packet & 128) != 0 ? 1 : count) * ((bits + 7) / 8));
                    remaining -= count;
                }
            }
            return work;
        }

        private long Hdr(Input r)
        {
            string magic = r.Line();
            Require(magic == "#?RADIANCE" || magic == "#?RGBE", "invalid Radiance HDR signature");
            bool format = false;
            int lines = 0;
            string line;
            while ((line = r.Line()).Length != 0)
            {
                Require(++lines <= 4096, "HDR header exceeds line limit");
                if (line == "FORMAT=32-bit_rle_rgbe" || line == "FORMAT=32-bit_rle_xyze")
                    format = true;
            }
            string[] words = r.Line().Split(new[] { ' ' }, StringSplitOptions.RemoveEmptyEntries);
            Require(
                format && words.Length == 4 && (words[0] == "-Y" || words[0] == "+Y") && (words[2] == "+X" || words[2] == "-X"),
                "unsupported HDR resolution/orientation header"
            );
            Require(int.TryParse(words[1], out int height) && int.TryParse(words[3], out _), "invalid HDR dimensions");
            int width = int.Parse(words[3]);
            long work = Pixels(width, height, 16);
            long start = r.Position;
            byte[] first = r.Bytes(4);
            if (width >= 8 && width <= 32767 && first[0] == 2 && first[1] == 2 && (first[2] & 128) == 0)
            {
                for (int row = 0; row < height; row++)
                {
                    byte[] header = row == 0 ? first : r.Bytes(4);
                    Require(header[0] == 2 && header[1] == 2 && (header[2] << 8 | header[3]) == width, "invalid HDR scanline header");
                    for (int channel = 0; channel < 4; channel++)
                    {
                        int remaining = width;
                        while (remaining > 0)
                        {
                            int packet = r.Byte();
                            int count = packet > 128 ? packet - 128 : packet;
                            Require(count > 0 && count <= remaining, "HDR run exceeds scanline");
                            r.Skip(packet > 128 ? 1 : count);
                            remaining -= count;
                        }
                    }
                }
            }
            else
            {
                r.At(start);
                long remaining = (long)width * height;
                int shift = 0;
                bool previous = false;
                while (remaining > 0)
                {
                    byte[] pixel = r.Bytes(4);
                    if (pixel[0] == 1 && pixel[1] == 1 && pixel[2] == 1)
                    {
                        Require(previous && shift <= 24, "invalid old HDR run");
                        long count = (long)pixel[3] << shift;
                        Require(count > 0 && count <= remaining, "old HDR run exceeds image");
                        remaining -= count;
                        shift += 8;
                    }
                    else
                    {
                        remaining--;
                        previous = true;
                        shift = 0;
                    }
                }
            }
            Require(r.Position == r.Length, "HDR pixel payload length mismatch");
            return work;
        }

        private long Exr(Input r)
        {
            Require(r.U32() == 20000630, "invalid EXR signature");
            uint version = r.U32();
            Require(
                (version & 255) == 2 && (version & ~(uint)(255 | 512 | 1024)) == 0,
                "deep or multipart EXR cannot be bounded by flat pixels; export a flat single-part EXR"
            );
            bool tiled = (version & 512) != 0;
            long width = 0,
                height = 0,
                dataWidth = 0,
                dataHeight = 0,
                pixelBytes = 0;
            int compression = -1;
            uint tileX = 0,
                tileY = 0;
            int tileMode = 0;
            var seen = new HashSet<string>(StringComparer.Ordinal);
            for (int attributes = 0; ; attributes++)
            {
                string name = r.ZeroString(255);
                if (name.Length == 0)
                    break;
                Require(attributes < 4096 && seen.Add(name), "too many or duplicate EXR attributes");
                string type = r.ZeroString(255);
                uint size = r.U32();
                Require(size <= 16 * 1024 * 1024, "EXR attribute exceeds metadata budget");
                long end = r.Range(size);
                if (name == "dataWindow" || name == "displayWindow")
                {
                    Require(type == "box2i" && size == 16, "invalid EXR image window");
                    int x = r.I32(),
                        y = r.I32();
                    long w = (long)r.I32() - x + 1,
                        h = (long)r.I32() - y + 1;
                    Pixels(w, h, 4);
                    width = Math.Max(width, w);
                    height = Math.Max(height, h);
                    if (name == "dataWindow")
                    {
                        dataWidth = w;
                        dataHeight = h;
                    }
                }
                else if (name == "channels")
                {
                    Require(type == "chlist", "invalid EXR channels");
                    int channels = 0;
                    while (r.Position < end && r.ZeroString(255).Length != 0)
                    {
                        Require(++channels <= 64, "EXR exceeds channel count limit");
                        uint sample = r.U32();
                        Require(sample <= 2, "unknown EXR pixel type");
                        pixelBytes += sample == 1 ? 2 : 4;
                        r.Skip(4);
                        Require(r.U32() > 0 && r.U32() > 0, "invalid EXR channel sampling");
                    }
                    Require(r.Position == end, "invalid EXR channel list");
                }
                else if (name == "compression")
                {
                    Require(size == 1, "invalid EXR compression attribute");
                    compression = r.Byte();
                    Require(compression <= 9, "unsupported EXR compression");
                }
                else if (name == "tiles")
                {
                    Require(type == "tiledesc" && size == 9, "invalid EXR tiles");
                    tileX = r.U32();
                    tileY = r.U32();
                    tileMode = r.Byte();
                }
                r.At(end);
            }
            Require(seen.Contains("dataWindow") && seen.Contains("displayWindow") && pixelBytes > 0 && compression >= 0, "missing EXR image metadata");
            long work = Pixels(width, height, Math.Max(16, pixelBytes));
            long chunks;
            if (tiled)
            {
                Require(
                    tileX > 0 && tileY > 0 && tileX <= limits.MaxSide && tileY <= limits.MaxSide && (tileMode & 15) <= 2 && (tileMode >> 4) <= 1,
                    "invalid EXR tile layout"
                );
                int mode = tileMode & 15;
                bool up = (tileMode >> 4) != 0;
                int LevelCount(long value)
                {
                    int count = 1;
                    while (value > 1)
                    {
                        value = up ? (value + 1) / 2 : value / 2;
                        count++;
                    }
                    return count;
                }
                long LevelSize(long value, int level)
                {
                    for (int i = 0; i < level; i++)
                        value = up ? (value + 1) / 2 : value / 2;
                    return Math.Max(1, value);
                }
                int lx = LevelCount(dataWidth),
                    ly = LevelCount(dataHeight);
                chunks = 0;
                work = 0;
                int xCount =
                    mode == 0 ? 1
                    : mode == 1 ? Math.Max(lx, ly)
                    : lx;
                int yCount = mode == 2 ? ly : 1;
                for (int x = 0; x < xCount; x++)
                for (int y = 0; y < yCount; y++)
                {
                    long w = LevelSize(dataWidth, x),
                        h = LevelSize(dataHeight, mode == 1 ? x : y);
                    chunks = checked(chunks + ((w + tileX - 1) / tileX) * ((h + tileY - 1) / tileY));
                    work = checked(work + Pixels(w, h, Math.Max(16, pixelBytes)));
                }
                work = Math.Max(work, Pixels(width, height, Math.Max(16, pixelBytes)));
            }
            else
            {
                int rows =
                    compression == 3 || compression == 5 ? 16
                    : compression == 4 || compression == 6 || compression == 7 || compression == 8 ? 32
                    : compression == 9 ? 256
                    : 1;
                chunks = (dataHeight + rows - 1) / rows;
            }
            Require(chunks > 0 && chunks <= 1000000, "EXR chunk table exceeds metadata limit");
            long tableEnd = r.Range(chunks * 8);
            var offsets = new long[chunks];
            for (int i = 0; i < offsets.Length; i++)
            {
                ulong offset = r.U64();
                Require(offset <= long.MaxValue, "EXR chunk offset overflow");
                offsets[i] = (long)offset;
            }
            Array.Sort(offsets);
            long previousEnd = tableEnd;
            foreach (long offset in offsets)
            {
                Require(offset >= previousEnd, "overlapping EXR chunks or header");
                r.At(offset);
                r.Skip(tiled ? 16 : 4);
                uint size = r.U32();
                Require(size > 0, "empty EXR pixel chunk");
                previousEnd = r.Range(size);
            }
            return work;
        }

        private long Tiff(Input r)
        {
            string order = r.Text(2);
            Require(order == "II" || order == "MM", "invalid TIFF signature");
            bool big = order == "MM";
            int version = r.U16(big);
            Require(version == 42 || version == 43, "invalid TIFF version");
            bool wide = version == 43;
            if (wide)
                Require(r.U16(big) == 8 && r.U16(big) == 0, "invalid BigTIFF offset metadata");
            long Offset()
            {
                ulong value = wide ? r.U64(big) : r.U32(big);
                Require(value <= long.MaxValue, "TIFF offset overflow");
                return (long)value;
            }
            var pending = new Stack<(long offset, int depth)>();
            pending.Push((Offset(), 0));
            long totalWork = 0;
            var seen = new HashSet<long>();
            int pages = 0;
            while (pending.Count > 0)
            {
                var page = pending.Pop();
                if (page.offset == 0)
                    continue;
                Require(++pages <= limits.MaxFrames && page.depth <= 32 && seen.Add(page.offset), "TIFF page cycle, depth or page limit exceeded");
                r.At(page.offset);
                long count = wide ? Offset() : r.U16(big);
                Require(count <= 4096, "TIFF IFD exceeds tag limit");
                r.Range(count * (wide ? 20L : 12L) + (wide ? 8 : 4));
                var tags = new Dictionary<int, (int type, long count, long field)>();
                for (int i = 0; i < count; i++)
                {
                    int tag = r.U16(big),
                        type = r.U16(big);
                    long values = Offset();
                    long field = r.Position;
                    r.Skip(wide ? 8 : 4);
                    Require(!tags.ContainsKey(tag), "duplicate TIFF tag");
                    tags.Add(tag, (type, values, field));
                }
                long next = Offset();
                if (next != 0)
                    pending.Push((next, page.depth));
                long[] Values(int tag, long fallback, int maximum = 65536)
                {
                    if (!tags.TryGetValue(tag, out var entry))
                        return new[] { fallback };
                    Require(
                        (entry.type == 3 || entry.type == 4 || entry.type == 13 || entry.type == 16 || entry.type == 18)
                            && entry.count > 0
                            && entry.count <= maximum,
                        "unsupported TIFF numeric tag or count"
                    );
                    int bytes =
                        entry.type == 3 ? 2
                        : entry.type == 16 || entry.type == 18 ? 8
                        : 4;
                    r.At(entry.field);
                    long start = entry.count * bytes <= (wide ? 8 : 4) ? entry.field : Offset();
                    r.At(start);
                    r.Range(entry.count * bytes);
                    var values = new long[entry.count];
                    for (int i = 0; i < values.Length; i++)
                    {
                        ulong value =
                            bytes == 2 ? (ulong)r.U16(big)
                            : bytes == 4 ? r.U32(big)
                            : r.U64(big);
                        Require(value <= long.MaxValue, "TIFF numeric overflow");
                        values[i] = (long)value;
                    }
                    return values;
                }
                long width = Values(256, 0, 1)[0],
                    height = Values(257, 0, 1)[0],
                    channels = Values(277, 1, 1)[0];
                Require(channels >= 1 && channels <= 64, "unsupported TIFF channel count");
                if (tags.ContainsKey(330))
                    foreach (long child in Values(330, 0, 4096))
                    {
                        Require(pending.Count < limits.MaxFrames, "TIFF sub-IFD count limit exceeded");
                        pending.Push((child, page.depth + 1));
                    }
                long[] depths = Values(258, 1, 64);
                long precision = 0,
                    largestSample = 0;
                Require(depths.Length == 1 || depths.Length == channels, "TIFF BitsPerSample does not match channels");
                foreach (long bits in depths)
                {
                    Require(bits >= 1 && bits <= 64, "unsupported TIFF bit depth");
                    precision += (bits + 7) / 8;
                    largestSample = Math.Max(largestSample, (bits + 7) / 8);
                }
                if (depths.Length == 1)
                    precision *= channels;
                long pixelBytes = Math.Max(4 * largestSample, precision);
                long imageDepth = Values(32997, 1, 1)[0],
                    planar = Values(284, 1, 1)[0];
                Require(imageDepth > 0 && imageDepth <= limits.MaxSide && (planar == 1 || planar == 2), "invalid TIFF depth or planar configuration");
                long canvasWork = checked(Pixels(width, height, pixelBytes) * imageDepth);
                bool tiles = tags.ContainsKey(322) || tags.ContainsKey(323) || tags.ContainsKey(324) || tags.ContainsKey(325) || tags.ContainsKey(32998);
                long workingWork,
                    striles;
                long DivideUp(long value, long unit) => value / unit + (value % unit == 0 ? 0 : 1);
                long RoundUp(long value, long unit) => checked(DivideUp(value, unit) * unit);
                long samplingX = 1,
                    samplingY = 1;
                if (Values(262, 0, 1)[0] == 6 && planar == 1)
                {
                    long[] sampling = tags.ContainsKey(530) ? Values(530, 0, 2) : new long[] { 2, 2 };
                    Require(channels == 3 && sampling.Length == 2, "invalid TIFF YCbCr sampling metadata");
                    samplingX = sampling[0];
                    samplingY = sampling[1];
                    Require(
                        (samplingX == 1 || samplingX == 2 || samplingX == 4) && (samplingY == 1 || samplingY == 2 || samplingY == 4),
                        "invalid TIFF YCbCr subsampling"
                    );
                }
                if (tiles)
                {
                    Require(
                        tags.ContainsKey(322)
                            && tags.ContainsKey(323)
                            && tags.ContainsKey(324)
                            && tags.ContainsKey(325)
                            && !tags.ContainsKey(273)
                            && !tags.ContainsKey(279),
                        "incomplete or mixed TIFF tile/strip layout"
                    );
                    long tileWidth = Values(322, 0, 1)[0],
                        tileHeight = Values(323, 0, 1)[0],
                        tileDepth = Values(32998, 1, 1)[0];
                    Require(tileDepth > 0 && tileDepth <= limits.MaxSide, "invalid TIFF tile depth");
                    // Native tile decoders allocate the full tile, including padding beyond the canvas.
                    // Sum the padded grid once per pixel's channels; planar storage only multiplies the offset count.
                    long tileWork = checked(Pixels(tileWidth, tileHeight, pixelBytes) * tileDepth);
                    long grid = checked(DivideUp(width, tileWidth) * DivideUp(height, tileHeight) * DivideUp(imageDepth, tileDepth));
                    workingWork = checked(tileWork * grid);
                    if (samplingX > 1 || samplingY > 1)
                        workingWork = checked(RoundUp(tileWidth, samplingX) * RoundUp(tileHeight, samplingY) * tileDepth * pixelBytes * 2 * grid);
                    striles = checked(grid * (planar == 2 ? channels : 1));
                }
                else
                {
                    long rows = Values(278, uint.MaxValue, 1)[0];
                    Require(rows > 0 && rows <= uint.MaxValue, "invalid TIFF RowsPerStrip");
                    // TIFFStripSize clamps oversized RowsPerStrip (including UINT32_MAX) to ImageLength.
                    rows = Math.Min(rows, height);
                    long fullStrips = height / rows,
                        remainder = height % rows;
                    long paddedRows = checked(fullStrips * RoundUp(rows, samplingY) + RoundUp(remainder, samplingY));
                    workingWork = checked(RoundUp(width, samplingX) * paddedRows * imageDepth * pixelBytes * 2);
                    striles = checked(DivideUp(height, rows) * (planar == 2 ? channels : 1));
                }
                totalWork = checked(totalWork + Math.Max(canvasWork, workingWork));
                Require(totalWork <= limits.MaxImageBytes, "TIFF aggregate page/tile work exceeds the per-image budget");
                long[] offsets = Values(tiles ? 324 : 273, 0),
                    lengths = Values(tiles ? 325 : 279, 0);
                Require(offsets.Length == lengths.Length && offsets.Length == striles, "TIFF strip/tile counts do not match its geometry and planes");
                for (int i = 0; i < offsets.Length; i++)
                {
                    Require(lengths[i] > 0, "missing TIFF pixel data");
                    r.Bounds(offsets[i], lengths[i]);
                }
            }
            Require(pages > 0, "missing TIFF IFD");
            return totalWork;
        }

        private static int Levels(long side)
        {
            int count = 1;
            while (side > 1)
            {
                side /= 2;
                count++;
            }
            return count;
        }

        private long Ktx(Input r, bool version2)
        {
            byte[] magic = r.Bytes(12);
            Require(Convert.ToBase64String(magic) == (version2 ? "q0tUWCAyMLsNChoK" : "q0tUWCAxMbsNChoK"), "invalid KTX signature");
            long width,
                height,
                depth,
                layers,
                faces;
            uint levels;
            long sampleBytes = 4;
            long expandedLevels = 0;
            if (version2)
            {
                uint vkFormat = r.U32();
                uint typeSize = r.U32();
                Require(typeSize == 1 || typeSize == 2 || typeSize == 4, "unsupported KTX2 type size");
                width = r.U32();
                height = Math.Max(1L, r.U32());
                depth = Math.Max(1L, r.U32());
                layers = Math.Max(1L, r.U32());
                faces = r.U32();
                levels = r.U32();
                uint scheme = r.U32();
                Require(scheme <= 3, "unsupported KTX2 supercompression");
                uint dfd = r.U32(),
                    dfdLength = r.U32(),
                    kvd = r.U32(),
                    kvdLength = r.U32();
                ulong sgd = r.U64(),
                    sgdLength = r.U64();
                r.Bounds(dfd, dfdLength);
                r.Bounds(kvd, kvdLength);
                r.Bounds((long)sgd, (long)sgdLength);
                Require(dfdLength >= 28 && dfdLength <= 65536, "missing or oversized KTX2 data format descriptor");
                Require(faces == 1 || faces == 6, "invalid KTX2 face count");
                Require(levels <= 32, "invalid KTX2 mip count");
                for (int i = 0; i < Math.Max(1, levels); i++)
                {
                    ulong offset = r.U64(),
                        length = r.U64(),
                        uncompressed = r.U64();
                    r.Bounds((long)offset, (long)length);
                    Require(length > 0 && uncompressed <= (ulong)(limits.MaxImageBytes - expandedLevels), "KTX2 level expansion exceeds image budget");
                    expandedLevels += (long)uncompressed;
                }
                r.At(dfd);
                Require(r.U32() == dfdLength, "KTX2 descriptor size mismatch");
                r.Skip(6);
                int blockSize = r.U16();
                Require(blockSize >= 24 && (blockSize - 24) % 16 == 0 && blockSize <= dfdLength - 4, "invalid KTX2 descriptor block");
                r.Skip(16);
                int maximumSampleBits = 8;
                bool floating = false;
                for (int sample = 0; sample < (blockSize - 24) / 16; sample++)
                {
                    r.U16();
                    maximumSampleBits = Math.Max(maximumSampleBits, r.Byte() + 1);
                    floating |= (r.Byte() & 128) != 0;
                    r.Skip(12);
                }
                sampleBytes =
                    floating || vkFormat >= 95 && vkFormat <= 109 || vkFormat == 143 || vkFormat == 144
                        ? 16
                        : Math.Max(4, 4 * ((Math.Min(maximumSampleBits, 32) + 7) / 8));
                // Compressed block descriptors can describe entire blocks, not individual samples.
                if (vkFormat == 0 && !floating)
                    sampleBytes = 4;
            }
            else
            {
                uint order = r.U32();
                Require(order == 0x04030201 || order == 0x01020304, "invalid KTX byte order");
                bool big = order == 0x01020304;
                r.U32(big);
                uint typeSize = r.U32(big);
                Require(typeSize == 1 || typeSize == 2 || typeSize == 4, "unsupported KTX sample size");
                r.U32(big);
                uint internalFormat = r.U32(big);
                r.U32(big);
                sampleBytes = internalFormat == 0x8e8e || internalFormat == 0x8e8f ? 16 : 4 * typeSize;
                width = r.U32(big);
                height = Math.Max(1L, r.U32(big));
                depth = Math.Max(1L, r.U32(big));
                layers = Math.Max(1L, r.U32(big));
                faces = r.U32(big);
                levels = r.U32(big);
                uint metadata = r.U32(big);
                Require(faces == 1 || faces == 6, "invalid KTX face count");
                Require(levels <= 32 && metadata <= 16 * 1024 * 1024, "KTX metadata/mipmap limit exceeded");
                r.Skip(metadata);
                for (int i = 0; i < Math.Max(1, levels); i++)
                {
                    uint size = r.U32(big);
                    Require(size > 0, "empty KTX level");
                    long copies = faces == 6 && layers == 1 ? 6 : 1;
                    r.Skip(checked(((size + 3L) & ~3L) * copies));
                }
                Require(r.Position == r.Length, "KTX payload length mismatch");
            }
            Require(levels <= Levels(Math.Max(width, Math.Max(height, depth))), "KTX mip count exceeds dimensions");
            long work = Pixels(width, height, sampleBytes, checked(depth * layers * faces));
            return Math.Max(checked(expandedLevels * 2), checked(work * (levels != 1 ? 2 : 1)));
        }

        private long Dds(Input r)
        {
            Require(r.Text(4) == "DDS " && r.U32() == 124, "invalid DDS header");
            r.U32();
            long height = r.U32(),
                width = r.U32();
            r.U32();
            long depth = Math.Max(1L, r.U32());
            uint levels = r.U32();
            r.Skip(44);
            Require(r.U32() == 32, "invalid DDS pixel format");
            uint flags = r.U32(),
                fourcc = r.U32(),
                bits = r.U32();
            r.Skip(16);
            r.U32();
            uint caps2 = r.U32();
            r.Skip(12);
            long layers = 1;
            long faces = (caps2 & 0x200) != 0 ? 6 : 1;
            long sampleBytes = Math.Max(4L, (bits + 7L) / 8);
            bool compressed = (flags & 4) != 0 && fourcc > 116;
            if (fourcc == 0x30315844)
            {
                uint format = r.U32(),
                    dimension = r.U32(),
                    misc = r.U32();
                layers = r.U32();
                r.U32();
                Require(format > 0 && format <= 191 && dimension >= 2 && dimension <= 4 && layers > 0, "unsupported DDS DX10 metadata");
                sampleBytes = format <= 22 || format == 95 || format == 96 ? 16 : 4;
                compressed = format >= 70 && format <= 99;
                faces = (misc & 4) != 0 ? 6 : 1;
            }
            Require((flags & 4) != 0 || bits > 0 && bits <= 128, "invalid DDS precision");
            Require(levels <= Levels(Math.Max(width, Math.Max(height, depth))) && r.Position < r.Length, "invalid DDS mip count or missing payload");
            if ((flags & 4) != 0 && fourcc != 0x30315844)
            {
                if (fourcc >= 111 && fourcc <= 116)
                    sampleBytes = 16;
                else if (fourcc == 36 || fourcc == 110)
                    sampleBytes = 8;
                else
                    Require(
                        fourcc == 0x31545844
                            || fourcc == 0x32545844
                            || fourcc == 0x33545844
                            || fourcc == 0x34545844
                            || fourcc == 0x35545844
                            || fourcc == 0x31495441
                            || fourcc == 0x32495441
                            || fourcc == 0x55344342
                            || fourcc == 0x53344342
                            || fourcc == 0x55354342
                            || fourcc == 0x53354342,
                        "unsupported DDS pixel FourCC"
                    );
            }
            long minimumPayload = 0,
                w = width,
                h = height,
                d = depth;
            for (int level = 0; level < Math.Max(1, levels); level++)
            {
                minimumPayload = checked(
                    minimumPayload + (compressed ? ((w + 3) / 4) * ((h + 3) / 4) * 8 : w * h * Math.Max(1, (bits + 7L) / 8)) * d * layers * faces
                );
                w = Math.Max(1, w / 2);
                h = Math.Max(1, h / 2);
                d = Math.Max(1, d / 2);
            }
            r.Range(minimumPayload);
            return checked(Pixels(width, height, sampleBytes, checked(depth * layers * faces)) * (levels > 1 ? 2 : 1));
        }

        private long Basis(Input r)
        {
            byte[] h = r.Bytes(77);
            Require(U16(h, 0) == 0x4273 && U16(h, 2) >= 0x10 && U16(h, 4) == 77 && U32(h, 8) == r.Length - 77, "invalid Basis header");
            int slices = (int)U24(h, 14),
                images = (int)U24(h, 17),
                format = h[20];
            Require(
                slices > 0 && slices <= 65536 && images > 0 && images <= limits.MaxFrames && format <= 33 && h[23] <= 4,
                "unsupported Basis metadata or slice count"
            );
            Require((U16(h, 21) & 8) == 0, "Basis external global codebook is unsupported; export a self-contained texture");
            r.Bounds(U32(h, 41), U24(h, 45));
            r.Bounds(U32(h, 50), U24(h, 54));
            r.Bounds(U32(h, 57), U32(h, 61));
            r.At(U32(h, 65));
            r.Range(slices * 23L);
            long work = 0;
            for (int i = 0; i < slices; i++)
            {
                byte[] s = r.Bytes(23);
                Require(U24(s, 0) < images && s[3] < 32, "invalid Basis image/mip index");
                work = checked(work + Pixels(U16(s, 5), U16(s, 7), format >= 2 && format <= 4 ? 16 : 4));
                r.Bounds(U32(s, 13), U32(s, 17));
                Require(U32(s, 17) > 0, "missing Basis slice payload");
            }
            return work;
        }

        private static int U16(byte[] b, int offset) => b[offset] | b[offset + 1] << 8;

        private static uint U24(byte[] b, int offset) => (uint)(b[offset] | b[offset + 1] << 8 | b[offset + 2] << 16);

        private static uint U32(byte[] b, int offset) => U24(b, offset) | (uint)b[offset + 3] << 24;

        /// <summary>Small bounded reads, checked offsets, and bounded rescans for nonseekable ZIP entry streams.</summary>
        private sealed class Input : IDisposable
        {
            private Stream stream;
            private readonly Stream original;
            private readonly Func<Stream> reopen;
            private readonly CancellationToken token;
            private long readBytes;
            private long operations;
            private readonly byte[] scratch = new byte[8192];
            private readonly byte[] buffered = new byte[8192];
            private int cursor,
                available;
            private long bufferStart = -1;
            internal long Position { get; private set; }
            internal long Length { get; }

            internal Input(Stream source, long length, Func<Stream> reopen, CancellationToken token)
            {
                Require(source != null && length > 0 && length <= 8L * 1024 * 1024 * 1024, "invalid encoded image size");
                stream = original = source;
                Length = length;
                this.reopen = reopen;
                this.token = token;
                if (stream.CanSeek)
                {
                    Require(stream.Length == length, "image length changed");
                    stream.Position = 0;
                }
            }

            internal void Bounds(long offset, long count) =>
                Require(offset >= 0 && count >= 0 && offset <= Length && count <= Length - offset, "image offset/payload exceeds file length");

            internal long Range(long count)
            {
                Bounds(Position, count);
                return Position + count;
            }

            private void Read(byte[] buffer, int count)
            {
                Range(count);
                token.ThrowIfCancellationRequested();
                Require(++operations <= Math.Max(16000000L, Length * 2), "image inspection step limit exceeded");
                int offset = 0;
                while (offset < count)
                {
                    if (cursor == available)
                    {
                        bufferStart = Position;
                        available = stream.Read(buffered, 0, (int)Math.Min(buffered.Length, Length - Position));
                        cursor = 0;
                        if (available == 0)
                            throw new EndOfStreamException();
                        readBytes += available;
                        Require(readBytes <= Length * 3 + 64 * 1024 * 1024, "image metadata requires excessive archive rescanning; export with metadata first");
                    }
                    int n = Math.Min(count - offset, available - cursor);
                    Buffer.BlockCopy(buffered, cursor, buffer, offset, n);
                    cursor += n;
                    offset += n;
                    Position += n;
                }
            }

            internal byte[] Bytes(int count)
            {
                Require(count >= 0 && count <= 4096, "metadata read exceeds bounded buffer");
                var result = new byte[count];
                Read(result, count);
                return result;
            }

            internal int Byte()
            {
                Read(scratch, 1);
                return scratch[0];
            }

            internal int U16(bool big = false)
            {
                int a = Byte(),
                    b = Byte();
                return big ? (a << 8) | b : a | (b << 8);
            }

            internal uint U24()
            {
                uint a = (uint)U16();
                return a | (uint)Byte() << 16;
            }

            internal uint U32(bool big = false)
            {
                uint a = (uint)U16(big),
                    b = (uint)U16(big);
                return big ? a << 16 | b : a | b << 16;
            }

            internal ulong U64(bool big = false)
            {
                ulong first = U32(big),
                    second = U32(big);
                return big ? first << 32 | second : first | second << 32;
            }

            internal int I32() => unchecked((int)U32());

            internal string Text(int count) => Encoding.ASCII.GetString(Bytes(count));

            internal string ZeroString(int max)
            {
                var text = new StringBuilder();
                for (int i = 0; i <= max; i++)
                {
                    int b = Byte();
                    if (b == 0)
                        return text.ToString();
                    text.Append((char)b);
                }
                throw Bad("image metadata string exceeds limit");
            }

            internal string Line()
            {
                var text = new StringBuilder();
                for (int i = 0; i < 4096; i++)
                {
                    int b = Byte();
                    if (b == 10)
                        return text.ToString().TrimEnd('\r');
                    text.Append((char)b);
                }
                throw Bad("image metadata line exceeds limit");
            }

            internal void Skip(long count) => At(Range(count));

            internal void At(long position)
            {
                Bounds(position, 0);
                token.ThrowIfCancellationRequested();
                if (bufferStart >= 0 && position >= bufferStart && position <= bufferStart + available)
                {
                    cursor = (int)(position - bufferStart);
                    Position = position;
                    return;
                }
                if (stream.CanSeek)
                {
                    stream.Position = position;
                    Position = position;
                    cursor = available = 0;
                    bufferStart = -1;
                    return;
                }
                if (position < Position)
                {
                    Require(reopen != null, "image metadata requires a seekable stream");
                    if (stream != original)
                        stream.Dispose();
                    stream = reopen();
                    Position = 0;
                    cursor = available = 0;
                    bufferStart = -1;
                }
                while (Position < position)
                    Read(scratch, (int)Math.Min(scratch.Length, position - Position));
            }

            public void Dispose()
            {
                if (stream != original)
                    stream.Dispose();
            }
        }
    }
}
