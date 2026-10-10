using System;
using System.IO;
using MCPForUnity.Editor.Services.PlayScenarios;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;

namespace MCPForUnity.Runtime.PlayScenarios
{
    /// <summary>A bounded diagnostic snapshot. It never evaluates game state or proves final success.</summary>
    public sealed class PlayScenarioPlayerProgress
    {
        public const int Limit = 16 * 1024;
        private readonly string path;
        private readonly PlayScenarioPlayerRequest request;
        private readonly PlayScenarioPlayerBundle bundle;
        private readonly int processId;
        private bool published;
        private long sequence;
        private long lastLoop;
        private long lastElapsed;
        private long nextPublish;

        public PlayScenarioPlayerProgress(string directory, PlayScenarioPlayerRequest request, PlayScenarioPlayerBundle bundle, int processId)
        {
            if (processId <= 0)
                throw new ArgumentOutOfRangeException(nameof(processId));
            this.path = PlayScenarioPlayerFiles.CheckedAbsolute(Path.Combine(directory, "progress.json"));
            this.request = request ?? throw new ArgumentNullException(nameof(request));
            this.bundle = bundle ?? throw new ArgumentNullException(nameof(bundle));
            this.processId = processId;
        }

        public void PublishInitial(PlayScenarioRun run)
        {
            if (published)
                throw new InvalidOperationException("Player startup progress was already published.");
            PlayScenarioPlayerFiles.WriteNew(path, Snapshot(run, 0, 0, 0).ToString(Formatting.None), Limit);
            published = true;
        }

        /// <summary>The runner calls this only after a real main-loop Tick completes.</summary>
        public bool PublishMainLoop(PlayScenarioRun run, long mainLoopSequence, long heartbeatUnixMs, long elapsedMs)
        {
            if (!published || mainLoopSequence <= lastLoop || heartbeatUnixMs <= 0 || elapsedMs < lastElapsed)
                throw new InvalidOperationException("Player progress requires monotonic actual main-loop observations.");
            lastLoop = mainLoopSequence;
            lastElapsed = elapsedMs;
            if (elapsedMs < nextPublish)
                return false;
            sequence++;
            PlayScenarioPlayerFiles.ReplaceExisting(path, Snapshot(run, mainLoopSequence, heartbeatUnixMs, elapsedMs).ToString(Formatting.None), Limit);
            nextPublish = elapsedMs + 1000;
            return true;
        }

        private JObject Snapshot(PlayScenarioRun run, long loop, long heartbeat, long elapsed)
        {
            if (run == null)
                throw new ArgumentNullException(nameof(run));
            PlayScenarioStepResult step = run.Steps.Count == 0 ? null : run.Steps[Math.Min(Math.Max(0, run.Cursor), run.Steps.Count - 1)];
            return new JObject
            {
                ["schema_version"] = 1,
                ["job_id"] = request.JobId,
                ["definition_hash"] = bundle.DefinitionHash,
                ["build_id"] = request.SchemaVersion == 2 ? bundle.BuildId : null,
                ["build_source_revision"] = request.SchemaVersion == 2 ? bundle.BuildSourceRevision : null,
                ["payload_hash"] = request.SchemaVersion == 2 ? request.PayloadHash : null,
                ["process_id"] = processId,
                ["sequence"] = sequence,
                ["main_loop_sequence"] = loop,
                ["heartbeat_unix_ms"] = heartbeat,
                ["elapsed_ms"] = elapsed,
                ["phase"] = run.Phase ?? "",
                ["iteration"] = step?.Iteration ?? 0,
                ["stage"] = step?.Stage ?? "",
                ["step_index"] = step?.StepIndex ?? -1,
                ["status"] = run.Status ?? "",
                ["last_observation"] = PlayScenarioEngine.Bounded(step?.Detail ?? "", 512),
            };
        }
    }
}
