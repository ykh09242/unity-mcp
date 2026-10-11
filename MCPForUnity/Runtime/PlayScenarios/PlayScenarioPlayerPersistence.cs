using System;
using MCPForUnity.Editor.Services.PlayScenarios;
using Newtonsoft.Json.Linq;

namespace MCPForUnity.Runtime.PlayScenarios
{
    /// <summary>Records one bounded storage failure without retaining an exception or replacing its cause.</summary>
    public sealed class PlayScenarioPlayerPersistence
    {
        private JObject first;
        public bool Failed => first != null;
        public JObject Diagnostic => first == null ? null : (JObject)first.DeepClone();
        public string Message =>
            first == null
                ? null
                : "Player persistence failed at " + (string)first["stage"] + ": " + (string)first["exception_type"] + ": " + (string)first["error"];

        public void Record(string stage, int? iteration, Exception error)
        {
            if (first != null)
                return;
            first = new JObject
            {
                ["schema_version"] = 1,
                ["stage"] = stage,
                ["exception_type"] = PlayScenarioEngine.Bounded(error.GetType().Name, 128),
                ["error"] = PlayScenarioEngine.Bounded(error.Message, 512),
                ["iteration"] = iteration,
            };
        }

        public void Execute(string stage, int? iteration, Action operation)
        {
            try
            {
                operation();
            }
            catch (Exception error)
            {
                Record(stage, iteration, error);
                throw;
            }
        }

        public void Attach(JObject report)
        {
            if (first == null)
                return;
            report["persistence_error"] = Diagnostic;
            report["exit_code"] = 2;
        }
    }
}
