using MCPForUnity.Editor.Helpers;
using Newtonsoft.Json.Linq;

namespace MCPForUnity.Editor.Tools
{
    /// <summary>The move and rename actions accept the same source and destination on the native wire.</summary>
    internal readonly struct AssetMoveRequest
    {
        internal string Path { get; }
        internal string Destination { get; }

        private AssetMoveRequest(string path, string destination)
        {
            Path = path;
            Destination = destination;
        }

        internal static ErrorResponse Decode(JObject parameters, out AssetMoveRequest request)
        {
            request = default;
            JToken path = parameters["path"];
            JToken destination = parameters["destination"];
            if (path != null && path.Type != JTokenType.Null && path.Type != JTokenType.String)
                return new ErrorResponse("'path' must be a string or null.");
            if (destination != null && destination.Type != JTokenType.Null && destination.Type != JTokenType.String)
                return new ErrorResponse("'destination' must be a string or null.");

            // Required-value errors follow any reference revision check, including for explicit JSON null.
            request = new AssetMoveRequest((string)path, (string)destination);
            return null;
        }
    }
}
