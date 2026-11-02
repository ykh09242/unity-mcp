using System;
using System.Collections;
using System.Collections.Generic;
using System.Linq;
using MCPForUnity.Editor.Tools.ProjectAuditor;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEditor;
using UnityEngine.TestTools;
using PackageInfo = UnityEditor.PackageManager.PackageInfo;

namespace MCPForUnityTests.Editor.Tools
{
    public class ProjectAuditorNativeIntegrationTests
    {
        [UnityTest]
        [Timeout(300000)]
        [Explicit("Requires the controlled base-plus-Auditor/Cecil fixture profile; select this exact method. See docs/PROJECT_AUDITOR.md.")]
        public IEnumerator InstalledOfficialPackageProducesActualFixtureDiagnostic()
        {
            var ownPackage = PackageInfo.FindForAssembly(typeof(ManageProjectAuditor).Assembly);
            Assert.IsNotNull(ownPackage, "The explicit integration requires the current MCP package in the controlled fixture project.");
            var allowed = new HashSet<string>(StringComparer.Ordinal)
            {
                ownPackage.name,
                "com.unity.test-framework",
                "com.unity.ext.nunit",
                "com.unity.nuget.newtonsoft-json",
                "com.unity.editorcoroutines",
                "com.unity.ugui",
                "com.unity.project-auditor",
                "com.unity.nuget.mono-cecil",
            };
            var registered = PackageInfo.GetAllRegisteredPackages();
            var unexpected = registered
                .Where(package => !package.name.StartsWith("com.unity.modules.", StringComparison.Ordinal) && !allowed.Contains(package.name))
                .Select(package => package.name)
                .ToArray();
            Assert.IsEmpty(
                unexpected,
                "This explicit bounded-prefix oracle requires base packages plus Auditor/Cecil. Additional optional runtime packages can fill the retained 4096-row prefix before the local fixture. See docs/PROJECT_AUDITOR.md. Unexpected: "
                    + string.Join(", ", unexpected.Take(16))
            );
            Assert.IsTrue(
                registered.Any(package => package.name == "com.unity.nuget.mono-cecil"),
                "The controlled fixture requires Auditor's official Mono.Cecil dependency."
            );
            var capability = JObject.FromObject(ManageProjectAuditor.HandleCommand(new JObject { ["action"] = "availability" }));
            Assert.AreEqual(
                "available",
                (string)capability["data"]["state"],
                "The explicitly selected fixture requires official Auditor 1.0.1 and its rules: " + capability
            );
            Assert.IsTrue(capability["data"].Value<bool>("rules_available"));
            string id = Guid.NewGuid().ToString("N");
            var started = JObject.FromObject(ManageProjectAuditor.HandleCommand(new JObject { ["action"] = "start", ["audit_id"] = id }));
            Assert.IsTrue(started.Value<bool>("success"), started.ToString());
            double deadline = EditorApplication.timeSinceStartup + 270;
            JObject status;
            do
            {
                yield return null;
                status = JObject.FromObject(ManageProjectAuditor.HandleCommand(new JObject { ["action"] = "status", ["audit_id"] = id }));
                Assert.IsTrue(status.Value<bool>("success"), status.ToString());
                Assert.Less(EditorApplication.timeSinceStartup, deadline, status.ToString());
            } while ((string)status["data"]["state"] == "queued" || (string)status["data"]["state"] == "running");
            Assert.AreEqual("completed", (string)status["data"]["state"], status.ToString());
            Assert.Greater((int)status["data"]["registered_rule_count"], 0);
            Assert.Greater((int)status["data"]["modules_completed"], 0);
            Assert.Greater((int)status["data"]["diagnostic_count"], 0);
            Assert.IsFalse(status["data"].Value<bool>("native_owner_retained"));
            string cursor = null;
            JObject fixture = null;
            int pages = 0;
            int rowsRead = 0;
            int allocations = 0;
            var samples = new JArray();
            var allocationPaths = new JArray();
            var fixturePaths = new JArray();
            do
            {
                var request = new JObject
                {
                    ["action"] = "issues",
                    ["audit_id"] = id,
                    ["page_size"] = 100,
                };
                if (cursor != null)
                    request["cursor"] = cursor;
                var page = JObject.FromObject(ManageProjectAuditor.HandleCommand(request));
                Assert.IsTrue(page.Value<bool>("success"), page.ToString());
                foreach (JObject row in (JArray)page["data"]["items"])
                {
                    rowsRead++;
                    string path = (string)row["RelativePath"] ?? "";
                    string boundedPath = path.Length > 256 ? path.Substring(0, 256) : path;
                    if (samples.Count < 8)
                        samples.Add(
                            new JObject
                            {
                                ["id"] = row["DescriptorId"],
                                ["category"] = row["Category"],
                                ["path"] = boundedPath,
                            }
                        );
                    if ((string)row["DescriptorId"] == "PAC2002")
                    {
                        allocations++;
                        if (allocationPaths.Count < 16)
                            allocationPaths.Add(boundedPath);
                    }
                    if (path.Contains("ProjectAuditorAuditFixture") && fixturePaths.Count < 16)
                        fixturePaths.Add(new JObject { ["id"] = row["DescriptorId"], ["path"] = boundedPath });
                }
                fixture = ((JArray)page["data"]["items"])
                    .OfType<JObject>()
                    .FirstOrDefault(row =>
                        (string)row["DescriptorId"] == "PAC2002"
                        && ((string)row["RelativePath"] ?? "").EndsWith("ProjectAuditorAuditFixture.cs", StringComparison.Ordinal)
                    );
                cursor = (string)page["data"]["next_cursor"];
                Assert.Less(++pages, 100);
            } while (fixture == null && cursor != null);
            var context = new JObject
            {
                ["status"] = status["data"],
                ["pages"] = pages,
                ["rows_read"] = rowsRead,
                ["pac2002_rows"] = allocations,
                ["samples"] = samples,
                ["pac2002_paths"] = allocationPaths,
                ["fixture_paths"] = fixturePaths,
            };
            Assert.IsNotNull(
                fixture,
                "The real report must contain PAC2002 for the deliberate runtime source fixture. " + context.ToString(Newtonsoft.Json.Formatting.None)
            );
            Assert.AreEqual("Code", (string)fixture["Category"]);
            Assert.IsNotEmpty((string)fixture["Severity"]);
            Assert.IsNotEmpty((string)fixture["Areas"]);
            Assert.IsNotEmpty((string)fixture["Description"]);
            Assert.IsNotEmpty((string)fixture["Recommendation"]);
            Assert.AreEqual(JTokenType.Integer, fixture["Line"].Type);
            Assert.GreaterOrEqual((int)fixture["Line"], 0);
        }
    }
}
