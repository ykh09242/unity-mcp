using System;
using System.Collections.Generic;
using System.IO;
using System.Runtime.InteropServices;
using System.Text;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Runtime.Helpers;
using Newtonsoft.Json;

namespace MCPForUnity.Editor.Services
{
    /// <summary>Coordinates temporary global preferences across Editors belonging to one OS user.</summary>
    internal sealed class EditorPreferenceLease
    {
        private const string IdleKey = "ApplicationIdleTime";
        private const string ModeKey = "InteractionMode";
        private readonly string root;
        private readonly Owner owner;
        private readonly Func<int, long, bool> isAlive;
        private readonly Func<string, int, int> read;
        private readonly Action<string, int> write;
        private readonly Action refresh;
        private bool refreshed;
        internal bool? LastAcquireSuppressed { get; private set; }

        internal EditorPreferenceLease(
            string root,
            int pid,
            long started,
            Func<int, long, bool> isAlive,
            Func<string, int, int> read,
            Action<string, int> write,
            Action refresh
        )
        {
            if (!Path.IsPathRooted(root) || pid <= 0 || started <= 0)
                throw new ArgumentException("Preference ownership requires an absolute root and process identity.");
            this.root = Path.GetFullPath(root);
            owner = new Owner { Pid = pid, Started = started };
            this.isAlive = isAlive;
            this.read = read;
            this.write = write;
            this.refresh = refresh;
        }

        internal bool Exists => File.Exists(Checked("state.json"));

        [Serializable]
        private sealed class Owner
        {
            public int Pid;
            public long Started;
        }

        [Serializable]
        private sealed class Record
        {
            [JsonProperty(Required = Required.Always)]
            public int Version = 1;

            [JsonProperty(Required = Required.Always)]
            public int Idle;

            [JsonProperty(Required = Required.Always)]
            public int Mode;

            [JsonProperty(Required = Required.Always)]
            public string Phase = "apply-idle";

            [JsonProperty(Required = Required.Always)]
            public bool Suppressed;

            [JsonProperty(Required = Required.Always)]
            public List<Owner> Owners = new List<Owner>();
        }

        // Callers retry lock contention without blocking the Editor or refusing native work.
        // Every preference write follows a durable intention; a stopped process cannot strand an unowned override.
        internal void Acquire(int? legacyIdle = null, int? legacyMode = null) => Update(true, false, legacyIdle, legacyMode);

        internal void Release() => Update(false, false, null, null);

        internal void Recover() => Update(false, true, null, null);

        private void Update(bool acquire, bool recoverOnly, int? legacyIdle, int? legacyMode)
        {
            if (!acquire && !Exists)
            {
                // Another Editor may have restored the shared preferences after we released.
                if (refreshed)
                {
                    refresh();
                    refreshed = false;
                }
                return;
            }
            SafePathUtility.ResolveWithinRoot(Path.GetPathRoot(root), root);
            Directory.CreateDirectory(root);
            using var locked = ExclusiveFileLease.Acquire(Checked("state.lock"));
            Record record = Read();
            bool changed = false;
            if (record != null)
            {
                changed = record.Owners.RemoveAll(item => !isAlive(item.Pid, item.Started)) != 0;
                if (!acquire && !recoverOnly)
                    changed |= record.Owners.RemoveAll(IsOwner) != 0;
                if (!MatchesApplied(record))
                {
                    changed |= !record.Suppressed;
                    record.Suppressed = true;
                }
                if (record.Owners.Count == 0)
                {
                    Restore(record);
                    record = null;
                }
            }
            if (record == null && acquire)
            {
                refreshed = false;
                record = new Record { Idle = legacyIdle ?? read(IdleKey, 4), Mode = legacyMode ?? read(ModeKey, 0) };
                record.Owners.Add(owner);
                if (legacyIdle.HasValue && legacyMode.HasValue)
                {
                    record.Phase = "active";
                    record.Suppressed = read(IdleKey, 4) != 0 || read(ModeKey, 0) != 1;
                }
                Write(record);
                if (record.Phase != "active")
                    FinishApply(record);
                else if (!record.Suppressed)
                    RefreshOnce();
                LastAcquireSuppressed = record.Suppressed;
                return;
            }
            if (record == null)
                return;
            if (acquire && !record.Owners.Exists(IsOwner))
            {
                record.Owners.Add(owner);
                changed = true;
            }
            if (changed)
                Write(record);
            if (acquire && !record.Suppressed)
            {
                if (record.Phase == "apply-idle" || record.Phase == "apply-mode")
                    FinishApply(record);
                else
                    RefreshOnce();
            }
            if (acquire)
                LastAcquireSuppressed = record.Suppressed;
        }

        private void FinishApply(Record record)
        {
            if (!MatchesApplied(record))
            {
                record.Suppressed = true;
                Write(record);
                return;
            }
            if (record.Phase == "apply-idle")
            {
                write(IdleKey, 0);
                record.Phase = "apply-mode";
                Write(record);
            }
            if (!MatchesApplied(record))
            {
                record.Suppressed = true;
                Write(record);
                return;
            }
            write(ModeKey, 1);
            record.Phase = "active";
            Write(record);
            RefreshOnce();
        }

        private void RefreshOnce()
        {
            if (refreshed)
                return;
            refresh();
            refreshed = true;
        }

        private bool IsOwner(Owner item) => item.Pid == owner.Pid && item.Started == owner.Started;

        private bool MatchesApplied(Record record)
        {
            int idle = read(IdleKey, 4);
            int mode = read(ModeKey, 0);
            switch (record.Phase)
            {
                case "apply-idle":
                    return (idle == record.Idle || idle == 0) && mode == record.Mode;
                case "apply-mode":
                    return idle == 0 && (mode == record.Mode || mode == 1);
                case "active":
                    return idle == 0 && mode == 1;
                case "restore-idle":
                    return (idle == record.Idle || idle == 0) && (mode == record.Mode || mode == 1);
                case "restore-mode":
                    return idle == record.Idle && (mode == record.Mode || mode == 1);
                default:
                    throw new IOException("Unknown preference recovery phase.");
            }
        }

        private void Restore(Record record)
        {
            if (!record.Suppressed)
            {
                // Restore each value only if it is still ours. Persist before each setter so
                // a crash halfway through restoration leaves a recoverable comparison.
                record.Phase = "restore-idle";
                Write(record);
                if (read(IdleKey, 4) == 0)
                    write(IdleKey, record.Idle);
                record.Phase = "restore-mode";
                Write(record);
                if (read(ModeKey, 0) == 1)
                    write(ModeKey, record.Mode);
            }
            // A manual change in another Editor still needs applying to this Editor's
            // interaction loop, even though suppressed restoration must not write values.
            refresh();
            File.Delete(Checked("state.json"));
            refreshed = false;
        }

        private Record Read()
        {
            string path = Checked("state.json");
            if (!File.Exists(path))
                return null;
            using var input = new FileStream(path, FileMode.Open, FileAccess.Read, FileShare.Read);
            // Metadata only: this accommodates thousands of concurrent Editors, independent of scene/test sizes.
            if (input.Length > 1024 * 1024)
                throw new IOException("Preference recovery metadata is oversized.");
            using var text = new StreamReader(input, Encoding.UTF8);
            using var json = new JsonTextReader(text) { MaxDepth = 16 };
            var record = new JsonSerializer().Deserialize<Record>(json);
            if (json.Read())
                throw new IOException("Unexpected trailing preference metadata.");
            if (
                record == null
                || record.Version != 1
                || record.Owners == null
                || record.Owners.Exists(item => item == null || item.Pid <= 0 || item.Started <= 0)
            )
                throw new IOException("Invalid preference ownership metadata.");
            return record;
        }

        private void Write(Record record)
        {
            string stage = Checked("state.stage");
            string path = Checked("state.json");
            // The cross-process lock makes a previous uncommitted stage safe to discard.
            File.Delete(stage);
            byte[] bytes = Encoding.UTF8.GetBytes(JsonConvert.SerializeObject(record));
            AtomicFilePublication.StageNew(stage, output => output.Write(bytes, 0, bytes.Length));
            if (!File.Exists(path))
                AtomicFilePublication.Create(stage, path);
            else if (RuntimeInformation.IsOSPlatform(OSPlatform.Windows))
                File.Replace(stage, path, null);
            else
            {
                AtomicFilePublication.Exchange(stage, path);
                File.Delete(stage);
            }
        }

        private string Checked(string name) => SafePathUtility.ResolveWithinRoot(root, name);
    }
}
