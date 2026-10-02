using System.Collections.Generic;
using System.Threading;
using System.Threading.Tasks;
using MCPForUnity.Editor.Constants;
using MCPForUnity.Editor.Services;
using MCPForUnity.Editor.Services.Transport;
using MCPForUnity.Editor.Tools;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEditor;

namespace MCPForUnityTests.Editor.Services
{
    public class ToolConsentSecurityTests
    {
        private static readonly string[] Names = { "execute_code", "manage_packages", "execute_menu_item", "manage_build", "batch_execute" };
        private readonly Dictionary<string, bool?> saved = new();

        [SetUp]
        public void SetUp()
        {
            foreach (string name in Names)
                foreach (string key in new[] { EditorPrefKeys.ToolEnabledPrefix + name, ToolDiscoveryService.GetConsentPreferenceKey(name) })
                {
                    saved[key] = EditorPrefs.HasKey(key) ? EditorPrefs.GetBool(key) : (bool?)null;
                    EditorPrefs.DeleteKey(key);
                }
        }

        [TearDown]
        public void TearDown()
        {
            foreach (var entry in saved)
                if (entry.Value.HasValue) EditorPrefs.SetBool(entry.Key, entry.Value.Value);
                else EditorPrefs.DeleteKey(entry.Key);
        }

        [TestCaseSource(nameof(Names))]
        public async Task DispatcherRejectsToolsWithoutConsent(string name)
        {
            string result = await TransportCommandDispatcher.ExecuteCommandJsonAsync(
                new JObject { ["type"] = name, ["params"] = new JObject() }.ToString(), CancellationToken.None);
            StringAssert.Contains("disabled", result.ToLowerInvariant());
        }

        [TestCase("execute_code")]
        [TestCase("manage_packages")]
        [TestCase("execute_menu_item")]
        [TestCase("manage_build")]
        public async Task BatchRejectsToolsWithoutConsent(string name)
        {
            var result = JObject.FromObject(await BatchExecute.HandleCommand(new JObject
            {
                ["commands"] = new JArray(new JObject { ["tool"] = name })
            }));
            Assert.IsFalse(result.Value<bool>("success"));
            StringAssert.Contains("disabled", result.ToString());
        }

        [TestCaseSource(nameof(Names))]
        public void FreshAndPreviouslyImplicitEnablementRequireEditorConsent(string name)
        {
            var service = new ToolDiscoveryService();
            Assert.IsTrue(service.GetToolMetadata(name).RequiresExplicitConsent);
            Assert.IsFalse(service.IsToolEnabled(name));
            EditorPrefs.SetBool(EditorPrefKeys.ToolEnabledPrefix + name, true);
            Assert.IsFalse(service.IsToolEnabled(name));
            service.InvalidateCache();
            Assert.IsFalse(service.IsToolEnabled(name));
            service.SetToolEnabled(name, true);
            Assert.IsTrue(new ToolDiscoveryService().IsToolEnabled(name));
            service.SetToolEnabled(name, false);
            Assert.IsFalse(new ToolDiscoveryService().IsToolEnabled(name));
        }
    }
}
