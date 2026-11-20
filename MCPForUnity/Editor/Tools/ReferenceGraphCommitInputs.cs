using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Security.Cryptography;
using System.Text.RegularExpressions;
using System.Threading;
using System.Threading.Tasks;
using MCPForUnity.Editor.Helpers;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEngine;
using UnityEngine.SceneManagement;

namespace MCPForUnity.Editor.Tools
{
    internal static class ReferenceGraphCommitInputs
    {
        private const int MaxFileBytes = 4 * 1024 * 1024;
        private const int MaxPageBytes = 16 * 1024 * 1024;
        private static readonly HashSet<string> Extensions = new HashSet<string>(StringComparer.OrdinalIgnoreCase)
        {
            ".cs",
            ".prefab",
            ".unity",
            ".asset",
            ".asmdef",
            ".asmref",
            ".controller",
            ".mat",
            ".shader",
            ".png",
            ".jpg",
            ".jpeg",
            ".tga",
        };

        internal static bool Eligible(string path) =>
            ReferenceGraphCollector.Admitted(path)
            && path.StartsWith("Assets/", StringComparison.Ordinal)
            && path.IndexOf("/Assets/Resources/GameData/", StringComparison.OrdinalIgnoreCase) < 0
            && !path.StartsWith("Assets/Resources/GameData/", StringComparison.OrdinalIgnoreCase)
            && Extensions.Contains(Path.GetExtension(path))
            && path.Length <= 512;

        internal static string FileStamp(string file)
        {
            var info = new FileInfo(GuardedFileSystem.CheckPath(file));
            return info.Exists ? info.Length + ":" + info.LastWriteTimeUtc.Ticks : null;
        }

        internal static async Task<JObject> Links(JArray paths, bool staged, CancellationToken token)
        {
            if (paths == null || paths.Count < 1 || paths.Count > 32)
                throw new ArgumentException("Invalid commit input count.");
            var selected = new HashSet<string>(paths.Values<string>(), StringComparer.Ordinal);
            if (selected.Count != paths.Count || selected.Any(path => !Eligible(path)))
                throw new ArgumentException("Invalid commit input.");
            var links = new JObject();
            foreach (string path in selected)
            {
                token.ThrowIfCancellationRequested();
                links[path] = new JArray(
                    AssetDatabase
                        .GetDependencies(path, false)
                        .Where(target => target != path && selected.Contains(target))
                        .Distinct(StringComparer.Ordinal)
                        .OrderBy(target => target, StringComparer.Ordinal)
                );
                if (staged)
                    await Task.Yield();
            }
            return new JObject
            {
                ["links"] = links,
                ["epoch"] = ReferenceGraphJournal.Epoch,
                ["revision"] = ReferenceGraphJournal.Revision,
            };
        }

        internal static async Task<JObject> Observe(JArray paths, bool closing, bool staged, CancellationToken token)
        {
            if (paths == null || paths.Count < 1 || paths.Count > 32)
                throw new ArgumentException("Invalid commit input count.");
            var files = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);
            var assets = new JArray();
            foreach (string path in paths.Values<string>())
            {
                token.ThrowIfCancellationRequested();
                if (!Eligible(path))
                    throw new ArgumentException("Invalid commit input.");
                ReferenceGraphImportWitness.Watch(path);
                files.Add(path, AssetPathUtility.GetFullAssetPath(path));
                assets.Add(
                    new JObject
                    {
                        ["path"] = path,
                        ["guid"] = AssetDatabase.AssetPathToGUID(path),
                        ["import_hash"] = AssetDatabase.GetAssetDependencyHash(path).ToString(),
                        ["import_witness"] = ReferenceGraphImportWitness.Read(path),
                    }
                );
            }
            string project = Path.GetDirectoryName(Application.dataPath);
            JObject state = EditorState();
            void Read()
            {
                long remaining = MaxPageBytes;
                if (!closing)
                    state["git_head"] = GitHead(project);
                foreach (JObject asset in assets.Cast<JObject>())
                {
                    token.ThrowIfCancellationRequested();
                    string file = files[asset.Value<string>("path")];
                    asset["stamp"] = FileStamp(file);
                    asset["meta_stamp"] = FileStamp(file + ".meta");
                    asset["asset_sha256"] = Hash(file, ref remaining);
                    asset["meta_sha256"] = Hash(file + ".meta", ref remaining);
                }
                if (closing)
                    state["git_head"] = GitHead(project);
            }
            if (staged)
                await Task.Run(Read, token);
            else
                Read();
            if (closing)
            {
                state.Merge(EditorState());
                foreach (JObject asset in assets.Cast<JObject>())
                {
                    string path = asset.Value<string>("path");
                    asset["guid"] = AssetDatabase.AssetPathToGUID(path);
                    asset["import_hash"] = AssetDatabase.GetAssetDependencyHash(path).ToString();
                    asset["import_witness"] = ReferenceGraphImportWitness.Read(path);
                }
            }
            state["assets"] = assets;
            return state;
        }

        private static JObject EditorState()
        {
            int dirty = 0;
            for (int index = 0; index < SceneManager.sceneCount; index++)
                if (SceneManager.GetSceneAt(index).isDirty)
                    dirty++;
            return new JObject
            {
                ["epoch"] = ReferenceGraphJournal.Epoch,
                ["revision"] = ReferenceGraphJournal.Revision,
                ["compiling"] = EditorApplication.isCompiling,
                ["compilation_failed"] = EditorUtility.scriptCompilationFailed,
                ["importing"] = EditorApplication.isUpdating,
                ["dirty_scenes"] = dirty,
                ["build_target"] = EditorUserBuildSettings.activeBuildTarget.ToString(),
                ["unity_version"] = Application.unityVersion,
            };
        }

        internal static string Hash(string file, ref long remaining)
        {
            using var sha = SHA256.Create();
            return BitConverter.ToString(sha.ComputeHash(ReadBytes(file, ref remaining))).Replace("-", "").ToLowerInvariant();
        }

        internal static byte[] ReadBytes(string file, ref long remaining)
        {
            using var stream = new FileStream(GuardedFileSystem.CheckPath(file), FileMode.Open, FileAccess.Read, FileShare.Read);
            long length = stream.Length;
            if (length > Math.Min(remaining, MaxFileBytes))
                throw new InvalidDataException("Commit input exceeds capture limits.");
            // Limit the allocation and bytes consumed even if an external writer grows the file.
            using var reader = new BinaryReader(stream);
            byte[] bytes = reader.ReadBytes((int)length);
            if (bytes.Length != stream.Length || stream.ReadByte() != -1)
                throw new InvalidDataException("Commit input changed while reading.");
            remaining -= bytes.Length;
            return bytes;
        }

        internal static string GitHead(string project)
        {
            string name = Path.DirectorySeparatorChar == '\\' ? "git.exe" : "git";
            string executable = (Environment.GetEnvironmentVariable("PATH") ?? "")
                .Split(Path.PathSeparator)
                .Select(directory => directory.Trim('"'))
                .Where(directory => directory.Length > 0 && Path.IsPathRooted(directory))
                .Select(directory => Path.Combine(directory, name))
                .FirstOrDefault(File.Exists);
            if (executable == null)
                return null;
            var start = new ProcessStartInfo(executable, "--no-pager rev-parse --verify HEAD^{commit}")
            {
                WorkingDirectory = project,
                UseShellExecute = false,
                CreateNoWindow = true,
                RedirectStandardOutput = true,
                RedirectStandardError = true,
            };
            foreach (
                string key in start.EnvironmentVariables.Keys.Cast<string>().Where(key => key.StartsWith("GIT_", StringComparison.OrdinalIgnoreCase)).ToArray()
            )
                start.EnvironmentVariables.Remove(key);
            start.EnvironmentVariables["GIT_OPTIONAL_LOCKS"] = "0";
            start.EnvironmentVariables["GIT_NO_REPLACE_OBJECTS"] = "1";
            start.EnvironmentVariables["GIT_NO_LAZY_FETCH"] = "1";
            using var process = Process.Start(start);
            if (!process.WaitForExit(3000))
            {
                process.Kill();
                return null;
            }
            string output = process.StandardOutput.ReadLine();
            return process.ExitCode == 0 && output != null && Regex.IsMatch(output, "^(?:[0-9a-f]{40}|[0-9a-f]{64})$") ? output : null;
        }
    }
}
