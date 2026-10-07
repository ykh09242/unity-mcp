using System;
using System.IO;
using MCPForUnity.Editor.Services.AssetGen;
using MCPForUnity.Editor.Services.AssetGen.Import;
using NUnit.Framework;
using UnityEditor;
using UnityEngine;
using UnityEngine.TestTools;

namespace MCPForUnityTests.Editor.AssetGen
{
    public class AudioImportResultTests
    {
        private string _root;
        private bool _previousIgnoreLogs;

        [SetUp]
        public void SetUp()
        {
            _root = "Assets/MCPAudioImportContracts_" + Guid.NewGuid().ToString("N");
            Directory.CreateDirectory(Path.GetFullPath(_root));
            _previousIgnoreLogs = LogAssert.ignoreFailingMessages;
        }

        [TearDown]
        public void TearDown()
        {
            LogAssert.ignoreFailingMessages = _previousIgnoreLogs;
            Assert.IsTrue(_root.StartsWith("Assets/MCPAudioImportContracts_", StringComparison.Ordinal));
            AssetDatabase.DeleteAsset(_root);
        }

        [Test]
        public void CorruptAudioWithRegisteredGuid_IsFailedInsteadOfDone()
        {
            string path = _root + "/invalid.wav";
            File.WriteAllText(Path.GetFullPath(path), "owned invalid audio bytes");
            LogAssert.ignoreFailingMessages = true;
            var result = AudioImportPipeline.ImportInto(Job(), path);

            Assert.IsNotEmpty(AssetDatabase.AssetPathToGUID(path), "Establish the registered-file false-success precondition.");
            Assert.IsInstanceOf<AudioImporter>(AssetImporter.GetAtPath(path), "Establish the importer-present/null-clip failure branch.");
            Assert.IsNull(AssetDatabase.LoadAssetAtPath<AudioClip>(path));
            Assert.AreEqual(AssetGenJobState.Failed, result.State);
            StringAssert.Contains("AudioClip", result.Error);
        }

        [TestCase(1, AudioClipLoadType.DecompressOnLoad)]
        [TestCase(11, AudioClipLoadType.CompressedInMemory)]
        [TestCase(31, AudioClipLoadType.Streaming)]
        public void ValidAudio_PreservesLengthPolicyAndRegisteredResult(int seconds, AudioClipLoadType expected)
        {
            string path = _root + "/valid.wav";
            WriteWave(path, seconds);
            var result = AudioImportPipeline.ImportInto(Job(), path);
            var clip = AssetDatabase.LoadAssetAtPath<AudioClip>(path);

            Assert.IsNotNull(clip, "Establish that the fixture produced an AudioClip.");
            Assert.AreEqual(seconds, clip.length, 0.05f);
            Assert.AreEqual(AssetGenJobState.Done, result.State, result.Error);
            Assert.AreEqual(path, result.AssetPath);
            Assert.AreEqual(AssetDatabase.AssetPathToGUID(path), result.AssetGuid);
            Assert.IsNotEmpty(result.AssetGuid);
            var importer = AssetImporter.GetAtPath(path) as AudioImporter;
            Assert.IsNotNull(importer);
            Assert.AreEqual(expected, importer.defaultSampleSettings.loadType);
            Assert.IsFalse(importer.forceToMono);
            Assert.IsFalse(importer.loadInBackground);
        }

        private static AssetGenJob Job() =>
            new AssetGenJob
            {
                JobId = Guid.NewGuid().ToString("N"),
                Kind = "audio",
                Provider = "fal",
                State = AssetGenJobState.Importing,
                Format = "wav",
            };

        private static void WriteWave(string path, int seconds)
        {
            const int sampleRate = 8000;
            int dataSize = seconds * sampleRate * 2;
            using (var writer = new BinaryWriter(File.Create(Path.GetFullPath(path))))
            {
                writer.Write(System.Text.Encoding.ASCII.GetBytes("RIFF"));
                writer.Write(36 + dataSize);
                writer.Write(System.Text.Encoding.ASCII.GetBytes("WAVEfmt "));
                writer.Write(16);
                writer.Write((short)1);
                writer.Write((short)1);
                writer.Write(sampleRate);
                writer.Write(sampleRate * 2);
                writer.Write((short)2);
                writer.Write((short)16);
                writer.Write(System.Text.Encoding.ASCII.GetBytes("data"));
                writer.Write(dataSize);
                writer.Write(new byte[dataSize]);
            }
        }
    }
}
