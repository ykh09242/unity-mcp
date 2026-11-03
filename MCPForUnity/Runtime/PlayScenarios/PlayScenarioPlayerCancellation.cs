using System;
using System.IO;
using MCPForUnity.Editor.Services.PlayScenarios;

namespace MCPForUnity.Runtime.PlayScenarios
{
    /// <summary>Poll the explicit cancellation file at most once per 100 ms of monotonic time.</summary>
    internal sealed class PlayScenarioPlayerCancellation
    {
        private readonly string path;
        private readonly string fallbackPath;
        private readonly string jobId;
        private long nextPoll;
        private bool cancelled;

        internal PlayScenarioPlayerCancellation(string directory, string jobId)
        {
            path = PlayScenarioPlayerFiles.CheckedAbsolute(Path.Combine(directory, "cancel"));
            fallbackPath = PlayScenarioPlayerFiles.CheckedAbsolute(Path.Combine(directory, "cancel." + jobId));
            this.jobId = jobId;
        }

        internal void RejectForeignMarkers()
        {
            string directory = Path.GetDirectoryName(path);
            foreach (string marker in Directory.EnumerateFiles(directory, "cancel.*"))
            {
                string checkedMarker = PlayScenarioPlayerFiles.CheckedAbsolute(marker);
                if (checkedMarker != path && checkedMarker != fallbackPath && checkedMarker != path + ".tmp")
                    throw new IOException("Player run directory contains foreign cancellation evidence.");
            }
        }

        internal bool Poll(long elapsedMilliseconds, bool force = false)
        {
            if (cancelled)
                return true;
            if (!force && elapsedMilliseconds < nextPoll)
                return false;
            nextPoll = elapsedMilliseconds > long.MaxValue - 100 ? long.MaxValue : elapsedMilliseconds + 100;
            // Cache path construction, never trust cached filesystem/reparse metadata.
            if (File.Exists(PlayScenarioPlayerFiles.CheckedAbsolute(path)))
            {
                if (PlayScenarioPlayerFiles.Read(path, 64) != jobId + "\n")
                    throw new IOException("Player cancellation does not belong to this request.");
                cancelled = true;
            }
            if (File.Exists(PlayScenarioPlayerFiles.CheckedAbsolute(fallbackPath)))
            {
                if (new FileInfo(fallbackPath).Length != 0)
                    throw new IOException("Player cancellation marker is invalid.");
                cancelled = true;
            }
            return cancelled;
        }

        internal static void FinishBeforeStart(PlayScenarioRun run, long now)
        {
            run.Status = "cancelled";
            run.Phase = "finished";
            run.Error = "Cancelled by caller before scenario execution.";
            run.Failure = new PlayScenarioFailure { Code = "cancelled", Message = run.Error };
            run.FinishedUnixMs = now;
            run.RunnerResourcesReleased = true;
            foreach (PlayScenarioStepResult step in run.Steps)
            {
                step.Status = "skipped";
                step.FinishedUnixMs = now;
            }
            foreach (PlayScenarioIterationResult iteration in run.IterationResults)
            {
                iteration.Status = "skipped";
                iteration.FinishedUnixMs = now;
            }
        }
    }
}
