using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Reflection;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Editor.Services.PlayScenarios;
using MCPForUnity.Editor.Tools;
using MCPForUnity.Runtime.PlayScenarios;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEngine;

namespace MCPForUnityTests.EditMode.Tools.PlayScenarios
{
    [Parallelizable(ParallelScope.None)]
    public class PlayScenarioParameterTests
    {
        private static JObject Template() =>
            JObject.Parse(
                @"{
          'name':'difficulty', 'completion_stable_ms':0,
          'steps':[{'name':'Anchor','action':'load_scene','scene':'Assets/Scenes/Owned.unity'},
                   {'name':'Score','action':'wait_state','state_id':'score','state_equals':0}],
          'parameters':{'score':{'type':'integer','default':0}},
          'parameter_bindings':[{'path':'/steps/1/state_equals','parameter':'score'}],
          'datasets':[{'name':'easy','values':{'score':1}}, {'name':'normal','values':{'score':2}}, {'name':'hard','values':{'score':3}}]
        }"
            );

        private static PlayScenarioDefinition Materialize(PlayScenarioDefinition definition, string dataset)
        {
            Type helper = typeof(PlayScenarioDefinition).Assembly.GetType("MCPForUnity.Editor.Services.PlayScenarios.PlayScenarioParameters");
            Assert.IsNotNull(helper, "Typed parameter materialization must exist in the runtime assembly.");
            MethodInfo method = helper.GetMethod("Materialize", BindingFlags.Public | BindingFlags.Static);
            Assert.IsNotNull(method);
            return (PlayScenarioDefinition)method.Invoke(null, new object[] { definition, dataset });
        }

        private sealed class Host : IPlayScenarioHost
        {
            public int Calls;

            public PlayScenarioObservation Evaluate(PlayScenarioStep step, bool first)
            {
                Assert.AreNotEqual("wait_state", step.Action, "State probes must use the registered provider rather than the ordinary host.");
                Calls++;
                return new PlayScenarioObservation(true, "ready");
            }

            public void Release() { }
        }

        private sealed class Provider : IPlayScenarioStateProvider
        {
            public readonly int Value;
            public int Reads;
            public int Observed = -1;

            public Provider(int value) => Value = value;

            public bool TryRead(out PlayScenarioStateValue value)
            {
                Reads++;
                Observed = Value;
                value = PlayScenarioStateValue.FromInteger(Value);
                return true;
            }
        }

        private static void ExecuteWithOwnedProvider(PlayScenarioEngine engine, Host host, Provider provider, string stateId)
        {
            engine.EnteredPlayMode();
            engine.Tick(1000, true); // The ordinary host completes the scene anchor.
            engine.Tick(1250, true); // No provider yet: prove the actual cause of the prior failure.
            Assert.AreEqual("running", engine.State.Status);
            StringAssert.Contains("Waiting for registered state provider: " + stateId, engine.State.Steps[1].Detail);
            Assert.AreEqual(0, provider.Reads);
            using (PlayScenarioStateRegistry.Register(stateId, provider))
            {
                for (long now = 1500; now < 10000 && engine.Running; now += 250)
                    engine.Tick(now, true);
                Assert.AreEqual("succeeded", engine.State.Status, JObject.FromObject(engine.State).ToString());
                Assert.Greater(provider.Reads, 0, "The engine must observe the real registered scalar.");
                Assert.AreEqual(1, host.Calls, "Only the load_scene anchor uses the ordinary host.");
            }
        }

        [Test]
        public void ThreeDatasetsProduceDistinctExecutedResultsIdentitiesAndHashes()
        {
            JObject json = Template();
            string stateId = "param." + Guid.NewGuid().ToString("N");
            json["steps"][1]["state_id"] = stateId;
            var template = PlayScenarioDefinition.Parse(json);
            int actualValue = 0;
            var names = new HashSet<string>();
            var hashes = new HashSet<string>();
            var observed = new List<int>();
            foreach (string dataset in new[] { "easy", "normal", "hard" })
            {
                var effective = Materialize(template, dataset);
                var host = new Host();
                var engine = new PlayScenarioEngine(PlayScenarioEngine.Create(effective, Guid.NewGuid().ToString("N"), 1, 30, 1000), host);
                var provider = new Provider(++actualValue);
                ExecuteWithOwnedProvider(engine, host, provider, stateId);
                names.Add(engine.State.Scenario.Name);
                hashes.Add(engine.State.Reproduction.DefinitionHash);
                observed.Add(provider.Observed);
            }
            Assert.AreEqual(3, names.Count);
            Assert.AreEqual(3, hashes.Count);
            CollectionAssert.AreEqual(new[] { 1, 2, 3 }, observed);
            Assert.AreEqual(0, (int)template.Steps[1].StateEquals, "Expansion cannot mutate saved template.");
        }

        [Test]
        public void PublicSaveAndRunSelectThreeEffectiveIdentitiesAndRejectCaseReuse()
        {
            Type service = typeof(PlayScenarioService);
            FieldInfo engineField = service.GetField("_engine", BindingFlags.Static | BindingFlags.NonPublic);
            FieldInfo logsField = service.GetField("_logs", BindingFlags.Static | BindingFlags.NonPublic);
            object priorEngine = engineField.GetValue(null);
            object priorLogs = logsField.GetValue(null);
            if ((priorEngine as PlayScenarioEngine)?.Running == true)
                Assert.Ignore("An active scenario owns the editor.");
            string name = "param-" + Guid.NewGuid().ToString("N");
            JObject json = Template();
            json["name"] = name;
            string stateId = "param." + Guid.NewGuid().ToString("N");
            json["steps"][1]["state_id"] = stateId;
            var store = new PlayScenarioStore(Path.GetDirectoryName(Application.dataPath));
            try
            {
                var saved = (IMcpResponse)ManagePlayScenario.HandleCommand(new JObject { ["action"] = "save", ["scenario"] = json });
                Assert.IsTrue(saved.Success);
                PlayScenarioDefinition template = store.Get(name);
                var hashes = new HashSet<string>();
                int actualValue = 0;
                foreach (string dataset in new[] { "easy", "normal", "hard" })
                {
                    var effective = Materialize(template, dataset);
                    var host = new Host();
                    string job = Guid.NewGuid().ToString("N");
                    var engine = new PlayScenarioEngine(PlayScenarioEngine.Create(effective, job, 1, 30, 1000), host);
                    var provider = new Provider(++actualValue);
                    ExecuteWithOwnedProvider(engine, host, provider, stateId);
                    Assert.AreEqual(actualValue, provider.Observed);
                    engineField.SetValue(null, engine);
                    logsField.SetValue(null, null);
                    var request = new JObject
                    {
                        ["action"] = "run",
                        ["name"] = name,
                        ["dataset"] = dataset,
                        ["job_id"] = job,
                        ["timeout_seconds"] = 30,
                    };
                    JObject response = JObject.FromObject(ManagePlayScenario.HandleCommand(request));
                    Assert.IsTrue((bool)response["success"], response.ToString());
                    Assert.AreEqual(name + "--" + dataset, (string)response["data"]["scenario"]["name"]);
                    hashes.Add((string)response["data"]["reproduction"]["definition_hash"]);
                    request["dataset"] = dataset == "easy" ? "hard" : "easy";
                    Assert.IsFalse(((IMcpResponse)ManagePlayScenario.HandleCommand(request)).Success, "A job ID cannot select another case.");
                }
                Assert.AreEqual(3, hashes.Count);
            }
            finally
            {
                engineField.SetValue(null, priorEngine);
                logsField.SetValue(null, priorLogs);
                store.Delete(name);
            }
        }

        [TestCase("/steps/0/scene")]
        [TestCase("/steps/1/state_id")]
        [TestCase("/name")]
        [TestCase("/steps/1/property/path")]
        public void ProtectedBindingIsRejectedBeforeStorage(string path)
        {
            JObject template = Template();
            template["parameter_bindings"][0]["path"] = path;
            Assert.Throws<ArgumentException>(() => PlayScenarioDefinition.Parse(template));
        }

        [Test]
        public void InvalidDatasetTypeAndExpandedStepBoundAreRejected()
        {
            JObject template = Template();
            template["datasets"][0]["values"]["score"] = true;
            Assert.Throws<ArgumentException>(() => PlayScenarioDefinition.Parse(template));
            template = Template();
            template["parameter_bindings"][0]["path"] = "/steps/1/timeout_seconds";
            template["datasets"][0]["values"]["score"] = 121;
            Assert.Throws<ArgumentException>(() => PlayScenarioDefinition.Parse(template));
        }

        [Test]
        public void CombinedDatasetMetadataByteLimitIsCheckedBeforeSave()
        {
            JObject value = Template();
            var parameters = new JObject();
            var bindings = new JArray();
            var steps = new JArray(value["steps"][0].DeepClone());
            for (int i = 0; i < 4; i++)
            {
                parameters["p" + i] = new JObject { ["type"] = "string", ["default"] = new string('x', 1024) };
                bindings.Add(new JObject { ["path"] = "/steps/" + (i + 1) + "/state_equals", ["parameter"] = "p" + i });
                steps.Add(
                    new JObject
                    {
                        ["name"] = "Probe" + i,
                        ["action"] = "wait_state",
                        ["state_id"] = "p" + i,
                        ["state_equals"] = "",
                    }
                );
            }
            var datasets = new JArray();
            for (int i = 0; i < 16; i++)
            {
                var values = new JObject();
                for (int j = 0; j < 4; j++)
                    values["p" + j] = new string('x', 1024);
                datasets.Add(new JObject { ["name"] = "case-" + i, ["values"] = values });
            }
            value["parameters"] = parameters;
            value["parameter_bindings"] = bindings;
            value["steps"] = steps;
            value["datasets"] = datasets;
            var error = Assert.Throws<ArgumentException>(() => PlayScenarioDefinition.Parse(value));
            StringAssert.Contains("64 KiB", error.Message);
        }

        [Test]
        public void AbsentParameterSchemaPreservesLegacySerializationAndHash()
        {
            JObject legacy = Template();
            legacy.Remove("parameters");
            legacy.Remove("parameter_bindings");
            legacy.Remove("datasets");
            var definition = PlayScenarioDefinition.Parse(legacy);
            JObject before = JObject.FromObject(definition);
            var effective = Materialize(definition, null);
            Assert.IsTrue(JToken.DeepEquals(before, JObject.FromObject(effective)));
            Assert.AreEqual(PlayScenarioReproduction.Hash(definition), PlayScenarioReproduction.Hash(effective));
            Assert.IsNull(before["parameters"]);
            Assert.IsNull(before["datasets"]);
        }
    }
}
