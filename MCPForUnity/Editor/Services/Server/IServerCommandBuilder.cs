using System.Collections.Generic;

namespace MCPForUnity.Editor.Services.Server
{
    /// <summary>Literal launch arguments, separate from the command rendered for manual use.</summary>
    public interface ILiteralServerCommandBuilder : IServerCommandBuilder
    {
        bool TryBuildLaunchCommand(out string fileName, out IReadOnlyList<string> arguments, out string error);
    }

    /// <summary>
    /// Interface for building uvx/server command strings.
    /// Handles platform-specific command construction for starting the MCP HTTP server.
    /// </summary>
    public interface IServerCommandBuilder
    {
        /// <summary>
        /// Attempts to build the command parts for starting the local HTTP server.
        /// </summary>
        /// <param name="fileName">Output: the executable file name (e.g., uvx path)</param>
        /// <param name="arguments">Output: the command arguments</param>
        /// <param name="displayCommand">Output: the full command string for display</param>
        /// <param name="error">Output: error message if the command cannot be built</param>
        /// <returns>True if the command was built successfully</returns>
        bool TryBuildCommand(out string fileName, out string arguments, out string displayCommand, out string error);

        /// <summary>
        /// Builds the uv path from the uvx path by replacing uvx with uv.
        /// </summary>
        /// <param name="uvxPath">Path to uvx executable</param>
        /// <returns>Path to uv executable</returns>
        string BuildUvPathFromUvx(string uvxPath);

        /// <summary>
        /// Gets the platform-specific PATH prepend string for finding uv/uvx.
        /// </summary>
        /// <returns>Paths to prepend to PATH environment variable</returns>
        string GetPlatformSpecificPathPrepend();

        /// <summary>
        /// Encodes a value for the legacy command string. Structured launches bypass this rendering.
        /// </summary>
        /// <param name="input">The input string</param>
        /// <returns>The value, quoted and escaped when it contains command-line punctuation or whitespace</returns>
        string QuoteIfNeeded(string input);
    }
}
