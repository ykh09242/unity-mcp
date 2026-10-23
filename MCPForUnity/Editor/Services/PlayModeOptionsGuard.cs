using System;
using System.IO;
using System.Text;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Runtime.Helpers;
using Newtonsoft.Json;
using UnityEditor;
using UnityEngine;

namespace MCPForUnity.Editor.Services
{
    /// <summary>Durable, conditional restoration of settings temporarily owned by one test run.</summary>
    [InitializeOnLoad]
    internal static class PlayModeOptionsGuard
    {
        private const string KeyPending = "MCPForUnity.PlayModeOptions.PendingRestore";
        private const string KeyEnabled = "MCPForUnity.PlayModeOptions.OriginalEnabled";
        private const string KeyOptions = "MCPForUnity.PlayModeOptions.OriginalOptions";
        private const string KeyRecord = "MCPForUnity.PlayModeOptions.Record.v2";
        private const int MaximumRecordBytes = 4096;
        internal static string MarkerPathForTests;
        internal static Action<string> SettingsBoundaryForTests;
        internal static Action<FileStream> FlushForTests;
        internal static Action<string> DeleteMarkerForTests;
        private static readonly string ProjectRoot = Path.GetFullPath(Path.Combine(Application.dataPath, ".."));
        private static readonly string Owner = AtomicAssetFile.Digest(
            Encoding.UTF8.GetBytes(ProjectRoot + "\n" + Environment.MachineName + "\n" + Environment.UserDomainName + "\n" + Environment.UserName)
        );
        private static string MarkerPath =>
            MarkerPathForTests == null
                ? SafePathUtility.ResolveWithinRoot(ProjectRoot, Path.Combine("Library", "MCPForUnity", "TestSettings", Owner + ".json"))
                : Path.GetFullPath(MarkerPathForTests);

        [Serializable]
        private sealed class Record
        {
            [JsonProperty(Required = Required.Always)]
            public int Version = 2;

            [JsonProperty(Required = Required.Always)]
            public string Owner;

            [JsonProperty(Required = Required.Always)]
            public string Run;

            [JsonProperty(Required = Required.Always)]
            public bool OriginalEnabled;

            [JsonProperty(Required = Required.Always)]
            public int OriginalOptions;

            [JsonProperty(Required = Required.Always)]
            public int AppliedOptions;

            [JsonProperty(Required = Required.Always)]
            public string Status = "pending";

            [JsonProperty(Required = Required.Always)]
            public string Phase = "prepared";
        }

        static PlayModeOptionsGuard() => RestoreIfIdle();

        internal static bool IsPending => TryLoad(out var record) && record.Status == "pending";

        internal static void RestoreIfIdle()
        {
            if (IsPending && !TestRunStatus.IsRunning && !TestJobManager.HasRunningJob)
                Restore();
        }

        internal static void Save(bool originalEnabled, EnterPlayModeOptions originalOptions) => Prepare(originalEnabled, originalOptions);

        private static Record Prepare(bool originalEnabled, EnterPlayModeOptions originalOptions)
        {
            if (IsPending)
                throw new IOException("A prior test run still owns its settings recovery record.");
            var record = new Record
            {
                Owner = Owner,
                Run = Guid.NewGuid().ToString("N"),
                OriginalEnabled = originalEnabled,
                OriginalOptions = (int)originalOptions,
                AppliedOptions = (int)(originalOptions | EnterPlayModeOptions.DisableDomainReload),
            };
            // A failure propagates to admission before EditorSettings is changed.
            Write(record);
            Cache(record);
            return record;
        }

        internal static void Apply(bool originalEnabled, EnterPlayModeOptions originalOptions)
        {
            var record = Prepare(originalEnabled, originalOptions);
            try
            {
                // Set nonzero options first: newer Editors also enable options in this setter.
                // Enabling first can replace None with Unity's default combined options.
                SetPhase(record, "apply-options");
                SettingsBoundaryForTests?.Invoke("before-apply-options");
                EditorSettings.enterPlayModeOptions = (EnterPlayModeOptions)record.AppliedOptions;
                SettingsBoundaryForTests?.Invoke("after-apply-options");
                SetPhase(record, "apply-enabled");
                SettingsBoundaryForTests?.Invoke("before-apply-enabled");
                EditorSettings.enterPlayModeOptionsEnabled = true;
                SettingsBoundaryForTests?.Invoke("after-apply-enabled");
                SetPhase(record, "active");
            }
            catch (Exception ex) when (ex is IOException || ex is UnauthorizedAccessException)
            {
                // A failed transition cannot admit tests; the prior durable phase still owns
                // any completed setter. Retry conditional recovery without hiding admission failure.
                Restore();
                throw;
            }
        }

        private static void SetPhase(Record record, string phase)
        {
            record.Phase = phase;
            Write(record);
            Cache(record);
        }

        private static bool OwnsCurrent(Record record, bool enabled, int options)
        {
            // Older Editors store these fields independently; newer Editors couple nonzero
            // options and enabled. Both exact setter outcomes are allowed only in their phase.
            bool expectedEnabled = enabled == record.OriginalEnabled || enabled;
            switch (record.Phase)
            {
                case "prepared": // Same-process legacy Save callers may have applied both values.
                case "active":
                    return enabled && options == record.AppliedOptions;
                case "apply-options":
                case "apply-enabled":
                    return expectedEnabled && options == record.AppliedOptions;
                case "restore-options":
                    return expectedEnabled && (options == record.OriginalOptions || options == record.AppliedOptions);
                case "restore-enabled":
                    return expectedEnabled && options == record.OriginalOptions;
                default:
                    return false;
            }
        }

        internal static void Restore()
        {
            if (!TryLoad(out var record) || record.Status != "pending")
                return;
            bool enabled = EditorSettings.enterPlayModeOptionsEnabled;
            int options = (int)EditorSettings.enterPlayModeOptions;
            bool original = enabled == record.OriginalEnabled && options == record.OriginalOptions;
            if (!original && !OwnsCurrent(record, enabled, options))
            {
                Retire(record, "conflict");
                McpLog.Warn("[PlayModeOptionsGuard] Settings changed after test admission; current values were preserved.");
                return;
            }
            try
            {
                if (!original)
                {
                    // Persist each setter's intent first. Both its before and after tuple are
                    // recoverable if the process stops between the setter and the next record.
                    SetPhase(record, "restore-options");
                    SettingsBoundaryForTests?.Invoke("before-restore-options");
                    EditorSettings.enterPlayModeOptions = (EnterPlayModeOptions)record.OriginalOptions;
                    SettingsBoundaryForTests?.Invoke("after-restore-options");
                    SetPhase(record, "restore-enabled");
                    SettingsBoundaryForTests?.Invoke("before-restore-enabled");
                    EditorSettings.enterPlayModeOptionsEnabled = record.OriginalEnabled;
                    SettingsBoundaryForTests?.Invoke("after-restore-enabled");
                }
                Retire(record, "complete");
            }
            catch (Exception ex) when (ex is IOException || ex is UnauthorizedAccessException)
            {
                McpLog.Warn("[PlayModeOptionsGuard] Settings recovery remains pending because a transition could not be persisted: " + ex.Message);
            }
        }

        internal static void Clear()
        {
            if (TryLoad(out var record) && record.Status == "pending")
                Retire(record, "complete");
            else
                SessionState.SetBool(KeyPending, false);
        }

        private static void Retire(Record record, string status)
        {
            record.Status = status;
            try
            {
                // Commit inactivity before best-effort deletion, so a leftover marker cannot
                // restore stale settings on the next process start.
                Write(record);
                Cache(record);
                if (status == "complete")
                {
                    try
                    {
                        if (DeleteMarkerForTests == null)
                            File.Delete(MarkerPath);
                        else
                            DeleteMarkerForTests(MarkerPath);
                    }
                    catch (IOException) { }
                    catch (UnauthorizedAccessException) { }
                }
            }
            catch (Exception ex)
            {
                McpLog.Warn("[PlayModeOptionsGuard] Recovery completion could not be persisted; its record was retained: " + ex.Message);
            }
        }

        private static void Cache(Record record)
        {
            SessionState.SetString(KeyRecord, JsonConvert.SerializeObject(record));
            SessionState.SetBool(KeyEnabled, record.OriginalEnabled);
            SessionState.SetInt(KeyOptions, record.OriginalOptions);
            SessionState.SetBool(KeyPending, record.Status == "pending");
        }

        private static Record Parse(string text)
        {
            if (string.IsNullOrEmpty(text) || Encoding.UTF8.GetByteCount(text) > MaximumRecordBytes)
                return null;
            var record = JsonConvert.DeserializeObject<Record>(
                text,
                new JsonSerializerSettings { MaxDepth = 4, MissingMemberHandling = MissingMemberHandling.Error }
            );
            int allowed = (int)(EnterPlayModeOptions.DisableDomainReload | EnterPlayModeOptions.DisableSceneReload);
            return
                record != null
                && record.Version == 2
                && record.Owner == Owner
                && Guid.TryParseExact(record.Run, "N", out _)
                && (record.OriginalOptions & ~allowed) == 0
                && record.AppliedOptions == (record.OriginalOptions | (int)EnterPlayModeOptions.DisableDomainReload)
                && (record.Status == "pending" || record.Status == "complete" || record.Status == "conflict")
                && (
                    record.Phase == "prepared"
                    || record.Phase == "apply-enabled"
                    || record.Phase == "apply-options"
                    || record.Phase == "active"
                    || record.Phase == "restore-options"
                    || record.Phase == "restore-enabled"
                )
                ? record
                : null;
        }

        private static string ReadText(string path)
        {
            using var stream = new FileStream(path, FileMode.Open, FileAccess.Read, FileShare.Read | FileShare.Delete);
            if (stream.Length > MaximumRecordBytes)
                throw new IOException("Settings recovery record is too large.");
            using var reader = new StreamReader(stream, new UTF8Encoding(false, true));
            return reader.ReadToEnd();
        }

        private static bool TryLoad(out Record record)
        {
            record = null;
            try
            {
                // Durable state is authoritative if present; do not fall back over foreign or
                // malformed records. SessionState only supports same-process legacy migration.
                if (File.Exists(MarkerPath))
                {
                    record = Parse(ReadText(MarkerPath));
                    return record != null;
                }
                string cached = SessionState.GetString(KeyRecord, "");
                if (!string.IsNullOrEmpty(cached))
                {
                    record = Parse(cached);
                    return record != null;
                }
                if (SessionState.GetBool(KeyPending, false))
                {
                    int original = SessionState.GetInt(KeyOptions, 0);
                    record = new Record
                    {
                        Owner = Owner,
                        Run = Guid.NewGuid().ToString("N"),
                        OriginalEnabled = SessionState.GetBool(KeyEnabled, false),
                        OriginalOptions = original,
                        AppliedOptions = original | (int)EnterPlayModeOptions.DisableDomainReload,
                    };
                    Cache(record);
                    return true;
                }
            }
            catch (Exception) { /* Unknown or unbound evidence cannot authorize a settings write. */ }
            return false;
        }

        private static void Write(Record record)
        {
            string path = MarkerPath;
            string previous = null;
            if (File.Exists(path))
            {
                previous = ReadText(path);
                Record prior;
                try
                {
                    prior = Parse(previous);
                }
                catch (Exception ex)
                {
                    throw new IOException("Settings recovery record is unverified.", ex);
                }
                if (prior == null || prior.Status == "pending" && prior.Run != record.Run)
                    throw new IOException("Settings recovery is owned by another or unverified run.");
            }
            string text = JsonConvert.SerializeObject(record);
            byte[] bytes = Encoding.UTF8.GetBytes(text);
            if (bytes.Length > MaximumRecordBytes)
                throw new IOException("Settings recovery record is too large.");
            Directory.CreateDirectory(Path.GetDirectoryName(path));
            path = MarkerPath; // Recheck directory links after creating the owned directory.
            string stage = Path.Combine(Path.GetDirectoryName(path), ".settings-" + Guid.NewGuid().ToString("N") + ".tmp");
            string backup = stage + ".bak";
            bool confirmed = false;
            try
            {
                AtomicFilePublication.StageNewWithFlush(stage, stream => stream.Write(bytes, 0, bytes.Length), FlushForTests);
                if (previous == null)
                    AtomicFilePublication.Create(stage, path);
                else
                {
                    AtomicFilePublication.Replace(stage, path, backup);
                    if (ReadText(backup) != previous)
                        throw new IOException("Settings recovery record changed during publication.");
                }
                if (ReadText(path) != text)
                    throw new IOException("Settings recovery publication could not be verified.");
                confirmed = true;
            }
            catch (Exception ex) when (ex is IOException || ex is UnauthorizedAccessException)
            {
                throw new IOException("Settings were not admitted because recovery persistence failed. Inspect retained records before retrying.", ex);
            }
            finally
            {
                if (confirmed)
                {
                    try
                    {
                        File.Delete(stage);
                        File.Delete(backup);
                    }
                    catch (IOException) { }
                    catch (UnauthorizedAccessException) { }
                }
            }
        }
    }
}
