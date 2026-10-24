using System;
using System.Collections.Generic;
using Newtonsoft.Json.Linq;

namespace MCPForUnity.Editor.Services.PlayScenarios
{
    /// <summary>Window-only metadata revision with separately retained, immutable completed evidence.</summary>
    internal sealed class PlayScenarioSuiteEditorSnapshot
    {
        internal readonly JObject Data;
        internal readonly long Revision;
        internal readonly IReadOnlyList<JObject> ChildReports;

        internal PlayScenarioSuiteEditorSnapshot(JObject data, long revision, IReadOnlyList<JObject> childReports)
        {
            Data = data;
            Revision = revision;
            ChildReports = childReports;
        }
    }

    internal sealed class PlayScenarioSuiteEditorProjection
    {
        private PlayScenarioSuiteReport source;
        private PlayScenarioSuiteEditorSnapshot snapshot;
        private long revision;

        internal PlayScenarioSuiteEditorSnapshot Read(PlayScenarioSuiteReport report, string status)
        {
            if (ReferenceEquals(source, report) && Matches(report, status))
                return snapshot;
            var children = new JArray();
            var evidence = new JObject[report.Scenarios.Count];
            for (int index = 0; index < report.Scenarios.Count; index++)
            {
                PlayScenarioSuiteChild child = report.Scenarios[index];
                children.Add(
                    new JObject
                    {
                        ["name"] = child.Name,
                        ["status"] = child.Status,
                        ["job_id"] = child.JobId,
                        ["skip_reason"] = child.SkipReason,
                    }
                );
                // The coordinator publishes a detached terminal report once. It subsequently
                // only replaces this reference. Do not parent it into another JObject: Json.NET
                // would clone the complete history every time just the summary changes.
                evidence[index] = child.Report;
            }
            var data = new JObject
            {
                ["suite_id"] = report.SuiteId,
                ["status"] = status,
                ["scenarios"] = children,
                ["error"] = report.Error,
                ["source_revision"] = report.SourceRevision,
                ["report_path"] = report.ReportPath,
                ["report_error"] = report.ReportError,
            };
            source = report;
            return snapshot = new PlayScenarioSuiteEditorSnapshot(data, ++revision, evidence);
        }

        private bool Matches(PlayScenarioSuiteReport report, string status)
        {
            if (snapshot == null || snapshot.ChildReports.Count != report.Scenarios.Count)
                return false;
            JObject data = snapshot.Data;
            if (
                (string)data["suite_id"] != report.SuiteId
                || (string)data["status"] != status
                || (string)data["error"] != report.Error
                || (string)data["source_revision"] != report.SourceRevision
                || (string)data["report_path"] != report.ReportPath
                || (string)data["report_error"] != report.ReportError
            )
                return false;
            var children = (JArray)data["scenarios"];
            for (int index = 0; index < report.Scenarios.Count; index++)
            {
                PlayScenarioSuiteChild child = report.Scenarios[index];
                JToken previous = children[index];
                if (
                    (string)previous["name"] != child.Name
                    || (string)previous["status"] != child.Status
                    || (string)previous["job_id"] != child.JobId
                    || (string)previous["skip_reason"] != child.SkipReason
                    || !ReferenceEquals(snapshot.ChildReports[index], child.Report)
                )
                    return false;
            }
            return true;
        }
    }
}
