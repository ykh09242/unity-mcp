using System;
using System.Collections.Generic;
using System.Linq;
using System.Reflection;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Editor.Services.PlayScenarios;
using MCPForUnity.Editor.Tools;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEditor;

namespace MCPForUnityTests.EditMode.Tools.PlayScenarios
{
    public class PlayScenarioServiceTests
    {
        private const string SessionKey = "MCPForUnity.PlayScenario.V1";
        private static readonly Type Service = typeof(PlayScenarioService);
        private readonly Dictionary<FieldInfo, object> _previous = new Dictionary<FieldInfo, object>();
        private string _saved;

        [SetUp]
        public void SetUp()
        {
            // Do not replace a user's active job if these tests run in their editor.
            var current = (PlayScenarioEngine)Service.GetField("_engine", BindingFlags.Static | BindingFlags.NonPublic).GetValue(null);
            if (current?.Running == true)
                Assert.Ignore("An active scenario owns the editor.");
            _saved = SessionState.GetString(SessionKey, "");
            foreach (FieldInfo field in Service.GetFields(BindingFlags.Static | BindingFlags.NonPublic).Where(field => !field.IsLiteral && !field.IsInitOnly))
                _previous[field] = field.GetValue(null);
        }

        [TearDown]
        public void TearDown()
        {
            if (_previous.Count == 0)
                return;
            Invoke("Detach");
            foreach (var pair in _previous)
                pair.Key.SetValue(null, pair.Value);
            if (_saved.Length == 0)
                SessionState.EraseString(SessionKey);
            else
                SessionState.SetString(SessionKey, _saved);
            _previous.Clear();
        }

        [Test]
        public void RestoreReschedulesAStartThatHadNotRequestedPlayYet()
        {
            PlayScenarioRun run = StartingRun();
            SessionState.SetString(SessionKey, JsonConvert.SerializeObject(run));
            Invoke("Restore");
            Assert.That(QueuedPlayRequests(), Is.EqualTo(1));
            var restored = (PlayScenarioEngine)Service.GetField("_engine", BindingFlags.Static | BindingFlags.NonPublic).GetValue(null);
            restored.Cancel(run.StartedUnixMs + 1);
            Invoke("Detach");
            Assert.That(QueuedPlayRequests(), Is.Zero);
            Assert.That(restored.State.Status, Is.EqualTo("cancelled"));
            Assert.That(Service.GetField("_logCallback", BindingFlags.Static | BindingFlags.NonPublic).GetValue(null), Is.Null);
        }

        [Test]
        public void RestorePreservesIsoLookingStepNamesAndLogsAsStrings()
        {
            PlayScenarioRun run = StartingRun();
            const string text = "2026-10-10T12:34:56.000Z";
            run.Scenario.Steps[0].Name = text;
            run.Steps[0].Name = text;
            run.Logs.Add(
                new PlayScenarioLog
                {
                    Type = "Log",
                    Message = text,
                    StackTrace = text,
                }
            );
            SessionState.SetString(SessionKey, JsonConvert.SerializeObject(run));
            Invoke("Restore");
            var restored = (PlayScenarioEngine)Service.GetField("_engine", BindingFlags.Static | BindingFlags.NonPublic).GetValue(null);
            Assert.That(restored.State.Steps[0].Name, Is.EqualTo(text));
            Assert.That(restored.State.Logs[0].Message, Is.EqualTo(text));
            restored.Cancel(run.StartedUnixMs + 1);
        }

        [TestCase("status")]
        [TestCase("cancel")]
        public void JobIdsWithTrailingNewlinesAreRejected(string action)
        {
            var response = (IMcpResponse)ManagePlayScenario.HandleCommand(new JObject { ["action"] = action, ["job_id"] = new string('a', 32) + "\n" });
            Assert.That(response.Success, Is.False);
        }

        private static int QueuedPlayRequests() =>
            EditorApplication
                .delayCall?.GetInvocationList()
                .Count(callback => callback.Method.DeclaringType == Service && callback.Method.Name == "RequestPlay")
            ?? 0;

        private static void Invoke(string name) => Service.GetMethod(name, BindingFlags.NonPublic | BindingFlags.Static).Invoke(null, null);

        private static PlayScenarioRun StartingRun()
        {
            PlayScenarioDefinition definition = PlayScenarioDefinition.Parse(
                JObject.Parse(
                    @"{
                'name':'service-test','steps':[{'name':'Menu','action':'load_scene','scene':'Assets/Menu.unity'}]}"
                )
            );
            return PlayScenarioEngine.Create(definition, Guid.NewGuid().ToString("N"), 1, 30, DateTimeOffset.UtcNow.ToUnixTimeMilliseconds());
        }
    }
}
