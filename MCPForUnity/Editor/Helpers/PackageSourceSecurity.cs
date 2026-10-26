using System;
using System.Text.RegularExpressions;
using Newtonsoft.Json.Linq;

namespace MCPForUnity.Editor.Helpers
{
    /// <summary>Credential-free package inputs and safe projections of legacy native diagnostics.</summary>
    internal static class PackageSourceSecurity
    {
        private static readonly Regex Url = new Regex(@"[a-z][a-z0-9+.-]*://[^\s'""<>]+", RegexOptions.IgnoreCase);

        internal static bool HasCredentials(string value)
        {
            if (string.IsNullOrEmpty(value))
                return false;
            string source = value.StartsWith("git+", StringComparison.OrdinalIgnoreCase) ? value.Substring(4) : value;
            if (!source.Contains("://"))
                return Regex.IsMatch(source, @"^[^\s/]*:[^\s/]*@[^\s/]+:");
            if (!Uri.TryCreate(source, UriKind.Absolute, out var uri))
                return true;
            string user = Uri.UnescapeDataString(uri.UserInfo);
            if (!string.IsNullOrEmpty(user) && (uri.Scheme != "ssh" || user.Contains(":")))
                return true;
            // UPM's only supported Git query is its package subdirectory selector.
            // Arbitrary query keys can carry signed URLs or credentials.
            if (!string.IsNullOrEmpty(uri.Query) && !Regex.IsMatch(uri.Query, @"^\?path=[^&#]*$", RegexOptions.IgnoreCase))
                return true;
            return false;
        }

        internal static string Redact(string value)
        {
            if (string.IsNullOrEmpty(value))
                return value;
            string redacted = Url.Replace(
                value,
                match =>
                {
                    string url = match.Value;
                    int authority = url.IndexOf("://", StringComparison.Ordinal) + 3;
                    int end = url.IndexOfAny(new[] { '/', '?', '#' }, authority);
                    if (end < 0)
                        end = url.Length;
                    int at = end > authority ? url.LastIndexOf('@', end - 1, end - authority) : -1;
                    if (at >= authority)
                        url = url.Substring(0, authority) + "[redacted]@" + url.Substring(at + 1);
                    int suffix = url.IndexOfAny(new[] { '?', '#' });
                    return suffix < 0 ? url : url.Substring(0, suffix) + "[redacted]";
                }
            );
            return Regex.Replace(
                redacted,
                @"(?<![\w/])[^\s/:@]+(?::[^\s/@]+)?@[^\s/:]+:[^\s'""<>]+",
                match =>
                {
                    string source = match.Value;
                    source = "[redacted]@" + source.Substring(source.IndexOf('@') + 1);
                    int suffix = source.IndexOfAny(new[] { '?', '#' });
                    return suffix < 0 ? source : source.Substring(0, suffix) + "[redacted]";
                }
            );
        }

        internal static string ForStorage(string value) => HasCredentials(value) ? Redact(value) : value;

        internal static JToken RedactResponse(object response)
        {
            var token = JToken.FromObject(response);
            RedactToken(token);
            return token;
        }

        private static void RedactToken(JToken token)
        {
            if (token is JValue value && value.Type == JTokenType.String)
                value.Value = Redact(value.Value<string>());
            else if (token is JContainer container)
                foreach (var child in container.Children())
                    RedactToken(child);
        }
    }
}
