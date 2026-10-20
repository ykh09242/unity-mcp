using System;
using System.IO;

namespace MCPForUnity.Runtime.PlayScenarios
{
    /// <summary>Poll the explicit cancellation file at most once per 100 ms of monotonic time.</summary>
    internal sealed class PlayScenarioPlayerCancellation
    {
        private readonly string path;
        private long nextPoll;
        private bool cancelled;

        internal PlayScenarioPlayerCancellation(string directory) => path = PlayScenarioPlayerFiles.CheckedAbsolute(Path.Combine(directory, "cancel"));

        internal bool Poll(long elapsedMilliseconds)
        {
            if (cancelled)
                return true;
            if (elapsedMilliseconds < nextPoll)
                return false;
            nextPoll = elapsedMilliseconds > long.MaxValue - 100 ? long.MaxValue : elapsedMilliseconds + 100;
            // Cache path construction, never trust cached filesystem/reparse metadata.
            cancelled = File.Exists(PlayScenarioPlayerFiles.CheckedAbsolute(path));
            return cancelled;
        }
    }
}
