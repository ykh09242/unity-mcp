using System;
using System.Text;
using System.Threading;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;

namespace MCPForUnity.Runtime.PlayScenarios
{
    internal sealed class PlayScenarioLoaderCapture : IDisposable
    {
        private readonly object gate = new object();
        private readonly int mainThread = Thread.CurrentThread.ManagedThreadId;
        private readonly JArray rows = new JArray();
        private readonly IDisposable subscription;
        private int dropped;
        private int encodedBytes;

        internal PlayScenarioLoaderCapture() => subscription = ReferenceGraphRuntime.Observe(Record);

        private void Record(ReferenceGraphRuntimeEvent entry)
        {
            if (entry.Kind != "load" && entry.Kind != "acquire")
                return;
            lock (gate)
            {
                // Retain bounded, exact scalars. Never truncate a key into a different lookup.
                if (rows.Count >= 128 || entry.Key.Length > 512 || entry.OwnerPath.Length > 512 || entry.Scheme.Length > 64)
                {
                    dropped++;
                    return;
                }
                string outcome = "unknown";
                string assetName = null;
                if (Thread.CurrentThread.ManagedThreadId == mainThread)
                {
                    if (entry.Asset == null)
                        outcome = "no_object";
                    else
                    {
                        outcome = "loaded";
                        string name = entry.Asset.name;
                        if (name.Length <= 256)
                            assetName = name;
                    }
                }
                var row = new JObject
                {
                    ["sequence"] = rows.Count + 1,
                    ["kind"] = entry.Kind,
                    ["scheme"] = entry.Scheme,
                    ["key"] = entry.Key,
                    ["owner_path"] = entry.OwnerPath,
                    ["outcome"] = outcome,
                    ["asset_name"] = assetName,
                };
                int size = Encoding.UTF8.GetByteCount(row.ToString(Formatting.None)) + 1;
                if (encodedBytes + size > 32 * 1024)
                {
                    dropped++;
                    return;
                }
                rows.Add(row);
                encodedBytes += size;
            }
        }

        internal JObject Snapshot(string jobId, string buildId, string sourceRevision, string payloadHash)
        {
            lock (gate)
            {
                return new JObject
                {
                    ["schema_version"] = 1,
                    ["job_id"] = jobId,
                    ["build_id"] = buildId,
                    ["build_source_revision"] = sourceRevision,
                    ["payload_hash"] = payloadHash,
                    ["dropped"] = dropped,
                    ["observations"] = rows.DeepClone(),
                };
            }
        }

        public void Dispose() => subscription.Dispose();
    }
}
