using System;
using System.IO;
using MCPForUnity.Runtime;
using MCPForUnity.Runtime.PlayScenarios;
using Newtonsoft.Json.Linq;
using UnityEngine;

namespace MCPForUnityTests.PlayScenarios.Player
{
    public sealed class ScenarioPlayerFixtureState : MonoBehaviour, IPlayScenarioResetParticipant
    {
        public int ClicksInIteration;
        public int TotalClicks;
        public int ResetCount;
        private IDisposable registration;
        private IDisposable setupRegistration;
        private IDisposable cleanupRegistration;
        private StageProbe setupProbe;
        private StageProbe cleanupProbe;
        private FixtureResource resource;
        private IDisposable subscription;
        private IDisposable handle;
        private MemoryStream stream;
        private int resourceIteration;
        private int faultIteration;
        private const int ManagedGrowthAllocationBytes = 1024 * 1024;
        private const int ManagedGrowthAllocationLimit = 30;
        private byte[][] retainedManagedGrowth;
        private int managedGrowthIteration;
        private int managedGrowthCount;
        public bool IsResetComplete => true;

        private void Awake()
        {
            registration = PlayScenarioResetRegistry.Register("fixture-state", this);
            setupProbe = new StageProbe(this, "setup");
            cleanupProbe = new StageProbe(this, "cleanup");
            setupRegistration = PlayScenarioStateRegistry.Register("fixture-setup", setupProbe);
            cleanupRegistration = PlayScenarioStateRegistry.Register("fixture-cleanup", cleanupProbe);
        }

        public void BeginReset()
        {
            ClicksInIteration = 0;
            ResetCount++;
            if (ScenarioPlayerFixture.Mode == "soak-resources")
            {
                resource = ScriptableObject.CreateInstance<FixtureResource>();
                resource.name = "Player fixture runtime clone";
                PlayScenarioResourceTracker.RegisterScriptableObject(resource, "player-fixture");
                Application.lowMemory += OnLowMemory;
                subscription = PlayScenarioResourceTracker.RegisterSubscription("player-fixture");
                stream = new MemoryStream();
                handle = PlayScenarioResourceTracker.RegisterHandle("player-fixture");
                WriteResources("retained");
            }
            Debug.Log("Player fixture reset:" + ResetCount);
        }

        private static string RunDirectory()
        {
            string[] args = Environment.GetCommandLineArgs();
            int index = Array.IndexOf(args, "--mcp-scenario-request");
            if (index < 0 || index + 1 >= args.Length)
                throw new InvalidOperationException("The Player fixture requires its owned native request.");
            return Path.GetDirectoryName(PlayScenarioPlayerFiles.CheckedAbsolute(args[index + 1]));
        }

        private void WriteResources(string stage)
        {
            PlayScenarioRegisteredResources captured = PlayScenarioResourceTracker.Capture();
            int pid;
            using (var process = System.Diagnostics.Process.GetCurrentProcess())
                pid = process.Id;
            var row = new JObject
            {
                ["iteration"] = ResetCount,
                ["stage"] = stage,
                ["process_id"] = pid,
                ["scriptable_objects"] = captured.ScriptableObjectCount,
                ["subscriptions"] = captured.SubscriptionCount,
                ["handles"] = captured.HandleCount,
                ["error"] = captured.Error,
                ["handle_closed"] = stream == null || !stream.CanWrite,
            };
            File.AppendAllText(
                Path.Combine(RunDirectory(), "fixture-resources.jsonl"),
                row.ToString(Newtonsoft.Json.Formatting.None) + "\n",
                new System.Text.UTF8Encoding(false)
            );
        }

        private static void OnLowMemory() { }

        private void RetainManagedGrowth()
        {
            if (managedGrowthIteration == ResetCount || managedGrowthCount >= ManagedGrowthAllocationLimit)
                return;
            if (retainedManagedGrowth == null)
                retainedManagedGrowth = new byte[ManagedGrowthAllocationLimit][];
            var allocation = new byte[ManagedGrowthAllocationBytes];
            // Touch every page and keep the allocation rooted in the existing fixture owner across scene loads.
            byte witness = (byte)(managedGrowthCount + 1);
            for (int offset = 0; offset < allocation.Length; offset += 4096)
                allocation[offset] = witness;
            allocation[allocation.Length - 1] = witness;
            retainedManagedGrowth[managedGrowthCount++] = allocation;
            managedGrowthIteration = ResetCount;
            WriteManagedGrowth("retained");
            GC.KeepAlive(retainedManagedGrowth);
        }

        private void WriteManagedGrowth(string stage)
        {
            int populated = 0;
            long bytes = 0;
            int witness = 0;
            if (retainedManagedGrowth != null)
                foreach (byte[] allocation in retainedManagedGrowth)
                    if (allocation != null)
                    {
                        populated++;
                        bytes += allocation.Length;
                        witness += allocation[allocation.Length - 1];
                    }
            int pid;
            using (var process = System.Diagnostics.Process.GetCurrentProcess())
                pid = process.Id;
            var row = new JObject
            {
                ["iteration"] = ResetCount,
                ["stage"] = stage,
                ["process_id"] = pid,
                ["retained_arrays"] = populated,
                ["retained_bytes"] = bytes,
                ["allocation_witness"] = witness,
                ["allocation_limit"] = ManagedGrowthAllocationLimit,
                ["allocation_bytes"] = ManagedGrowthAllocationBytes,
            };
            File.AppendAllText(
                Path.Combine(RunDirectory(), "fixture-memory-growth.jsonl"),
                row.ToString(Newtonsoft.Json.Formatting.None) + "\n",
                new System.Text.UTF8Encoding(false)
            );
        }

        private void ReleaseManagedGrowth()
        {
            if (retainedManagedGrowth == null)
                return;
            Array.Clear(retainedManagedGrowth, 0, retainedManagedGrowth.Length);
            retainedManagedGrowth = null;
            managedGrowthCount = 0;
            WriteManagedGrowth("released");
        }

        private bool CleanupResources()
        {
            if (resourceIteration == ResetCount)
                return true;
            if (subscription != null)
            {
                Application.lowMemory -= OnLowMemory;
                subscription.Dispose();
                subscription = null;
                stream.Dispose();
                handle.Dispose();
                handle = null;
                Destroy(resource);
                return false;
            }
            if (resource != null)
                return false;
            WriteResources("destroyed-and-disposed");
            resourceIteration = ResetCount;
            return true;
        }

        private sealed class FixtureResource : ScriptableObject { }

        private void OnDestroy()
        {
            ReleaseManagedGrowth();
            registration?.Dispose();
            setupRegistration?.Dispose();
            cleanupRegistration?.Dispose();
        }

        private void OnApplicationQuit() => ReleaseManagedGrowth();

        private sealed class StageProbe : IPlayScenarioStateProvider
        {
            private readonly ScenarioPlayerFixtureState owner;
            private readonly string stage;

            internal StageProbe(ScenarioPlayerFixtureState owner, string stage)
            {
                this.owner = owner;
                this.stage = stage;
            }

            public bool TryRead(out PlayScenarioStateValue value)
            {
                if (owner.ResetCount == 2 && ScenarioPlayerFixture.Mode == "iteration-" + stage + "-failure")
                    throw new InvalidOperationException("Player fixture iteration 2 " + stage + " failure.");
                if (stage == "cleanup" && owner.faultIteration != owner.ResetCount)
                {
                    string mode = ScenarioPlayerFixture.Mode;
                    if (mode == "soak-budget-growth")
                        owner.RetainManagedGrowth();
                    string blocker =
                        mode == "soak-mid-io" && owner.ResetCount == 13 ? "iteration-0013.json.tmp"
                        : mode == "soak-final-io" && owner.ResetCount == 3 ? "run.json.tmp"
                        : null;
                    if (blocker != null)
                    {
                        Directory.CreateDirectory(Path.Combine(RunDirectory(), blocker));
                        Debug.Log("PLAYER_FIXTURE_PERSISTENCE_BLOCKED:" + blocker);
                    }
                    owner.faultIteration = owner.ResetCount;
                }
                value = PlayScenarioStateValue.FromBoolean(stage != "cleanup" || ScenarioPlayerFixture.Mode != "soak-resources" || owner.CleanupResources());
                return true;
            }
        }
    }
}
