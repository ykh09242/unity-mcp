using System;
using MCPForUnity.Editor.Tools.Build;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEditor.Build.Reporting;

namespace MCPForUnity.Editor.Tools.Build
{
    /// <summary>Extracts only bounded scalar detail rows; never retains native BuildReport objects or message text.</summary>
    internal static class BuildReportCapture
    {
        internal static void AddDetails(JObject record, BuildReport report)
        {
            var steps = new JArray();
            BuildStep[] nativeSteps = report.steps;
            int stepCount = Math.Min(nativeSteps.Length, BuildReportStore.MaxSteps);
            for (int i = 0; i < stepCount; i++)
            {
                BuildStep step = nativeSteps[i];
                steps.Add(
                    new JObject
                    {
                        ["name"] = BuildReportStore.SafeText(step.name),
                        ["depth"] = step.depth,
                        ["duration_seconds"] = Math.Max(0, step.duration.TotalSeconds),
                        ["message_count"] = step.messages?.Length ?? 0,
                    }
                );
            }
            record["steps"] = steps;
            record["steps_complete"] = nativeSteps.Length <= BuildReportStore.MaxSteps;
            var packed = new JArray();
            PackedAssets[] containers = report.packedAssets;
            bool complete = containers.Length <= 128;
            int work = 0;
            for (int i = 0; i < Math.Min(containers.Length, 128) && packed.Count < BuildReportStore.MaxPackedAssets; i++)
            {
                PackedAssetInfo[] contents = containers[i].contents;
                int processed = 0;
                for (int j = 0; j < contents.Length; j++)
                {
                    if (++work > 16384 || packed.Count >= BuildReportStore.MaxPackedAssets)
                    {
                        complete = false;
                        break;
                    }
                    processed++;
                    var asset = contents[j];
                    packed.Add(
                        new JObject { ["source_asset_path"] = BuildReportStore.AssetName(asset.sourceAssetPath), ["packed_size_bytes"] = asset.packedSize }
                    );
                }
                if (work > 16384)
                    break;
                if (packed.Count >= BuildReportStore.MaxPackedAssets && (i + 1 < containers.Length || processed < contents.Length))
                    complete = false;
            }
            record["packed_assets"] = packed;
            // Unity may omit packed appendices unless DetailedBuildReport was requested; do not turn that into an authoritative empty inventory.
            record["packed_assets_complete"] = complete && (containers.Length > 0 || (report.summary.options & BuildOptions.DetailedBuildReport) != 0);
        }
    }
}
