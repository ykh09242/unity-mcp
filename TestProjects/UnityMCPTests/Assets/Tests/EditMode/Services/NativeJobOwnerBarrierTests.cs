using System;
using System.Collections.Generic;
using System.Reflection;
using MCPForUnity.Editor.Services;
using MCPForUnity.Editor.Tools;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEditor;

namespace MCPForUnityTests.Editor.Services
{
    public class NativeJobOwnerBarrierTests
    {
        [Test]
        public void FailedPublicJobStillOwnsUnreleasedNativeResources()
        {
            var jobs = (Dictionary<string, TestJob>)typeof(TestJobManager).GetField("Jobs", BindingFlags.Static | BindingFlags.NonPublic).GetValue(null);
            string id = Guid.NewGuid().ToString("N");
            using var control = new NativeTestRunControl(id, 0, () => 0, () => null, _ => false);
            var job = new TestJob
            {
                JobId = id,
                Status = TestJobStatus.Failed,
                Control = control,
            };
            try
            {
                jobs.Add(id, job);
                Assert.IsTrue(TestJobManager.NativeAdmissionBlocked);
                var snapshot = (JObject)
                    typeof(EditorStateCache)
                        .GetMethod("BuildSnapshot", BindingFlags.Static | BindingFlags.NonPublic)
                        .Invoke(null, new object[] { "native-control-test" });
                Assert.IsTrue((bool)snapshot["tests"]["native_control_unreleased"]);
                if ((bool)snapshot["tests"]["is_running"] == false)
                    Assert.AreEqual("native_test_cleanup", (string)snapshot["activity"]["phase"]);
                control.State["termination"] = "unknown";
                Assert.IsTrue(TestJobManager.NativeRecoveryRequired);
                control.AbortBeforeExecute();
                Assert.IsTrue(jobs[id].Control.Released);
                snapshot = (JObject)
                    typeof(EditorStateCache)
                        .GetMethod("BuildSnapshot", BindingFlags.Static | BindingFlags.NonPublic)
                        .Invoke(null, new object[] { "native-control-released-test" });
                Assert.IsFalse((bool)snapshot["tests"]["native_control_unreleased"]);
            }
            finally
            {
                jobs.Remove(id);
            }
        }

        [TestCase("MCPForUnity.Packages.NativeMutationV1", true)]
        [TestCase("MCPForUnity.Packages.NativeQueryV1", false)]
        public void LostRequestMarkerReportsRecoveryWithoutInventingALiveRequest(string key, bool mutation)
        {
            if (ManagePackages.HasNativeMutation || ManagePackages.HasNativeQuery)
                Assert.Ignore("An unowned native package request is active.");
            bool previous = SessionState.GetBool(key, false);
            try
            {
                SessionState.SetBool(key, true);
                Assert.IsFalse(ManagePackages.HasNativeMutation);
                Assert.IsFalse(ManagePackages.HasNativeQuery);
                Assert.IsTrue(mutation ? ManagePackages.MutationRecoveryRequired : ManagePackages.QueryRecoveryRequired);
            }
            finally
            {
                SessionState.SetBool(key, previous);
            }
        }

        [Test]
        public void HistoricalUnknownPackageOutcomesDoNotInventNativeOwnership()
        {
            if (
                ManagePackages.HasNativeMutation
                || ManagePackages.HasNativeQuery
                || ManagePackages.MutationRecoveryRequired
                || ManagePackages.QueryRecoveryRequired
            )
                Assert.Ignore("An unowned native package request or recovery receipt is active.");
            var jobs = (Dictionary<string, PackageJob>)typeof(PackageJobManager).GetField("Jobs", BindingFlags.Static | BindingFlags.NonPublic).GetValue(null);
            string id = Guid.NewGuid().ToString("N");
            try
            {
                foreach (string operation in new[] { "add", "remove", "embed", "resolve" })
                {
                    jobs[id] = new PackageJob
                    {
                        JobId = id,
                        Operation = operation,
                        Status = PackageJobStatus.Unknown,
                        RecoveryReason = "native_completion_unobserved",
                    };
                    Assert.IsFalse(ManagePackages.HasNativeMutation);
                    Assert.IsFalse(ManagePackages.HasNativeQuery);
                    Assert.IsFalse(ManagePackages.MutationRecoveryRequired);
                    Assert.IsFalse(ManagePackages.QueryRecoveryRequired);
                }
            }
            finally
            {
                jobs.Remove(id);
            }
        }
    }
}
