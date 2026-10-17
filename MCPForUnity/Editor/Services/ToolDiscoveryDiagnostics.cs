using System;
using System.Collections.Generic;
using System.Linq;
using System.Security.Cryptography;
using System.Text;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;

namespace MCPForUnity.Editor.Services
{
    public interface IToolDiscoveryDiagnostics
    {
        JObject GetDiagnostics(string toolName = null, int limit = 64);
    }

    /// <summary>Bounded observations accumulated during existing discovery, never a discovery trigger.</summary>
    internal sealed class ToolDiscoveryDiagnostics
    {
        private readonly List<JObject> _rows = new List<JObject>();
        private readonly string _epoch = Guid.NewGuid().ToString("N");
        private int _dropped;
        private bool _complete;

        internal static bool ValidName(string name) =>
            name == null || (name.Length > 0 && name.Length <= 128 && name.All(c => char.IsLetterOrDigit(c) || c == '_' || c == '.' || c == '-'));

        internal static string CandidateId(Type type)
        {
            using (var hash = SHA256.Create())
                return BitConverter
                    .ToString(hash.ComputeHash(Encoding.UTF8.GetBytes(type.FullName + "|" + type.Assembly.FullName)))
                    .Replace("-", "")
                    .ToLowerInvariant();
        }

        private static string Safe(string value, int maximum) => value != null && value.Length <= maximum ? value : null;

        internal void Record(Type type, string name, string state, string reason, string action)
        {
            if (_rows.Count >= 512)
            {
                _dropped++;
                return;
            }
            var row = new JObject
            {
                ["tool_name"] = Safe(name, 128),
                ["candidate_id"] = CandidateId(type),
                ["declaring_type"] = Safe(type.FullName, 256),
                ["assembly"] = Safe(type.Assembly.GetName().Name, 64),
                ["stage"] = "native_discovery",
                ["state"] = state,
                ["reason"] = reason,
                ["next_action"] = action,
            };
            _rows.Add(row);
            if (Encoding.UTF8.GetByteCount(Snapshot(null, 512).ToString(Formatting.None)) > 128 * 1024)
            {
                _rows.RemoveAt(_rows.Count - 1);
                _dropped++;
            }
        }

        internal void Shadow(string name)
        {
            foreach (var row in _rows.Where(row => (string)row["tool_name"] == name && (string)row["reason"] != "duplicate_shadowed"))
            {
                row["state"] = "rejected";
                row["reason"] = "duplicate_shadowed";
                row["next_action"] = "rename_duplicate_or_review_order";
            }
            while (_rows.Count > 0 && Encoding.UTF8.GetByteCount(Snapshot(null, 512).ToString(Formatting.None)) > 128 * 1024)
            {
                _rows.RemoveAt(_rows.Count - 1);
                _dropped++;
            }
        }

        internal void Complete() => _complete = true;

        internal JObject Snapshot(string name, int limit)
        {
            var selected = _rows.Where(row => name == null || (string)row["tool_name"] == name).Take(limit).Select(row => row.DeepClone());
            return new JObject
            {
                ["schema_version"] = "unity-mcp/native-tool-diagnostics@1",
                ["epoch"] = _epoch,
                ["complete"] = _complete,
                ["truncated"] = _dropped > 0 || _rows.Count(row => name == null || (string)row["tool_name"] == name) > limit,
                ["dropped"] = _dropped,
                ["rows"] = new JArray(selected),
            };
        }
    }
}
