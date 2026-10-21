using System;
using System.IO;
using System.Reflection;
using System.Text;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using Newtonsoft.Json.Serialization;
using UnityEditor;

namespace MCPForUnity.Editor.Services.PlayScenarios
{
    /// <summary>Two-slot immutable inputs with an atomic mutable-state pointer; legacy full snapshots remain readable.</summary>
    internal sealed class PlayScenarioSessionCheckpoint
    {
        private const string Pointer = "_frozen_definition";
        private readonly string key;
        private readonly string member;
        private readonly int? maximumBytes;
        private readonly JsonSerializer serializer;
        private WeakReference frozen;
        private string identity;
        private int frozenBytes;

        internal PlayScenarioSessionCheckpoint(string key, Type type, string member, int? maximumBytes = null)
        {
            this.key = key;
            this.member = member;
            this.maximumBytes = maximumBytes;
            serializer = JsonSerializer.Create(
                new JsonSerializerSettings { ContractResolver = new MutableResolver(type, member), DateParseHandling = DateParseHandling.None }
            );
        }

        private sealed class MutableResolver : DefaultContractResolver
        {
            private readonly Type type;
            private readonly string member;

            internal MutableResolver(Type type, string member)
            {
                this.type = type;
                this.member = member;
            }

            protected override JsonProperty CreateProperty(MemberInfo info, MemberSerialization serialization)
            {
                JsonProperty property = base.CreateProperty(info, serialization);
                if (info.DeclaringType == type && property.PropertyName == member)
                    property.Ignored = true;
                return property;
            }
        }

        internal void Write(object state, object definition)
        {
            bool changed = frozen?.Target != definition || identity == null;
            string next = changed ? (identity?.StartsWith("0:", StringComparison.Ordinal) == true ? "1:" : "0:") + Guid.NewGuid().ToString("N") : identity;
            string inputs = changed ? new JObject { ["id"] = next, ["value"] = JToken.FromObject(definition) }.ToString(Formatting.None) : null;
            JObject mutable = JObject.FromObject(state, serializer);
            mutable[Pointer] = next;
            string json = mutable.ToString(Formatting.None);
            int bytes = inputs == null ? frozenBytes : Encoding.UTF8.GetByteCount(inputs);
            if (maximumBytes.HasValue && (long)bytes + Encoding.UTF8.GetByteCount(json) > maximumBytes.Value)
                throw new InvalidOperationException("Suite session snapshot exceeds its 40 MiB limit.");
            if (changed)
                SessionState.SetString(key + ".definition." + next[0], inputs);
            // If either write fails, the previous pointer still resolves to its other slot.
            SessionState.SetString(key, json);
            if (changed)
            {
                SessionState.EraseString(key + ".definition." + (next[0] == '0' ? '1' : '0'));
                identity = next;
                frozenBytes = bytes;
                frozen = new WeakReference(definition);
            }
        }

        internal JObject Read(string saved)
        {
            JObject value = Parse(saved);
            if (value[Pointer] == null)
                return value;
            string pointer = value[Pointer].Type == JTokenType.String ? (string)value[Pointer] : null;
            if (
                pointer == null
                || pointer.Length != 34
                || (pointer[0] != '0' && pointer[0] != '1')
                || pointer[1] != ':'
                || !Guid.TryParseExact(pointer.Substring(2), "N", out _)
            )
                throw new InvalidDataException("Invalid frozen checkpoint identity.");
            string inputs = SessionState.GetString(key + ".definition." + pointer[0], "");
            if (maximumBytes.HasValue && (long)Encoding.UTF8.GetByteCount(inputs) + Encoding.UTF8.GetByteCount(saved) > maximumBytes.Value)
                throw new InvalidDataException("Suite session snapshot exceeds its limit.");
            JObject definition = Parse(inputs);
            if ((string)definition["id"] != pointer || definition["value"] == null || value[member] != null)
                throw new InvalidDataException("Frozen checkpoint identity does not match its state.");
            value[member] = definition["value"];
            value.Remove(Pointer);
            identity = pointer;
            frozen = null;
            return value;
        }

        private static JObject Parse(string json)
        {
            using var reader = new JsonTextReader(new StringReader(json)) { DateParseHandling = DateParseHandling.None, MaxDepth = 48 };
            JObject value = JObject.Load(reader, new JsonLoadSettings { DuplicatePropertyNameHandling = DuplicatePropertyNameHandling.Error });
            if (reader.Read())
                throw new InvalidDataException("Checkpoint has trailing JSON content.");
            return value;
        }
    }
}
