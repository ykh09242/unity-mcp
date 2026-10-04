using System;
using System.Collections;
using System.Reflection;
using MCPForUnity.Editor.Tools;
using MCPForUnity.Editor.Tools.Build;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEditor;
using UnityEditor.Build;

namespace MCPForUnityTests.EditMode.Tools
{
    public class BuildCommandContractTests
    {
        [TestCase(BuildTarget.StandaloneWindows)]
        [TestCase(BuildTarget.StandaloneWindows64)]
        [TestCase(BuildTarget.StandaloneOSX)]
        [TestCase(BuildTarget.StandaloneLinux64)]
        public void StandaloneServerBackendTargetsDedicatedServerSettings(BuildTarget target)
        {
            Assert.AreEqual(NamedBuildTarget.Server,
                BuildTargetMapping.GetNamedBuildTarget(target, StandaloneBuildSubtarget.Server));
            var player = NamedBuildTarget.FromBuildTargetGroup(BuildTargetGroup.Standalone);
            Assert.AreEqual(player, BuildTargetMapping.GetNamedBuildTarget(target, StandaloneBuildSubtarget.Player));
            Assert.AreEqual(player, BuildTargetMapping.GetNamedBuildTarget(target));
        }

        [TestCase(BuildTarget.Android, BuildTargetGroup.Android)]
        [TestCase(BuildTarget.iOS, BuildTargetGroup.iOS)]
        [TestCase(BuildTarget.WebGL, BuildTargetGroup.WebGL)]
        public void NonStandaloneBackendIgnoresStandaloneSubtarget(BuildTarget target, BuildTargetGroup group)
        {
            Assert.AreEqual(NamedBuildTarget.FromBuildTargetGroup(group),
                BuildTargetMapping.GetNamedBuildTarget(target, StandaloneBuildSubtarget.Server));
        }

        [Test]
        public void PollingMetadataFitsServerRegistrationBudget()
        {
            var metadata = typeof(ManageBuild).GetCustomAttribute<McpForUnityToolAttribute>();
            Assert.IsNotNull(metadata);
            Assert.IsTrue(metadata.RequiresPolling);
            Assert.AreEqual("status", metadata.PollAction);
            Assert.That(metadata.MaxPollSeconds, Is.InRange(1, 600));
        }

        [TestCase(false)]
        [TestCase(true)]
        public void OmittedOrNullSettingsValue_ReadsWithoutClearing(bool explicitNull)
        {
            var target = NamedBuildTarget.FromBuildTargetGroup(
                BuildPipeline.GetBuildTargetGroup(EditorUserBuildSettings.activeBuildTarget));
            string original = PlayerSettings.GetScriptingDefineSymbols(target);
            var parameters = new JObject { ["action"] = "settings", ["property"] = "defines" };
            if (explicitNull) parameters["value"] = JValue.CreateNull();
            var response = JObject.FromObject(ManageBuild.HandleCommand(parameters));
            Assert.IsTrue(response.Value<bool>("success"));
            Assert.AreEqual(original, PlayerSettings.GetScriptingDefineSymbols(target));
        }

        [Test]
        public void ExplicitEmptyDefines_ClearsAndReturnsEmptyValue()
        {
            var target = NamedBuildTarget.FromBuildTargetGroup(
                BuildPipeline.GetBuildTargetGroup(EditorUserBuildSettings.activeBuildTarget));
            string original = PlayerSettings.GetScriptingDefineSymbols(target);
            try
            {
                PlayerSettings.SetScriptingDefineSymbols(target, "MCP_BUILD_CONTRACT_TEST");
                var response = JObject.FromObject(ManageBuild.HandleCommand(new JObject
                    { ["action"] = "settings", ["property"] = "defines", ["value"] = "" }));
                Assert.IsTrue(response.Value<bool>("success"));
                Assert.AreEqual("", response["data"].Value<string>("value"));
                Assert.AreEqual("", PlayerSettings.GetScriptingDefineSymbols(target));
            }
            finally { PlayerSettings.SetScriptingDefineSymbols(target, original); }
        }

        [Test]
        public void ExplicitEmptyBackend_IsRejectedInsteadOfRead()
        {
            var response = JObject.FromObject(ManageBuild.HandleCommand(new JObject
                { ["action"] = "settings", ["property"] = "scripting_backend", ["value"] = "" }));
            Assert.IsFalse(response.Value<bool>("success"));
            StringAssert.Contains("Unknown scripting_backend", response.Value<string>("error"));
        }

#if UNITY_6000_0_OR_NEWER
        [Test]
        public void MissingProfile_RejectsBeforeChangingScriptingBackend()
        {
            var target = NamedBuildTarget.FromBuildTargetGroup(
                BuildPipeline.GetBuildTargetGroup(EditorUserBuildSettings.activeBuildTarget));
            var original = PlayerSettings.GetScriptingBackend(target);
            string requested = original == ScriptingImplementation.IL2CPP ? "mono" : "il2cpp";
            try
            {
                var response = JObject.FromObject(ManageBuild.HandleCommand(new JObject
                    { ["action"] = "build", ["profile"] = "Assets/MCPMissingBuildProfile-" + Guid.NewGuid().ToString("N") + ".asset", ["scripting_backend"] = requested }));
                Assert.IsFalse(response.Value<bool>("success"));
                StringAssert.Contains("Build profile not found", response.Value<string>("error"));
                Assert.AreEqual(original, PlayerSettings.GetScriptingBackend(target));
            }
            finally { PlayerSettings.SetScriptingBackend(target, original); }
        }
#endif

        [TestCase(BuildJobState.Pending)]
        [TestCase(BuildJobState.Building)]
        [TestCase(BuildJobState.Succeeded)]
        [TestCase(BuildJobState.Failed)]
        [TestCase(BuildJobState.Cancelled)]
        public void BatchStatus_RemainsPendingUntilTerminal(BuildJobState state)
        {
            string id = "mcp-test-batch-" + Guid.NewGuid().ToString("N");
            BuildJobStore.AddBatchJob(new BatchJob(id) { State = state });
            try
            {
                var response = JObject.FromObject(ManageBuild.HandleCommand(new JObject
                    { ["action"] = "status", ["job_id"] = id }));
                Assert.IsTrue(response.Value<bool>("success"));
                Assert.AreEqual(id, response["data"].Value<string>("job_id"));
                Assert.AreEqual(state.ToString().ToLowerInvariant(), response["data"].Value<string>("result"));
                Assert.AreEqual(state == BuildJobState.Pending || state == BuildJobState.Building ? "pending" : null,
                    response.Value<string>("_mcp_status"));
            }
            finally
            {
                var store = (IDictionary)typeof(BuildJobStore).GetField("_batchJobs", BindingFlags.NonPublic | BindingFlags.Static).GetValue(null);
                store.Remove(id);
            }
        }
    }
}
