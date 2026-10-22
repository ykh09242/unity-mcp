using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Text;
using System.Text.RegularExpressions;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Editor.Services;
using MCPForUnity.Runtime;
using MCPForUnity.Runtime.Helpers;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEngine;

namespace MCPForUnity.Editor.Tools
{
    /// <summary>Opt-in disk-backed captures. No retained asset objects or whole-session event lists.</summary>
    internal static class ReferenceGraphRuntimeCapture
    {
        private static readonly Dictionary<string, Capture> Active = new Dictionary<string, Capture>();
        private static string runtimeSession = Guid.NewGuid().ToString("N");
        private static bool subscribed;

        private static string Location(string token)
        {
            if (token == null || !Regex.IsMatch(token, "\\A[a-f0-9]{64}\\z"))
                throw new ArgumentException("Invalid capture credential.");
            string user = Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData);
            if (string.IsNullOrEmpty(user) || !Path.IsPathRooted(user))
                throw new InvalidOperationException("A private user data directory is unavailable.");
            return SafePathUtility.ResolveWithinRoot(user, "MCPForUnity/ReferenceRuntime/" + ReferenceGraphJournal.InstanceId + "/" + token);
        }

        private static string SafeFile(string location, string extension) =>
            SafePathUtility.ResolveWithinRoot(Path.GetPathRoot(location), location + extension);

        internal static JObject Handle(JObject request)
        {
            string token = request.Value<string>("token"),
                action = request.Value<string>("action");
            string location = Location(token);
            if (action == "runtime_start")
            {
                if (Active.TryGetValue(token, out Capture existing))
                    return existing.Status();
                if (File.Exists(SafeFile(location, ".json")))
                {
                    JObject previous = ReadMetadata(location);
                    if (previous.Value<string>("state") == "active")
                        previous["state"] = "interrupted";
                    return previous;
                }
                Directory.CreateDirectory(Path.GetDirectoryName(location));
                // No lifecycle observer is retained while idle. A gap starts a
                // fresh context even when Play Mode disables domain reload.
                if (Active.Count == 0)
                    runtimeSession = Guid.NewGuid().ToString("N");
                var capture = new Capture(location, runtimeSession);
                Active.Add(token, capture);
                if (!subscribed)
                {
                    subscribed = true;
                    AssemblyReloadEvents.beforeAssemblyReload += BeforeReload;
                    EditorApplication.quitting += BeforeQuit;
                    EditorApplication.playModeStateChanged += PlayChanged;
                }
                return capture.Status();
            }
            if (action == "runtime_stop" || action == "runtime_release")
            {
                if (Active.TryGetValue(token, out Capture current))
                {
                    current.Close("stopped");
                    Active.Remove(token);
                    UnsubscribeIfIdle();
                }
                if (action == "runtime_release")
                {
                    File.Delete(SafeFile(location, ".events"));
                    File.Delete(SafeFile(location, ".json"));
                    File.Delete(SafeFile(location, ".json.pending"));
                    return new JObject { ["released"] = true };
                }
                return ReadMetadata(location);
            }
            if (action != "runtime_read")
                throw new ArgumentException("Unknown runtime capture action.");
            long offset = request.Value<long?>("offset") ?? 0;
            int limit = request.Value<int?>("page_size") ?? 128;
            if (offset < 0 || limit < 1 || limit > 1000)
                throw new ArgumentException("Runtime capture requires a nonnegative byte cursor and a 1-1000 record page.");
            if (Active.TryGetValue(token, out Capture active))
                return active.Read(offset, limit);
            JObject metadata = ReadMetadata(location);
            if (metadata.Value<string>("state") == "active")
                metadata["state"] = "interrupted";
            return ReadPage(location, metadata, offset, limit);
        }

        private static JObject ReadMetadata(string location) => JObject.Parse(File.ReadAllText(SafeFile(location, ".json"), Encoding.UTF8));

        private static JObject ReadPage(string location, JObject metadata, long offset, int limit)
        {
            var events = new JArray();
            using (var stream = new FileStream(SafeFile(location, ".events"), FileMode.Open, FileAccess.Read, FileShare.ReadWrite))
            using (var reader = new BinaryReader(stream, Encoding.UTF8))
            {
                if (offset > stream.Length)
                    throw new InvalidOperationException("Runtime cursor is past the capture.");
                stream.Position = offset;
                while (events.Count < limit && stream.Position < stream.Length)
                {
                    if (stream.Length - stream.Position < 4)
                        throw new InvalidOperationException("Runtime capture has an incomplete record.");
                    int size = reader.ReadInt32();
                    if (size < 0 || size > stream.Length - stream.Position)
                        throw new InvalidOperationException("Runtime capture record is incomplete or invalid.");
                    events.Add(JObject.Parse(Encoding.UTF8.GetString(reader.ReadBytes(size))));
                }
                metadata["next_offset"] = stream.Position;
                metadata["has_more"] = stream.Position < stream.Length;
            }
            metadata["events"] = events;
            return metadata;
        }

        private static void BeforeReload() => CloseAll("domain_reload");

        private static void BeforeQuit() => CloseAll("editor_quit");

        private static void PlayChanged(PlayModeStateChange state)
        {
            if (state == PlayModeStateChange.ExitingPlayMode || state == PlayModeStateChange.ExitingEditMode)
            {
                CloseAll("play_transition");
                runtimeSession = Guid.NewGuid().ToString("N");
            }
        }

        internal static void CloseAll(string reason)
        {
            foreach (Capture capture in Active.Values.ToArray())
                capture.Close(reason);
            Active.Clear();
            UnsubscribeIfIdle();
        }

        private static void UnsubscribeIfIdle()
        {
            if (Active.Count != 0 || !subscribed)
                return;
            AssemblyReloadEvents.beforeAssemblyReload -= BeforeReload;
            EditorApplication.quitting -= BeforeQuit;
            EditorApplication.playModeStateChanged -= PlayChanged;
            subscribed = false;
        }

        private sealed class Capture
        {
            private readonly object gate = new object();
            private readonly string location;
            private readonly JObject metadata;
            private BinaryWriter writer;
            private IDisposable subscription;
            private long sequence;

            internal Capture(string path, string session)
            {
                location = path;
                metadata = new JObject
                {
                    ["state"] = "active",
                    ["count"] = 0,
                    ["error"] = null,
                    ["runtime_session"] = session,
                    ["playing"] = EditorApplication.isPlaying,
                    ["build"] = ReferenceGraphCompilation.Status(),
                };
                writer = new BinaryWriter(
                    new FileStream(SafeFile(location, ".events"), FileMode.CreateNew, FileAccess.Write, FileShare.Read, 65536),
                    Encoding.UTF8
                );
                try
                {
                    Save();
                    subscription = ReferenceGraphRuntime.Observe(Record);
                }
                catch
                {
                    writer.Dispose();
                    writer = null;
                    File.Delete(SafeFile(location, ".events"));
                    throw;
                }
            }

            private void Record(ReferenceGraphRuntimeEvent observation)
            {
                lock (gate)
                {
                    if (writer == null)
                        return;
                    try
                    {
                        string path = "",
                            guid = "";
                        if (ToolConsentThread.IsMainThread && observation.Asset != null)
                        {
                            path = AssetDatabase.GetAssetPath(observation.Asset);
                            if (ReferenceGraphCollector.Admitted(path))
                                guid = AssetDatabase.AssetPathToGUID(path);
                            else
                                path = "";
                        }
                        var row = new JObject
                        {
                            ["sequence"] = sequence + 1,
                            ["kind"] = observation.Kind,
                            ["scheme"] = observation.Scheme,
                            ["key"] = observation.Key,
                            ["handle"] = observation.Handle,
                            ["owner_path"] = ReferenceGraphCollector.Admitted(observation.OwnerPath) ? observation.OwnerPath : "",
                            ["target_guid"] = guid,
                            ["target_path"] = path,
                            ["utc_ticks"] = DateTime.UtcNow.Ticks,
                        };
                        byte[] bytes = Encoding.UTF8.GetBytes(row.ToString(Formatting.None));
                        writer.Write(bytes.Length);
                        writer.Write(bytes);
                        metadata["count"] = ++sequence;
                    }
                    catch (Exception error)
                    {
                        metadata["error"] = error.GetType().Name;
                        Close("observer_failed");
                    }
                }
            }

            internal JObject Status()
            {
                lock (gate)
                    return (JObject)metadata.DeepClone();
            }

            internal JObject Read(long offset, int limit)
            {
                lock (gate)
                {
                    writer?.Flush();
                    return ReadPage(location, Status(), offset, limit);
                }
            }

            private void Save()
            {
                string path = SafeFile(location, ".json"),
                    temporary = SafeFile(location, ".json.pending");
                File.WriteAllText(temporary, metadata.ToString(Formatting.None), Encoding.UTF8);
                if (File.Exists(path))
                    File.Replace(temporary, path, null);
                else
                    File.Move(temporary, path);
            }

            internal void Close(string reason)
            {
                lock (gate)
                {
                    if (writer == null)
                        return;
                    subscription?.Dispose();
                    subscription = null;
                    try
                    {
                        writer?.Dispose();
                    }
                    catch (Exception error)
                    {
                        metadata["error"] = error.GetType().Name;
                    }
                    writer = null;
                    metadata["state"] = reason;
                    try
                    {
                        Save();
                    }
                    catch (Exception error)
                    {
                        metadata["error"] = error.GetType().Name;
                    }
                }
            }
        }
    }
}
