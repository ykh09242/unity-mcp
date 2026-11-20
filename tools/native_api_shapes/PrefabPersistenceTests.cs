using System;
using System.Collections;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Threading;
using MCPForUnity.Editor.Services.Transport;
using MCPForUnity.Editor.Tools.Addressables;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEditor;
using UnityEngine;

namespace MCPForUnityTests.Editor.Tools
{
    public partial class AddressablesAuthoringTests
    {
        private static string CrashDirectory(string phase) => Path.GetFullPath(Path.Combine(Application.dataPath, "..", "..", "..", phase));

        private static string CrashRun()
        {
            Assert.IsTrue(Application.isBatchMode);
            string run = Environment.GetEnvironmentVariable("MCP_PREFAB_FIXTURE_RUN");
            Assert.IsTrue(Guid.TryParseExact(run, "N", out _), "Requires the disposable package-shapes runner.");
            return run;
        }

        private static string PrivateKeyPath(string run) => Path.Combine(Application.dataPath, "..", "Library", "prefab-cut-" + run);

        private static void Publish(string directory, string name, JObject record)
        {
            string path = Path.Combine(directory, name);
            using (var stream = new FileStream(path + ".tmp", FileMode.CreateNew, FileAccess.Write))
            using (var writer = new StreamWriter(stream))
                writer.Write(record.ToString());
            File.Move(path + ".tmp", path);
        }

        [Test, Explicit("Interrupted by the parent of this disposable batch Editor.")]
        public void CrashAfterPrefabSave() => InterruptAt(nameof(CrashAfterPrefabSave), "prefab_saved");

        [Test, Explicit("Interrupted by the parent of this disposable batch Editor.")]
        public void CrashAfterCreationJournal() => InterruptAt(nameof(CrashAfterCreationJournal), "created");

        [Test, Explicit("Interrupted by the parent of this disposable batch Editor.")]
        public void CrashAfterRegistrationSave() => InterruptAt(nameof(CrashAfterRegistrationSave), "registration_saved");

        private void InterruptAt(string phase, string boundary)
        {
            string run = CrashRun();
            foreach (var owned in new[] { first, second, settings }.Cast<UnityEngine.Object>())
            {
                EditorUtility.SetDirty(owned);
                AssetDatabase.SaveAssetIfDirty(owned);
            }
            string key = Guid.NewGuid().ToString("N") + Guid.NewGuid().ToString("N");
            string destination = PreviewPrefab(key);
            scenes.ClearDirtiness();
            using (var stream = new FileStream(PrivateKeyPath(run), FileMode.CreateNew, FileAccess.Write))
            using (var writer = new StreamWriter(stream))
                writer.Write(key);
            var workflow = typeof(ManageAddressables).Assembly.GetType("MCPForUnity.Editor.Tools.Addressables.AddressablePrefabWorkflow", true);
            var observer = workflow.GetField("PersistenceObserver", BindingFlags.NonPublic | BindingFlags.Static);
            Assert.IsNotNull(observer, "Only the disposable package has the process-death observer.");
            Action<string> stop = reached =>
            {
                if (reached != boundary)
                    return;
                Publish(
                    CrashDirectory(phase),
                    "boundary.json",
                    new JObject
                    {
                        ["run_id"] = run,
                        ["boundary"] = boundary,
                        ["process_id"] = Process.GetCurrentProcess().Id,
                        ["session"] = CommandResumeState.SessionId,
                        ["root"] = root,
                        ["root_guid"] = rootGuid,
                        ["prefab"] = destination,
                        ["group"] = AssetDatabase.GetAssetPath((UnityEngine.Object)first),
                        ["settings"] = settingsPath,
                        ["user_entry_guid"] = assetGuid,
                    }
                );
                // Keep the next production write unreachable until the runner terminates its own child.
                Thread.Sleep(Timeout.Infinite);
            };
            observer.SetValue(null, stop);
            try
            {
                var created = Workflow(key, "create_prefab");
                Assert.AreEqual("created", (string)created.SelectToken("data.state"), created.ToString());
                if (boundary == "registration_saved")
                    Workflow(key, "register_prefab");
                Assert.Fail("The requested persistence boundary returned.");
            }
            finally
            {
                observer.SetValue(null, null);
            }
        }

        [Test, Explicit("Reopens the terminated prefab writer's project.")]
        public void ReopenAfterPrefabSave() => InspectCrash(nameof(CrashAfterPrefabSave), nameof(ReopenAfterPrefabSave), "creating", 0);

        [Test, Explicit("Reopens the terminated prefab writer's project.")]
        public void ReopenAfterCreationJournal() => InspectCrash(nameof(CrashAfterCreationJournal), nameof(ReopenAfterCreationJournal), "created", 0);

        [Test, Explicit("Reopens the terminated prefab writer's project.")]
        public void ReopenAfterRegistrationSave() => InspectCrash(nameof(CrashAfterRegistrationSave), nameof(ReopenAfterRegistrationSave), "registering", 1);

        private void InspectCrash(string crashPhase, string reopenPhase, string journalPhase, int entryCount)
        {
            var cut = JObject.Parse(File.ReadAllText(Path.Combine(CrashDirectory(crashPhase), "boundary.json")));
            string run = CrashRun();
            Assert.AreEqual(run, (string)cut["run_id"]);
            Assert.AreNotEqual((string)cut["session"], CommandResumeState.SessionId);
            string originalRoot = (string)cut["root"];
            Assert.AreEqual((string)cut["root_guid"], AssetDatabase.AssetPathToGUID(originalRoot));
            string key = File.ReadAllText(PrivateKeyPath(run));
            string path = (string)cut["prefab"];
            string guid = AssetDatabase.AssetPathToGUID(path);
            Assert.IsNotEmpty(guid);
            Assert.IsNotNull(AssetDatabase.LoadAssetAtPath<GameObject>(path));
            Assert.AreNotEqual(AssetDatabase.AssetPathToGUID(originalRoot + "/Template.prefab"), guid);
            var originalSettings = AssetDatabase.LoadAssetAtPath((string)cut["settings"], settingsType);
            var originalGroup = AssetDatabase.LoadMainAssetAtPath((string)cut["group"]);
            string userGuid = journalPhase == "registering" ? guid : (string)cut["user_entry_guid"];
            string userAddress = "manual/" + run;
            int Count() =>
                ((IEnumerable)Get(originalSettings, "groups"))
                    .Cast<object>()
                    .Where(group => group != null)
                    .SelectMany(group => ((IEnumerable)Get(group, "entries")).Cast<object>())
                    .Count(item => (string)Get(item, "guid") == guid);
            Assert.AreEqual(entryCount, Count());
            Assert.AreEqual(userAddress, Get(Invoke(originalSettings, "FindAssetEntry", userGuid), "address"));
            var observed = Workflow(key, "reconcile_prefab");
            string state = journalPhase == "created" ? "conflict" : "unknown";
            Assert.AreEqual(state, (string)observed.SelectToken("data.state"), observed.ToString());
            var facts = (JObject)observed.SelectToken("data.reconciliation");
            Assert.AreEqual(journalPhase, (string)facts["recorded_phase"]);
            Assert.AreEqual(guid, (string)facts["observed_prefab_guid"]);
            Assert.AreEqual(entryCount, (int)facts["observed_entry_count"]);
            Assert.AreEqual(journalPhase == "creating" ? null : guid, (string)facts["expected_prefab_guid"]);
            Assert.AreEqual(entryCount == 0 ? null : userAddress, (string)facts["observed_entry_address"]);
            Assert.IsFalse((bool)facts["replan_available"]);
            if (journalPhase == "created")
                Assert.AreEqual("settings_revision", (string)observed.SelectToken("data.conflict.field"));
            string next = Guid.NewGuid().ToString("N") + Guid.NewGuid().ToString("N");
            var refused = Workflow(next, "replan_prefab", new JObject { ["previous_checkpoint"] = key, ["expected_observation"] = new string('0', 64) });
            Assert.IsFalse(refused.Value<bool>("success"), refused.ToString());
            foreach (var owner in new[] { key, next })
            foreach (var action in new[] { "create_prefab", "register_prefab" })
                Assert.AreEqual("unknown", (string)Workflow(owner, action).SelectToken("data.state"));
            Assert.AreEqual(guid, AssetDatabase.AssetPathToGUID(path));
            Assert.AreEqual(1, Directory.GetFiles(originalRoot, "Result*.prefab").Length);
            int remainingEntries = Count();
            string retainedAddress = (string)Get(Invoke(originalSettings, "FindAssetEntry", userGuid), "address");
            Assert.AreEqual(entryCount, remainingEntries);
            Assert.AreEqual(userAddress, retainedAddress);
            Assert.IsFalse(EditorUtility.IsDirty(originalGroup));
            Publish(
                CrashDirectory(reopenPhase),
                "reopened.json",
                new JObject
                {
                    ["run_id"] = run,
                    ["process_id"] = Process.GetCurrentProcess().Id,
                    ["session"] = CommandResumeState.SessionId,
                    ["guid"] = guid,
                    ["entry_count"] = remainingEntries,
                    ["user_address"] = retainedAddress,
                    ["recorded_phase"] = facts["recorded_phase"],
                    ["state"] = observed.SelectToken("data.state"),
                }
            );
        }
    }
}
