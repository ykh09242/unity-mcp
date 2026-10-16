using System;
using System.Globalization;
using System.Linq;
using System.Text;
using System.Threading.Tasks;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;

namespace MCPForUnity.Editor.Services
{
    public interface IToolSettingsStatusSource
    {
        ToolSettingsStatus Status { get; }
        event Action StatusChanged;
        void NotifyToolSettingsChanged();
        Task RetryToolSettingsAsync();
    }

    public sealed class ToolSettingsSnapshot
    {
        private readonly JArray _tools;
        internal readonly string Identity;
        public string Epoch { get; }
        public long Revision { get; }
        public int EnabledToolCount => _tools.Count;
        public JArray Tools => (JArray)_tools.DeepClone();

        internal ToolSettingsSnapshot(string epoch, long revision, JArray tools, string identity)
        {
            Epoch = epoch;
            Revision = revision;
            _tools = tools;
            Identity = identity;
        }
    }

    public sealed class ToolSettingsStatus
    {
        public string Epoch { get; }
        public long DesiredRevision { get; }
        public long? ConfirmedRevision { get; }
        public long? ServerCatalogRevision { get; }
        public string SessionId { get; }
        public string Stage { get; }
        public string Reason { get; }
        public string Message { get; }
        public bool IsConnected { get; }
        public bool CanRetry { get; }

        internal ToolSettingsStatus(
            string epoch,
            long desired,
            long? confirmed,
            long? catalog,
            string session,
            string stage,
            string reason,
            string message,
            bool connected,
            bool retry
        )
        {
            Epoch = epoch;
            DesiredRevision = desired;
            ConfirmedRevision = confirmed;
            ServerCatalogRevision = catalog;
            SessionId = session;
            Stage = stage;
            Reason = reason;
            Message = message;
            IsConnected = connected;
            CanRetry = retry;
        }
    }

    /// <summary>Memory-only intent and receipt state; transport owners supply scheduling and cancellation.</summary>
    public sealed class ToolSettingsState : IToolSettingsStatusSource
    {
        public const string Capability = "tool_settings_ack_v1";
        public const long MaxRevision = 9007199254740991L;
        public static ToolSettingsState Current { get; } = new();
        private readonly object _gate = new();
        private string _epoch = Guid.NewGuid().ToString("N");
        private long _revision;
        private ToolSettingsSnapshot _desired;
        private ToolSettingsSnapshot _attempt;
        private bool _inFlight;
        private object _owner;
        private string _session;
        private bool _supported;
        private bool _dirty;
        private long? _confirmed;
        private long? _catalog;
        private string _stage = "saved";
        private string _reason;
        private string _message;
        private Func<Task> _retry;
        private Task _retryTask;

        public event Action StatusChanged;

        public ToolSettingsStatus Status
        {
            get
            {
                lock (_gate)
                    return new ToolSettingsStatus(_epoch, _revision, _confirmed, _catalog, _session, _stage, _reason, _message, _owner != null, CanRetry());
            }
        }

        public ToolSettingsSnapshot Capture(JArray tools, JObject enablement)
        {
            JArray copied;
            string identity;
            try
            {
                if (tools == null || enablement == null || tools.Count > 256)
                    throw new ArgumentException("Tool settings exceed supported catalog bounds.");
                if (Encoding.UTF8.GetByteCount(tools.ToString(Formatting.None)) > 512 * 1024)
                    throw new ArgumentException("Tool catalog exceeds 512 KiB.");
                if (enablement.Properties().Any(property => property.Value.Type != JTokenType.Boolean))
                    throw new ArgumentException("Tool enablement must contain Boolean values.");
                if (Encoding.UTF8.GetByteCount(enablement.ToString(Formatting.None)) > 512 * 1024)
                    throw new ArgumentException("Tool enablement snapshot exceeds 512 KiB.");
                copied = new JArray(
                    tools
                        .OrderBy(tool => (string)tool["name"], StringComparer.Ordinal)
                        .Select(tool =>
                        {
                            var descriptor = (JObject)Canonical(tool);
                            descriptor.Remove("enabled");
                            descriptor.Remove("auto_register");
                            descriptor.Remove("is_built_in");
                            if (
                                descriptor.Property("group") != null
                                && (
                                    descriptor["group"].Type == JTokenType.Null
                                    || descriptor["group"].Type == JTokenType.String && string.IsNullOrWhiteSpace((string)descriptor["group"])
                                )
                            )
                                descriptor["group"] = "core";
                            return descriptor;
                        })
                );
                identity = new JObject { ["tools"] = copied, ["enabled"] = Canonical(enablement) }.ToString(Formatting.None);
            }
            catch (Exception error)
            {
                PreparationFailed(CaptureFailureMessage(error.GetBaseException().GetType().Name));
                throw;
            }
            ToolSettingsSnapshot snapshot;
            lock (_gate)
            {
                if (_desired == null || _desired.Identity != identity)
                {
                    if (_revision == MaxRevision)
                    {
                        _epoch = Guid.NewGuid().ToString("N");
                        _revision = 0;
                        _confirmed = _catalog = null;
                        _attempt = null;
                        _inFlight = false;
                    }
                    _desired = new ToolSettingsSnapshot(_epoch, ++_revision, copied, identity);
                    SetStage(
                        _owner == null ? "saved"
                            : _supported ? "pending"
                            : "unconfirmed",
                        _owner != null && !_supported ? "unsupported_peer" : null
                    );
                }
                else if (_dirty || _stage == "preparation_failed")
                    SetStage(
                        _confirmed == _revision ? "applied"
                        : _owner == null ? "saved"
                        : _supported ? "pending"
                        : "unconfirmed"
                    );
                _dirty = false;
                snapshot = _desired;
            }
            StatusChanged?.Invoke();
            return snapshot;
        }

        public void NotifyToolSettingsChanged()
        {
            lock (_gate)
            {
                _dirty = true;
                SetStage(_owner != null && _supported ? "pending" : "saved");
            }
            StatusChanged?.Invoke();
        }

        public void SetConnection(object owner, string sessionId, bool supported, Func<Task> retry)
        {
            if (owner == null || string.IsNullOrEmpty(sessionId))
                throw new ArgumentException("A live registration owner and session are required.");
            lock (_gate)
            {
                if (!ReferenceEquals(_owner, owner) || _session != sessionId)
                {
                    _attempt = null;
                    _inFlight = false;
                    _confirmed = _catalog = null;
                    _retryTask = null;
                }
                _owner = owner;
                _session = sessionId;
                _supported = supported;
                _retry = retry;
                SetStage(supported ? "pending" : "unconfirmed", supported ? null : "unsupported_peer");
            }
            StatusChanged?.Invoke();
        }

        public void Disconnect(object owner)
        {
            lock (_gate)
            {
                if (!ReferenceEquals(_owner, owner))
                    return;
                _owner = null;
                _session = null;
                _attempt = null;
                _inFlight = false;
                _retry = null;
                _retryTask = null;
                SetStage("disconnected");
            }
            StatusChanged?.Invoke();
        }

        public ToolSettingsSnapshot BeginAttempt(object owner)
        {
            ToolSettingsSnapshot snapshot;
            lock (_gate)
            {
                if (!ReferenceEquals(_owner, owner) || !_supported || _desired == null || _inFlight || _dirty || _stage == "preparation_failed")
                    return null;
                _attempt = _desired;
                _inFlight = true;
                snapshot = _attempt;
                SetStage("sending");
            }
            StatusChanged?.Invoke();
            return snapshot;
        }

        public void MarkSent(object owner, ToolSettingsSnapshot snapshot)
        {
            lock (_gate)
            {
                if (!OwnsAttempt(owner, snapshot) || !_inFlight || !ReferenceEquals(_desired, snapshot) || _dirty)
                    return;
                SetStage("awaiting_ack");
            }
            StatusChanged?.Invoke();
        }

        public bool AcceptReceipt(object owner, JObject receipt)
        {
            lock (_gate)
            {
                if (
                    !ReferenceEquals(_owner, owner)
                    || !_supported
                    || _attempt == null
                    || receipt == null
                    || receipt["type"]?.Type != JTokenType.String
                    || receipt["session_id"]?.Type != JTokenType.String
                    || receipt["settings_epoch"]?.Type != JTokenType.String
                    || receipt["status"]?.Type != JTokenType.String
                    || (receipt["reason"] != null && receipt["reason"].Type != JTokenType.Null && receipt["reason"].Type != JTokenType.String)
                    || (receipt["message"] != null && receipt["message"].Type != JTokenType.Null && receipt["message"].Type != JTokenType.String)
                    || receipt.Value<string>("type") != "tool_settings_result"
                    || receipt.Value<string>("session_id") != _session
                    || receipt.Value<string>("settings_epoch") != _attempt.Epoch
                    || !Integer(receipt["desired_revision"], out long revision)
                    || revision != _attempt.Revision
                    || !Integer(receipt["catalog_revision"], out long catalog)
                    || catalog < 0
                    || !Integer(receipt["enabled_tool_count"], out long count)
                    || count < 0
                    || count > 256
                )
                    return false;
                string status = receipt.Value<string>("status");
                if ((status != "applied" && status != "rejected") || (status == "applied" && count != _attempt.EnabledToolCount))
                    return false;
                if (_confirmed.HasValue && revision <= _confirmed.Value && !_inFlight)
                    return false;
                _inFlight = false;
                if (status == "applied" && (!_confirmed.HasValue || revision > _confirmed.Value))
                {
                    _confirmed = revision;
                    _catalog = catalog;
                }
                if (_desired.Revision == revision && !_dirty)
                    SetStage(status, receipt.Value<string>("reason"), receipt.Value<string>("message"));
            }
            StatusChanged?.Invoke();
            return true;
        }

        public void FailAttempt(object owner, ToolSettingsSnapshot snapshot, string stage, string reason, string message)
        {
            if (stage != "unconfirmed" && stage != "send_failed")
                throw new ArgumentException("A local attempt can be unconfirmed or send_failed, not a server rejection.");
            lock (_gate)
            {
                if (!OwnsAttempt(owner, snapshot))
                    return;
                _inFlight = false;
                if (ReferenceEquals(_desired, snapshot) && !_dirty)
                    SetStage(stage, reason, message);
            }
            StatusChanged?.Invoke();
        }

        public Task RetryToolSettingsAsync()
        {
            TaskCompletionSource<bool> completion;
            Func<Task> retry;
            object owner;
            lock (_gate)
            {
                if (_retryTask != null && !_retryTask.IsCompleted)
                    return _retryTask;
                if (!CanRetry())
                    return Task.CompletedTask;
                completion = new TaskCompletionSource<bool>(TaskCreationOptions.RunContinuationsAsynchronously);
                _retryTask = completion.Task;
                retry = _retry;
                owner = _owner;
            }
            _ = CompleteRetryAsync(retry, owner, completion);
            return completion.Task;
        }

        private async Task CompleteRetryAsync(Func<Task> retry, object owner, TaskCompletionSource<bool> completion)
        {
            try
            {
                await retry().ConfigureAwait(false);
            }
            catch (Exception error)
            {
                lock (_gate)
                    if (ReferenceEquals(_owner, owner))
                        SetStage("send_failed", "retry_failed", error.Message);
            }
            finally
            {
                completion.TrySetResult(true);
                StatusChanged?.Invoke();
            }
        }

        public void PreparationFailed(string message)
        {
            lock (_gate)
                SetStage("preparation_failed", "invalid_catalog", message);
            StatusChanged?.Invoke();
        }

        internal void PreparationFailed(object owner, ToolSettingsStatus expected, string exceptionType)
        {
            lock (_gate)
            {
                if (!ReferenceEquals(_owner, owner) || _epoch != expected.Epoch || _revision != expected.DesiredRevision)
                    return;
                SetStage("preparation_failed", "capture_failed", CaptureFailureMessage(exceptionType));
            }
            StatusChanged?.Invoke();
        }

        internal static string CaptureFailureMessage(string exceptionType) => "Catalog preparation failed (" + exceptionType + ").";

        private bool CanRetry() =>
            _owner != null
            && _supported
            && _desired != null
            && !_inFlight
            && _retry != null
            && (_retryTask == null || _retryTask.IsCompleted)
            && _stage != "applied";

        private bool OwnsAttempt(object owner, ToolSettingsSnapshot snapshot) => ReferenceEquals(_owner, owner) && ReferenceEquals(_attempt, snapshot);

        private void SetStage(string stage, string reason = null, string message = null)
        {
            _stage = stage;
            _reason = Bounded(reason);
            _message = Bounded(message);
        }

        private static string Bounded(string value) =>
            value == null ? null : new string(value.Take(512).Select(character => char.IsControl(character) ? ' ' : character).ToArray());

        private static bool Integer(JToken token, out long value)
        {
            value = 0;
            return token?.Type == JTokenType.Integer
                && long.TryParse(token.ToString(), NumberStyles.None, CultureInfo.InvariantCulture, out value)
                && value <= MaxRevision;
        }

        private static JToken Canonical(JToken token) =>
            token switch
            {
                JObject obj => new JObject(
                    obj.Properties()
                        .OrderBy(property => property.Name, StringComparer.Ordinal)
                        .Select(property => new JProperty(property.Name, Canonical(property.Value)))
                ),
                JArray array => new JArray(array.Select(Canonical)),
                _ => token.DeepClone(),
            };
    }
}
