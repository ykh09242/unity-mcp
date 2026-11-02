using System;
using System.Linq;
using System.Reflection;
using System.Security.Cryptography;
using System.Text;
using System.Threading;
using MCPForUnity.Editor.Tools;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEditor;

namespace MCPForUnityTests.EditMode.Tools
{
    public class ReferenceGraphCommandTests
    {
        private static JObject Call(JObject request) => JObject.FromObject(ManageReferenceGraph.HandleCommand(request));

        [Test]
        public void IdentityIsReturnedAndRequiredBeforeReadingPages()
        {
            JObject status = Call(new JObject { ["action"] = "status" });
            Assert.That(status.Value<bool>("success"), Is.True, status.ToString());
            string instance = status["data"].Value<string>("instance_id");
            string project = status["data"].Value<string>("project_id");
            Assert.That(instance, Is.Not.Empty);
            Assert.That(project, Is.Not.Empty);
            JObject request = new JObject { ["action"] = "inventory", ["page_size"] = 1 };
            Assert.That(Call(request).Value<string>("error"), Is.EqualTo("reference_scope_changed"));
            request["expected_instance_id"] = instance;
            request["expected_project_id"] = "another-project";
            Assert.That(Call(request).Value<string>("error"), Is.EqualTo("reference_scope_changed"));
            request["expected_project_id"] = project;
            JObject result = Call(request);
            Assert.That(result.Value<bool>("success"), Is.True, result.ToString());
            Assert.That(result["data"].Value<string>("instance_id"), Is.EqualTo(instance));
            Assert.That(result["data"].Value<string>("project_id"), Is.EqualTo(project));
            Assert.That(result["data"]["assets"], Is.TypeOf<JArray>());
            request["offset"] = 1;
            request["snapshot"] = "retired-inventory";
            Assert.That(Call(request).Value<string>("error"), Is.EqualTo("reference_inventory_snapshot_invalid"));
        }

        [TestCase("user_id")]
        [TestCase("database")]
        [TestCase("root")]
        public void CollectorRejectsServerStateAndFilesystemAuthority(string field)
        {
            JObject result = Call(new JObject { ["action"] = "status", [field] = "unexpected" });
            Assert.That(result.Value<bool>("success"), Is.False);
        }

        [Test]
        public void CancellationDoesNotRunCollection()
        {
            using (var source = new CancellationTokenSource())
            {
                source.Cancel();
                Assert.Throws<OperationCanceledException>(() => ManageReferenceGraph.HandleCommand(new JObject { ["action"] = "status" }, source.Token));
            }
        }

        [Test]
        public void NumericAndNullIdentityFieldsAreRejected()
        {
            Assert.That(Call(new JObject { ["action"] = "changes", ["after"] = "0" }).Value<bool>("success"), Is.False);
            Assert.That(Call(new JObject { ["action"] = "status", ["expected_instance_id"] = JValue.CreateNull() }).Value<bool>("success"), Is.False);
        }

        [Test]
        public void ProbeAcceptsOptionalBooleanStaticProof()
        {
            JToken status = Call(new JObject { ["action"] = "status" })["data"];
            string guid = AssetDatabase.FindAssets("ReferenceGraphCommandTests t:MonoScript")[0];
            var request = new JObject
            {
                ["action"] = "probe",
                ["paths"] = new JArray(AssetDatabase.GUIDToAssetPath(guid)),
                ["expected_project_id"] = status["project_id"],
                ["expected_instance_id"] = status["instance_id"],
            };
            Assert.IsNull(Call(request)["data"]["targets"][0]["dependency_count"]);
            request["static_proof"] = false;
            Assert.IsNull(Call(request)["data"]["targets"][0]["dependency_count"]);
            request["static_proof"] = true;
            JObject result = Call(request);
            Assert.IsTrue(result.Value<bool>("success"), result.ToString());
            Assert.AreEqual(guid, result["data"]["targets"][0].Value<string>("guid"));
            Assert.AreEqual(JTokenType.Integer, result["data"]["targets"][0]["dependency_count"].Type);
            foreach (JToken invalid in new JArray("true", 1, JValue.CreateNull()))
            {
                request["static_proof"] = invalid;
                Assert.IsFalse(Call(request).Value<bool>("success"));
            }
        }

        [TestCase("ignore_rules")]
        [TestCase("ignore_source")]
        public void IgnoreEvidenceRequiresIdentityAndRejectsFilesystemAuthority(string action)
        {
            var request = new JObject { ["action"] = action };
            Assert.AreEqual("reference_scope_changed", Call(request).Value<string>("error"));
            request["root"] = "outside";
            Assert.IsFalse(Call(request).Value<bool>("success"));
        }

        [TestCase("true")]
        [TestCase(1)]
        public void IgnoreValidationRequiresAnExactBoolean(object value)
        {
            Assert.IsFalse(Call(new JObject { ["action"] = "ignore_rules", ["validate"] = JToken.FromObject(value) }).Value<bool>("success"));
        }

        [Test]
        public void CombinedStatusValidatesTheCurrentIgnoreSnapshot()
        {
            JObject status = (JObject)Call(new JObject { ["action"] = "status" })["data"];
            var rules = Call(
                new JObject
                {
                    ["action"] = "ignore_rules",
                    ["expected_instance_id"] = status["instance_id"],
                    ["expected_project_id"] = status["project_id"],
                    ["page_size"] = 1,
                }
            );
            Assert.IsTrue(rules.Value<bool>("success"), rules.ToString());
            string snapshot = rules["data"].Value<string>("snapshot");
            string digest;
            using (SHA256 hash = SHA256.Create())
                digest = string.Concat(hash.ComputeHash(Encoding.UTF8.GetBytes(snapshot)).Select(value => value.ToString("x2")));
            JObject combined = Call(new JObject { ["action"] = "status", ["validate_ignore_current"] = true });
            Assert.IsTrue(combined.Value<bool>("success"), combined.ToString());
            Assert.IsTrue(combined["data"].Value<bool>("ignore_current_verified"));
            Assert.AreEqual(digest, combined["data"].Value<string>("ignore_snapshot_digest"));
            Assert.AreEqual(status.Value<string>("instance_id"), combined["data"].Value<string>("instance_id"));
            Assert.IsFalse(Call(new JObject { ["action"] = "status", ["validate_ignore_current"] = "true" }).Value<bool>("success"));
        }

        [TestCase("dependencies")]
        [TestCase("source")]
        public void RequestedAssetGuardChecksPathRevisionAndIgnoreBeforeCollection(string action)
        {
            const string path = "Assets/Tests/EditMode/Tools/ReferenceGraphCommandTests.cs";
            JObject status = (JObject)Call(new JObject { ["action"] = "status" })["data"];
            Assert.AreEqual(1, status.Value<int>("asset_guard_version"));
            var request = new JObject
            {
                ["action"] = "ignore_rules",
                ["expected_instance_id"] = status["instance_id"],
                ["expected_project_id"] = status["project_id"],
            };
            Assert.IsTrue(Call(request).Value<bool>("success"));
            JObject proof = (JObject)Call(new JObject { ["action"] = "status", ["validate_ignore_current"] = true })["data"];
            Assert.IsTrue(proof.Value<bool>("ignore_current_verified"));
            var guard = new JObject
            {
                ["path"] = path,
                ["epoch"] = proof["epoch"],
                ["revision"] = proof["revision"],
                ["ignore_digest"] = proof["ignore_snapshot_digest"],
            };
            request["action"] = action;
            request["guid"] = AssetDatabase.AssetPathToGUID(path);
            request["asset_guard"] = guard;
            JObject collected = Call(request);
            Assert.IsTrue(collected.Value<bool>("success"), collected.ToString());
            Assert.IsTrue(collected["data"].Value<bool>("asset_guard_current"));

            foreach (string field in new[] { "path", "epoch", "revision", "ignore_digest" })
            {
                JObject stale = (JObject)request.DeepClone();
                stale["asset_guard"][field] = field == "revision" ? (JToken)(-1) : "stale";
                // Guard even a continuation whose offset would otherwise be invalid.
                stale["offset"] = int.MaxValue;
                JObject result = Call(stale);
                Assert.IsTrue(result.Value<bool>("success"), result.ToString());
                Assert.IsFalse(result["data"].Value<bool>("asset_guard_current"));
                Assert.IsNull(result["data"]["targets"]);
                Assert.IsNull(result["data"]["bytes_base64"]);
            }
            guard.Remove("ignore_digest");
            Assert.IsFalse(Call(request).Value<bool>("success"));
        }

        [Test]
        public void CombinedStatusDoesNotProveAnObsoleteIgnoreSnapshot()
        {
            JObject status = (JObject)Call(new JObject { ["action"] = "status" })["data"];
            JObject captured = Call(
                new JObject
                {
                    ["action"] = "ignore_rules",
                    ["expected_instance_id"] = status["instance_id"],
                    ["expected_project_id"] = status["project_id"],
                }
            );
            Assert.IsTrue(captured.Value<bool>("success"), captured.ToString());
            Type collector = typeof(ManageReferenceGraph).Assembly.GetType("MCPForUnity.Editor.Tools.ReferenceGraphCollector");
            object rules = collector.GetField("ignoreRules", BindingFlags.Static | BindingFlags.NonPublic).GetValue(null);
            rules.GetType().GetMethod("Invalidate", BindingFlags.Instance | BindingFlags.NonPublic).Invoke(rules, null);
            JObject result = Call(new JObject { ["action"] = "status", ["validate_ignore_current"] = true });
            Assert.IsTrue(result.Value<bool>("success"), result.ToString());
            Assert.IsFalse(result["data"].Value<bool>("ignore_current_verified"));
            Assert.IsNull(result["data"]["ignore_snapshot_digest"]);
        }

        [Test]
        public void CompilationPagesRequireIdentityRevisionAndTypedPathArray()
        {
            JObject status = Call(new JObject { ["action"] = "status" })["data"] as JObject;
            var request = new JObject
            {
                ["action"] = "compilation",
                ["paths"] = new JArray(),
                ["revision"] = status["compilation_context"]["revision"],
            };
            Assert.AreEqual("reference_scope_changed", Call(request).Value<string>("error"));
            request["expected_instance_id"] = status["instance_id"];
            request["expected_project_id"] = status["project_id"];
            Assert.IsTrue(Call(request).Value<bool>("success"));
            request["paths"] = new JArray(42);
            Assert.IsFalse(Call(request).Value<bool>("success"));
        }
    }
}
