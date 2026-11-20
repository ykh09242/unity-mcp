using System;
using MCPForUnity.Editor.Tools.Addressables;
using Newtonsoft.Json.Linq;
using NUnit.Framework;

namespace MCPForUnityTests.Editor.Tools
{
    public class AddressablesCapabilityReportTests
    {
        private sealed class MissingRegistration
        {
            public void CreateOrMoveEntry(string guid, object group) => throw new InvalidOperationException();
        }

        [TestCase("inspect")]
        [TestCase("preview_prefab")]
        [TestCase("create_prefab")]
        [TestCase("prefab_status")]
        [TestCase("reconcile_prefab")]
        [TestCase("replan_prefab")]
        public void InspectionCopyAndRecoveryDoNotNeedRegistrationWriter(string action)
        {
            Assert.IsNull(AddressablesCapabilities.Writer(action, typeof(MissingRegistration), typeof(object), typeof(object), null));
            var error = Assert.Throws<AddressablesException>(() =>
                AddressablesCapabilities.Writer("register_prefab", typeof(MissingRegistration), typeof(object), typeof(object), null)
            );
            Assert.AreEqual("addressables_api_unsupported", error.Code);
            StringAssert.Contains("CreateOrMoveEntry", error.Message);
        }

        [Test]
        public void NoSettingsAssetRequiredForPerActionPackageEvidence()
        {
            var result = JObject.FromObject(ManageAddressables.HandleCommand(new JObject { ["action"] = "capabilities" }));
            Assert.IsTrue(result.Value<bool>("success"), result.ToString());
            var operations = (JObject)result["data"]["operations"];
            foreach (string name in new[] { "inspect", "create_prefab", "register_prefab", "reconcile_prefab", "replan_prefab" })
            {
                var observation = (JObject)operations[name];
                Assert.AreEqual(JTokenType.Boolean, observation["available"].Type);
                if (!observation.Value<bool>("available"))
                    Assert.IsNotEmpty(observation.Value<string>("reason"));
            }
            Assert.IsTrue(operations["cancel_prefab"].Value<bool>("available"));
            if (Type.GetType("UnityEditor.AddressableAssets.Settings.AddressableAssetSettings, Unity.Addressables.Editor") == null)
                Assert.AreEqual("addressables_unavailable", operations["inspect"].Value<string>("code"));
        }
    }
}
