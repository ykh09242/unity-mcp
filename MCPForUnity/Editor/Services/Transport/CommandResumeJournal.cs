using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Security.Cryptography;
using System.Text;
using MCPForUnity.Editor.Tools;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using UnityEditor;

namespace MCPForUnity.Editor.Services.Transport
{
    /// <summary>Bounded declarative checkpoints. Restoring a journal never schedules work.</summary>
    internal sealed class CommandResumeJournal
    {
        internal const string Capability = "command_resume_v1";
        internal const int MaxEntries = 128;
        internal const int MaxSnapshotBytes = 2 * 1024 * 1024;
        internal const int MaxResultBytes = 8192;
        private readonly object gate = new();
        private readonly Action<string> write;
        private readonly Func<long> clock;
        private readonly Func<string, JObject, bool> continuationPolicy;
        private readonly string domain = Guid.NewGuid().ToString("N");
        private readonly Dictionary<string, Entry> entries = new(StringComparer.Ordinal);
        private readonly Dictionary<string, Ticket> active = new(StringComparer.Ordinal);
        private bool unavailable;
        internal string SessionId { get; private set; } = Guid.NewGuid().ToString("N");

        internal sealed class Ticket
        {
            internal string Id;
            internal JObject Request;
            internal CommandResumeJournal Owner;
            internal Action Cancel;
            internal Func<bool> Revalidate;
        }

        internal sealed class Admission
        {
            internal Ticket Ticket;
            internal string State;
            internal JToken Payload;
            internal JObject Receipt;
            internal bool NotStarted;
            internal string RefusalReason;

            internal TransportCommandResponse Response(JObject arbitration)
            {
                var response = TransportCommandResponse.FromObject(
                    Payload
                        ?? new JObject
                        {
                            ["status"] = "error",
                            ["code"] = State == "unknown" ? "command_outcome_unknown" : "command_resume_" + State,
                            ["error"] =
                                State == "unavailable"
                                    ? "Command continuation or completed result is unavailable; do not replay"
                                    : "Command continuation state: " + State,
                        }
                );
                if (State == "queued" || State == "executing")
                    response.WithAdmission(arbitration, true, new NativeAdmissionBlocker("operation_busy", true));
                else if (NotStarted)
                    response.WithAdmission(arbitration, true, new NativeAdmissionBlocker(RefusalReason, false));
                else
                    response.WithAdmission(arbitration);
                return response.WithResume(Receipt);
            }
        }

        private sealed class Entry
        {
            public string id;
            public string fingerprint;
            public long deadline;
            public string phase;
            public string domain;
            public bool volatile_targets;
            public bool completed_uncached;
            public bool not_started;
            public string refusal_reason;
            public JToken result;
        }

        internal CommandResumeJournal(Func<string> read, Action<string> write, Func<long> clock, Func<string, JObject, bool> continuationPolicy = null)
        {
            this.write = write;
            this.clock = clock;
            this.continuationPolicy = continuationPolicy ?? ((_, __) => true);
            try
            {
                string text = read();
                if (string.IsNullOrEmpty(text))
                {
                    Persist();
                    return;
                }
                if (Encoding.UTF8.GetByteCount(text) > MaxSnapshotBytes)
                    throw new JsonException("Journal limit");
                using var input = new JsonTextReader(new StringReader(text)) { DateParseHandling = DateParseHandling.None, MaxDepth = 64 };
                var snapshot = JObject.Load(input);
                if (input.Read())
                    throw new JsonException("Trailing journal content");
                if (
                    (int?)snapshot["version"] != 1
                    || !IsIdentity((string)snapshot["session"])
                    || !(snapshot["entries"] is JArray rows)
                    || rows.Count > MaxEntries
                )
                    throw new JsonException("Invalid journal");
                if ((string)snapshot["digest"] != Digest((string)snapshot["session"] + "\n" + rows.ToString(Formatting.None)))
                    throw new JsonException("Checkpoint integrity changed");
                SessionId = (string)snapshot["session"];
                foreach (var row in rows)
                {
                    var entry = row.ToObject<Entry>();
                    if (
                        entry == null
                        || !IsIdentity(entry.id)
                        || entry.fingerprint?.Length != 64
                        || entry.deadline <= 0
                        || !new[] { "queued", "executing", "completed", "canceled", "unavailable" }.Contains(entry.phase)
                        || !IsIdentity(entry.domain)
                        || entries.ContainsKey(entry.id)
                    )
                        throw new JsonException("Invalid checkpoint");
                    entries.Add(entry.id, entry);
                }
            }
            catch (Exception)
            {
                unavailable = true;
            }
        }

        internal static bool IsIdentity(string value) => value != null && value.Length == 32 && value.All(c => c >= '0' && c <= '9' || c >= 'a' && c <= 'f');

        private bool Persist()
        {
            try
            {
                var snapshot = new JObject
                {
                    ["version"] = 1,
                    ["session"] = SessionId,
                    ["entries"] = JArray.FromObject(entries.Values),
                };
                // JRaw may retain whitespace or number spelling. Normalize the persisted
                // representation before hashing so reading valid JSON cannot change its digest.
                using var normalized = new JsonTextReader(new StringReader(snapshot["entries"].ToString(Formatting.None)))
                {
                    DateParseHandling = DateParseHandling.None,
                    MaxDepth = 64,
                };
                snapshot["entries"] = JArray.Load(normalized);
                snapshot["digest"] = Digest(SessionId + "\n" + snapshot["entries"].ToString(Formatting.None));
                string text = snapshot.ToString(Formatting.None);
                if (Encoding.UTF8.GetByteCount(text) > MaxSnapshotBytes)
                    throw new JsonException("Journal limit");
                write(text);
                return true;
            }
            catch (Exception)
            {
                unavailable = true;
                return false;
            }
        }

        internal Admission Admit(JObject request, string name, JObject parameters)
        {
            lock (gate)
            {
                if (
                    request == null
                    || request.Count != 5
                    || request["version"]?.Type != JTokenType.Integer
                    || request["version"].ToString(Formatting.None) != "1"
                    || request["invocation_id"]?.Type != JTokenType.String
                    || request["editor_session_id"]?.Type != JTokenType.String
                    || request["action"]?.Type != JTokenType.String
                    || request["deadline_unix_ms"]?.Type != JTokenType.Integer
                    || !long.TryParse(request["deadline_unix_ms"].ToString(Formatting.None), out long parsedDeadline)
                )
                    return Reply(null, "unavailable");
                string id = request?.Value<string>("invocation_id");
                string session = request?.Value<string>("editor_session_id");
                string action = request?.Value<string>("action");
                long? deadline = parsedDeadline;
                if (
                    unavailable
                    || !IsIdentity(id)
                    || session != SessionId
                    || !deadline.HasValue
                    || deadline <= 0
                    || string.IsNullOrWhiteSpace(name)
                    || !new[] { "new", "status", "resume", "cancel" }.Contains(action)
                )
                    return Reply(request, "unavailable");
                if (clock() >= deadline)
                    return Reply(request, "expired");
                string fingerprint = Fingerprint(name, parameters ?? new JObject());
                if (entries.TryGetValue(id, out Entry entry))
                {
                    if (entry.fingerprint != fingerprint || entry.deadline != deadline)
                        return Reply(request, "unavailable");
                }
                else
                {
                    if (action != "new" && action != "cancel")
                        return Reply(request, "unknown");
                    // Only expired entries may be removed: no live duplicate fence is evicted.
                    foreach (
                        string expired in entries
                            .Where(pair => pair.Value.deadline <= clock() && !active.ContainsKey(pair.Key))
                            .Select(pair => pair.Key)
                            .ToArray()
                    )
                        entries.Remove(expired);
                    if (entries.Count >= MaxEntries)
                        return Reply(request, "unavailable");
                    entry = new Entry
                    {
                        id = id,
                        fingerprint = fingerprint,
                        deadline = deadline.Value,
                        phase = "queued",
                        domain = domain,
                        volatile_targets = HasVolatileTargets(parameters, name) || !continuationPolicy(name, parameters ?? new JObject()),
                    };
                    entries.Add(id, entry);
                    if (!Persist())
                        return Reply(request, "unavailable");
                }
                if (action == "cancel")
                {
                    if (entry.phase != "completed" && entry.phase != "unavailable")
                    {
                        entry.phase = "canceled";
                        bool persisted = Persist();
                        if (active.TryGetValue(id, out Ticket owner))
                            owner.Cancel?.Invoke();
                        if (!persisted)
                            return Reply(request, "unavailable");
                    }
                    return Reply(request, entry.phase, entry.result, entry);
                }
                if (entry.phase == "executing")
                    return Reply(request, entry.domain == domain && active.ContainsKey(id) ? "executing" : "unknown");
                if (entry.phase != "queued")
                    return Reply(request, entry.phase, entry.result, entry);
                if (action == "status" || active.ContainsKey(id))
                    return Reply(request, "queued");
                if (
                    entry.domain != domain
                    && (entry.volatile_targets || HasVolatileTargets(parameters, name) || !continuationPolicy(name, parameters ?? new JObject()))
                )
                    return Reply(request, "unavailable");
                var ticket = new Ticket
                {
                    Id = id,
                    Request = (JObject)request.DeepClone(),
                    Owner = this,
                    Revalidate = entry.domain == domain ? null : () => continuationPolicy(name, parameters ?? new JObject()),
                };
                active.Add(id, ticket);
                var accepted = Reply(request, "queued");
                accepted.Ticket = ticket;
                return accepted;
            }
        }

        internal bool TryStart(Ticket ticket)
        {
            lock (gate)
            {
                if (unavailable || !Owns(ticket) || !entries.TryGetValue(ticket.Id, out Entry entry) || entry.phase != "queued" || clock() >= entry.deadline)
                    return false;
                if (ticket.Revalidate != null && !ticket.Revalidate())
                {
                    entry.phase = "unavailable";
                    Persist();
                    return false;
                }
                entry.phase = "executing";
                entry.domain = domain;
                return Persist();
            }
        }

        internal Admission Complete(Ticket ticket, JToken result, bool notStarted = false, string refusalReason = null)
        {
            lock (gate)
            {
                if (unavailable || !Owns(ticket) || !entries.TryGetValue(ticket.Id, out Entry entry))
                    return Reply(ticket?.Request, "unavailable");
                if (entry.phase != "canceled")
                {
                    entry.not_started = notStarted;
                    entry.refusal_reason = notStarted ? refusalReason : null;
                    if (result == null || Encoding.UTF8.GetByteCount(result.ToString(Formatting.None)) > MaxResultBytes)
                    {
                        entry.phase = "unavailable";
                        entry.completed_uncached = true;
                        entry.result = new JObject
                        {
                            ["status"] = "error",
                            ["code"] = "completed_result_not_retained",
                            ["error"] =
                                "Command execution completed, but its result exceeded the continuation cache limit and was not retained. Do not replay the command.",
                        };
                    }
                    else
                    {
                        entry.phase = "completed";
                        entry.result = result.DeepClone();
                    }
                    if (!Persist())
                        return Reply(ticket.Request, "unavailable");
                }
                // A durable terminal fence permits the original large response once. Only later
                // observations report that no replayable result was retained.
                return Reply(ticket.Request, entry.completed_uncached ? "completed" : entry.phase, entry.completed_uncached ? null : entry.result, entry);
            }
        }

        internal void Release(Ticket ticket)
        {
            lock (gate)
                if (Owns(ticket))
                    active.Remove(ticket.Id);
        }

        internal Admission Inspect(Ticket ticket)
        {
            lock (gate)
            {
                if (unavailable || ticket == null || !entries.TryGetValue(ticket.Id, out Entry entry))
                    return Reply(ticket?.Request, "unavailable");
                return Reply(ticket.Request, clock() >= entry.deadline ? "expired" : entry.phase, entry.result, entry);
            }
        }

        internal void BindCancellation(Ticket ticket, Action cancel)
        {
            lock (gate)
            {
                if (!Owns(ticket))
                    return;
                ticket.Cancel = cancel;
                if (entries[ticket.Id].phase == "canceled")
                    cancel();
            }
        }

        private bool Owns(Ticket ticket) =>
            ticket != null && ticket.Owner == this && active.TryGetValue(ticket.Id, out Ticket current) && ReferenceEquals(current, ticket);

        private Admission Reply(JObject request, string state, JToken payload = null, Entry metadata = null) =>
            new Admission
            {
                State = state,
                Payload = payload?.DeepClone(),
                NotStarted = metadata?.not_started ?? false,
                RefusalReason = metadata?.refusal_reason,
                Receipt = new JObject
                {
                    ["version"] = 1,
                    ["invocation_id"] = request?["invocation_id"]?.Type == JTokenType.String ? request.Value<string>("invocation_id") : null,
                    ["editor_session_id"] = SessionId,
                    ["state"] = state,
                },
            };

        private static string Fingerprint(string name, JObject parameters)
        {
            JToken Canonical(JToken value) =>
                value is JObject obj
                    ? new JObject(obj.Properties().OrderBy(p => p.Name, StringComparer.Ordinal).Select(p => new JProperty(p.Name, Canonical(p.Value))))
                : value is JArray array ? new JArray(array.Select(Canonical))
                : value.DeepClone();
            return Digest(name + "\n" + Canonical(parameters).ToString(Formatting.None));
        }

        private static string Digest(string text)
        {
            using var hash = SHA256.Create();
            return BitConverter.ToString(hash.ComputeHash(Encoding.UTF8.GetBytes(text))).Replace("-", "").ToLowerInvariant();
        }

        // These are scene selectors in the GameObject, Camera, Scene, Physics and
        // ObjectResolver handlers. Ordinary numeric settings are not selectors.
        private static readonly HashSet<string> NumericSceneSelectors = new(StringComparer.Ordinal)
        {
            "target",
            "referenceobject",
            "lookattarget",
            "parent",
            "find",
            "follow",
            "lookat",
            "camera",
            "viewtarget",
            "sceneviewtarget",
            "othertarget",
            "connectedbody",
        };

        private static bool IsNumericSelector(JToken value) =>
            value is JValue scalar
            && (value.Type == JTokenType.Integer || value.Type == JTokenType.String || value.Type == JTokenType.Float)
            && long.TryParse(
                scalar.ToString(System.Globalization.CultureInfo.InvariantCulture),
                System.Globalization.NumberStyles.Integer,
                System.Globalization.CultureInfo.InvariantCulture,
                out _
            );

        // Refuse process-local identity continuations; never recapture or reinterpret a target.
        private static bool HasVolatileTargets(JToken value, string commandName = null, bool componentValues = false)
        {
            if (componentValues && IsNumericSelector(value))
                return true;
            if (value?.Type == JTokenType.String && value.Value<string>().StartsWith("mcp-oh-", StringComparison.Ordinal))
                return true;
            if (value is JObject obj)
            {
                // Batch wrappers retain each child's actual command/action semantics.
                if (obj["tool"]?.Type == JTokenType.String && obj["params"] is JObject child && HasVolatileTargets(child, obj.Value<string>("tool")))
                    return true;
                string action = obj["action"]?.Type == JTokenType.String ? obj.Value<string>("action").ToLowerInvariant() : null;
                bool gameObject = commandName == "manage_gameobject" || commandName?.StartsWith("gameobject_", StringComparison.Ordinal) == true;
                bool nameAlias =
                    obj["target"] == null
                    && (
                        gameObject && action != "create"
                        || (commandName == "manage_graphics" || commandName == "manage_render_environment") && action == "skybox_set_sun"
                    );
                bool componentCommand = commandName == "manage_components" || commandName == "manage_components_selected";
                foreach (var property in obj.Properties())
                {
                    string key = property.Name.Replace("_", "").ToLowerInvariant();
                    if (
                        key.Contains("instanceid")
                        || key.Contains("scenehandle")
                        || key == "objectid"
                        || key == "handle"
                        || key == "objecthandle"
                        || NumericSceneSelectors.Contains(key) && IsNumericSelector(property.Value)
                        || key == "targets" && property.Value is JArray targets && targets.Any(IsNumericSelector)
                        || key == "name" && nameAlias && IsNumericSelector(property.Value)
                        || key == "id" && commandName?.StartsWith("get_gameobject", StringComparison.Ordinal) == true && IsNumericSelector(property.Value)
                    )
                        return true;
                    bool assignment =
                        componentValues
                        || key == "componentproperties"
                        || key == "componentstoadd"
                        || componentCommand && (key == "properties" || key == "value");
                    bool encodedContainer =
                        (key == "componentproperties" || key == "componentstoadd" || key == "properties") && property.Value.Type == JTokenType.String;
                    if (encodedContainer)
                    {
                        try
                        {
                            string text = property.Value.Value<string>();
                            if (text.Length > MaxSnapshotBytes)
                                return true;
                            using var reader = new JsonTextReader(new StringReader(text)) { DateParseHandling = DateParseHandling.None, MaxDepth = 64 };
                            var parsed = JToken.Load(reader);
                            if (!(parsed is JObject) && !(parsed is JArray) || reader.Read() || HasVolatileTargets(parsed, componentValues: assignment))
                                return true;
                        }
                        catch (JsonException)
                        {
                            return true;
                        }
                    }
                    else if (HasVolatileTargets(property.Value, componentValues: assignment))
                        return true;
                }
            }
            else if (value is JArray array)
                return array.Any(item => HasVolatileTargets(item, componentValues: componentValues));
            return false;
        }
    }

    [InitializeOnLoad]
    internal static class CommandResumeState
    {
        private const string Key = "MCPForUnity.CommandResume.v1";
        internal static readonly CommandResumeJournal Journal = new CommandResumeJournal(
            () => SessionState.GetString(Key, null),
            value => SessionState.SetString(Key, value),
            () => DateTimeOffset.UtcNow.ToUnixTimeMilliseconds(),
            CanContinue
        );
        internal static string SessionId => Journal.SessionId;

        private static bool CanContinue(string name, JObject parameters)
        {
            string reason = ToolOperationPolicy.Resolve(name, parameters, name == "ping" || CommandRegistry.IsTrustedBuiltInHandler(name)).Reason;
            if (reason == "untrusted_handler" || reason == "arbitrary_execution")
                return false;
            if (reason == "batch_envelope")
            {
                if (!(parameters["commands"] is JArray commands))
                    return false;
                foreach (var item in commands)
                    if (
                        !(item is JObject child)
                        || child["tool"]?.Type != JTokenType.String
                        || !CanContinue((string)child["tool"], child["params"] as JObject ?? new JObject())
                    )
                        return false;
            }
            return true;
        }
    }
}
