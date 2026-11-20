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
            foreach (string field in DeclaredActionContracts.AssetMoveRequiredFields)
            {
                JToken value = parameters[field];
                if (value != null && value.Type != JTokenType.Null && value.Type != JTokenType.String)
                    return new ErrorResponse($"'{field}' must be a string or null.");
            }

            // Required-value errors follow any reference revision check, including for explicit JSON null.
            request = new AssetMoveRequest((string)parameters[DeclaredActionContracts.AssetPath], (string)parameters[DeclaredActionContracts.AssetDestination]);
            return null;
        }
    }
}
