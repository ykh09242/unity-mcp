using System;
using System.Globalization;
using Newtonsoft.Json.Linq;

namespace MCPForUnity.Editor.Helpers
{
    /// <summary>Authoritative bounds for asset, UI and physics result pages.</summary>
    public static class PaginationBounds
    {
        public const int MaxPageSize = 1000;
        public const int MaxPreviewPageSize = 32;
        public const int MaxPreviewEdge = 256;
        public const int MaxPreviewPngBytes = 256 * 1024;
        public const int MaxPreviewBase64Bytes = 4 * 1024 * 1024;

        public static bool TryRead(JToken token, int fallback, int minimum, int maximum,
            string name, out int value, out string error)
        {
            value = fallback;
            error = null;
            if (token == null || token.Type == JTokenType.Null) return true;
            string text = token.ToString();
            if ((token.Type != JTokenType.Integer && token.Type != JTokenType.Float
                && token.Type != JTokenType.String) || text.Length > 64
                || !decimal.TryParse(text, NumberStyles.Float, CultureInfo.InvariantCulture, out decimal number)
                || number != decimal.Truncate(number) || number < minimum || number > maximum)
            {
                error = $"'{name}' must be an integer between {minimum} and {maximum}.";
                return false;
            }
            value = (int)number;
            return true;
        }

        public static long StartIndex(int pageNumber, int pageSize) => ((long)pageNumber - 1) * pageSize;

        public sealed class PreviewBudget
        {
            public int Base64Bytes { get; private set; }
            public bool CanGenerate => Base64Bytes < MaxPreviewBase64Bytes;

            public bool TryReserve(int pngBytes)
            {
                if (pngBytes <= 0 || pngBytes > MaxPreviewPngBytes) return false;
                int encodedBytes = ((pngBytes + 2) / 3) * 4;
                if (encodedBytes > MaxPreviewBase64Bytes - Base64Bytes) return false;
                Base64Bytes += encodedBytes;
                return true;
            }
        }
    }
}
