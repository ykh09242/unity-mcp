using System.IO;
using MCPForUnity.Editor.Models;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;

namespace MCPForUnity.Editor.Services.Transport
{
    /// <summary>Read transport JSON without interpreting string contents as dates.</summary>
    internal static class TransportJson
    {
        internal static Command DeserializeCommand(string json) =>
            JsonConvert.DeserializeObject<Command>(json, new JsonSerializerSettings { DateParseHandling = DateParseHandling.None });

        internal static JObject ParseObject(string json)
        {
            using (var reader = new JsonTextReader(new StringReader(json)) { DateParseHandling = DateParseHandling.None })
            {
                var value = JObject.Load(reader);
                // Match JObject.Parse: consume the rest so trailing non-comment content still fails.
                while (reader.Read()) { }
                return value;
            }
        }

        internal static JToken ParseToken(string json)
        {
            using (var reader = new JsonTextReader(new StringReader(json)) { DateParseHandling = DateParseHandling.None })
            {
                var value = JToken.Load(reader);
                // Match JToken.Parse completion, not just its first value.
                while (reader.Read()) { }
                return value;
            }
        }
    }
}
