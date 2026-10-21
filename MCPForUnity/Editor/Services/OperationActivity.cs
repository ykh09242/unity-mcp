using System;
using System.Collections.Generic;
using System.Linq;
using System.Threading;
using MCPForUnity.Editor.Services.PlayScenarios;
using MCPForUnity.Editor.Tools;
using MCPForUnity.Editor.Tools.Build;
using Newtonsoft.Json.Linq;

namespace MCPForUnity.Editor.Services
{
    // Detached scalar observations only. Existing managers retain execution and cancellation ownership.
    internal sealed class OperationActivityRow
    {
        internal string Owner,
            Id,
            Operation,
            State,
            Phase,
            Detail,
            Cancellation,
            EvidenceId;
        internal long Started;
        internal long? Finished;
        internal bool Active,
            CanCancel;
        internal string Key => Owner + ":" + Id;
    }

    internal static class OperationActivity
    {
        private static long revision;
        internal static long Revision => Interlocked.Read(ref revision);

        internal static void Invalidate() => Interlocked.Increment(ref revision);

        internal static List<OperationActivityRow> Capture()
        {
            var rows = new List<OperationActivityRow>();
            TestJobManager.AppendActivity(rows);
            BuildJobStore.AppendActivity(rows);
            PlayScenarioService.AppendActivity(rows);
            return rows.OrderByDescending(row => row.Active).ThenByDescending(row => row.Started).ThenBy(row => row.Key, StringComparer.Ordinal).ToList();
        }

        internal static OperationActivityRow TestRow(TestJob job)
        {
            var control = job.NativeControl;
            bool requested = control?["reason"]?.Type == JTokenType.String;
            bool released = (bool?)control?["released"] == true;
            bool canRequest = job.Control?.CanRequest == true;
            bool active = job.Status == TestJobStatus.Running || (control != null && !released);
            return new OperationActivityRow
            {
                Owner = "test",
                Id = job.JobId,
                Operation = "Tests · " + job.Mode,
                State = job.Status.ToString().ToLowerInvariant(),
                Phase = job.CompletionKind ?? "executing",
                Started = job.StartedUnixMs,
                Finished = active ? null : job.FinishedUnixMs,
                Active = active,
                Detail =
                    job.CompletedTests
                    + " completed"
                    + (job.TotalTests.HasValue ? " / " + job.TotalTests : "")
                    + (job.CompletionKind == "deadline_exceeded" ? " · deadline observed; inspect native termination separately" : ""),
                Cancellation =
                    job.Status == TestJobStatus.Cancelled && released ? "Cancellation confirmed; native owner released"
                    : requested ? "Cancellation requested · native " + ((string)control["termination"] ?? "unknown")
                    : active && job.Status != TestJobStatus.Running ? "Native termination not confirmed"
                    : active && !canRequest ? "Cancellation unavailable: no supported native control owner"
                    : "No cancellation request observed",
                CanCancel = active && !requested && canRequest,
                EvidenceId = job.JobId,
            };
        }

        internal static OperationActivityRow ScenarioRow(PlayScenarioRun state, bool finalized)
        {
            bool active = state.Status == "running" || !finalized;
            return new OperationActivityRow
            {
                Owner = "scenario",
                Id = state.JobId,
                Operation = "Play scenario · " + state.Scenario?.Name,
                State = active ? "running" : state.Status,
                Phase = !finalized && state.Status != "running" ? "finalizing" : state.Phase,
                Started = state.StartedUnixMs,
                Finished = active ? null : state.FinishedUnixMs,
                Active = active,
                Detail =
                    "Step " + (state.Cursor + 1) + " · runner resources " + (state.RunnerResourcesReleased == true ? "released" : "not confirmed released"),
                Cancellation =
                    state.Status == "cancelled"
                        ? (
                            finalized && state.RunnerResourcesReleased == true ? "Cancellation confirmed"
                            : finalized ? "Cancellation requested; resource release not confirmed"
                            : "Cancellation requested; finalizing"
                        )
                        : "No cancellation request observed",
                CanCancel = state.Status == "running",
                EvidenceId = state.JobId,
            };
        }

        internal static object Cancel(OperationActivityRow row)
        {
            try
            {
                // Resolve the owner/id again after confirmation; never start or replay work.
                switch (row.Owner)
                {
                    case "test":
                        return TestJobManager.CancelJob(row.Id);
                    case "scenario":
                        return PlayScenarioService.Cancel(row.Id);
                    case "batch":
                        return ManageBuild.HandleCommand(new JObject { ["action"] = "cancel", ["job_id"] = row.Id });
                    default:
                        return new { observation = "This owner does not expose cancellation." };
                }
            }
            catch (Exception)
            {
                return new
                {
                    observation = "Cancellation could not be requested. The job may have expired or native control is unavailable; inspect its current status.",
                };
            }
            finally
            {
                Invalidate();
            }
        }

        internal static object Evidence(OperationActivityRow row)
        {
            switch (row.Owner)
            {
                case "test":
                    return TestJobManager.ToSerializable(TestJobManager.GetJob(row.Id), false, false);
                case "scenario":
                    return PlayScenarioService.EditorStatus(row.Id);
                case "build":
                case "batch":
                    return row.EvidenceId == null
                        ? new { observation = "Retained report is not available yet." }
                        : ManageBuild.HandleCommand(new JObject { ["action"] = "report", ["report_id"] = row.EvidenceId });
                default:
                    return null;
            }
        }
    }
}
