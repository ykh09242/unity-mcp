using System;
using System.IO;
using System.Linq;
using System.Security.Cryptography;
using System.Text;
using MCPForUnity.Editor.Services.PlayScenarios;
using Newtonsoft.Json.Linq;
using UnityEditor.SceneManagement;
using UnityEngine;

namespace VerifiedWorkflows
{
    public static class GraphicalCheckpointBuild
    {
        public static void Build()
        {
            PhysicsTriggerSetup.Configure();
            string project = Path.GetDirectoryName(Application.dataPath);
            var probe = GameObject.Find("FixtureTrigger").GetComponent<PhysicsTriggerProbe>();
            if (probe.GetComponent<TriggerFrame>() == null)
            {
                var frame = probe.gameObject.AddComponent<TriggerFrame>();
                frame.Mover = probe.Mover;
                frame.View = new GameObject("Contact indicator").AddComponent<Camera>();
                frame.View.clearFlags = CameraClearFlags.SolidColor;
                frame.View.cullingMask = 0;
                if (!EditorSceneManager.SaveScene(probe.gameObject.scene))
                    throw new IOException("Could not persist the graphical trigger fixture.");
            }
            var store = new PlayScenarioStore(project);
            var scenario = JObject.FromObject(store.Get("fixture-trigger-matrix"));
            scenario["name"] = "graphical-trigger-checkpoint";
            var step = (JObject)scenario["steps"][2];
            step["stable_for_ms"] = 500;
            step["screenshot"] = new JObject
            {
                ["name"] = "contact-frame",
                ["width"] = 640,
                ["height"] = 360,
            };
            var definition = PlayScenarioDefinition.Parse(scenario);
            store.Save(definition);
            string[] paths =
            {
                "Assets/TriggerMatrixFixture.unity",
                "Assets/PhysicsTriggerProbe.cs",
                "Assets/TriggerFrame.cs",
                "Assets/Editor/GraphicalCheckpointBuild.cs",
                "ProjectSettings/DynamicsManager.asset",
                "ProjectSettings/TagManager.asset",
                "Packages/manifest.json",
            };
            using (var sha = SHA256.Create())
            {
                var inputs = new JObject();
                foreach (string path in paths.OrderBy(value => value, StringComparer.Ordinal))
                    inputs[path] = Hex(sha.ComputeHash(File.ReadAllBytes(Path.Combine(project, path))));
                string revision = Hex(sha.ComputeHash(Encoding.UTF8.GetBytes(inputs.ToString(Newtonsoft.Json.Formatting.None))));
                File.WriteAllText(Path.Combine(FixtureRun.Argument("--fixture-output"), "build-inputs.json"), inputs.ToString());
                PlayScenarioPlayerBuild.Build(definition.Name, FixtureRun.Argument("--mcp-scenario-output"), revision);
            }
        }

        private static string Hex(byte[] data) => BitConverter.ToString(data).Replace("-", "").ToLowerInvariant();
    }
}
