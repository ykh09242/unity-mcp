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
    }
}
