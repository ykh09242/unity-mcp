using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Security.Cryptography;
using System.Text;
using System.Threading;
using MCPForUnity.Editor.Constants;
using MCPForUnity.Editor.Helpers;
using UnityEditor;

namespace MCPForUnity.Editor.Services
{
    public enum ToolConsentScope
    {
        None,
        EditorSession,
        Project,
    }

    public readonly struct ToolConsentStatus
    {
        public ToolConsentScope Scope { get; }
        public bool HasGrant { get; }
        public bool Enabled { get; }
        public bool RequiresExplicitGrant { get; }
        public bool LegacyGrantIgnored { get; }
        public string ErrorCode { get; }

        internal ToolConsentStatus(ToolConsentScope scope, bool enabled, bool explicitGrant, bool legacy, string error)
        {
            Scope = scope;
            HasGrant = scope != ToolConsentScope.None;
            Enabled = HasGrant && enabled;
            RequiresExplicitGrant = explicitGrant;
            LegacyGrantIgnored = legacy;
            ErrorCode = error;
        }
    }

    public readonly struct ToolConsentChangeResult
    {
        public bool Succeeded { get; }
        public bool DeniedInCurrentDomain { get; }
        public bool ReloadDenialConfirmed { get; }
        public bool RestartDenialConfirmed { get; }
        public string ErrorCode { get; }

        internal ToolConsentChangeResult(bool succeeded, bool denied, bool reload, bool restart, string error)
        {
            Succeeded = succeeded;
            DeniedInCurrentDomain = denied;
            ReloadDenialConfirmed = reload;
            RestartDenialConfirmed = restart;
            ErrorCode = error;
        }
    }

    public interface IToolConsentService
    {
        ToolConsentStatus GetStatus(string canonicalToolName);
        ToolConsentChangeResult Grant(string canonicalToolName, ToolConsentScope scope);
        ToolConsentChangeResult Revoke(string canonicalToolName);
        ToolConsentChangeResult SetEnabled(string canonicalToolName, bool enabled);
    }

    internal enum ToolConsentStorageScope
    {
        Project,
        Session,
    }

    // Managed-only first-touch guard: querying consent on a worker cannot trigger
    // ProjectIdentityUtility's Unity initialization or native identity fallback.
    internal static class ToolConsentThread
    {
        private static int _editorThread;
        internal static bool IsMainThread => _editorThread != 0 && Thread.CurrentThread.ManagedThreadId == _editorThread;

        internal static void CaptureEditorMainThread() => Interlocked.CompareExchange(ref _editorThread, Thread.CurrentThread.ManagedThreadId, 0);
    }

    internal interface IToolConsentStorage
    {
        bool GetBool(ToolConsentStorageScope scope, string key);
        void SetBool(ToolConsentStorageScope scope, string key, bool value);
        void DeleteBool(ToolConsentStorageScope scope, string key);
        string GetString(ToolConsentStorageScope scope, string key);
        void SetString(ToolConsentStorageScope scope, string key, string value);
    }

    internal sealed class NativeToolConsentStorage : IToolConsentStorage
    {
        private static void RequireMainThread()
        {
            if (!ToolConsentThread.IsMainThread)
                throw new InvalidOperationException("main_thread_required");
        }

        public bool GetBool(ToolConsentStorageScope scope, string key)
        {
            RequireMainThread();
            return scope == ToolConsentStorageScope.Project ? EditorPrefs.GetBool(key, false) : SessionState.GetBool(key, false);
        }

        public void SetBool(ToolConsentStorageScope scope, string key, bool value)
        {
            RequireMainThread();
            if (scope == ToolConsentStorageScope.Project)
                EditorPrefs.SetBool(key, value);
            else
                SessionState.SetBool(key, value);
        }

        public void DeleteBool(ToolConsentStorageScope scope, string key)
        {
            RequireMainThread();
            if (scope == ToolConsentStorageScope.Project)
                EditorPrefs.DeleteKey(key);
            else
                SessionState.EraseBool(key);
        }

        public string GetString(ToolConsentStorageScope scope, string key)
        {
            RequireMainThread();
            return scope == ToolConsentStorageScope.Project ? EditorPrefs.GetString(key, "") : SessionState.GetString(key, "");
        }

        public void SetString(ToolConsentStorageScope scope, string key, string value)
        {
            RequireMainThread();
            if (scope == ToolConsentStorageScope.Project)
                EditorPrefs.SetString(key, value);
            else
                SessionState.SetString(key, value);
        }
    }

    // Independent of service registration/Reset. A managed-domain reload is a
    // different lifetime: only verified native storage can deny across that boundary.
    internal sealed class ToolConsentProcessState
    {
        internal static readonly ToolConsentProcessState Shared = new ToolConsentProcessState();

        private sealed class Entry
        {
            internal long Revision,
                Owner;
            internal bool Denied;
            internal string Error;
        }

        private readonly Dictionary<string, Entry> _entries = new Dictionary<string, Entry>(StringComparer.Ordinal);
        private readonly object _gate = new object();
        private readonly int _capacity;
        private bool _denyAll;

        internal ToolConsentProcessState(int capacity = 1024)
        {
            _capacity = capacity;
        }

        internal long Revision(string key)
        {
            lock (_gate)
                return _entries.TryGetValue(key, out var e) ? e.Revision : 0;
        }

        internal bool Denied(string key, out string error)
        {
            lock (_gate)
            {
                error =
                    _denyAll ? "consent_capacity"
                    : _entries.TryGetValue(key, out var e) && e.Denied ? e.Error
                    : null;
                return error != null;
            }
        }

        internal bool Begin(string key, bool explicitGrant, out long owner, out string error)
        {
            lock (_gate)
            {
                owner = 0;
                if (_denyAll)
                {
                    error = "consent_capacity";
                    return false;
                }
                if (!_entries.TryGetValue(key, out var e))
                {
                    if (_entries.Count >= _capacity)
                    {
                        _denyAll = true;
                        error = "consent_capacity";
                        return false;
                    }
                    e = new Entry();
                    _entries.Add(key, e);
                }
                if (e.Owner != 0)
                {
                    // Native setters can invoke Editor callbacks after committing.
                    // Reject the nested mutation AND invalidate the outer publication.
                    e.Revision++;
                    e.Denied = true;
                    e.Error = "reentrant_mutation";
                    error = e.Error;
                    return false;
                }
                if (e.Denied && !explicitGrant)
                {
                    error = "explicit_grant_required";
                    return false;
                }
                e.Revision++;
                e.Owner = e.Revision;
                e.Denied = true;
                e.Error = "mutation_pending";
                owner = e.Owner;
                error = null;
                return true;
            }
        }

        internal bool Current(string key, long owner)
        {
            lock (_gate)
                return !_denyAll && _entries.TryGetValue(key, out var e) && e.Owner == owner && e.Revision == owner;
        }

        internal bool Publish(string key, long owner)
        {
            lock (_gate)
            {
                if (_denyAll || !_entries.TryGetValue(key, out var e) || e.Owner != owner || e.Revision != owner)
                    return false;
                e.Denied = false;
                e.Error = null;
                e.Owner = 0;
                return true;
            }
        }

        internal void Fail(string key, long owner, string error)
        {
            lock (_gate)
            {
                if (!_entries.TryGetValue(key, out var e))
                {
                    _denyAll = true;
                    return;
                }
                e.Denied = true;
                e.Error = error;
                if (e.Owner == owner)
                    e.Owner = 0;
            }
        }
    }

    internal readonly struct ToolConsentKeys
    {
        internal string ProjectId { get; }
        internal string ProjectGrant => EditorPrefKeys.ToolConsentProjectPrefix + ProjectId + "." + _name;
        internal string Enabled => EditorPrefKeys.ToolEnabledProjectPrefix + ProjectId + "." + _name;
        internal string Denied => EditorPrefKeys.ToolConsentDeniedPrefix + ProjectId + "." + _name;
        internal string SessionId => EditorPrefKeys.ToolConsentSessionPrefix + ProjectId + ".Id";
        internal string ProcessKey => ProjectId + "." + _name;
        private readonly string _name;

        internal ToolConsentKeys(string projectId, string name)
        {
            ProjectId = projectId;
            _name = name;
        }

        internal string SessionGrant(string nonce) => EditorPrefKeys.ToolConsentSessionPrefix + ProjectId + "." + nonce + "." + _name;
    }

    public sealed class ToolConsentService : IToolConsentService
    {
        private readonly IToolConsentStorage _storage;
        private readonly Func<bool> _isMainThread;
        private readonly Func<string> _projectRoot;
        private readonly Func<string> _createSessionId;
        private readonly ToolConsentProcessState _process;

        public ToolConsentService()
            : this(
                new NativeToolConsentStorage(),
                () => ToolConsentThread.IsMainThread,
                () => ProjectIdentityUtility.TryGetConsentProjectRoot(out var root) ? root : null,
                ToolConsentProcessState.Shared
            ) { }

        internal ToolConsentService(
            IToolConsentStorage storage,
            Func<bool> isMainThread,
            Func<string> projectRoot,
            ToolConsentProcessState processState,
            Func<string> createSessionId = null
        )
        {
            _storage = storage;
            _isMainThread = isMainThread;
            _projectRoot = projectRoot;
            _process = processState;
            _createSessionId = createSessionId ?? (() => Guid.NewGuid().ToString("N"));
        }

        internal static string NormalizeProjectRoot(string root)
        {
            if (string.IsNullOrEmpty(root) || root.Length > 4096)
                return null;
            try
            {
                string volume = Path.GetPathRoot(root);
                if (!Path.IsPathRooted(root) || string.IsNullOrEmpty(volume) || Path.DirectorySeparatorChar == '\\' && volume.Length < 3)
                    return null;
                root = Path.GetFullPath(root);
                // Reject drive-relative/root-relative Windows paths before
                // GetFullPath can use cwd, and preserve an actual volume root.
                if (root.Length > Path.GetPathRoot(root).Length)
                    root = root.TrimEnd(Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar);
                if (Path.DirectorySeparatorChar == '\\')
                    root = root.Replace('\\', '/').ToLowerInvariant();
                return string.IsNullOrEmpty(root) ? null : root;
            }
            catch (Exception)
            {
                return null;
            }
        }

        internal static ToolConsentKeys KeysFor(string normalizedRoot, string name)
        {
            using var sha = SHA256.Create();
            string id = BitConverter.ToString(sha.ComputeHash(Encoding.UTF8.GetBytes("consent-v2\n" + normalizedRoot))).Replace("-", "").ToLowerInvariant();
            return new ToolConsentKeys(id, name);
        }

        private static bool ValidName(string name) =>
            !string.IsNullOrEmpty(name) && name.Length <= 128 && name.All(c => char.IsLetterOrDigit(c) || c == '_' || c == '.' || c == '-');

        private static bool ValidNonce(string nonce) => nonce != null && nonce.Length == 32 && nonce.All(c => c >= '0' && c <= '9' || c >= 'a' && c <= 'f');

        private bool Capture(string name, out string root, out ToolConsentKeys keys, out string error)
        {
            root = null;
            keys = default;
            error = "main_thread_required";
            // This predicate is managed-only; all native identity/storage is later.
            if (!_isMainThread())
                return false;
            error = "invalid_tool_name";
            if (!ValidName(name))
                return false;
            error = "project_identity_unavailable";
            try
            {
                root = NormalizeProjectRoot(_projectRoot());
            }
            catch (Exception)
            {
                return false;
            }
            if (root == null)
                return false;
            keys = KeysFor(root, name);
            error = null;
            return true;
        }

        private void Fence(string root, ToolConsentKeys keys, long owner)
        {
            if (
                !_isMainThread()
                || !string.Equals(NormalizeProjectRoot(_projectRoot()), root, StringComparison.Ordinal)
                || !_process.Current(keys.ProcessKey, owner)
            )
                throw new InvalidOperationException("consent_state_changed");
        }

        private static ToolConsentStatus DeniedStatus(string error, bool explicitGrant = true) =>
            new ToolConsentStatus(ToolConsentScope.None, false, explicitGrant, false, error);

        private static ToolConsentChangeResult NotApplied(string error, bool denied = false) => new ToolConsentChangeResult(false, denied, false, false, error);

        public ToolConsentStatus GetStatus(string name)
        {
            if (!Capture(name, out var root, out var keys, out var error))
                return DeniedStatus(error);
            if (_process.Denied(keys.ProcessKey, out error))
                return DeniedStatus(error);
            long revision = _process.Revision(keys.ProcessKey);
            try
            {
                if (_storage.GetBool(ToolConsentStorageScope.Session, keys.Denied))
                    return DeniedStatus("explicit_grant_required");
                string nonce = _storage.GetString(ToolConsentStorageScope.Session, keys.SessionId);
                bool project = _storage.GetBool(ToolConsentStorageScope.Project, keys.ProjectGrant);
                bool session = ValidNonce(nonce) && _storage.GetBool(ToolConsentStorageScope.Session, keys.SessionGrant(nonce));
                bool enabled = _storage.GetBool(ToolConsentStorageScope.Project, keys.Enabled);
                bool legacy = _storage.GetBool(ToolConsentStorageScope.Project, EditorPrefKeys.ToolEnabledPrefix + "ExplicitConsent." + name);
                if (
                    !string.Equals(NormalizeProjectRoot(_projectRoot()), root, StringComparison.Ordinal)
                    || _process.Revision(keys.ProcessKey) != revision
                    || _process.Denied(keys.ProcessKey, out _)
                )
                    return DeniedStatus("consent_state_changed");
                if (project && session)
                    return DeniedStatus("scope_conflict");
                return new ToolConsentStatus(
                    project ? ToolConsentScope.Project
                        : session ? ToolConsentScope.EditorSession
                        : ToolConsentScope.None,
                    enabled,
                    false,
                    legacy,
                    null
                );
            }
            catch (Exception)
            {
                return DeniedStatus("storage_unconfirmed");
            }
        }

        public ToolConsentChangeResult SetEnabled(string name, bool enabled)
        {
            if (!Capture(name, out _, out var keys, out var error))
                return NotApplied(error);
            if (!enabled)
                return Revoke(name);
            var status = GetStatus(name);
            if (status.RequiresExplicitGrant)
                return NotApplied(status.ErrorCode ?? "explicit_grant_required", _process.Denied(keys.ProcessKey, out _));
            if (status.Enabled)
                return new ToolConsentChangeResult(true, false, false, false, null);
            return ChangeGrant(name, status.HasGrant ? status.Scope : ToolConsentScope.EditorSession, false);
        }

        public ToolConsentChangeResult Grant(string name, ToolConsentScope scope) => ChangeGrant(name, scope, true);

        private ToolConsentChangeResult ChangeGrant(string name, ToolConsentScope scope, bool explicitGrant)
        {
            if (!Capture(name, out var root, out var keys, out var error))
                return NotApplied(error);
            if (scope != ToolConsentScope.EditorSession && scope != ToolConsentScope.Project)
                return NotApplied("invalid_scope");
            if (!_process.Begin(keys.ProcessKey, explicitGrant, out long owner, out error))
                return NotApplied(error, true);
            string nonce = null;
            try
            {
                SetVerified(ToolConsentStorageScope.Session, keys.Denied, true);
                Fence(root, keys, owner);
                nonce = _storage.GetString(ToolConsentStorageScope.Session, keys.SessionId);
                Fence(root, keys, owner);
                if (!ValidNonce(nonce) && scope == ToolConsentScope.EditorSession)
                {
                    nonce = _createSessionId();
                    if (!ValidNonce(nonce))
                        throw new InvalidOperationException("invalid_session_identity");
                    _storage.SetString(ToolConsentStorageScope.Session, keys.SessionId, nonce);
                    if (!string.Equals(_storage.GetString(ToolConsentStorageScope.Session, keys.SessionId), nonce, StringComparison.Ordinal))
                        throw new InvalidOperationException("storage_unconfirmed");
                    Fence(root, keys, owner);
                }
                SetVerified(ToolConsentStorageScope.Project, keys.Enabled, false);
                Fence(root, keys, owner);
                DeleteVerified(ToolConsentStorageScope.Project, keys.ProjectGrant);
                Fence(root, keys, owner);
                if (ValidNonce(nonce))
                {
                    DeleteVerified(ToolConsentStorageScope.Session, keys.SessionGrant(nonce));
                    Fence(root, keys, owner);
                }
                if (scope == ToolConsentScope.Project)
                    SetVerified(ToolConsentStorageScope.Project, keys.ProjectGrant, true);
                else
                    SetVerified(ToolConsentStorageScope.Session, keys.SessionGrant(nonce), true);
                Fence(root, keys, owner);
                SetVerified(ToolConsentStorageScope.Project, keys.Enabled, true);
                Fence(root, keys, owner);
                VerifyGrant(keys, nonce, scope, true);
                Fence(root, keys, owner);
                DeleteVerified(ToolConsentStorageScope.Session, keys.Denied);
                Fence(root, keys, owner);
                VerifyGrant(keys, nonce, scope, false);
                Fence(root, keys, owner);
                var success = new ToolConsentChangeResult(true, false, false, false, null);
                if (!_process.Publish(keys.ProcessKey, owner))
                    throw new InvalidOperationException("consent_state_changed");
                return success; // No native calls/callbacks after publication.
            }
            catch (Exception)
            {
                var failure = CleanupDenied(keys, nonce);
                _process.Fail(keys.ProcessKey, owner, "storage_unconfirmed");
                return new ToolConsentChangeResult(false, true, failure.ReloadDenialConfirmed, failure.RestartDenialConfirmed, "storage_unconfirmed");
            }
        }

        public ToolConsentChangeResult Revoke(string name)
        {
            if (!Capture(name, out _, out var keys, out var error))
                return NotApplied(error);
            if (!_process.Begin(keys.ProcessKey, true, out long owner, out error))
                return NotApplied(error, true);
            string nonce = null;
            try
            {
                nonce = _storage.GetString(ToolConsentStorageScope.Session, keys.SessionId);
            }
            catch (Exception) { }
            var result = CleanupDenied(keys, nonce);
            _process.Fail(keys.ProcessKey, owner, result.Succeeded ? "explicit_grant_required" : "storage_unconfirmed");
            return result;
        }

        private ToolConsentChangeResult CleanupDenied(ToolConsentKeys keys, string nonce)
        {
            // Each attempt/readback is independent: a throwing denial write must
            // never skip deletion/disable, or permit a preexisting project grant.
            Try(() => _storage.SetBool(ToolConsentStorageScope.Session, keys.Denied, true));
            Try(() => _storage.SetBool(ToolConsentStorageScope.Project, keys.Enabled, false));
            Try(() => _storage.DeleteBool(ToolConsentStorageScope.Project, keys.ProjectGrant));
            if (!ValidNonce(nonce))
                Try(() => nonce = _storage.GetString(ToolConsentStorageScope.Session, keys.SessionId));
            bool validNonce = ValidNonce(nonce);
            if (validNonce)
                Try(() => _storage.DeleteBool(ToolConsentStorageScope.Session, keys.SessionGrant(nonce)));
            bool denied = ReadConfirmed(() => _storage.GetBool(ToolConsentStorageScope.Session, keys.Denied));
            bool projectAbsent = ReadConfirmed(() => !_storage.GetBool(ToolConsentStorageScope.Project, keys.ProjectGrant));
            bool disabled = ReadConfirmed(() => !_storage.GetBool(ToolConsentStorageScope.Project, keys.Enabled));
            bool sessionAbsent = validNonce
                ? ReadConfirmed(() => !_storage.GetBool(ToolConsentStorageScope.Session, keys.SessionGrant(nonce)))
                : ReadConfirmed(() => !ValidNonce(_storage.GetString(ToolConsentStorageScope.Session, keys.SessionId)));
            bool cleared = projectAbsent && disabled && sessionAbsent;
            return new ToolConsentChangeResult(cleared, true, denied || cleared, projectAbsent && disabled, cleared ? null : "storage_unconfirmed");
        }

        private static void Try(Action action)
        {
            try
            {
                action();
            }
            catch (Exception) { }
        }

        private static bool ReadConfirmed(Func<bool> read)
        {
            try
            {
                return read();
            }
            catch (Exception)
            {
                return false;
            }
        }

        private void SetVerified(ToolConsentStorageScope scope, string key, bool value)
        {
            _storage.SetBool(scope, key, value);
            if (_storage.GetBool(scope, key) != value)
                throw new InvalidOperationException("storage_unconfirmed");
        }

        private void DeleteVerified(ToolConsentStorageScope scope, string key)
        {
            _storage.DeleteBool(scope, key);
            if (_storage.GetBool(scope, key))
                throw new InvalidOperationException("storage_unconfirmed");
        }

        private void VerifyGrant(ToolConsentKeys keys, string nonce, ToolConsentScope scope, bool denied)
        {
            bool project = _storage.GetBool(ToolConsentStorageScope.Project, keys.ProjectGrant);
            bool session = ValidNonce(nonce) && _storage.GetBool(ToolConsentStorageScope.Session, keys.SessionGrant(nonce));
            if (
                project != (scope == ToolConsentScope.Project)
                || session != (scope == ToolConsentScope.EditorSession)
                || !_storage.GetBool(ToolConsentStorageScope.Project, keys.Enabled)
                || _storage.GetBool(ToolConsentStorageScope.Session, keys.Denied) != denied
            )
                throw new InvalidOperationException("storage_unconfirmed");
        }
    }
}
