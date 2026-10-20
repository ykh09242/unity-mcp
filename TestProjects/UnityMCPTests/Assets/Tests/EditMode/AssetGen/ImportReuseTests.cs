using System;
using System.IO;
using MCPForUnity.Editor.Services.AssetGen;
using MCPForUnity.Editor.Services.AssetGen.Import;
using NUnit.Framework;
using UnityEditor;
using UnityEngine;

namespace MCPForUnityTests.Editor.AssetGen
{
    public class ImportWorkCounter : AssetPostprocessor
    {
        internal static string Watched;
        internal static int Count;

        private void OnPostprocessTexture(Texture2D texture)
        {
            if (assetPath == Watched)
                Count++;
        }

        private void OnPostprocessAudio(AudioClip clip)
        {
            if (assetPath == Watched)
                Count++;
        }
    }

    public class ImportReuseTests
    {
        private string root;

        [SetUp]
        public void SetUp()
        {
            root = "Assets/MCPImportReuse_" + Guid.NewGuid().ToString("N");
            Directory.CreateDirectory(Path.GetFullPath(root));
        }

        [TearDown]
        public void TearDown()
        {
            ImportWorkCounter.Watched = null;
            AssetDatabase.DeleteAsset(root);
        }

        private static AssetGenJob Job(string kind) => new AssetGenJob { Kind = kind, State = AssetGenJobState.Importing };

        private static void Image(string path, int width)
        {
            var texture = new Texture2D(width, 4);
            try
            {
                File.WriteAllBytes(Path.GetFullPath(path), texture.EncodeToPNG());
            }
            finally
            {
                UnityEngine.Object.DestroyImmediate(texture);
            }
        }

        private static void Audio(string path, int seconds)
        {
            const int rate = 8000;
            int size = seconds * rate * 2;
            using var w = new BinaryWriter(File.Create(Path.GetFullPath(path)));
            w.Write(System.Text.Encoding.ASCII.GetBytes("RIFF"));
            w.Write(36 + size);
            w.Write(System.Text.Encoding.ASCII.GetBytes("WAVEfmt "));
            w.Write(16);
            w.Write((short)1);
            w.Write((short)1);
            w.Write(rate);
            w.Write(rate * 2);
            w.Write((short)2);
            w.Write((short)16);
            w.Write(System.Text.Encoding.ASCII.GetBytes("data"));
            w.Write(size);
            w.Write(new byte[size]);
        }

        [Test]
        public void ImageRetrySkipsImportsButChangedSourceAndSettingsStillApply()
        {
            string path = root + "/image.png";
            Image(path, 8);
            Assert.That(ImageImportPipeline.ImportInto(Job("image"), path, true, true, true).State, Is.EqualTo(AssetGenJobState.Done));
            string guid = AssetDatabase.AssetPathToGUID(path);
            ImportWorkCounter.Watched = path;
            ImportWorkCounter.Count = 0;
            Assert.That(ImageImportPipeline.ImportInto(Job("image"), path, true, true, true).State, Is.EqualTo(AssetGenJobState.Done));
            Assert.That(ImportWorkCounter.Count, Is.Zero, "Unchanged retry should not invoke the native importer.");
            Assert.That(ImageImportPipeline.ImportInto(Job("image"), path, false, false, false).State, Is.EqualTo(AssetGenJobState.Done));
            Assert.That(ImportWorkCounter.Count, Is.EqualTo(1));
            var stamp = File.GetLastWriteTimeUtc(Path.GetFullPath(path));
            Image(path, 16);
            File.SetLastWriteTimeUtc(Path.GetFullPath(path), stamp);
            ImageImportPipeline.ImportInto(Job("image"), path, false, false, false);
            Assert.That(AssetDatabase.LoadAssetAtPath<Texture2D>(path).width, Is.EqualTo(16));
            Assert.That(AssetDatabase.AssetPathToGUID(path), Is.EqualTo(guid));
        }

        [Test]
        public void AudioRetrySkipsImportsButChangedSourceStillUpdatesLengthPolicy()
        {
            string path = root + "/audio.wav";
            Audio(path, 1);
            Assert.That(AudioImportPipeline.ImportInto(Job("audio"), path).State, Is.EqualTo(AssetGenJobState.Done));
            string guid = AssetDatabase.AssetPathToGUID(path);
            ImportWorkCounter.Watched = path;
            ImportWorkCounter.Count = 0;
            Assert.That(AudioImportPipeline.ImportInto(Job("audio"), path).State, Is.EqualTo(AssetGenJobState.Done));
            Assert.That(ImportWorkCounter.Count, Is.Zero);
            Audio(path, 31);
            AudioImportPipeline.ImportInto(Job("audio"), path);
            Assert.That(AssetDatabase.LoadAssetAtPath<AudioClip>(path).length, Is.EqualTo(31).Within(0.05));
            Assert.That(((AudioImporter)AssetImporter.GetAtPath(path)).defaultSampleSettings.loadType, Is.EqualTo(AudioClipLoadType.Streaming));
            Assert.That(AssetDatabase.AssetPathToGUID(path), Is.EqualTo(guid));
        }
    }
}
