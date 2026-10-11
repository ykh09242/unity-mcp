using System;
using System.Collections;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Security.Cryptography;
using System.Text;
using System.Threading;
using MCPForUnity.Editor.Windows.PlayScenarios;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEngine;
using UnityEngine.TestTools;
using UnityEngine.UIElements;

namespace MCPForUnityTests.EditMode.Windows
{
    public class PlayScenarioSoakBaselineSelectorTests
    {
        private string root;

        private sealed class BaselineTestWindow : UnityEditor.EditorWindow { }

        [SetUp]
        public void SetUp()
        {
            root = Path.Combine(Path.GetTempPath(), "unity-mcp-soak-baselines-" + Guid.NewGuid().ToString("N"));
            Directory.CreateDirectory(root);
        }

        [TearDown]
        public void TearDown() => Directory.Delete(root, true);

        private static string Hash(byte[] bytes)
        {
            using (var hash = SHA256.Create())
                return BitConverter.ToString(hash.ComputeHash(bytes)).Replace("-", "").ToLowerInvariant();
        }

        private string Registry()
        {
            string saved = Path.Combine(root, "saved");
            Directory.CreateDirectory(saved);
            var files = new JArray();
            foreach (
                string name in new[]
                {
                    "request.json",
                    "definition.json",
                    "process.json",
                    "run.json",
                    "soak.json",
                    "client-soak.json",
                    "iterations.jsonl",
                    "iteration-0001.json",
                }.OrderBy(name => name, StringComparer.Ordinal)
            )
            {
                byte[] bytes = Encoding.UTF8.GetBytes("{}");
                File.WriteAllBytes(Path.Combine(saved, name), bytes);
                files.Add(new JObject { ["name"] = name, ["sha256"] = Hash(bytes) });
            }
            string manifest = string.Concat(files.OfType<JObject>().Select(file => (string)file["name"] + ":" + (string)file["sha256"] + "\n"));
            var entry = new JObject
            {
                ["name"] = "reviewed",
                ["directory"] = saved,
                ["snapshot_file"] = Path.Combine(saved, "soak.json"),
                ["registered_utc"] = "2026-10-12T00:00:00+00:00",
                ["evidence_sha256"] = Hash(Encoding.UTF8.GetBytes(manifest)),
                ["files"] = files,
                ["provenance"] = new JObject(),
            };
            var registry = new JObject
            {
                ["schema_version"] = 1,
                ["revision"] = 2,
                ["entries"] = new JArray(entry),
                ["pins"] = new JArray(new JObject { ["name"] = "reviewed" }),
                ["history"] = new JArray(
                    new JObject
                    {
                        ["revision"] = 1,
                        ["action"] = "register",
                        ["name"] = "reviewed",
                        ["previous_pins"] = new JArray(),
                        ["pins"] = new JArray(),
                        ["at_utc"] = "2026-10-12T00:00:00+00:00",
                        ["reason"] = "Explicit saved soak registration",
                    },
                    new JObject
                    {
                        ["revision"] = 2,
                        ["action"] = "pin",
                        ["name"] = "reviewed",
                        ["previous_pins"] = new JArray(),
                        ["pins"] = new JArray("reviewed"),
                        ["at_utc"] = "2026-10-12T00:01:00+00:00",
                        ["reason"] = "Reviewed successful soak",
                    }
                ),
            };
            string path = Path.Combine(root, "baselines.json");
            File.WriteAllText(path, registry.ToString());
            return path;
        }

        [Test]
        public void ExplicitRegistryReadsOnlyItsManifestAndRefusesMutation()
        {
            string registry = Registry();
            var entry = PlayScenarioSoakBaselineRegistry.Read(registry, CancellationToken.None).Single();
            Assert.IsTrue(entry.Pinned);
            Assert.AreEqual(Path.Combine(root, "saved", "soak.json"), PlayScenarioSoakBaselineRegistry.ValidateSnapshot(entry, CancellationToken.None));
            File.WriteAllText(Path.Combine(root, "saved", "process.json"), "{}\n");
            Assert.Throws<InvalidDataException>(() => PlayScenarioSoakBaselineRegistry.ValidateSnapshot(entry, CancellationToken.None));
        }

        [TestCase("duplicate")]
        [TestCase("chain")]
        [TestCase("path")]
        [TestCase("unknown")]
        [TestCase("oversized")]
        public void MalformedRegistryNeverProvidesSelectableEntries(string damage)
        {
            string path = Registry();
            JObject registry = JObject.Parse(File.ReadAllText(path));
            switch (damage)
            {
                case "duplicate":
                    File.WriteAllText(path, "{\"schema_version\":1,\"schema_version\":1}");
                    break;
                case "chain":
                    registry["history"][1]["previous_pins"] = new JArray("reviewed");
                    File.WriteAllText(path, registry.ToString());
                    break;
                case "path":
                    registry["entries"][0]["snapshot_file"] = Path.Combine(root, "other.json");
                    File.WriteAllText(path, registry.ToString());
                    break;
                case "unknown":
                    registry["unexpected"] = true;
                    File.WriteAllText(path, registry.ToString());
                    break;
                case "oversized":
                    File.WriteAllBytes(path, new byte[PlayScenarioSoakBaselineRegistry.RegistryLimit + 1]);
                    break;
            }
            Assert.That(() => PlayScenarioSoakBaselineRegistry.Read(path, CancellationToken.None), Throws.Exception);
        }

        private static void Click(VisualElement parent, string name)
        {
            var button = parent.Q<Button>(name);
            typeof(Clickable).GetMethod("Invoke", BindingFlags.Instance | BindingFlags.NonPublic).Invoke(button.clickable, new object[] { null });
        }

        private static IEnumerator Finish(PlayScenarioImportWorker worker)
        {
            DateTime deadline = DateTime.UtcNow.AddSeconds(10);
            while (worker.IsBusy && DateTime.UtcNow < deadline)
            {
                worker.Pump();
                yield return null;
            }
            Assert.IsFalse(worker.IsBusy, "Bounded import must complete.");
        }

        [UnityTest]
        public IEnumerator NamedSelectionFeedsExistingBaselinePathAndChangedRegistryClearsChoices()
        {
            string registry = Registry();
            var window = ScriptableObject.CreateInstance<BaselineTestWindow>();
            try
            {
                window.Show();
                yield return null;
                var parent = window.rootVisualElement;
                Assert.IsNotNull(parent.panel, "Source-change callbacks require an attached UI panel.");
                using (var editor = new PlayScenarioSoakComparisonEditor(parent, _ => ""))
                {
                    parent.Q<TextField>("soakBaselineRegistryPath").value = registry;
                    Click(parent, "loadSoakBaselineRegistry");
                    var worker = (PlayScenarioImportWorker)parent.Q<Foldout>("soakBaselineRegistry").userData;
                    yield return Finish(worker);
                    Assert.AreEqual("reviewed", parent.Q<DropdownField>("soakRegisteredBaseline").value);
                    Click(parent, "useSoakRegisteredBaseline");
                    yield return Finish(worker);
                    Assert.AreEqual(Path.Combine(root, "saved", "soak.json"), parent.Q<TextField>("soakBaselinePath").value);
                    File.AppendAllText(Path.Combine(root, "saved", "process.json"), "\n");
                    Click(parent, "importSoakBaseline");
                    yield return Finish((PlayScenarioImportWorker)parent.Q<TextField>("soakBaselinePath").userData);
                    StringAssert.Contains("evidence changed", parent.Q<Label>("soakBaselineState").text);
                    parent.Q<TextField>("soakBaselineRegistryPath").value = Path.Combine(root, "other.json");
                    Assert.IsEmpty(parent.Q<DropdownField>("soakRegisteredBaseline").choices);
                    Assert.AreEqual("", parent.Q<TextField>("soakBaselinePath").value);
                    Assert.IsFalse(parent.Q<Button>("useSoakRegisteredBaseline").enabledSelf);
                }
            }
            finally
            {
                UnityEngine.Object.DestroyImmediate(window);
            }
        }
    }
}
