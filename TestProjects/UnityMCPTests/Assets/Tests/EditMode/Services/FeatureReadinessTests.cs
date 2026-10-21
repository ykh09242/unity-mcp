using System;
using MCPForUnity.Editor.Resources.Project;
using MCPForUnity.Editor.Services;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEditor;
using UnityEngine.SceneManagement;

namespace MCPForUnityTests.Editor.Services
{
    public class FeatureReadinessTests
    {
        [TestCase(false, null, false, false, "package_metadata_unknown", "unknown")]
        [TestCase(true, null, false, false, "missing_package", "unsupported")]
        [TestCase(true, "2.10.1", false, true, "unsupported_package_version", "unsupported")]
        [TestCase(true, "3.1.0", false, false, "package_api_unavailable", "unknown")]
        [TestCase(true, "3.1.0", true, false, "preflight_required", "unknown")]
        public void PackageEvidenceNeverTreatsInstallationAsTargetReadiness(
            bool observed,
            string version,
            bool api,
            bool incompatible,
            string reason,
            string status
        )
        {
            JObject result = FeatureReadiness.DescribePackage(
                "com.unity.cinemachine",
                version,
                observed,
                api,
                incompatible,
                true,
                false,
                "CinemachineCamera API",
                "inspect_camera_targets"
            );
            Assert.AreEqual(reason, result["reason"].Value<string>());
            Assert.AreEqual(status, result["status"].Value<string>());
            Assert.AreNotEqual(true, result["ready"].Value<bool?>());
            if (!observed || (version != null && !incompatible && !api))
                Assert.AreEqual(JTokenType.Null, result["supported"].Type);
        }

        [Test]
        public void DisabledToolAndBusyEditorRemainIndependentOfPackageSupport()
        {
            JObject result = FeatureReadiness.Describe(true, "preflight_required", false, true, "inspect_camera_targets");
            Assert.IsTrue(result["supported"].Value<bool>());
            Assert.IsFalse(result["ready"].Value<bool>());
            Assert.AreEqual("blocked", result["status"].Value<string>());
            CollectionAssert.AreEqual(new[] { "tool_disabled", "editor_busy" }, result["blocking_reasons"].ToObject<string[]>());
            Assert.AreEqual("review_tool_enablement", result["next_action"].Value<string>());
            result = FeatureReadiness.Describe(false, "missing_platform_module", true, false, "inspect_build_modules");
            Assert.AreEqual("unsupported", result["status"].Value<string>());
            Assert.AreEqual("missing_platform_module", result["reason"].Value<string>());
        }

        [Test]
        public void PackageCacheInvalidatesAndDoesNotCacheFailedObservation()
        {
            int calls = 0;
            string version = null;
            var cache = new FeatureReadiness.PackageVersions(_ =>
            {
                calls++;
                return version;
            });
            Assert.IsTrue(cache.TryRead("package", out string first));
            Assert.IsNull(first);
            version = "3.1.0";
            Assert.IsTrue(cache.TryRead("package", out string cached));
            Assert.IsNull(cached);
            Assert.AreEqual(1, calls);
            cache.Invalidate();
            Assert.IsTrue(cache.TryRead("package", out string changed));
            Assert.AreEqual(version, changed);
            Assert.AreEqual(2, calls);
            int attempts = 0;
            cache = new FeatureReadiness.PackageVersions(_ =>
            {
                if (++attempts == 1)
                    throw new InvalidOperationException();
                return "2.7.0";
            });
            Assert.IsFalse(cache.TryRead("package", out _));
            Assert.IsTrue(cache.TryRead("package", out string recovered));
            Assert.AreEqual("2.7.0", recovered);
        }

        [Test]
        public void ProjectInfoReadinessIsOptInAndPreservesCurrentProjectState()
        {
            var target = EditorUserBuildSettings.activeBuildTarget;
            var scene = SceneManager.GetActiveScene();
            bool dirty = scene.isDirty;
            JObject ordinary = JObject.FromObject(ProjectInfo.HandleCommand(new JObject()));
            Assert.IsTrue(ordinary["success"].Value<bool>());
            Assert.AreEqual(JTokenType.Null, ordinary["data"]["featureReadiness"].Type);
            JObject detailed = JObject.FromObject(ProjectInfo.HandleCommand(new JObject { ["include_readiness"] = true }));
            Assert.IsTrue(detailed["success"].Value<bool>(), detailed.ToString());
            var features = (JObject)detailed["data"]["featureReadiness"];
            Assert.AreEqual(5, features.Count);
            foreach (JProperty feature in features.Properties())
                Assert.AreNotEqual(true, feature.Value["ready"].Value<bool?>());
            Assert.AreEqual("Player", features["build"]["observed"]["subtarget"].Value<string>());
            Assert.AreEqual(JTokenType.Null, features["build"]["observed"]["backend_toolchain_ready"].Type);
            Assert.AreEqual(target, EditorUserBuildSettings.activeBuildTarget);
            Assert.AreEqual(dirty, scene.isDirty);
            Assert.AreEqual("cinemachine", FeatureReadiness.FeatureForTool("manage_cinemachine"));
            Assert.IsNull(FeatureReadiness.FeatureForTool("manage_camera"));
        }
    }
}
