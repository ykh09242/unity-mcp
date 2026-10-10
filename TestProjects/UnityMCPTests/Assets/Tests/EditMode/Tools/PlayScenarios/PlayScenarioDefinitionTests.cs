using System;
using System.Linq;
using MCPForUnity.Editor.Services.PlayScenarios;
using Newtonsoft.Json.Linq;
using NUnit.Framework;

namespace MCPForUnityTests.Editor.Tools.PlayScenarios
{
    public class PlayScenarioDefinitionTests
    {
        internal static JObject Valid(string name = "menu-start") =>
            new JObject
            {
                ["name"] = name,
                ["steps"] = new JArray(
                    new JObject
                    {
                        ["name"] = "Load menu",
                        ["action"] = "load_scene",
                        ["scene"] = "Assets/Scenes/Menu.unity",
                    },
                    new JObject
                    {
                        ["name"] = "Start",
                        ["action"] = "click_ui",
                        ["target"] = "Canvas/Start",
                    },
                    new JObject
                    {
                        ["name"] = "Game scene",
                        ["action"] = "wait_scene",
                        ["scene"] = "Assets/Scenes/Game.unity",
                    },
                    new JObject
                    {
                        ["name"] = "Player",
                        ["action"] = "wait_object",
                        ["target"] = "Player",
                    }
                ),
            };

        [Test]
        public void ParsePreservesOrderAndDefaults()
        {
            var parsed = PlayScenarioDefinition.Parse(Valid());
            Assert.AreEqual("menu-start", parsed.Name);
            Assert.AreEqual(250, parsed.PollIntervalMs);
            CollectionAssert.AreEqual(new[] { "load_scene", "click_ui", "wait_scene", "wait_object" }, parsed.Steps.Select(step => step.Action));
            Assert.IsTrue(parsed.Steps.All(step => step.TimeoutSeconds == 30));
            Assert.AreEqual("Canvas/Start", parsed.Steps[1].Target);
            Assert.IsNull(parsed.Steps[0].Target);
        }

        [Test]
        public void RejectsInvalidNamesAndUnknownFields()
        {
            foreach (string name in new[] { "", "Upper", "../a", "a/b", "a\\b", "-a", "a\n", new string('a', 65) })
                Assert.Throws<ArgumentException>(() => PlayScenarioDefinition.Parse(Valid(name)), name);
            var value = Valid();
            value["extra"] = true;
            Assert.Throws<ArgumentException>(() => PlayScenarioDefinition.Parse(value));
            value = Valid();
            ((JObject)value["steps"][0])["extra"] = true;
            Assert.Throws<ArgumentException>(() => PlayScenarioDefinition.Parse(value));
        }

        [Test]
        public void EnforcesIntegerBoundsAndTypes()
        {
            foreach (JToken number in new JToken[] { 99, 2001, 250.0, "250", true, JValue.CreateNull(), long.MaxValue })
            {
                var value = Valid();
                value["poll_interval_ms"] = number;
                Assert.Throws<ArgumentException>(() => PlayScenarioDefinition.Parse(value));
            }
            foreach (JToken number in new JToken[] { 0, 121, 30.0, "30", JValue.CreateNull() })
            {
                var value = Valid();
                value["steps"][0]["timeout_seconds"] = number;
                Assert.Throws<ArgumentException>(() => PlayScenarioDefinition.Parse(value));
            }
            var valid = Valid();
            valid["poll_interval_ms"] = 100;
            valid["steps"][0]["timeout_seconds"] = 120;
            Assert.AreEqual(120, PlayScenarioDefinition.Parse(valid).Steps[0].TimeoutSeconds);
        }

        [Test]
        public void RejectsNoncanonicalAndForbiddenPaths()
        {
            foreach (
                string scene in new[]
                {
                    "/Assets/Menu.unity",
                    "Assets/../Menu.unity",
                    "Assets//Menu.unity",
                    "Assets/./Menu.unity",
                    "Assets/Menu.UNITY",
                    "Assets/Resources/GameData/Menu.unity",
                    "Assets/gAmEdAtA/Menu.unity",
                    "Assets/C:/Menu.unity",
                    "Assets\\Menu.unity",
                    "Assets/A. /Menu.unity",
                }
            )
            {
                var value = Valid();
                value["steps"][0]["scene"] = scene;
                Assert.Throws<ArgumentException>(() => PlayScenarioDefinition.Parse(value), scene);
            }
            foreach (
                string target in new[]
                {
                    "/Root",
                    "Root/",
                    "Root//Child",
                    "Root/../Child",
                    "Root/./Child",
                    "Root\\Child",
                    "Root\n",
                    new string('a', 4097),
                    string.Join("/", Enumerable.Repeat("a", 129)),
                }
            )
            {
                var value = Valid();
                value["steps"][1]["target"] = target;
                Assert.Throws<ArgumentException>(() => PlayScenarioDefinition.Parse(value), target);
            }
            var accepted = Valid();
            accepted["steps"][1]["target"] = string.Join("/", Enumerable.Repeat("a", 128));
            Assert.AreEqual(4, PlayScenarioDefinition.Parse(accepted).Steps.Count);
        }

        [Test]
        public void EnforcesActionSpecificFieldsAndFirstLoad()
        {
            foreach (string action in new[] { "", "load", "wait_object", "click_ui", "wait_scene" })
            {
                var value = Valid();
                value["steps"][0]["action"] = action;
                Assert.Throws<ArgumentException>(() => PlayScenarioDefinition.Parse(value));
            }
            var scene = Valid();
            scene["steps"][0]["target"] = JValue.CreateNull();
            Assert.Throws<ArgumentException>(() => PlayScenarioDefinition.Parse(scene));
            var target = Valid();
            target["steps"][1]["scene"] = "Assets/Menu.unity";
            Assert.Throws<ArgumentException>(() => PlayScenarioDefinition.Parse(target));
            var empty = Valid();
            empty["steps"] = new JArray();
            Assert.Throws<ArgumentException>(() => PlayScenarioDefinition.Parse(empty));
            var many = Valid();
            many["steps"] = new JArray(Enumerable.Range(0, 33).Select(_ => Valid()["steps"][0].DeepClone()));
            Assert.Throws<ArgumentException>(() => PlayScenarioDefinition.Parse(many));
            var wrong = Valid();
            wrong["steps"] = new JArray(1);
            Assert.Throws<ArgumentException>(() => PlayScenarioDefinition.Parse(wrong));
            Assert.Throws<ArgumentException>(() => PlayScenarioDefinition.Parse(null));
        }

        [Test]
        public void AdvancedConditionsAndLifecycleRoundTripWithoutLeakingWaitFields()
        {
            var value = Valid();
            value["setup_steps"] = new JArray(value["steps"][0].DeepClone());
            value["steps"] = new JArray(value["steps"][3].DeepClone());
            value["cleanup_steps"] = new JArray(
                new JObject
                {
                    ["name"] = "Cleanup",
                    ["action"] = "wait_scene",
                    ["scene"] = "Assets/Scenes/Menu.unity",
                }
            );
            value["steps"][0]["component"] = "Example.PlayerState";
            value["steps"][0]["property"] = new JObject { ["path"] = "ready", ["equals"] = true };
            value["steps"][0]["active"] = false;
            value["steps"][0]["count"] = 1;
            value["steps"][0]["stable_for_ms"] = 500;
            value["log_policy"] = new JObject { ["allowed_messages"] = new JArray("Expected\nmessage") };
            value["metrics"] = new JObject { ["enabled"] = true };
            value["diagnostics"] = new JObject { ["screenshot_on_failure"] = true };
            var definition = PlayScenarioDefinition.Parse(value);
            Assert.AreEqual("strict", definition.LogPolicy.Mode);
            Assert.IsTrue(definition.Metrics.Enabled);
            Assert.IsTrue(definition.Diagnostics.ScreenshotOnFailure);
            Assert.AreEqual(false, definition.Steps[0].Active);
            Assert.AreEqual("ready", definition.Steps[0].Property.Path);
            var serialized = JObject.FromObject(definition);
            Assert.IsNull(serialized["setup_steps"][0]["count"]);
            Assert.IsNull(serialized["cleanup_steps"][0]["property"]);
            Assert.AreEqual(500, PlayScenarioDefinition.Parse(serialized).Steps[0].StableForMs);
        }

        [Test]
        public void AdvancedFieldsRejectNullCoercionAndWrongActions()
        {
            foreach (
                string key in new[]
                {
                    "setup_steps",
                    "cleanup_steps",
                    "log_policy",
                    "metrics",
                    "diagnostics",
                    "completion_stable_ms",
                    "cleanup_timeout_seconds",
                }
            )
            {
                var value = Valid();
                value[key] = JValue.CreateNull();
                Assert.Throws<ArgumentException>(() => PlayScenarioDefinition.Parse(value), key);
            }
            foreach (string key in new[] { "count", "active", "component", "property", "stable_for_ms" })
            {
                var value = Valid();
                value["steps"][3][key] = JValue.CreateNull();
                Assert.Throws<ArgumentException>(() => PlayScenarioDefinition.Parse(value), key);
                value = Valid();
                value["steps"][1][key] = JValue.CreateNull();
                Assert.Throws<ArgumentException>(() => PlayScenarioDefinition.Parse(value), "click " + key);
            }
            var wrong = Valid();
            wrong["metrics"] = new JObject { ["enabled"] = "true" };
            Assert.Throws<ArgumentException>(() => PlayScenarioDefinition.Parse(wrong));
        }

        [Test]
        public void ConditionsEnforceCountScalarAndStabilityBounds()
        {
            foreach (
                JToken expected in new JToken[]
                {
                    JValue.CreateNull(),
                    new JObject(),
                    new JArray(),
                    new JValue(double.NaN),
                    new JValue(double.PositiveInfinity),
                    new string('a', 1025),
                }
            )
            {
                var value = Valid();
                value["steps"][3]["component"] = "Example.PlayerState";
                value["steps"][3]["property"] = new JObject { ["path"] = "value", ["equals"] = expected };
                Assert.Throws<ArgumentException>(() => PlayScenarioDefinition.Parse(value));
            }
            var absent = Valid();
            absent["steps"][3]["count"] = 0;
            Assert.AreEqual(0, PlayScenarioDefinition.Parse(absent).Steps[3].Count);
            absent["steps"][3]["active"] = false;
            Assert.Throws<ArgumentException>(() => PlayScenarioDefinition.Parse(absent));
            var multiple = Valid();
            multiple["steps"][3]["count"] = 2;
            multiple["steps"][3]["component"] = "Example.PlayerState";
            Assert.Throws<ArgumentException>(() => PlayScenarioDefinition.Parse(multiple));
            var stability = Valid();
            stability["steps"][3]["timeout_seconds"] = 1;
            stability["steps"][3]["stable_for_ms"] = 1000;
            Assert.Throws<ArgumentException>(() => PlayScenarioDefinition.Parse(stability));
            stability["steps"][3]["stable_for_ms"] = 999;
            Assert.AreEqual(999, PlayScenarioDefinition.Parse(stability).Steps[3].StableForMs);
        }

        [Test]
        public void LogPolicyRequiresUniqueBoundedLiteralsAndStageArraysAreBounded()
        {
            foreach (JToken allowed in new JToken[] { new JArray("x", "x"), new JArray(""), new JArray(new string('x', 1025)), new JArray(true) })
            {
                var value = Valid();
                value["log_policy"] = new JObject { ["allowed_messages"] = allowed };
                Assert.Throws<ArgumentException>(() => PlayScenarioDefinition.Parse(value));
            }
            var valueWithSetup = Valid();
            valueWithSetup["setup_steps"] = new JArray(valueWithSetup["steps"][1].DeepClone());
            Assert.Throws<ArgumentException>(() => PlayScenarioDefinition.Parse(valueWithSetup));
            var overflow = Valid();
            overflow["cleanup_steps"] = new JArray(Enumerable.Range(0, 17).Select(_ => overflow["steps"][0].DeepClone()));
            Assert.Throws<ArgumentException>(() => PlayScenarioDefinition.Parse(overflow));
            var legacy = PlayScenarioDefinition.Parse(Valid());
            Assert.AreEqual(0, legacy.SetupSteps.Count);
            Assert.AreEqual(30, legacy.CleanupTimeoutSeconds);
            Assert.AreEqual(250, legacy.CompletionStableMs);
            Assert.AreEqual("strict", legacy.LogPolicy.Mode);
            Assert.IsFalse(legacy.Metrics.Enabled);
        }
    }
}
