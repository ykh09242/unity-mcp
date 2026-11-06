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

        internal static JObject Handle(JObject request) => HandleAt(request, Location(request.Value<string>("token")));

        private static JObject HandleAt(JObject request, string location)
        {
            string token = request.Value<string>("token"),
                action = request.Value<string>("action");
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
                string spoolRoot = ReferenceGraphRuntimeSpool.Root(location);
                Directory.CreateDirectory(spoolRoot);
                // No lifecycle observer is retained while idle. A gap starts a
                // fresh context even when Play Mode disables domain reload.
                if (Active.Count == 0)
                    runtimeSession = Guid.NewGuid().ToString("N");
                Capture capture;
                lock (ReferenceGraphRuntimeSpool.Gate)
                    using (ReferenceGraphRuntimeSpool.Lock(Path.Combine(spoolRoot, ".spool.lock")))
                    {
                        string pressure = ReferenceGraphRuntimeSpool.Pressure(location, 0, true);
                        if (pressure != null)
                            return new JObject
                            {
                                ["state"] = "storage_limit",
                                ["stop_reason"] = pressure,
                                ["count"] = 0,
                            };
                        Directory.CreateDirectory(Path.GetDirectoryName(location));
                        capture = new Capture(location, runtimeSession);
                    }
                Active.Add(token, capture);
                if (!subscribed)
                {
                    subscribed = true;
                    AssemblyReloadEvents.beforeAssemblyReload += BeforeReload;
                    EditorApplication.quitting += BeforeQuit;
                    EditorApplication.playModeStateChanged += PlayChanged;
                    EditorApplication.update += DrainCaptures;
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
                    lock (ReferenceGraphRuntimeSpool.Gate)
                    {
                        string root = ReferenceGraphRuntimeSpool.Root(location);
                        if (Directory.Exists(root) && Directory.Exists(Path.GetDirectoryName(location)))
                            using (ReferenceGraphRuntimeSpool.Lock(Path.Combine(root, ".spool.lock")))
                            {
                                // Admission can refuse a fresh Editor before its
                                // directory/capture exists. Release is still a
                                // successful no-op and must not create a lease.
                                bool present =
                                    File.Exists(SafeFile(location, ".events"))
                                    || File.Exists(SafeFile(location, ".json"))
                                    || File.Exists(SafeFile(location, ".json.pending"))
                                    || File.Exists(SafeFile(location, ".lease"));
                                if (!present)
                                    return new JObject { ["released"] = true };
                                using (ReferenceGraphRuntimeSpool.Lock(SafeFile(location, ".lease")))
                                {
                                    File.Delete(SafeFile(location, ".events"));
                                    File.Delete(SafeFile(location, ".json"));
                                    File.Delete(SafeFile(location, ".json.pending"));
                                }
                                File.Delete(SafeFile(location, ".lease"));
                            }
                    }
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

        private static void DrainCaptures()
        {
            foreach (Capture capture in Active.Values.ToArray())
                capture.Drain();
        }

        private static void UnsubscribeIfIdle()
        {
            if (Active.Count != 0 || !subscribed)
                return;
            AssemblyReloadEvents.beforeAssemblyReload -= BeforeReload;
            EditorApplication.quitting -= BeforeQuit;
            EditorApplication.playModeStateChanged -= PlayChanged;
            EditorApplication.update -= DrainCaptures;
            subscribed = false;
        }

        private sealed class Capture
        {
            private readonly object gate = new object();
            private readonly string location;
            private readonly JObject metadata;
            private BinaryWriter writer;
            private IDisposable subscription;
            private FileStream lease;
            private long sequence;
            private readonly Queue<byte[]> pending = new Queue<byte[]>();
            private long pendingBytes;
            private bool waiting;

            internal Capture(string path, string session)
            {
                location = path;
                metadata = new JObject
                {
                    ["format"] = "unity-mcp-native-runtime",
                    ["version"] = 1,
                    ["editor_id"] = Path.GetFileName(Path.GetDirectoryName(path)),
                    ["capture_id"] = Path.GetFileName(path),
                    ["lease_protocol"] = "byte-range-v1",
                    ["state"] = "active",
                    ["stop_reason"] = null,
                    ["max_capture_bytes"] = ReferenceGraphRuntimeSpool.CaptureBytes,
                    ["max_total_bytes"] = ReferenceGraphRuntimeSpool.TotalBytes,
                    ["max_record_bytes"] = ReferenceGraphRuntimeSpool.RecordBytes,
                    ["max_capture_count"] = ReferenceGraphRuntimeSpool.CaptureCount,
                    ["count"] = 0,
                    ["pending_count"] = 0,
                    ["pending_bytes"] = 0,
                    ["storage_waiting"] = false,
                    ["unwritten_count"] = 0,
                    ["error"] = null,
                    ["runtime_session"] = session,
                    ["playing"] = EditorApplication.isPlaying,
                    ["build"] = ReferenceGraphCompilation.Status(),
                };
                lease = ReferenceGraphRuntimeSpool.Lock(SafeFile(location, ".lease"));
                try
                {
                    writer = new BinaryWriter(
                        new FileStream(SafeFile(location, ".events"), FileMode.CreateNew, FileAccess.Write, FileShare.Read, 65536),
                        Encoding.UTF8
                    );
                    Save();
                    subscription = ReferenceGraphRuntime.Observe(Record);
                }
                catch
                {
                    writer?.Dispose();
                    writer = null;
                    // An uncertain creation/save never authorizes deleting an
                    // existing event file. Preserve all evidence for recovery.
                    lease.Dispose();
                    lease = null;
                    throw;
                }
            }

            private void Record(ReferenceGraphRuntimeEvent observation)
            {
                lock (gate)
                {
                    if (writer == null)
                        return;
                    bool accepted = false;
                    try
                    {
                        // Reject oversized scalar input before JSON escaping can
                        // allocate a larger serialized record.
                        long inputCharacters =
                            (long)observation.Scheme.Length + observation.Key.Length + observation.OwnerPath.Length + (observation.Handle?.Length ?? 0);
                        if (inputCharacters > ReferenceGraphRuntimeSpool.RecordBytes)
                        {
                            metadata["stop_reason"] = "max_record_bytes";
                            Finish("storage_limit", 1);
                            return;
                        }
                        bool attemptedDrain = false;
                        if (pending.Count >= ReferenceGraphRuntimeSpool.PendingRecords || pendingBytes >= ReferenceGraphRuntimeSpool.PendingBytes)
                        {
                            DrainPending(1);
                            attemptedDrain = true;
                            if (writer == null)
                                return;
                        }
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
                            ["sequence"] = sequence + pending.Count + 1,
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
                        string pressure = bytes.Length > ReferenceGraphRuntimeSpool.RecordBytes ? "max_record_bytes" : null;
                        if (writer.BaseStream.Position + pendingBytes + 4 + bytes.Length > ReferenceGraphRuntimeSpool.CaptureBytes)
                            pressure = "max_capture_bytes";
                        if (pressure != null)
                        {
                            metadata["stop_reason"] = pressure;
                            Finish("storage_limit", 1);
                            return;
                        }
                        if (
                            pending.Count >= ReferenceGraphRuntimeSpool.PendingRecords
                            || pendingBytes + 4 + bytes.Length > ReferenceGraphRuntimeSpool.PendingBytes
                        )
                        {
                            if (!attemptedDrain)
                            {
                                DrainPending(1);
                                attemptedDrain = true;
                                if (writer == null)
                                    return;
                            }
                        }
                        if (
                            pending.Count >= ReferenceGraphRuntimeSpool.PendingRecords
                            || pendingBytes + 4 + bytes.Length > ReferenceGraphRuntimeSpool.PendingBytes
                        )
                        {
                            metadata["stop_reason"] = "pending_capacity";
                            Finish("storage_backpressure", 1);
                            return;
                        }
                        pending.Enqueue(bytes);
                        accepted = true;
                        pendingBytes += 4 + bytes.Length;
                        if (!attemptedDrain)
                            DrainPending();
                    }
                    catch (Exception error)
                    {
                        metadata["error"] = error.GetType().Name;
                        Finish("observer_failed", accepted ? 0 : 1);
                    }
                }
            }

            internal JObject Status()
            {
                lock (gate)
                {
                    metadata["pending_count"] = pending.Count;
                    metadata["pending_bytes"] = pendingBytes;
                    metadata["storage_waiting"] = waiting;
                    return (JObject)metadata.DeepClone();
                }
            }

            internal void Drain()
            {
                lock (gate)
                {
                    if (writer == null || pending.Count == 0)
                        return;
                    try
                    {
                        DrainPending();
                    }
                    catch (Exception error)
                    {
                        metadata["error"] = error.GetType().Name;
                        Finish("observer_failed");
                    }
                }
            }

            private void DrainPending(long additionalUnwritten = 0)
            {
                if (writer == null || pending.Count == 0)
                    return;
                lock (ReferenceGraphRuntimeSpool.Gate)
                {
                    if (!ReferenceGraphRuntimeSpool.TryLock(Path.Combine(ReferenceGraphRuntimeSpool.Root(location), ".spool.lock"), out FileStream storage))
                    {
                        waiting = true;
                        return;
                    }
                    using (storage)
                    {
                        int count = 0;
                        long bytes = 0;
                        foreach (byte[] record in pending)
                        {
                            if (count >= ReferenceGraphRuntimeSpool.DrainRecords || bytes + 4 + record.Length > ReferenceGraphRuntimeSpool.DrainBytes)
                                break;
                            bytes += 4 + record.Length;
                            count++;
                        }
                        string pressure = ReferenceGraphRuntimeSpool.Pressure(location, bytes, false);
                        if (pressure != null)
                        {
                            metadata["stop_reason"] = pressure;
                            Finish("storage_limit", additionalUnwritten);
                            return;
                        }
                        for (int i = 0; i < count; i++)
                        {
                            byte[] record = pending.Peek();
                            writer.Write(record.Length);
                            writer.Write(record);
                            writer.Flush();
                            pending.Dequeue();
                            pendingBytes -= 4 + record.Length;
                            metadata["count"] = ++sequence;
                        }
                        waiting = pending.Count > 0;
                    }
                }
            }

            internal JObject Read(long offset, int limit)
            {
                lock (gate)
                {
                    Drain();
                    writer?.Flush();
                    return ReadPage(location, Status(), offset, limit);
                }
            }

            private void Save()
            {
                string path = SafeFile(location, ".json"),
                    temporary = SafeFile(location, ".json.pending");
                string text = metadata.ToString(Formatting.None);
                if (Encoding.UTF8.GetByteCount(text) + Encoding.UTF8.GetPreamble().Length > ReferenceGraphRuntimeSpool.MetadataBytes)
                    throw new IOException("Runtime metadata exceeds the storage limit.");
                File.WriteAllText(temporary, text, Encoding.UTF8);
                if (File.Exists(path))
                    File.Replace(temporary, path, null);
                else
                    File.Move(temporary, path);
            }

            internal void Close(string reason)
            {
                lock (gate)
                {
                    Drain();
                    if (writer == null)
                        return;
                    if (pending.Count != 0)
                    {
                        metadata["stop_reason"] = "pending_events_on_" + reason;
                        Finish("storage_backpressure");
                        return;
                    }
                    Finish(reason);
                }
            }

            private void Finish(string reason, long additionalUnwritten = 0)
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
                metadata["unwritten_count"] = pending.Count + additionalUnwritten;
                pending.Clear();
                pendingBytes = 0;
                waiting = false;
                metadata["pending_count"] = 0;
                metadata["pending_bytes"] = 0;
                metadata["storage_waiting"] = false;
                metadata["state"] = reason;
                if (metadata["stop_reason"].Type == JTokenType.Null)
                    metadata["stop_reason"] = reason;
                try
                {
                    Save();
                }
                catch (Exception error)
                {
                    metadata["error"] = error.GetType().Name;
                }
                finally
                {
                    lease?.Dispose();
                    lease = null;
                }
            }
        }
    }
}
