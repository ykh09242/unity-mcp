using System;
using System.Collections.Generic;
using System.IO;
using System.Text;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Editor.Tools;
using MCPForUnity.Editor.Tools.Recording;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEngine;

namespace MCPForUnityTests.EditMode.Tools
{
    public class ManageRecordingTests
    {
        private string _root;
        private RecordingJob _job;

        private sealed class FakeEncoder : IRecordingEncoder
        {
            private readonly string _path;
            internal readonly List<double> Timestamps = new List<double>();
            internal int Disposals;
            internal bool RejectFrame;
            internal bool FailDispose;

            internal FakeEncoder(string path)
            {
                _path = path;
                File.WriteAllText(_path, "partial");
            }

            public bool AddFrame(Texture2D frame, double elapsed)
            {
                if (RejectFrame)
                    return false;
                Timestamps.Add(elapsed);
                return true;
            }

            public void Dispose()
            {
                Disposals++;
                if (FailDispose)
                    throw new IOException("Fixture codec finalization failed");
                File.WriteAllText(_path, "finalized");
            }
        }

        [SetUp]
        public void SetUp()
        {
            _job = null;
            _root = Path.Combine(Path.GetTempPath(), "McpRecordingTests-" + Guid.NewGuid().ToString("N"));
        }

        [TearDown]
        public void TearDown()
        {
            _job?.Finish("interrupted", "test_cleanup", 200);
            if (Directory.Exists(_root))
                Directory.Delete(_root, true);
        }

        private FakeEncoder Start(RecordingOptions options = null)
        {
            options = options ?? new RecordingOptions("game_view", 32, 32, 10, 2);
            string output = RecordingOptions.ResolveOutputPath(_root, null, "fixture.mp4", "fixture");
            FakeEncoder encoder = null;
            _job = new RecordingJob("fixture", options, _root, output, 100, (path, config) => encoder = new FakeEncoder(path));
            return encoder;
        }

        [TestCase(15, 32, 10, 1)]
        [TestCase(33, 32, 10, 1)]
        [TestCase(32, 1922, 10, 1)]
        [TestCase(32, 32, 0, 1)]
        [TestCase(32, 32, 31, 1)]
        [TestCase(32, 32, 10, 0)]
        [TestCase(32, 32, 10, 61)]
        [TestCase(1920, 1920, 30, 60)]
        public void RejectsUnboundedAndInvalidOptions(int width, int height, int fps, double duration) =>
            Assert.Throws<ArgumentException>(() => new RecordingOptions("game_view", width, height, fps, duration));

        [Test]
        public void RejectsNonFiniteDurationAndUnknownSource()
        {
            Assert.Throws<ArgumentException>(() => new RecordingOptions("game_view", 32, 32, 10, double.NaN));
            Assert.Throws<ArgumentException>(() => new RecordingOptions("game_view", 32, 32, 10, double.PositiveInfinity));
            Assert.Throws<ArgumentException>(() => new RecordingOptions("camera", 32, 32, 10, 1));
        }

        [Test]
        public void OutputStaysInRecordingRootAndNeverOverwrites()
        {
            string output = RecordingOptions.ResolveOutputPath(_root, "Captures/Recordings/Session", "owned", "id");
            Assert.That(output, Is.EqualTo(Path.Combine(_root, "Captures", "Recordings", "Session", "owned.mp4")));
            Assert.Throws<InvalidOperationException>(() => RecordingOptions.ResolveOutputPath(_root, "Packages", "owned", "id"));
            Assert.Throws<ArgumentException>(() => RecordingOptions.ResolveOutputPath(_root, "Captures/Recordings/../Recordings", "owned", "id"));
            Assert.Throws<ArgumentException>(() => RecordingOptions.ResolveOutputPath(_root, null, "../owned", "id"));
            Assert.Throws<ArgumentException>(() => RecordingOptions.ResolveOutputPath(_root, null, "CON.mp4", "id"));
            Assert.Throws<ArgumentException>(() => RecordingOptions.ResolveOutputPath(_root, null, " ", "id"));
            Assert.Throws<ArgumentException>(() => RecordingOptions.ResolveOutputPath(_root, null, "owned.mov", "id"));
            Directory.CreateDirectory(Path.GetDirectoryName(output));
            File.WriteAllText(output, "existing");
            Assert.Throws<IOException>(() => RecordingOptions.ResolveOutputPath(_root, "Captures/Recordings/Session", "owned", "id"));
            Assert.That(File.ReadAllText(output), Is.EqualTo("existing"));
        }

        [Test]
        public void MissedFramesUseRealTimestampsWithoutCatchupAndFinishOnce()
        {
            var encoder = Start();
            int cleanupCalls = 0;
            _job.SetCleanup(() => cleanupCalls++);
            _job.AddFrame(null, 32, 32, 100);
            _job.AddFrame(null, 32, 32, 100.05);
            _job.AddFrame(null, 32, 32, 100.25);
            Assert.That(encoder.Timestamps, Is.EqualTo(new[] { 0.0, 0.25 }).Within(0.0001));
            Assert.That(_job.Snapshot(100.25).Value<int>("skipped_sample_slots"), Is.EqualTo(1));
            Assert.That(_job.Snapshot(100.25).Value<bool>("variable_frame_rate"), Is.True);
            _job.Tick(102);
            _job.Finish("stopped", "duplicate", 103);
            Assert.That(_job.Status, Is.EqualTo("completed"));
            Assert.That(encoder.Disposals, Is.EqualTo(1));
            Assert.That(cleanupCalls, Is.EqualTo(1));
            Assert.That(File.ReadAllText(_job.OutputPath), Is.EqualTo("finalized"));
            Assert.That(Directory.GetDirectories(Path.GetDirectoryName(_job.OutputPath)), Is.Empty);
        }

        [Test]
        public void ExplicitStopPublishesOnlyFinalizedNonemptyRecording()
        {
            var encoder = Start();
            _job.AddFrame(null, 32, 32, 100.3);
            _job.Finish("stopped", "stop_requested", 100.5);
            Assert.That(_job.Status, Is.EqualTo("stopped"));
            Assert.That(encoder.Disposals, Is.EqualTo(1));
            Assert.That(_job.Snapshot(999).Value<double>("elapsed_seconds"), Is.EqualTo(0.5));
            Assert.That(_job.Snapshot(999).Value<string>("output_path"), Is.Not.Null);
        }

        [Test]
        public void EmptyCaptureFailsAndDeletesPartialOutput()
        {
            var encoder = Start();
            _job.Tick(102);
            Assert.That(_job.Status, Is.EqualTo("failed"));
            Assert.That(_job.Snapshot(102).Value<string>("error"), Does.Contain("No frames"));
            Assert.That(encoder.Disposals, Is.EqualTo(1));
            Assert.That(File.Exists(_job.OutputPath), Is.False);
            Assert.That(Directory.GetFileSystemEntries(Path.GetDirectoryName(_job.OutputPath)), Is.Empty);
        }

        [TestCase("assembly_reload")]
        [TestCase("play_mode_changed")]
        public void InterruptDisposesAndDeletesPartialOutput(string reason)
        {
            var encoder = Start();
            _job.AddFrame(null, 32, 32, 100);
            _job.Finish("interrupted", reason, 101);
            Assert.That(_job.Status, Is.EqualTo("interrupted"));
            Assert.That(encoder.Disposals, Is.EqualTo(1));
            Assert.That(_job.Snapshot(101).Value<string>("reason"), Is.EqualTo(reason));
            Assert.That(File.Exists(_job.OutputPath), Is.False);
            Assert.That(Directory.GetFileSystemEntries(Path.GetDirectoryName(_job.OutputPath)), Is.Empty);
        }

        [Test]
        public void EncoderFailureDisposesWithoutClaimingARecordedFrame()
        {
            var encoder = Start();
            encoder.RejectFrame = true;
            Assert.Throws<InvalidOperationException>(() => _job.AddFrame(null, 32, 32, 100));
            _job.Finish("failed", "capture_failed", 100, "rejected");
            Assert.That(_job.Frames, Is.Zero);
            Assert.That(encoder.Disposals, Is.EqualTo(1));
            Assert.That(File.Exists(_job.OutputPath), Is.False);
        }

        [Test]
        public void FailedFinalizationNeverPublishesVideo()
        {
            var encoder = Start();
            _job.AddFrame(null, 32, 32, 100);
            encoder.FailDispose = true;
            _job.Finish("stopped", "stop_requested", 101);
            Assert.That(_job.Status, Is.EqualTo("failed"));
            Assert.That(_job.Snapshot(101).Value<string>("error"), Does.Contain("finalization failed"));
            Assert.That(File.Exists(_job.OutputPath), Is.False);
            Assert.That(Directory.GetFileSystemEntries(Path.GetDirectoryName(_job.OutputPath)), Is.Empty);
        }

        [Test]
        public void FileCreatedDuringRecordingIsNotOverwrittenAtPublication()
        {
            Start();
            _job.AddFrame(null, 32, 32, 100);
            File.WriteAllText(_job.OutputPath, "raced");
            _job.Finish("stopped", "stop_requested", 101);
            Assert.That(_job.Status, Is.EqualTo("failed"));
            Assert.That(File.ReadAllText(_job.OutputPath), Is.EqualTo("raced"));
        }

        [Test]
        public void SourceReadbackIsIncludedInAggregateBudget()
        {
            Start(new RecordingOptions("game_view", 32, 32, 30, 60));
            for (int index = 0; index < 59; index++)
                _job.AddFrame(null, 8192, 4096, 100 + index * 0.04);
            Assert.Throws<InvalidOperationException>(() => _job.ValidateSourceBudget(8192, 4096));
            Assert.Throws<InvalidOperationException>(() => _job.AddFrame(null, 8192, 4096, 103));
            Assert.That(_job.Frames, Is.EqualTo(59));
        }

        [Test]
        public void EncoderCreationFailureDeletesItsStagingFile()
        {
            var options = new RecordingOptions("scene_view", 32, 32, 10, 2);
            string output = RecordingOptions.ResolveOutputPath(_root, null, "fixture.mp4", "fixture");
            Assert.Throws<IOException>(() =>
                new RecordingJob(
                    "fixture",
                    options,
                    _root,
                    output,
                    100,
                    (path, config) =>
                    {
                        File.WriteAllText(path, "partial");
                        throw new IOException("Fixture unavailable codec");
                    }
                )
            );
            Assert.That(Directory.GetFileSystemEntries(Path.GetDirectoryName(output)), Is.Empty);
        }

        [Test]
        public void CleanupFailureStillDisposesEncoderAndDeletesPartialVideo()
        {
            var encoder = Start();
            _job.AddFrame(null, 32, 32, 100);
            _job.SetCleanup(() => throw new InvalidOperationException("Fixture cleanup failure"));
            _job.Finish("stopped", "stop_requested", 101);
            Assert.That(encoder.Disposals, Is.EqualTo(1));
            Assert.That(_job.Status, Is.EqualTo("failed"));
            Assert.That(File.Exists(_job.OutputPath), Is.False);
            Assert.That(Directory.GetFileSystemEntries(Path.GetDirectoryName(_job.OutputPath)), Is.Empty);
        }

        [Test]
        [Category("NativeMediaEncoder")]
        public void NativeEncoderPublishesFinalizedMp4ContainerWithTimestampedFrames()
        {
            if (Application.platform != RuntimePlatform.WindowsEditor && Application.platform != RuntimePlatform.OSXEditor)
                Assert.Ignore("Native MP4 recording is supported only on Windows and macOS Editors.");

            var options = new RecordingOptions("scene_view", 64, 64, 10, 1);
            string output = RecordingOptions.ResolveOutputPath(_root, null, "native-fixture.mp4", "native-fixture");
            var texture = new Texture2D(options.Width, options.Height, TextureFormat.RGBA32, false);
            try
            {
                // Native construction/encoding failures on supported platforms must fail this test.
                // There is deliberately no batch-mode skip or codec-unavailable catch.
                _job = new RecordingJob("native-fixture", options, _root, output, 100, (path, config) => new UnityRecordingEncoder(path, config));
                var pixels = new Color32[options.Width * options.Height];
                for (int frameIndex = 0; frameIndex < 4; frameIndex++)
                {
                    for (int pixelIndex = 0; pixelIndex < pixels.Length; pixelIndex++)
                        pixels[pixelIndex] = new Color32((byte)(frameIndex * 60), (byte)(pixelIndex % options.Width * 4), 128, 255);
                    texture.SetPixels32(pixels);
                    texture.Apply(false, false);
                    _job.AddFrame(texture, options.Width, options.Height, 100 + frameIndex * 0.12);
                }
                Assert.That(_job.Frames, Is.EqualTo(4));
                Assert.That(File.Exists(output), Is.False, "The staging video must not be published before encoder finalization.");
                _job.Tick(101);
                Assert.That(_job.Status, Is.EqualTo("completed"), _job.Snapshot(101).ToString());
                Assert.That(_job.Snapshot(101).Value<string>("output_path"), Is.EqualTo(output.Replace('\\', '/')));
                Assert.That(Directory.GetDirectories(Path.GetDirectoryName(output)), Is.Empty, "Native encoder staging directory must be removed.");
                AssertFinalizedMp4(File.ReadAllBytes(output));
            }
            finally
            {
                UnityEngine.Object.DestroyImmediate(texture);
            }
        }

        internal static void AssertFinalizedMp4(byte[] bytes)
        {
            Assert.That(bytes.Length, Is.GreaterThan(24), "Native encoder must publish a nonempty MP4.");
            Assert.That(Encoding.ASCII.GetString(bytes, 4, 4), Is.EqualTo("ftyp"));
            var boxes = new HashSet<string>();
            int offset = 0;
            while (offset < bytes.Length)
            {
                Assert.That(bytes.Length - offset, Is.GreaterThanOrEqualTo(8), "Truncated MP4 box header.");
                uint size32 = ReadBigEndianUInt32(bytes, offset);
                string type = Encoding.ASCII.GetString(bytes, offset + 4, 4);
                ulong size = size32;
                int headerSize = 8;
                if (size32 == 1)
                {
                    headerSize = 16;
                    Assert.That(bytes.Length - offset, Is.GreaterThanOrEqualTo(headerSize), "Truncated large MP4 box header.");
                    size = ((ulong)ReadBigEndianUInt32(bytes, offset + 8) << 32) | ReadBigEndianUInt32(bytes, offset + 12);
                }
                else if (size32 == 0)
                    size = (ulong)(bytes.Length - offset);
                Assert.That(size, Is.GreaterThanOrEqualTo((ulong)headerSize), "Invalid MP4 box size: " + type);
                Assert.That(size, Is.LessThanOrEqualTo((ulong)(bytes.Length - offset)), "Truncated MP4 box: " + type);
                boxes.Add(type);
                offset += (int)size;
            }
            Assert.That(boxes, Does.Contain("mdat"), "Finalized MP4 must contain encoded media data.");
            Assert.That(boxes, Does.Contain("moov"), "Finalized MP4 must contain its movie metadata/index.");
        }

        private static uint ReadBigEndianUInt32(byte[] bytes, int offset) =>
            ((uint)bytes[offset] << 24) | ((uint)bytes[offset + 1] << 16) | ((uint)bytes[offset + 2] << 8) | bytes[offset + 3];

        [Test]
        public void StatusAndStopRequireExplicitKnownJobIds()
        {
            Assert.That(ManageRecording.HandleCommand(new JObject { ["action"] = "status" }), Is.TypeOf<ErrorResponse>());
            Assert.That(ManageRecording.HandleCommand(new JObject { ["action"] = "stop", ["job_id"] = "unknown" }), Is.TypeOf<ErrorResponse>());
        }

        [Test]
        public void DurationParsingRejectsStringsAndBooleansBeforeEncoderCreation()
        {
            foreach (JToken value in new JToken[] { new JValue("1"), new JValue(true), JValue.CreateNull() })
            {
                var response = ManageRecording.HandleCommand(new JObject { ["action"] = "start", ["duration_seconds"] = value });
                Assert.That(response, Is.TypeOf<ErrorResponse>());
                Assert.That(((ErrorResponse)response).Error, Does.Contain("finite JSON number"));
            }
            Assert.That(new RecordingOptions("game_view", 32, 32, 30, 0.1).MaxFrames, Is.EqualTo(3));
        }
    }
}
