using System;
using System.IO;
using System.Linq;
using System.Security.Cryptography;
using System.Text;
using MCPForUnity.Editor.Helpers;
using Newtonsoft.Json.Linq;

// Only path selection, validation and Editor refresh are boundaries. The complete
// production commit method and its byte/option helpers are inserted unchanged.
internal static class Program
{
    private const int MaxPreviewTextBytes = 8 * 1024 * 1024;
    private static readonly UTF8Encoding StrictFileUtf8 = new UTF8Encoding(false, true);
    private static Action<string> _preparedCommitStageForTests;
    private static Action<string, bool> _preparedRefreshForTests;

    private static string ComputeSha256(string value) => RawSha256(StrictFileUtf8.GetBytes(value));

    private static ErrorResponse ValidateOrderedFinal(string value, JObject options) => null;

    // PRODUCTION_COMMIT

    // PRODUCTION_BATCH

    private static string ToCamelCase(string value) => StringCaseUtility.ToCamelCase(value);

    private static void Require(bool condition, string message)
    {
        if (!condition)
            throw new InvalidOperationException(message);
    }

    private static void Main()
    {
        string root = Path.Combine(AppContext.BaseDirectory, "cases-" + Guid.NewGuid().ToString("N"));
        Directory.CreateDirectory(root);
        int count = 0;
        foreach (bool bom in new[] { false, true })
        foreach (bool batch in new[] { false, true })
        foreach (string scenario in new[] { "normal", "before_compare", "before_replace", "observation_failure", "later_write" })
        {
            string target = Path.Combine(root, (++count).ToString() + ".cs");
            byte[] original = OrderedBytes("// original\n", bom);
            byte[] external = OrderedBytes("// external\n", bom);
            byte[] candidate = OrderedBytes("// candidate\n", bom);
            File.WriteAllBytes(target, original);
            int refreshes = 0;
            _preparedRefreshForTests = (_, _) => refreshes++;
            _preparedCommitStageForTests = stage =>
            {
                if (
                    stage == scenario
                    || stage == "before_replace" && scenario == "observation_failure"
                    || stage == "after_replace" && scenario == "later_write"
                )
                    File.WriteAllBytes(target, external);
                if (stage == "after_replace" && scenario == "observation_failure")
                    throw new IOException("Owned observation fault");
            };
            var parameters = new JObject
            {
                ["action"] = "apply_prepared_script",
                ["name"] = "Probe",
                ["path"] = target,
                ["original_bytes_sha256"] = RawSha256(original),
                ["candidate_bytes_sha256"] = RawSha256(candidate),
                ["candidate_contents"] = "// candidate\n",
                ["bom"] = bom,
            };
            var result = JObject.FromObject(ApplyPreparedScript(target, target, batch ? NormalizeParameterKeys(parameters, "manage_script") : parameters));
            bool displaced = scenario == "before_replace" || scenario == "observation_failure";
            if (displaced)
            {
                Require(result["data"].Value<bool>("applied"), "Returned replacement must remain known");
                string backup = result["data"].Value<string>("recovery_backup");
                Require(backup != null && File.Exists(backup), "Displaced external bytes must retain a reported recovery backup");
                Require(File.ReadAllBytes(backup).SequenceEqual(external), "Recovery backup must contain the external version");
                Require(File.ReadAllBytes(target).SequenceEqual(candidate), "Must not blindly roll back committed bytes");
                if (scenario == "before_replace")
                    Require(
                        !result.Value<bool>("success") && result["data"].Value<string>("outcome") == "conflict",
                        "External writer must be reported as a conflict"
                    );
            }
            else
            {
                byte[] expected = scenario == "normal" ? candidate : external;
                Require(File.ReadAllBytes(target).SequenceEqual(expected), "Incorrect target bytes for " + scenario);
                Require(Directory.GetFiles(root, Path.GetFileName(target) + ".*.prepared.*").Length == 0, "Verified attempt must clean its sidecars");
            }
            Require(refreshes == (scenario == "normal" ? 1 : 0), "Conflict must not request refresh");
            Console.WriteLine($"PASS {scenario} bom={bom} batch={batch}");
        }
        Console.WriteLine($"PASS {count} actual prepared-script commit scenarios");
    }
}

internal static class AssetPathUtility
{
    internal static string GetFullAssetPath(string path) => Path.GetFullPath(path);
}

internal static class MCPServiceLocator
{
    internal static class ToolDiscovery
    {
        internal sealed class Metadata
        {
            public bool IsBuiltIn => true;
        }

        internal static Metadata GetToolMetadata(string name) => new Metadata();
    }
}

namespace GameObjects
{
    internal static class ManageGameObject
    {
        internal static JObject NormalizeParameterAliases(JObject source) => throw new InvalidOperationException("Unexpected GameObject route");

        internal static void ResolveParameterAlias(JObject source, string canonical, string alias) =>
            throw new InvalidOperationException("Unexpected scene route");
    }
}

internal static class ManageScriptRefreshHelpers
{
    internal static void ImportAndRequestCompile(string path) => throw new InvalidOperationException("Unexpected native refresh");

    internal static void ScheduleScriptRefresh(string path) => throw new InvalidOperationException("Unexpected native refresh");
}
