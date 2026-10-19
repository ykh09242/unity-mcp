using System;
using System.Linq;
using System.Reflection;
using MCPForUnity.Runtime.PlayScenarios;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using NUnit.Framework;

namespace MCPForUnityTests
{
    public sealed class PlayScenarioPlayerSuiteTests
    {
        private static JObject Definition(string name, string scene) =>
            new JObject
            {
                ["name"] = name,
                ["steps"] = new JArray(
                    new JObject
                    {
                        ["name"] = "load",
                        ["action"] = "load_scene",
                        ["scene"] = scene,
                    }
                ),
            };

        private static JObject Input() =>
            new JObject
            {
                ["name"] = "three-frozen",
                ["children"] = new JArray(
                    new JObject { ["definition"] = Definition("first", "Assets/First.unity") },
                    new JObject { ["definition"] = Definition("second", "Assets/Second.unity") },
                    new JObject { ["definition"] = Definition("third", "Assets/Third.unity") }
                ),
                ["unity_version"] = "6000.0.69f1",
                ["package_version"] = "1.0.0",
                ["build_id"] = new string('a', 32),
                ["build_source_revision"] = null,
                ["failure_policy"] = "continue",
                ["isolation"] = "fresh-process",
            };

        private static JObject Create(JObject input)
        {
            Type helper = typeof(PlayScenarioPlayerBundle).Assembly.GetType("MCPForUnity.Runtime.PlayScenarios.PlayScenarioPlayerSuite");
            Assert.IsNotNull(helper, "PLAY14 frozen multi-definition Player suite handler is missing.");
            MethodInfo method = helper.GetMethod("Create", BindingFlags.Public | BindingFlags.Static);
            Assert.IsNotNull(method, "PLAY14 public suite creation entry is missing.");
            return (JObject)method.Invoke(null, new object[] { input });
        }

        [Test]
        public void ThreeDefinitions_FreezeDistinctIdentitiesAndExactSceneUnion()
        {
            JObject input = Input();
            JObject manifest = Create(input);
            Assert.AreEqual(3, (int)manifest["schema_version"]);
            Assert.AreEqual(3, manifest["children"].Count());
            CollectionAssert.AreEqual(
                new[] { "Assets/First.unity", "Assets/Second.unity", "Assets/Third.unity" },
                manifest["scene_paths"].ToObject<string[]>()
            );
            string frozen = manifest.ToString();
            input["children"][1]["definition"]["steps"][0]["scene"] = "Assets/Changed.unity";
            Assert.AreEqual(frozen, manifest.ToString(), "A caller edit must never change an already frozen child.");
            Assert.AreNotEqual((string)manifest["children"][0]["definition_hash"], (string)manifest["children"][1]["definition_hash"]);
            Assert.AreNotEqual((string)manifest["children"][1]["definition_hash"], (string)manifest["children"][2]["definition_hash"]);
            Assert.DoesNotThrow(() => PlayScenarioPlayerBundle.Parse(frozen));
        }

        [TestCase("child")]
        [TestCase("scene")]
        public void FrozenSuite_RejectsChangedChildOrUnion(string changed)
        {
            JObject manifest = Create(Input());
            if (changed == "child")
                manifest["children"][1]["definition"]["steps"][0]["scene"] = "Assets/Changed.unity";
            else
                manifest["scene_paths"] = new JArray("Assets/First.unity");
            Assert.Throws<ArgumentException>(() => PlayScenarioPlayerBundle.Parse(manifest.ToString()));
        }

        [Test]
        public void ExplicitSharedProcess_IsRefusedBeforeAnyDefinitionRuns()
        {
            JObject input = Input();
            input["isolation"] = "shared-process";
            TargetInvocationException error = Assert.Throws<TargetInvocationException>(() => Create(input));
            Assert.IsInstanceOf<ArgumentException>(error.InnerException);
            StringAssert.Contains("fresh-process", error.InnerException.Message);
        }

        [Test]
        public void ChildRequest_SelectsExactFrozenSecondDefinitionAndRejectsHashDrift()
        {
            JObject manifest = Create(Input());
            var request = new JObject
            {
                ["schema_version"] = 2,
                ["job_id"] = new string('b', 32),
                ["scenario_name"] = manifest["children"][1]["scenario_name"],
                ["definition_hash"] = manifest["children"][1]["definition_hash"],
                ["build_id"] = manifest["build_id"],
                ["build_source_revision"] = null,
                ["payload_hash"] = new string('c', 64),
                ["repeat_count"] = 1,
                ["timeout_seconds"] = 60,
            };
            PlayScenarioPlayerBundle bundle = PlayScenarioPlayerBundle.Parse(manifest.ToString());
            PlayScenarioPlayerRequest.Parse(request.ToString()).ValidateBundle(bundle);
            Assert.AreEqual("second", bundle.Definition.Name);
            Assert.AreEqual((string)manifest["children"][1]["definition_hash"], bundle.DefinitionHash);
            request["definition_hash"] = new string('d', 64);
            Assert.Throws<ArgumentException>(() =>
                PlayScenarioPlayerRequest.Parse(request.ToString()).ValidateBundle(PlayScenarioPlayerBundle.Parse(manifest.ToString()))
            );
        }

        [Test]
        public void NativeExponentDefinition_ExportsExactManifestForPortableAdmission()
        {
            JObject input = Input();
            input["children"][0]["definition"]["steps"][0]["scene"] = "Assets/Numeric_\u00e9_\U0001f600.unity";
            ((JArray)input["children"][0]["definition"]["steps"]).Add(
                new JObject
                {
                    ["name"] = "numeric",
                    ["action"] = "wait_state",
                    ["state_id"] = "numeric",
                    ["state_equals"] = 0.000001,
                }
            );
            JObject manifest = Create(input);
            StringAssert.Contains("1E-06", (string)manifest["children"][0]["definition_json"]);
            var inventory = new JArray(
                new JObject
                {
                    ["path"] = PlayScenarioPlayerBundle.ExecutableName,
                    ["size_bytes"] = 0,
                    ["sha256"] = PlayScenarioPlayerPayload.HashText(""),
                }
            );
            manifest["payload_inventory"] = inventory;
            manifest["payload_inventory_json"] = PlayScenarioPlayerBundle.CanonicalJson(inventory);
            manifest["payload_hash"] = PlayScenarioPlayerPayload.HashText((string)manifest["payload_inventory_json"]);
            string json = manifest.ToString(Formatting.None);
            Assert.DoesNotThrow(() => PlayScenarioPlayerBundle.Parse(json));
            TestContext.WriteLine("PLAY14_NATIVE_NUMERIC_MANIFEST:" + json);
            string path = Environment.GetEnvironmentVariable("MCP_PLAY14_NATIVE_SUITE_MANIFEST");
            if (!string.IsNullOrEmpty(path))
                PlayScenarioPlayerFiles.WriteNew(PlayScenarioPlayerFiles.CheckedAbsolute(path), json, PlayScenarioPlayerBundle.Limit);
        }
    }
}
