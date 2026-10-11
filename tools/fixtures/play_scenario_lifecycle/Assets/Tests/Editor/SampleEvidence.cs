using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Security.Cryptography;
using System.Text;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Editor.Tools;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEngine;

namespace UnityMcpLifecycleSample.Tests
{
    internal static class SampleEvidence
    {
        private static string Project => Path.GetDirectoryName(Application.dataPath);
        private static string Folder => Path.Combine(Project, "Library/MCPForUnity/PlayScenarioIntegrationEvidence");

        internal static JObject Command(JObject input)
        {
            object response = ManagePlayScenario.HandleCommand(input);
            Assert.That(response, Is.TypeOf<SuccessResponse>(), JObject.FromObject(response).ToString());
            object data = ((SuccessResponse)response).Data;
            return data is JObject json ? json : JObject.FromObject(data);
        }

        internal static void Prepare()
        {
            Directory.CreateDirectory(Folder);
            string session = Path.Combine(Folder, ".session.json");
            if (!File.Exists(session))
                File.WriteAllText(
                    session,
                    new JObject
                    {
                        ["session_id"] = Guid.NewGuid().ToString("N"),
                        ["started_unix_ms"] = DateTimeOffset.UtcNow.ToUnixTimeMilliseconds(),
                    }.ToString()
                );
        }

        internal static void Export(JObject done)
        {
            Assert.That((string)done["report_path"], Does.StartWith("Library/MCPForUnity/PlayScenarioRuns/"));
            Assert.That((string)done["report_error"], Is.Null);
            string path = Path.Combine(Project, (string)done["report_path"]);
            JObject stored = JObject.Parse(File.ReadAllText(path));
            // Preserve the actual persisted file even when the equality assertion fails.
            File.Copy(path, Path.Combine(Folder, (string)done["job_id"] + ".json"), overwrite: true);
            foreach (
                string field in new[]
                {
                    "job_id",
                    "status",
                    "resource_checks",
                    "iteration_results_version",
                    "iteration_results",
                    "failure",
                    "cleanup_failures",
                    "runner_resources_released",
                    "phase",
                    "reproduction",
                }
            )
                Assert.That(JToken.DeepEquals(stored[field], done[field]), Is.True, "Persisted " + field + " differs.");
        }

        internal static void Record(string method, List<JObject> reports, JArray identities)
        {
            string exact = typeof(LifecycleScenarioTests).FullName + "." + method;
            JObject session = JObject.Parse(File.ReadAllText(Path.Combine(Folder, ".session.json")));
            var references = new JArray();
            foreach (JObject report in reports)
            {
                string id = (string)report["job_id"];
                Assert.That(Guid.TryParseExact(id, "N", out _), Is.True);
                string file = id + ".json";
                byte[] bytes = File.ReadAllBytes(Path.Combine(Folder, file));
                references.Add(
                    new JObject
                    {
                        ["id"] = id,
                        ["file"] = file,
                        ["sha256"] = Hash(bytes),
                    }
                );
            }
            string identityFolder = Path.Combine(Project, "Library/LifecycleSampleEvidence");
            Directory.CreateDirectory(identityFolder);
            byte[] identityBytes = Encoding.UTF8.GetBytes(identities.ToString());
            File.WriteAllBytes(Path.Combine(identityFolder, method + ".json"), identityBytes);
            File.WriteAllText(
                Path.Combine(Folder, Hash(Encoding.UTF8.GetBytes(exact)) + ".receipt.json"),
                new JObject
                {
                    ["schema_version"] = 1,
                    ["method"] = exact,
                    ["session_id"] = (string)session["session_id"],
                    ["completed_unix_ms"] = DateTimeOffset.UtcNow.ToUnixTimeMilliseconds(),
                    ["reports"] = references,
                    ["identity_evidence_sha256"] = Hash(identityBytes),
                }.ToString()
            );
        }

        private static string Hash(byte[] data)
        {
            using (SHA256 hash = SHA256.Create())
                return string.Concat(hash.ComputeHash(data).Select(value => value.ToString("x2")));
        }
    }
}
