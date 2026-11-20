using System;
using System.Collections;
using System.Collections.Generic;
using System.IO;
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
        private const string AuditorPackage = "com.unity.project-auditor";
        private const string RulesPackage = "com.unity.project-auditor-rules";
        private const string CecilPackage = "com.unity.nuget.mono-cecil";
        private const string FixturePath = "Assets/Scripts/ProjectAuditorAuditFixture.cs";

        [UnityTest]
        [Timeout(300000)]
        [Explicit("Requires the controlled base-plus-Auditor/Cecil fixture profile; select this exact method. See docs/PROJECT_AUDITOR.md.")]
        public IEnumerator InstalledOfficialPackageProducesActualFixtureDiagnostic() => ProducesActualFixtureDiagnostic("package");

        [UnityTest]
        [Timeout(300000)]
        [Explicit("Requires the controlled Editor-integrated Auditor profile with official rules. See docs/PROJECT_AUDITOR.md.")]
        public IEnumerator IntegratedEditorProducesActualFixtureDiagnostic() => ProducesActualFixtureDiagnostic("editor");

        [Test]
        [Explicit("Requires the controlled Editor-integrated Auditor profile without rules. See docs/PROJECT_AUDITOR.md.")]
        public void IntegratedEditorRequiresRulesBeforeStarting() => RequiresUnavailableAudit("editor", "rules_package_not_installed", true);

        [Test]
        [Explicit("Requires the controlled package-absent profile on an Editor without integrated Auditor. See docs/PROJECT_AUDITOR.md.")]
        public void EditorWithoutAuditorRejectsStart() => RequiresUnavailableAudit("none", "package_not_installed", false);

        private static IEnumerator ProducesActualFixtureDiagnostic(string integration)
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
                AuditorPackage,
                RulesPackage,
                CecilPackage,
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
            var installedAuditor = registered.SingleOrDefault(package => package.name == AuditorPackage);
            var capability = ObserveAvailability();
            Assert.AreEqual(integration, capability.Value<string>("integration"), capability.ToString());
            Assert.AreEqual(
                "available",
                capability.Value<string>("state"),
                "The explicitly selected fixture requires the official analysis API and installed rules: " + capability
            );
            Assert.IsTrue(capability.Value<bool>("api_supported"), capability.ToString());
            Assert.IsTrue(capability.Value<bool>("rules_available"), capability.ToString());
            Assert.Greater(capability.Value<int>("bundled_rule_count"), 0, capability.ToString());
            if (integration == "editor")
            {
                AssertNoAuditorDependency();
                Assert.IsNull(installedAuditor, capability.ToString());
                Assert.AreEqual(RulesPackage, capability.Value<string>("rules_package"), capability.ToString());
            }
            else
            {
                Assert.IsNotNull(installedAuditor, capability.ToString());
                Assert.IsTrue(registered.Any(package => package.name == CecilPackage), "The fixture requires Auditor's official Mono.Cecil dependency.");
            }
            Assert.AreEqual(installedAuditor != null, capability.Value<bool>("package_installed"), capability.ToString());
            Assert.AreEqual(installedAuditor?.version, capability.Value<string>("version"), capability.ToString());
            var rules = registered.Single(package => package.name == capability.Value<string>("rules_package"));
            Assert.AreEqual(rules.version, capability.Value<string>("rules_version"), capability.ToString());
            string id = Guid.NewGuid().ToString("N");
            var started = Call("start", id);
            Assert.IsTrue(started.Value<bool>("success"), started.ToString());
            Assert.AreEqual("queued", (string)started["data"]["state"], started.ToString());
            Assert.IsFalse(started["data"].Value<bool>("native_invoked"), started.ToString());
            double deadline = EditorApplication.timeSinceStartup + 270;
            JObject status;
            do
            {
                yield return null;
                status = Call("status", id);
                Assert.IsTrue(status.Value<bool>("success"), status.ToString());
                Assert.Less(EditorApplication.timeSinceStartup, deadline, status.ToString());
            } while ((string)status["data"]["state"] == "queued" || (string)status["data"]["state"] == "running");
            Assert.AreEqual("completed", (string)status["data"]["state"], status.ToString());
            Assert.Greater((int)status["data"]["registered_rule_count"], 0);
            Assert.Greater((int)status["data"]["modules_completed"], 0);
            Assert.Greater((int)status["data"]["diagnostic_count"], 0);
            Assert.AreEqual(0, (int)status["data"]["modules_failed"], status.ToString());
            Assert.AreEqual(0, (int)status["data"]["modules_interrupted"], status.ToString());
            Assert.IsTrue(status["data"].Value<bool>("native_invoked"), status.ToString());
            Assert.IsTrue(status["data"].Value<bool>("diagnostic_count_exact"), status.ToString());
            Assert.AreEqual(false, (bool?)status["data"]["clean"], status.ToString());
            Assert.IsFalse(status["data"].Value<bool>("native_owner_retained"));
            var repeatedStart = Call("start", id);
            Assert.IsTrue(repeatedStart.Value<bool>("success"), repeatedStart.ToString());
            Assert.IsTrue(JToken.DeepEquals(status["data"], repeatedStart["data"]), repeatedStart.ToString());
            string cursor = null;
            JObject fixture = null;
            int fixtureCount = 0;
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
                    if ((string)row["DescriptorId"] == "PAC2002" && path.Replace('\\', '/') == FixturePath)
                    {
                        fixture = row;
                        fixtureCount++;
                    }
                }
                cursor = (string)page["data"]["next_cursor"];
                Assert.Less(++pages, 100);
            } while (cursor != null);
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
            Assert.AreEqual(
                1,
                fixtureCount,
                "The real report must contain one PAC2002 diagnostic for the deliberate runtime fixture. " + context.ToString(Newtonsoft.Json.Formatting.None)
            );
            Assert.AreEqual((int)status["data"]["retained_issue_count"], rowsRead);
            Assert.AreEqual("Code", (string)fixture["Category"]);
            Assert.IsNotEmpty((string)fixture["Severity"]);
            Assert.IsNotEmpty((string)fixture["Areas"]);
            Assert.IsNotEmpty((string)fixture["Description"]);
            Assert.IsNotEmpty((string)fixture["Recommendation"]);
            Assert.AreEqual(JTokenType.Integer, fixture["Line"].Type);
            Assert.Greater((int)fixture["Line"], 0);
            TestContext.WriteLine("AUDITOR_STATUS=" + status["data"].ToString(Newtonsoft.Json.Formatting.None));
            TestContext.WriteLine("AUDITOR_DIAGNOSTIC=" + fixture.ToString(Newtonsoft.Json.Formatting.None));
        }

        private static void RequiresUnavailableAudit(string integration, string reason, bool apiSupported)
        {
            AssertNoAuditorDependency();
            Assert.IsFalse(PackageInfo.GetAllRegisteredPackages().Any(package => package.name == RulesPackage));
            var capability = ObserveAvailability();
            Assert.AreEqual("unavailable", capability.Value<string>("state"), capability.ToString());
            Assert.AreEqual(reason, capability.Value<string>("reason"), capability.ToString());
            Assert.AreEqual(integration, capability.Value<string>("integration"), capability.ToString());
            Assert.AreEqual(apiSupported, capability.Value<bool>("api_supported"), capability.ToString());
            Assert.IsFalse(capability.Value<bool>("rules_available"), capability.ToString());
            Assert.IsFalse(capability.Value<bool>("package_installed"), capability.ToString());
            Assert.IsNotNull(capability["version"], capability.ToString());
            Assert.IsNull(capability.Value<string>("version"), capability.ToString());
            if (integration == "editor")
            {
                Assert.AreEqual(RulesPackage, capability.Value<string>("rules_package"));
                Assert.IsNotNull(capability["rules_version"], capability.ToString());
                Assert.IsNull(capability.Value<string>("rules_version"), capability.ToString());
            }
            string id = Guid.NewGuid().ToString("N");
            var started = Call("start", id);
            Assert.IsFalse(started.Value<bool>("success"), started.ToString());
            Assert.AreEqual("project_auditor_unavailable", started.Value<string>("error"), started.ToString());
            Assert.AreEqual("unavailable", (string)started["data"]["state"], started.ToString());
            Assert.AreEqual(reason, (string)started["data"]["reason"], started.ToString());
            Assert.IsFalse(started["data"].Value<bool>("native_invoked"), started.ToString());
            Assert.IsFalse(started["data"].Value<bool>("native_owner_retained"), started.ToString());
            Assert.AreEqual(JTokenType.Null, started["data"]["clean"].Type, started.ToString());
            var status = Call("status", id);
            Assert.IsTrue(status.Value<bool>("success"), status.ToString());
            Assert.IsTrue(JToken.DeepEquals(started["data"], status["data"]), status.ToString());
            TestContext.WriteLine("AUDITOR_REJECTED_START=" + started["data"].ToString(Newtonsoft.Json.Formatting.None));
        }

        private static JObject ObserveAvailability()
        {
            string[] paths = { "Packages/manifest.json", "Packages/packages-lock.json", "ProjectSettings/ProjectAuditorSettings.asset" };
            string[] ReadConfiguration() => paths.Select(path => File.Exists(path) ? File.ReadAllText(path) : null).ToArray();
            string[] before = ReadConfiguration();
            var target = EditorUserBuildSettings.activeBuildTarget;
            var response = Call("availability");
            Assert.IsTrue(response.Value<bool>("success"), response.ToString());
            Assert.IsTrue(JToken.DeepEquals(response, Call("availability")), response.ToString());
            Assert.AreEqual(target, EditorUserBuildSettings.activeBuildTarget);
            CollectionAssert.AreEqual(before, ReadConfiguration());
            var capability = (JObject)response["data"];
            Assert.AreEqual(JTokenType.Null, capability["supported_version"].Type);
            Assert.IsFalse(capability.Value<bool>("registered_rules_observed"), capability.ToString());
            Assert.IsFalse(capability.Value<bool>("cancel_supported"), capability.ToString());
            Assert.IsFalse(capability.Value<bool>("external_audits_observable"), capability.ToString());
            TestContext.WriteLine("AUDITOR_AVAILABILITY=" + capability.ToString(Newtonsoft.Json.Formatting.None));
            return capability;
        }

        private static void AssertNoAuditorDependency()
        {
            var manifest = JObject.Parse(File.ReadAllText("Packages/manifest.json"));
            Assert.IsNull(manifest["dependencies"]?[AuditorPackage], "The fixture must exercise the Editor without the standalone Auditor dependency.");
        }

        private static JObject Call(string action, string id = null)
        {
            var request = new JObject { ["action"] = action };
            if (id != null)
                request["audit_id"] = id;
            return JObject.FromObject(ManageProjectAuditor.HandleCommand(request));
        }
    }
}
