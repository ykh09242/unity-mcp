using System;
using System.Diagnostics;
using System.IO;
using System.Text;
using System.Threading;
using MCPForUnity.Editor.Services.PlayScenarios;
using MCPForUnity.Runtime.PlayScenarios;
using Newtonsoft.Json.Linq;
using NUnit.Framework;

namespace MCPForUnityTests
{
    public sealed class PlayScenarioPlayerAssuranceTests
    {
        private string directory;

        [SetUp]
        public void SetUp()
        {
            directory = Path.Combine(Path.GetTempPath(), "MCPPlayerAssurance_" + Guid.NewGuid().ToString("N"));
            Directory.CreateDirectory(directory);
        }

        [TearDown]
        public void TearDown()
        {
            if (Directory.Exists(directory))
                Directory.Delete(directory, true);
        }

        private static PlayScenarioDefinition Definition() =>
            PlayScenarioDefinition.Parse(
                new JObject
                {
                    ["name"] = "player-assurance",
                    ["setup_steps"] = new JArray(
                        new JObject
                        {
                            ["name"] = "load",
                            ["action"] = "load_scene",
                            ["scene"] = "Assets/Unit.unity",
                        }
                    ),
                    ["steps"] = new JArray(
                        new JObject
                        {
                            ["name"] = "ready",
                            ["action"] = "wait_state",
                            ["state_id"] = "unit-ready",
                            ["state_equals"] = true,
                        }
                    ),
                }
            );

        private static JObject Manifest() => PlayScenarioPlayerBundle.Create(Definition(), "6000.0.69f1", "1.0.0", new string('a', 32), "build-label");

        private static JObject Request(JObject manifest) =>
            new JObject
            {
                ["schema_version"] = manifest["schema_version"],
                ["job_id"] = new string('b', 32),
                ["scenario_name"] = manifest["scenario_name"],
                ["definition_hash"] = manifest["definition_hash"],
                ["repeat_count"] = 2,
                ["timeout_seconds"] = 60,
                ["source_revision"] = "run-label",
                ["build_id"] = manifest["build_id"],
                ["build_source_revision"] = manifest["build_source_revision"],
                ["payload_hash"] = new string('c', 64),
            };

        [Test]
        public void VersionTwoBindsBuildIdentitySeparatelyFromRunLabel()
        {
            JObject manifest = Manifest();
            PlayScenarioPlayerBundle bundle = PlayScenarioPlayerBundle.Parse(manifest.ToString());
            JObject request = Request(manifest);
            PlayScenarioPlayerRequest.Parse(request.ToString()).ValidateBundle(bundle);
            foreach (string field in new[] { "build_id", "build_source_revision" })
            {
                JObject wrong = (JObject)request.DeepClone();
                wrong[field] = field == "build_id" ? new string('d', 32) : "other-build";
                Assert.Throws<ArgumentException>(() => PlayScenarioPlayerRequest.Parse(wrong.ToString()).ValidateBundle(bundle));
            }
            request.Remove("build_source_revision");
            Assert.Throws<ArgumentException>(() => PlayScenarioPlayerRequest.Parse(request.ToString()));
            manifest["build_source_revision"] = null;
            bundle = PlayScenarioPlayerBundle.Parse(manifest.ToString());
            request = Request(manifest);
            PlayScenarioPlayerRequest parsed = PlayScenarioPlayerRequest.Parse(request.ToString());
            parsed.ValidateBundle(bundle);
            var report = new JObject { ["reproduction"] = new JObject() };
            parsed.AddReproduction(report, bundle);
            Assert.That(report["reproduction"]["build_source_revision"].Type, Is.EqualTo(JTokenType.Null));
            Assert.That(JObject.Parse(report.ToString())["reproduction"]["build_source_revision"].Type, Is.EqualTo(JTokenType.Null));
            Assert.That((string)report["reproduction"]["payload_verification"], Is.EqualTo("launcher_admission"));
        }

        [Test]
        public void LegacyVersionCannotPromoteItselfToVerifiedPayload()
        {
            JObject manifest = Manifest();
            manifest["schema_version"] = 1;
            manifest.Remove("build_id");
            manifest.Remove("build_source_revision");
            PlayScenarioPlayerBundle bundle = PlayScenarioPlayerBundle.Parse(manifest.ToString());
            JObject request = Request(manifest);
            request.Remove("build_id");
            request.Remove("build_source_revision");
            request.Remove("payload_hash");
            PlayScenarioPlayerRequest parsed = PlayScenarioPlayerRequest.Parse(request.ToString());
            parsed.ValidateBundle(bundle);
            var report = new JObject { ["reproduction"] = new JObject() };
            parsed.AddReproduction(report, bundle);
            Assert.That((string)report["reproduction"]["payload_verification"], Is.EqualTo("unverified_legacy"));
            foreach (string field in new[] { "build_id", "build_source_revision", "payload_hash" })
            {
                Assert.That(report["reproduction"][field].Type, Is.EqualTo(JTokenType.Null));
                Assert.That(JObject.Parse(report.ToString())["reproduction"][field].Type, Is.EqualTo(JTokenType.Null));
            }
            request["payload_hash"] = new string('c', 64);
            Assert.Throws<ArgumentException>(() => PlayScenarioPlayerRequest.Parse(request.ToString()));
            manifest["build_id"] = new string('a', 32);
            Assert.Throws<ArgumentException>(() => PlayScenarioPlayerBundle.Parse(manifest.ToString()));
        }

        [Test]
        public void InventoryIncludesAllOutputAndRejectsTamperExtraAndMissingFiles()
        {
            File.WriteAllText(Path.Combine(directory, "player.exe"), "exe");
            Directory.CreateDirectory(Path.Combine(directory, "Data"));
            string data = Path.Combine(directory, "Data", "debug.pdb");
            File.WriteAllText(data, "debug");
            JObject manifest = Manifest();
            PlayScenarioPlayerPayload.Attach(manifest, directory);
            Assert.That(((JArray)manifest["payload_inventory"]).Count, Is.EqualTo(2));
            PlayScenarioPlayerPayload.Verify(directory, manifest);
            File.WriteAllText(Path.Combine(directory, PlayScenarioPlayerBundle.ManifestName), manifest.ToString());
            PlayScenarioPlayerPayload.Verify(directory, manifest);
            File.WriteAllText(data, "changed");
            Assert.Throws<IOException>(() => PlayScenarioPlayerPayload.Verify(directory, manifest));
            File.WriteAllText(data, "debug");
            string extra = Path.Combine(directory, "extra.txt");
            File.WriteAllText(extra, "extra");
            Assert.Throws<IOException>(() => PlayScenarioPlayerPayload.Verify(directory, manifest));
            File.Delete(extra);
            File.Delete(data);
            Assert.Throws<IOException>(() => PlayScenarioPlayerPayload.Verify(directory, manifest));
        }

        [TestCase("../outside")]
        [TestCase("Data/../outside")]
        [TestCase("scenario-bundle.json")]
        [TestCase("CON.txt")]
        [TestCase("Data/alias.")]
        [TestCase("Data/alias ")]
        [TestCase("Assets/Resources/GameData/never-read.json")]
        [TestCase(".env.local")]
        [TestCase("Data/invalid\u007f")]
        public void ManifestRejectsInvalidOrSelfReferencingInventory(string path)
        {
            var manifest = Manifest();
            var inventory = new JArray(
                new JObject
                {
                    ["path"] = path,
                    ["size_bytes"] = 1,
                    ["sha256"] = new string('a', 64),
                }
            );
            string json = PlayScenarioPlayerBundle.CanonicalJson(inventory);
            manifest["payload_inventory"] = inventory;
            manifest["payload_inventory_json"] = json;
            manifest["payload_hash"] = PlayScenarioPlayerPayload.HashText(json);
            Assert.Throws<ArgumentException>(() => PlayScenarioPlayerPayload.ValidateManifest(manifest));
        }

        [Test]
        public void InventoryRejectsHashMismatchAndBoundsDiscoveredDirectories()
        {
            File.WriteAllText(Path.Combine(directory, "player.exe"), "exe");
            JObject manifest = Manifest();
            PlayScenarioPlayerPayload.Attach(manifest, directory);
            manifest["payload_hash"] = new string('0', 64);
            Assert.Throws<ArgumentException>(() => PlayScenarioPlayerPayload.ValidateManifest(manifest));
            for (int index = 0; index < 4096; index++)
                Directory.CreateDirectory(Path.Combine(directory, index.ToString()));
            Assert.Throws<IOException>(() => PlayScenarioPlayerPayload.Inventory(directory));
        }

        [Test]
        public void ProgressStartsWithoutLivenessThenThrottlesCompletedLoopSnapshots()
        {
            JObject manifest = Manifest();
            PlayScenarioPlayerBundle bundle = PlayScenarioPlayerBundle.Parse(manifest.ToString());
            PlayScenarioPlayerRequest request = PlayScenarioPlayerRequest.Parse(Request(manifest).ToString());
            PlayScenarioRun run = PlayScenarioEngine.Create(bundle.Definition, request.JobId, 2, 60, 1000);
            run.Steps[0].Detail = new string('x', 1000);
            var progress = new PlayScenarioPlayerProgress(directory, request, bundle, 42);
            progress.PublishInitial(run);
            string path = Path.Combine(directory, "progress.json");
            JObject initial = JObject.Parse(File.ReadAllText(path));
            Assert.That((long)initial["sequence"], Is.Zero);
            Assert.That((long)initial["main_loop_sequence"], Is.Zero);
            Assert.That((long)initial["heartbeat_unix_ms"], Is.Zero);
            Assert.That(progress.PublishMainLoop(run, 1, 1001, 1), Is.True);
            string first = File.ReadAllText(path);
            Assert.That(progress.PublishMainLoop(run, 2, 1002, 2), Is.False);
            Assert.That(File.ReadAllText(path), Is.EqualTo(first));
            Assert.That(progress.PublishMainLoop(run, 3, 2001, 1001), Is.True);
            JObject latest = JObject.Parse(File.ReadAllText(path));
            Assert.That((long)latest["sequence"], Is.EqualTo(2));
            Assert.That((long)latest["main_loop_sequence"], Is.EqualTo(3));
            Assert.That((int)latest["process_id"], Is.EqualTo(42));
            Assert.That((string)latest["build_id"], Is.EqualTo(bundle.BuildId));
            Assert.That(((string)latest["last_observation"]).Length, Is.EqualTo(512));
            Assert.That(new FileInfo(path).Length, Is.LessThanOrEqualTo(PlayScenarioPlayerProgress.Limit));
            Assert.Throws<InvalidOperationException>(() => progress.PublishMainLoop(run, 3, 2002, 1002));
            Assert.Throws<InvalidOperationException>(() => progress.PublishMainLoop(run, 4, 2002, 1000));
            Assert.That(run.QueryCounts.TargetSearches, Is.Zero);
        }

        // Windows replacement can briefly deny a new reader handle. Retry only opening,
        // at most three attempts within 50 ms; reading and JSON validation are never retried.
        private static FileStream OpenProgressSnapshot(string path)
        {
            var deadline = Stopwatch.StartNew();
            for (int attempt = 0; ; attempt++)
            {
                try
                {
                    return new FileStream(path, FileMode.Open, FileAccess.Read, FileShare.ReadWrite | FileShare.Delete);
                }
                catch (IOException error)
                {
                    int code = error.HResult & 0xffff;
                    if (
                        Environment.OSVersion.Platform != PlatformID.Win32NT
                        || attempt >= 2
                        || deadline.ElapsedMilliseconds >= 40
                        || (code != 2 && code != 32 && code != 33)
                    )
                        throw;
                    Thread.Sleep(10);
                    if (deadline.ElapsedMilliseconds >= 50)
                        throw;
                }
            }
        }

        [Test]
        public void DiagnosticReaderDoesNotRetryMalformedJson()
        {
            string path = Path.Combine(directory, "progress.json");
            File.WriteAllText(path, "{invalid");
            using (var stream = OpenProgressSnapshot(path))
            using (var reader = new StreamReader(stream, Encoding.UTF8))
                Assert.Throws<Newtonsoft.Json.JsonReaderException>(() => JObject.Parse(reader.ReadToEnd()));
        }

        [Test]
        public void AtomicReplacementWorksWithConcurrentShareDeleteReaderAndPreservesEvidenceOnFailure()
        {
            string path = Path.Combine(directory, "progress.json");
            PlayScenarioPlayerFiles.WriteNew(path, "{\"sequence\":0}", 1024);
            Exception readerError = null;
            int reads = 0;
            using (var stop = new CancellationTokenSource())
            using (var started = new ManualResetEventSlim())
            {
                var readerThread = new Thread(() =>
                {
                    try
                    {
                        while (!stop.IsCancellationRequested)
                        {
                            using (var stream = OpenProgressSnapshot(path))
                            using (var reader = new StreamReader(stream, Encoding.UTF8))
                            {
                                JObject.Parse(reader.ReadToEnd());
                                Interlocked.Increment(ref reads);
                                started.Set();
                            }
                        }
                    }
                    catch (Exception error)
                    {
                        readerError = error;
                        started.Set();
                    }
                })
                {
                    IsBackground = true,
                };
                readerThread.Start();
                try
                {
                    Assert.That(started.Wait(3000), Is.True);
                    for (int index = 1; index <= 64; index++)
                        PlayScenarioPlayerFiles.ReplaceExisting(path, "{\"sequence\":" + index + "}", 1024);
                }
                finally
                {
                    stop.Cancel();
                    Assert.That(readerThread.Join(3000), Is.True);
                }
            }
            Assert.That(readerError, Is.Null);
            Assert.That(reads, Is.GreaterThan(0));
            UnityEngine.Debug.Log(
                "PLAYER_PROGRESS_ATOMIC_READER_VALID_READS:" + reads + ";final_sequence:" + (int)JObject.Parse(File.ReadAllText(path))["sequence"]
            );
            Assert.That((int)JObject.Parse(File.ReadAllText(path))["sequence"], Is.EqualTo(64));
            File.WriteAllText(path + ".tmp", "other-owned-evidence");
            Assert.Throws<IOException>(() => PlayScenarioPlayerFiles.ReplaceExisting(path, "{}", 1024));
            Assert.That(File.ReadAllText(path + ".tmp"), Is.EqualTo("other-owned-evidence"));
            Assert.That((int)JObject.Parse(File.ReadAllText(path))["sequence"], Is.EqualTo(64));
        }
    }
}
