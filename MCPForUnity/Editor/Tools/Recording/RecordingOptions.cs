using System;
using System.IO;
using System.Linq;
using MCPForUnity.Runtime.Helpers;

namespace MCPForUnity.Editor.Tools.Recording
{
    internal sealed class RecordingOptions
    {
        internal const int MaxDimension = 1920;
        internal const int MaxFps = 30;
        internal const double MaxDurationSeconds = 60;
        internal const long MaxPixelFrames = 2000000000L;
        internal const string DefaultFolder = "Captures/Recordings";

        internal readonly string Source;
        internal readonly int Width;
        internal readonly int Height;
        internal readonly int Fps;
        internal readonly double Duration;
        internal readonly int MaxFrames;

        internal RecordingOptions(string source, int width, int height, int fps, double duration)
        {
            if (source != "game_view" && source != "scene_view")
                throw new ArgumentException("capture_source must be 'game_view' or 'scene_view'.");
            if (width < 16 || height < 16 || width > MaxDimension || height > MaxDimension || width % 2 != 0 || height % 2 != 0)
                throw new ArgumentException($"Recording width/height must be even integers from 16 to {MaxDimension}.");
            if (fps < 1 || fps > MaxFps)
                throw new ArgumentException($"fps must be between 1 and {MaxFps}.");
            if (double.IsNaN(duration) || double.IsInfinity(duration) || duration < 0.1 || duration > MaxDurationSeconds)
                throw new ArgumentException($"duration_seconds must be between 0.1 and {MaxDurationSeconds}.");
            int frames = (int)Math.Ceiling(duration * fps);
            if ((long)width * height * frames > MaxPixelFrames)
                throw new ArgumentException("Recording exceeds the total pixel-frame budget; reduce resolution, fps or duration.");
            Source = source;
            Width = width;
            Height = height;
            Fps = fps;
            Duration = duration;
            MaxFrames = frames;
        }

        internal static string ResolveOutputPath(string projectRoot, string folder, string fileName, string jobId)
        {
            string root = SafePathUtility.ResolveWithinRoot(projectRoot, DefaultFolder);
            string requested = string.IsNullOrWhiteSpace(folder) ? root : folder;
            if (requested.Replace('\\', '/').Split('/').Any(part => part == ".."))
                throw new ArgumentException("Recording output paths must not contain traversal segments.");
            string outputFolder = SafePathUtility.ResolveWithinRoot(root, Path.IsPathRooted(requested) ? requested : Path.Combine(projectRoot, requested));
            string name = fileName == null ? $"recording-{jobId}.mp4" : fileName;
            if (
                string.IsNullOrWhiteSpace(name)
                || name.Length > 128
                || name != name.Trim()
                || name.EndsWith(".", StringComparison.Ordinal)
                || name.Any(c => c < 32 || "<>:\"/\\|?*".IndexOf(c) >= 0)
                || name.IndexOfAny(Path.GetInvalidFileNameChars()) >= 0
            )
                throw new ArgumentException("file_name must be a plain filename without paths or reserved characters (up to 128 characters).");
            if (!name.EndsWith(".mp4", StringComparison.OrdinalIgnoreCase))
            {
                if (Path.HasExtension(name))
                    throw new ArgumentException("Only .mp4 recording output is supported.");
                name += ".mp4";
            }
            string stem = name.Split('.')[0].TrimEnd(' ', '.').ToUpperInvariant();
            if (
                new[]
                {
                    "CON",
                    "PRN",
                    "AUX",
                    "NUL",
                    "COM1",
                    "COM2",
                    "COM3",
                    "COM4",
                    "COM5",
                    "COM6",
                    "COM7",
                    "COM8",
                    "COM9",
                    "LPT1",
                    "LPT2",
                    "LPT3",
                    "LPT4",
                    "LPT5",
                    "LPT6",
                    "LPT7",
                    "LPT8",
                    "LPT9",
                }.Contains(stem)
            )
                throw new ArgumentException("file_name is a reserved device name.");
            string fullPath = SafePathUtility.ResolveWithinRoot(root, Path.Combine(outputFolder, name));
            if (File.Exists(fullPath) || Directory.Exists(fullPath))
                throw new IOException("Recording output already exists; choose a new filename. Existing files are never overwritten.");
            return fullPath;
        }
    }
}
