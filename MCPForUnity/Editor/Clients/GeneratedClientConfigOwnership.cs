using System;
using System.Security.Cryptography;
using System.Text;
using MCPForUnity.Editor.Constants;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Editor.Models;
using UnityEditor;

namespace MCPForUnity.Editor.Clients
{
    /// <summary>Receipts for entries written explicitly by this editor, never inferred from their shape.</summary>
    internal static class GeneratedClientConfigOwnership
    {
        internal static string Key(string format, string path) => "MCPForUnity.Generated" + format + "Config." + Fingerprint(path);

        internal static bool CanAutomaticallyRefresh(string format, string path, string entry) =>
            EditorPrefs.GetBool(EditorPrefKeys.AutoRegisterEnabled, true) && Owns(format, path, entry);

        // An automatic writer may update only the exact entry admitted by status.
        // Recheck the same receipt and preferences at the final file/CLI boundary.
        internal sealed class AutomaticRefresh
        {
            private readonly string format,
                path,
                entry;
            private readonly bool followsTransportSelection;
            internal ConfiguredTransport Transport { get; }

            private AutomaticRefresh(string format, string path, string entry, ConfiguredTransport transport, bool followsTransportSelection)
            {
                this.format = format;
                this.path = path;
                this.entry = entry;
                this.followsTransportSelection = followsTransportSelection;
                Transport = transport;
            }

            internal static AutomaticRefresh Capture(
                string format,
                string path,
                string entry,
                bool stdioOnly,
                bool supportsHttp = true,
                ConfiguredTransport? selectedTransport = null
            )
            {
                if (!CanAutomaticallyRefresh(format, path, entry))
                    return null;
                var selected = selectedTransport ?? HttpEndpointUtility.GetCurrentServerTransport();
                if (stdioOnly && selected != ConfiguredTransport.Stdio)
                    return null;
                // A stdio-only client can refresh normally regardless of the HTTP
                // preference. A version migration still requires global stdio selection.
                return new AutomaticRefresh(format, path, entry, supportsHttp ? selected : ConfiguredTransport.Stdio, stdioOnly || supportsHttp);
            }

            internal void ValidateEntry(string identity, string current)
            {
                if (identity != path || current != entry)
                    throw new McpConfigurationHelper.ConfigurationConflictException();
                ValidateOwnership();
            }

            internal bool MatchesPriorReceipt(string identity) => identity == path && Owns(format, path, entry);

            internal void ValidateOwnership()
            {
                if (
                    !CanAutomaticallyRefresh(format, path, entry) || (followsTransportSelection && HttpEndpointUtility.GetCurrentServerTransport() != Transport)
                )
                    throw new McpConfigurationHelper.ConfigurationConflictException();
            }

            internal void ValidateProposal(string endpoint)
            {
                ValidateOwnership();
                // Check what the writer actually built even if the preference changed
                // during generation and returned to its original value before commit.
                string expected = Transport == ConfiguredTransport.Stdio ? null : HttpEndpointUtility.GetMcpRpcUrl();
                if (endpoint != expected)
                    throw new McpConfigurationHelper.ConfigurationConflictException();
            }
        }

        internal static string Receipt(string format, string path) => EditorPrefs.HasKey(Key(format, path)) ? EditorPrefs.GetString(Key(format, path)) : null;

        internal static void RestoreReceipt(string format, string path, string receipt)
        {
            if (receipt == null)
                EditorPrefs.DeleteKey(Key(format, path));
            else
                EditorPrefs.SetString(Key(format, path), receipt);
        }

        internal static bool Owns(string format, string path, string entry)
        {
            if (entry == null)
                return false;
            string stored = EditorPrefs.GetString(Key(format, path), string.Empty);
            return !string.IsNullOrEmpty(stored) && stored == Fingerprint(entry);
        }

        internal static void Record(string format, string path, string entry)
        {
            RestoreReceipt(format, path, EntryReceipt(entry));
        }

        internal static string EntryReceipt(string entry) => entry == null ? null : Fingerprint(entry);

        private static string Fingerprint(string value)
        {
            using var sha = SHA256.Create();
            return BitConverter.ToString(sha.ComputeHash(Encoding.UTF8.GetBytes(value))).Replace("-", string.Empty);
        }
    }
}
