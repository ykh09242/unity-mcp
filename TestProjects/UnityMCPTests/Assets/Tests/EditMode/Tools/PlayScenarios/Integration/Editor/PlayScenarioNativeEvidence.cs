using System;
using System.IO;
using System.Linq;
using System.Security.Cryptography;
using System.Text;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEngine;

namespace MCPForUnityTests.PlayScenarios.Integration
{
    internal static class PlayScenarioNativeEvidence
    {
        private static string Folder => Path.Combine(Path.GetDirectoryName(Application.dataPath), "Library/MCPForUnity/PlayScenarioIntegrationEvidence");

        internal static void PrepareSession()
        {
            Directory.CreateDirectory(Folder);
            string path = Path.Combine(Folder, ".session.json");
            if (!File.Exists(path))
                File.WriteAllText(
                    path,
                    new JObject
                    {
                        ["session_id"] = Guid.NewGuid().ToString("N"),
                        ["started_unix_ms"] = DateTimeOffset.UtcNow.ToUnixTimeMilliseconds(),
                    }.ToString()
                );
        }

        // Call only after the final assertion in the top-level test body.
        internal static void Record(string method, params JObject[] reports)
        {
            string exactMethod = method.Contains(".") ? method : typeof(PlayScenarioNativeFlowTests).FullName + "." + method;
            JObject session = JObject.Parse(File.ReadAllText(Path.Combine(Folder, ".session.json")));
            var references = new JArray();
            foreach (JObject report in reports)
            {
                bool suite = report["suite_id"] != null;
                string id = (string)report[suite ? "suite_id" : "job_id"];
                Assert.That(Guid.TryParseExact(id, "N", out _), Is.True);
                string filename = id + (suite ? ".suite.json" : ".json");
                byte[] bytes = File.ReadAllBytes(Path.Combine(Folder, filename));
                JObject exported = JObject.Parse(System.Text.Encoding.UTF8.GetString(bytes));
                Assert.That((string)exported[suite ? "suite_id" : "job_id"], Is.EqualTo(id));
                Assert.That((string)exported["status"], Is.EqualTo((string)report["status"]));
                using (SHA256 hash = SHA256.Create())
                    references.Add(
                        new JObject
                        {
                            ["id"] = id,
                            ["file"] = filename,
                            ["sha256"] = string.Concat(hash.ComputeHash(bytes).Select(value => value.ToString("x2"))),
                        }
                    );
            }
            string receiptName;
            using (SHA256 hash = SHA256.Create())
                receiptName = string.Concat(hash.ComputeHash(Encoding.UTF8.GetBytes(exactMethod)).Select(value => value.ToString("x2"))) + ".receipt.json";
            File.WriteAllText(
                Path.Combine(Folder, receiptName),
                new JObject
                {
                    ["schema_version"] = 1,
                    ["method"] = exactMethod,
                    ["session_id"] = (string)session["session_id"],
                    ["completed_unix_ms"] = DateTimeOffset.UtcNow.ToUnixTimeMilliseconds(),
                    ["reports"] = references,
                }.ToString()
            );
        }

        internal static void RecordSuitesExported(string method, params string[] suiteIds)
        {
            var reports = new JObject[suiteIds.Length];
            for (int index = 0; index < suiteIds.Length; index++)
            {
                Assert.That(Guid.TryParseExact(suiteIds[index], "N", out _), Is.True);
                reports[index] = JObject.Parse(File.ReadAllText(Path.Combine(Folder, suiteIds[index] + ".suite.json")));
            }
            Record(method, reports);
        }

        internal static void RecordExported(string method, string jobId)
        {
            Assert.That(Guid.TryParseExact(jobId, "N", out _), Is.True);
            Record(method, JObject.Parse(File.ReadAllText(Path.Combine(Folder, jobId + ".json"))));
        }
    }
}
