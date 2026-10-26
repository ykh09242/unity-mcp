using MCPForUnity.Editor.Services;
using Newtonsoft.Json.Linq;
using NUnit.Framework;

namespace MCPForUnity.Editor.Tests.EditMode.Services
{
    public class ToolOperationPolicyTests
    {
        [TestCase("ping", "{}", "Control")]
        [TestCase("manage_packages", "{\"action\":\"ping\"}", "Control")]
        [TestCase("get_selection", "{}", "SharedRead")]
        [TestCase("manage_script", "{\"action\":\"get_sha\"}", "SharedRead")]
        [TestCase("manage_script", "{\"action\":\"validate\"}", "SharedRead")]
        [TestCase("manage_script", "{\"action\":\"validate\",\"level\":\"basic\"}", "SharedRead")]
        [TestCase("manage_script", "{\"action\":\"validate\",\"level\":\"standard\"}", "SharedRead")]
        [TestCase("manage_script", "{\"action\":\"validate\",\"level\":\"BASIC\"}", "SharedRead")]
        [TestCase("manage_script", "{\"action\":\"validate\",\"level\":null}", "Exclusive")]
        [TestCase("manage_script", "{\"action\":\"validate\",\"level\":\"strict\"}", "Exclusive")]
        [TestCase("manage_script", "{\"action\":\"validate\",\"level\":\"comprehensive\"}", "Exclusive")]
        [TestCase("manage_script", "{\"action\":\"validate\",\"level\":false}", "Exclusive")]
        [TestCase("manage_script", "{\"action\":\"read\"}", "Exclusive")]
        [TestCase("find_gameobjects", "{}", "SharedRead")]
        [TestCase("query_physics", "{\"action\":\"raycast\"}", "SharedRead")]
        [TestCase("apply_physics_force", "{\"action\":\"raycast\"}", "Exclusive")]
        [TestCase("capture_ui_snapshot", "{\"readOnlyHint\":true}", "Exclusive")]
        [TestCase("inspect_menu_item", "{}", "Exclusive")]
        [TestCase("get_unknown_future_tool", "{\"readOnlyHint\":true}", "Exclusive")]
        [TestCase("manage_reference_graph", "{\"action\":\"query\"}", "Exclusive")]
        [TestCase("execute_menu_item_observed", "{\"action\":\"inspect\"}", "Exclusive")]
        [TestCase("batch_execute", "{\"commands\":[{\"tool\":\"ping\"}]}", "Exclusive")]
        [TestCase("manage_ugui", "{\"action\":\"diagnose_raycast\"}", "SharedRead")]
        [TestCase("manage_ugui", "{\"action\":\"diagnose_raycast\",\"allow_custom_filters\":false}", "SharedRead")]
        [TestCase("manage_ugui", "{\"action\":\"diagnose_raycast\",\"allow_custom_filters\":null}", "Exclusive")]
        [TestCase("manage_ugui", "{\"action\":\"diagnose_raycast\",\"allow_custom_filters\":\"false\"}", "Exclusive")]
        [TestCase("manage_ui", "{\"action\":\"patch_source\",\"preview\":true}", "SharedRead")]
        [TestCase("manage_ui", "{\"action\":\"patch_source\",\"preview\":false}", "Exclusive")]
        public void ReviewedModesRequireExactRouteAndFlags(string command, string json, string expected)
        {
            // Given / When
            var policy = ToolOperationPolicy.Resolve(command, JObject.Parse(json), true);
            // Then
            Assert.That(policy.Mode.ToString(), Is.EqualTo(expected));
        }

        [TestCase("ping")]
        [TestCase("get_selection")]
        [TestCase("query_physics")]
        public void RegisteredOverridesNeverInheritBuiltinEffects(string command)
        {
            // Given / When
            var policy = ToolOperationPolicy.Resolve(command, JObject.Parse("{\"action\":\"raycast\",\"readOnlyHint\":true}"), false);
            // Then
            Assert.That(policy.Mode, Is.EqualTo(ToolOperationMode.Exclusive));
            Assert.That(policy.Reason, Is.EqualTo("untrusted_handler"));
        }

        [TestCase("refresh_unity", "{}", "TriggerOnly")]
        [TestCase("manage_packages", "{\"action\":\"resolve_packages\"}", "TriggerOnly")]
        [TestCase("manage_packages", "{\"action\":\"list_packages\"}", "TrackedJob")]
        [TestCase("run_tests", "{}", "TrackedJob")]
        [TestCase("manage_editor", "{\"action\":\"play\"}", "TriggerOnly")]
        [TestCase("manage_editor", "{\"action\":\"play\",\"wait_until\":\"first_frame\"}", "TrackedJob")]
        [TestCase("manage_input", "{\"action\":\"key\",\"is_playing\":true}", "Command")]
        public void LifetimesDescribeOwnedJobsRatherThanStablePlayOrRequestedFlags(string command, string json, string expected)
        {
            // Given / When
            var policy = ToolOperationPolicy.Resolve(command, JObject.Parse(json), true);
            // Then
            Assert.That(policy.Lifetime.ToString(), Is.EqualTo(expected));
        }

        [TestCase("ping", "Control")]
        [TestCase("get_selection", "SharedRead")]
        [TestCase("find_gameobjects", "SharedRead")]
        [TestCase("manage_build", "Exclusive")]
        [TestCase("query_physics", "Exclusive")]
        [TestCase("manage_ugui", "Exclusive")]
        public void MissingNullEmptyAndNonstringActionsHaveDistinctContracts(string command, string missingMode)
        {
            // Given / When
            var missing = ToolOperationPolicy.Resolve(command, new JObject(), true);
            var explicitNull = ToolOperationPolicy.Resolve(command, new JObject { ["action"] = JValue.CreateNull() }, true);
            // Then
            Assert.That(missing.Mode.ToString(), Is.EqualTo(missingMode));
            Assert.That(explicitNull.Mode, Is.EqualTo(missing.Mode));
            Assert.That(explicitNull.Lifetime, Is.EqualTo(missing.Lifetime));
            foreach (var action in new JToken[] { new JValue(""), new JValue(false), new JValue(0), new JArray(), new JObject() })
                Assert.That(ToolOperationPolicy.Resolve(command, new JObject { ["action"] = action }, true).Mode, Is.EqualTo(ToolOperationMode.Exclusive));
        }
    }
}
