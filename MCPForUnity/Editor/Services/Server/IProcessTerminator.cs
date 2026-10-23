namespace MCPForUnity.Editor.Services.Server
{
    /// <summary>
    /// Legacy process-termination injection seam retained for source compatibility.
    /// ServerManagementService accepts this interface but does not use it to stop servers.
    /// </summary>
    public interface IProcessTerminator
    {
        /// <summary>
        /// Terminates a process using platform-appropriate methods.
        /// On Unix: Tries SIGTERM first with grace period, then SIGKILL.
        /// On Windows: Tries taskkill, then taskkill /F.
        /// </summary>
        /// <param name="pid">The process ID to terminate</param>
        /// <returns>True if the process was terminated successfully</returns>
        bool Terminate(int pid);
    }
}
