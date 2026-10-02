using System;
using System.IO;
using System.Net;
using System.Net.Sockets;

namespace MCPForUnity.Editor.Services.AssetGen.Http
{
    /// <summary>Outbound policy for untrusted provider artifacts, including every redirect hop.</summary>
    internal static class AssetDownloadPolicy
    {
        // Provider-owned artifact hosts only. See docs/asset-gen-download-security.md for sources.
        // Never allow shared storage suffixes such as amazonaws.com or arbitrary provider URLs.
        internal static Uri RequireAllowedUrl(string provider, string url)
        {
            if (string.IsNullOrWhiteSpace(url) || url.IndexOf('\\') >= 0
                || !Uri.TryCreate(url, UriKind.Absolute, out Uri uri)
                || uri.Scheme != Uri.UriSchemeHttps || uri.Port != 443
                || uri.UserInfo.Length != 0 || uri.Fragment.Length != 0
                || uri.HostNameType != UriHostNameType.Dns
                || !IsAllowedHost(provider, uri.IdnHost))
            {
                // URLs can contain signed credentials. Do not include them in errors or logs.
                throw new InvalidOperationException("Refusing provider download: expected an HTTPS URL on an approved artifact host, port 443, without userinfo or a fragment.");
            }
            return uri;
        }

        private static bool IsAllowedHost(string provider, string host)
        {
            switch (provider?.ToLowerInvariant())
            {
                case "tripo":
                    return EqualsHost(host, "tripo-data.rg1.data.tripo3d.com");
                case "meshy":
                    return EqualsHost(host, "assets.meshy.ai");
                case "fal":
                    return EqualsHost(host, "fal.media")
                        || host.EndsWith(".fal.media", StringComparison.OrdinalIgnoreCase);
                case "sketchfab":
                    return EqualsHost(host, "sketchfab-prod-media.s3.amazonaws.com");
                // OpenRouter documents inline base64 results; there is no documented download CDN.
                default:
                    return false;
            }
        }

        private static bool EqualsHost(string actual, string expected)
            => string.Equals(actual, expected, StringComparison.OrdinalIgnoreCase);

        internal static void RequirePublicAddresses(IPAddress[] addresses)
        {
            if (addresses == null || addresses.Length == 0)
                throw new IOException("Provider download host has no addresses.");
            // Reject mixed public/private answers, not just the address selected for the first try.
            foreach (IPAddress address in addresses)
                if (!IsPublicAddress(address))
                    throw new InvalidOperationException("Refusing provider download: DNS returned a non-public address.");
        }

        internal static bool IsPublicAddress(IPAddress address)
        {
            if (address == null) return false;
            if (address.IsIPv4MappedToIPv6) address = address.MapToIPv4();
            byte[] b = address.GetAddressBytes();
            if (address.AddressFamily == AddressFamily.InterNetwork)
            {
                return !(b[0] == 0 || b[0] == 10 || b[0] == 127 || b[0] >= 224
                    || (b[0] == 100 && b[1] >= 64 && b[1] <= 127)
                    || (b[0] == 169 && b[1] == 254)
                    || (b[0] == 172 && b[1] >= 16 && b[1] <= 31)
                    || (b[0] == 192 && b[1] == 168)
                    || (b[0] == 192 && b[1] == 0 && (b[2] == 0 || b[2] == 2))
                    || (b[0] == 192 && b[1] == 88 && b[2] == 99)
                    || (b[0] == 198 && (b[1] == 18 || b[1] == 19))
                    || (b[0] == 198 && b[1] == 51 && b[2] == 100)
                    || (b[0] == 203 && b[1] == 0 && b[2] == 113)
                    // Azure's platform virtual IP is not a public Internet service.
                    || (b[0] == 168 && b[1] == 63 && b[2] == 129 && b[3] == 16));
            }
            if (address.AddressFamily != AddressFamily.InterNetworkV6 || address.ScopeId != 0)
                return false;

            // Only global unicast 2000::/3. Exclude special-purpose 2001::/23, documentation,
            // and 6to4 (which can embed a private IPv4 destination). This also excludes NAT64,
            // IPv4-compatible, loopback, link/site-local, ULA, multicast and reserved ranges.
            return (b[0] & 0xe0) == 0x20
                && !(b[0] == 0x20 && b[1] == 0x01 && b[2] < 2)
                && !(b[0] == 0x20 && b[1] == 0x01 && b[2] == 0x0d && b[3] == 0xb8)
                && !(b[0] == 0x20 && b[1] == 0x02)
                && !(b[0] == 0x3f && b[1] == 0xfe) // retired 6bone range
                && !(b[0] == 0x3f && b[1] == 0xff && (b[2] & 0xf0) == 0);
        }
    }
}
