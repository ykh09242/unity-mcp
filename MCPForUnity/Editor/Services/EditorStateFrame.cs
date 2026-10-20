using System;
using System.Globalization;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;

namespace MCPForUnity.Editor.Services
{
    /// <summary>Immutable data only; the sender never borrows the mutable editor cache.</summary>
    internal sealed class EditorStateFrame
    {
        internal sealed class Content
        {
            private readonly string _prefix;
            internal readonly long Sequence;

            internal Content(string epoch, JObject snapshot)
            {
                Sequence = snapshot.Value<long>("sequence");
                var content = (JObject)snapshot.DeepClone();
                content.Remove("observed_at_unix_ms");
                string json = content.ToString(Formatting.None);
                _prefix =
                    "{\"type\":\"editor_state\",\"epoch\":"
                    + JsonConvert.ToString(epoch)
                    + ",\"sequence\":"
                    + Sequence.ToString(CultureInfo.InvariantCulture)
                    + ",\"state\":"
                    + json.Substring(0, json.Length - 1)
                    + (content.Count == 0 ? "" : ",")
                    + "\"observed_at_unix_ms\":";
            }

            internal EditorStateFrame Observe(long observedAtUnixMs) => new EditorStateFrame(this, observedAtUnixMs);

            internal string Serialize(long observedAtUnixMs)
            {
                string timestamp = observedAtUnixMs.ToString(CultureInfo.InvariantCulture);
                return string.Concat(_prefix, timestamp, "},\"observed_at_unix_ms\":", timestamp, "}");
            }
        }

        private readonly Content _content;
        internal long Sequence => _content.Sequence;
        internal long ObservedAtUnixMs { get; }

        private EditorStateFrame(Content content, long observedAtUnixMs)
        {
            _content = content;
            ObservedAtUnixMs = observedAtUnixMs;
        }

        internal string ToJson() => _content.Serialize(ObservedAtUnixMs);
    }
}
