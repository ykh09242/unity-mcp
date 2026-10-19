using System;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Text;
using MCPForUnity.Editor.Tools;
using MCPForUnity.Runtime.Helpers;
using MCPForUnityTests.Editor.Helpers;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEditor;
using UnityEngine;
using UnityEngine.Audio;
using Object = UnityEngine.Object;

namespace MCPForUnityTests.Editor.Tools
{
    public class ManageAudioDiagnosticsTests
    {
        private readonly PrefabTestSceneFixture scene = new PrefabTestSceneFixture();
        private string folder;
        private AudioSource source;
        private AudioClip generated;

        private static JObject Send(JObject request) => JObject.FromObject(ManageAudio.HandleCommand(request));

        private JObject Request(string action) =>
            new JObject
            {
                ["action"] = action,
                ["target"] = source.gameObject.GetInstanceIDCompat(),
                ["searchMethod"] = "by_id",
            };

        [SetUp]
        public void Setup()
        {
            scene.PrepareRunnerBootstrap();
            scene.Create("AudioDiagnostics_", Guid.NewGuid().ToString("N"));
            source = new GameObject("OwnedSpeaker").AddComponent<AudioSource>();
            source.playOnAwake = false;
            generated = AudioClip.Create("OwnedClip", 128, 1, 8000, false);
            source.clip = generated;
        }

        [TearDown]
        public void Cleanup()
        {
            if (folder != null)
                AssetDatabase.DeleteAsset(folder);
            if (generated != null)
                Object.DestroyImmediate(generated);
            scene.Close();
            scene.RestoreRunnerBootstrap();
            folder = null;
        }

        private void Listener() => new GameObject("OwnedListener").AddComponent<AudioListener>();

        [TestCase("missing_listener")]
        [TestCase("source_muted")]
        [TestCase("source_volume_zero")]
        [TestCase("beyond_max_distance")]
        [TestCase("clip_missing")]
        public void Diagnose_ReportsSeparateObservedCausesWithoutChangingSource(string cause)
        {
            // Given
            if (cause != "missing_listener")
                Listener();
            if (cause == "source_muted")
                source.mute = true;
            if (cause == "source_volume_zero")
                source.volume = 0;
            if (cause == "beyond_max_distance")
            {
                source.spatialBlend = 1;
                source.maxDistance = 3;
                source.transform.position = Vector3.right * 50;
            }
            if (cause == "clip_missing")
                source.clip = null;
            var clipBefore = source.clip;
            bool muteBefore = source.mute;
            float volumeBefore = source.volume;
            // When
            var result = Send(Request("diagnose"));
            // Then
            Assert.IsTrue(result.Value<bool>("success"), result.ToString());
            Assert.That(result["data"]["causes"].Values<string>(), Does.Contain(cause));
            Assert.AreEqual("unverified", result["data"].Value<string>("output_device_audibility"));
            Assert.AreSame(clipBefore, source.clip);
            Assert.AreEqual(muteBefore, source.mute);
            Assert.AreEqual(volumeBefore, source.volume);
        }

        [Test]
        public void Diagnose_ReportsClipLoadStateWithoutLoadingOrPlaying()
        {
            // Given
            Listener();
            AudioDataLoadState before = generated.loadState;
            // When
            var result = Send(Request("diagnose"));
            // Then
            Assert.IsTrue(result.Value<bool>("success"), result.ToString());
            Assert.AreEqual(before.ToString(), result["data"]["clip"].Value<string>("load_state"));
            Assert.AreEqual(before, generated.loadState);
            Assert.IsFalse(source.isPlaying);
        }

        private string Wav()
        {
            folder = "Assets/audio-diagnostics-" + Guid.NewGuid().ToString("N");
            Directory.CreateDirectory(Path.Combine(Application.dataPath, folder.Substring(7)));
            string path = folder + "/Owned.wav";
            using (var writer = new BinaryWriter(File.Create(Path.Combine(Application.dataPath, path.Substring(7)))))
            {
                writer.Write(Encoding.ASCII.GetBytes("RIFF"));
                writer.Write(36 + 256);
                writer.Write(Encoding.ASCII.GetBytes("WAVEfmt "));
                writer.Write(16);
                writer.Write((short)1);
                writer.Write((short)1);
                writer.Write(8000);
                writer.Write(16000);
                writer.Write((short)2);
                writer.Write((short)16);
                writer.Write(Encoding.ASCII.GetBytes("data"));
                writer.Write(256);
                writer.Write(new byte[256]);
            }
            AssetDatabase.ImportAsset(path, ImportAssetOptions.ForceSynchronousImport);
            return path;
        }

        [Test]
        public void ImportPolicy_ApplyPlatformReadbackPreservesDefaultsGuidAndSceneReference()
        {
            // Given
            string path = Wav();
            source.clip = AssetDatabase.LoadAssetAtPath<AudioClip>(path);
            string guid = AssetDatabase.AssetPathToGUID(path);
            var importer = (AudioImporter)AssetImporter.GetAtPath(path);
            var defaults = importer.defaultSampleSettings;
            bool mono = importer.forceToMono;
            // When
            var result = Send(
                new JObject
                {
                    ["action"] = "apply_import_settings",
                    ["target"] = path,
                    ["platform"] = "Standalone",
                    ["settings"] = new JObject { ["load_type"] = "CompressedInMemory", ["quality"] = 0.42 },
                }
            );
            // Then
            Assert.IsTrue(result.Value<bool>("success"), result.ToString());
            importer = (AudioImporter)AssetImporter.GetAtPath(path);
            Assert.IsTrue(importer.ContainsSampleSettingsOverride("Standalone"));
            Assert.AreEqual(AudioClipLoadType.CompressedInMemory, importer.GetOverrideSampleSettings("Standalone").loadType);
            Assert.AreEqual(0.42f, importer.GetOverrideSampleSettings("Standalone").quality, 0.001f);
            Assert.AreEqual(defaults.loadType, importer.defaultSampleSettings.loadType);
            Assert.AreEqual(defaults.quality, importer.defaultSampleSettings.quality);
            Assert.AreEqual(mono, importer.forceToMono);
            Assert.AreEqual(guid, AssetDatabase.AssetPathToGUID(path));
            Assert.AreEqual(path, AssetDatabase.GetAssetPath(source.clip));
            var read = Send(
                new JObject
                {
                    ["action"] = "read_import_settings",
                    ["target"] = path,
                    ["platform"] = "Standalone",
                }
            );
            Assert.IsTrue(read.Value<bool>("success"), read.ToString());
            Assert.AreEqual("CompressedInMemory", read["data"]["sample_settings"].Value<string>("load_type"));
        }

        [Test]
        public void Routing_ExactGroupReadbackPreservesClipAndSupportsUndo()
        {
            // Given: the editor creates/imports a real owned mixer through its own test-only factory.
            folder = "Assets/audio-routing-" + Guid.NewGuid().ToString("N");
            Directory.CreateDirectory(Path.Combine(Application.dataPath, folder.Substring(7)));
            string path = folder + "/Owned.mixer";
            Type controller = AppDomain
                .CurrentDomain.GetAssemblies()
                .Select(a => a.GetType("UnityEditor.Audio.AudioMixerController"))
                .FirstOrDefault(t => t != null);
            Assert.IsNotNull(controller, "The licensed editor must expose its test mixer factory.");
            MethodInfo create = controller.GetMethod("CreateMixerControllerAtPath", BindingFlags.Public | BindingFlags.Static);
            Assert.IsNotNull(create);
            create.Invoke(null, new object[] { path });
            AssetDatabase.ImportAsset(path, ImportAssetOptions.ForceSynchronousImport);
            var mixer = AssetDatabase.LoadAssetAtPath<AudioMixer>(path);
            Assert.IsNotNull(mixer);
            AudioMixerGroup group = mixer.FindMatchingGroups("").Single();
            Assert.IsTrue(AssetDatabase.TryGetGUIDAndLocalFileIdentifier(group, out string guid, out long id));
            Assert.AreEqual(AssetDatabase.AssetPathToGUID(path), guid);
            var clip = source.clip;
            source.volume = 0.31f;
            var request = Request("get_routing");
            request["mixer_path"] = path;
            var inventory = Send(request);
            Assert.IsTrue(inventory.Value<bool>("success"), inventory.ToString());
            Assert.AreEqual(id.ToString(), inventory["data"]["available_groups"][0].Value<string>("group_id"));
            // When
            request["action"] = "set_routing";
            request["group_id"] = id.ToString();
            var result = Send(request);
            // Then
            Assert.IsTrue(result.Value<bool>("success"), result.ToString());
            Assert.AreSame(group, source.outputAudioMixerGroup);
            Assert.AreEqual(id.ToString(), result["data"]["routing"].Value<string>("group_id"));
            Assert.AreSame(clip, source.clip);
            Assert.AreEqual(0.31f, source.volume);
            Undo.FlushUndoRecordObjects();
            Undo.PerformUndo();
            Assert.IsNull(source.outputAudioMixerGroup);
            Assert.AreSame(clip, source.clip);
        }

        [TestCase("null")]
        [TestCase("\"invalid\"")]
        [TestCase("[]")]
        [TestCase("false")]
        public void ImportPolicy_ClearWithSuppliedMalformedOrNullSettingsPreservesRealOverride(string settingsJson)
        {
            // Given
            string path = Wav();
            var importer = (AudioImporter)AssetImporter.GetAtPath(path);
            var sample = importer.GetOverrideSampleSettings("Standalone");
            sample.quality = 0.37f;
            Assert.IsTrue(importer.SetOverrideSampleSettings("Standalone", sample));
            importer.SaveAndReimport();
            importer = (AudioImporter)AssetImporter.GetAtPath(path);
            Assert.IsTrue(importer.ContainsSampleSettingsOverride("Standalone"));
            var before = importer.GetOverrideSampleSettings("Standalone");
            string guid = AssetDatabase.AssetPathToGUID(path);
            // When
            var result = Send(
                new JObject
                {
                    ["action"] = "apply_import_settings",
                    ["target"] = path,
                    ["platform"] = "Standalone",
                    ["clear_override"] = true,
                    ["settings"] = JToken.Parse(settingsJson),
                }
            );
            // Then
            Assert.IsFalse(result.Value<bool>("success"), result.ToString());
            importer = (AudioImporter)AssetImporter.GetAtPath(path);
            Assert.IsTrue(importer.ContainsSampleSettingsOverride("Standalone"));
            Assert.AreEqual(before.quality, importer.GetOverrideSampleSettings("Standalone").quality);
            Assert.AreEqual(before.loadType, importer.GetOverrideSampleSettings("Standalone").loadType);
            Assert.AreEqual(guid, AssetDatabase.AssetPathToGUID(path));
        }

        [Test]
        public void ImportPolicy_InvalidSettingsRefuseAllChanges()
        {
            // Given
            string path = Wav();
            var importer = (AudioImporter)AssetImporter.GetAtPath(path);
            bool before = importer.forceToMono;
            // When
            var result = Send(
                new JObject
                {
                    ["action"] = "apply_import_settings",
                    ["target"] = path,
                    ["platform"] = "Default",
                    ["settings"] = new JObject { ["force_to_mono"] = !before, ["quality"] = 2 },
                }
            );
            // Then
            Assert.IsFalse(result.Value<bool>("success"), result.ToString());
            Assert.AreEqual(before, ((AudioImporter)AssetImporter.GetAtPath(path)).forceToMono);
        }
    }
}
