using System;
using System.Collections.Generic;
using MCPForUnity.Editor.Services.PlayScenarios;
using UnityEditor.UIElements;
using UnityEngine.UIElements;

namespace MCPForUnity.Editor.Windows.PlayScenarios
{
    internal static class PlayScenarioOptionsEditor
    {
        internal static void Render(VisualElement container, PlayScenarioDefinition draft, Action<Action> edit)
        {
            container.Clear();
            var options = new Foldout { text = "Run policies and diagnostics", name = "scenarioOptions" };
            Integer(
                options,
                "Completion quiet window (ms)",
                "completionStableMs",
                draft.CompletionStableMs,
                value => edit(() => draft.CompletionStableMs = value)
            );
            Integer(options, "Cleanup timeout (s)", "cleanupTimeout", draft.CleanupTimeoutSeconds, value => edit(() => draft.CleanupTimeoutSeconds = value));
            var policy = draft.LogPolicy ?? new PlayScenarioLogPolicy();
            policy.AllowedMessages = policy.AllowedMessages ?? new List<string>();
            var mode = new PopupField<string>("Error log policy", new List<string> { "strict", "log_only" }, policy.Mode == "log_only" ? 1 : 0)
            {
                name = "logPolicyMode",
            };
            mode.RegisterValueChangedCallback(evt =>
                edit(() =>
                {
                    draft.LogPolicy = policy;
                    policy.Mode = evt.newValue;
                })
            );
            options.Add(mode);
            var allowed = new Foldout { text = "Allowed exact error messages", name = "allowedMessages" };
            Action renderAllowed = null;
            renderAllowed = () =>
            {
                allowed.Clear();
                for (int index = 0; index < policy.AllowedMessages.Count; index++)
                {
                    int position = index;
                    var row = new VisualElement();
                    row.AddToClassList("scenario-row");
                    var message = new TextField("Message " + (index + 1))
                    {
                        name = "allowedMessage" + index,
                        value = policy.AllowedMessages[index],
                        isDelayed = true,
                    };
                    message.RegisterValueChangedCallback(evt =>
                        edit(() =>
                        {
                            draft.LogPolicy = policy;
                            policy.AllowedMessages[position] = evt.newValue;
                        })
                    );
                    row.Add(message);
                    row.Add(
                        new Button(() =>
                        {
                            edit(() =>
                            {
                                draft.LogPolicy = policy;
                                policy.AllowedMessages.RemoveAt(position);
                            });
                            renderAllowed();
                        })
                        {
                            text = "Remove",
                            name = "removeAllowedMessage" + index,
                        }
                    );
                    allowed.Add(row);
                }
                var add = new Button(() =>
                {
                    edit(() =>
                    {
                        draft.LogPolicy = policy;
                        policy.AllowedMessages.Add("");
                    });
                    renderAllowed();
                })
                {
                    text = "Add allowed message",
                    name = "addAllowedMessage",
                };
                add.SetEnabled(policy.AllowedMessages.Count < 32);
                allowed.Add(add);
            };
            renderAllowed();
            options.Add(allowed);
            options.Add(new Label("Strict fails on unexpected Error/Assert/Exception logs. Allowed messages match the complete message exactly."));
            var metrics = draft.Metrics ?? new PlayScenarioMetricsOptions();
            var metricSection = new Foldout { text = "Memory and runner diagnostics", name = "metricsOptions" };
            var enabled = new Toggle("Sample after cleanup") { name = "metricsEnabled", value = metrics.Enabled };
            var metricFields = new VisualElement();
            enabled.RegisterValueChangedCallback(evt =>
            {
                edit(() =>
                {
                    draft.Metrics = metrics;
                    metrics.Enabled = evt.newValue;
                });
                metricFields.SetEnabled(evt.newValue);
            });
            Integer(
                metricFields,
                "Warmup iterations",
                "metricsWarmup",
                metrics.WarmupIterations,
                value =>
                    edit(() =>
                    {
                        draft.Metrics = metrics;
                        metrics.WarmupIterations = value;
                    })
            );
            Integer(
                metricFields,
                "Consecutive increases",
                "metricsConsecutive",
                metrics.ConsecutiveIncreases,
                value =>
                    edit(() =>
                    {
                        draft.Metrics = metrics;
                        metrics.ConsecutiveIncreases = value;
                    })
            );
            Integer(
                metricFields,
                "Managed growth (bytes)",
                "metricsManagedGrowth",
                metrics.ManagedGrowthBytes,
                value =>
                    edit(() =>
                    {
                        draft.Metrics = metrics;
                        metrics.ManagedGrowthBytes = value;
                    })
            );
            Integer(
                metricFields,
                "Allocated growth (bytes)",
                "metricsAllocatedGrowth",
                metrics.AllocatedGrowthBytes,
                value =>
                    edit(() =>
                    {
                        draft.Metrics = metrics;
                        metrics.AllocatedGrowthBytes = value;
                    })
            );
            Integer(
                metricFields,
                "Object growth count",
                "metricsObjectGrowth",
                metrics.ObjectGrowthCount,
                value =>
                    edit(() =>
                    {
                        draft.Metrics = metrics;
                        metrics.ObjectGrowthCount = value;
                    })
            );
            metricFields.SetEnabled(metrics.Enabled);
            metricSection.Add(enabled);
            metricSection.Add(metricFields);
            metricSection.Add(
                new Label(
                    "One sample per successful iteration, after cleanup. Trends are diagnostic warnings and never prove a leak. Static and persistent state can accumulate across repeats."
                )
            );
            options.Add(metricSection);
            var screenshot = new Toggle("Screenshot on failure") { name = "screenshotOnFailure", value = draft.Diagnostics?.ScreenshotOnFailure ?? false };
            screenshot.RegisterValueChangedCallback(evt =>
                edit(() => draft.Diagnostics = new PlayScenarioDiagnosticsOptions { ScreenshotOnFailure = evt.newValue })
            );
            options.Add(screenshot);
            container.Add(options);
        }

        private static void Integer(VisualElement container, string label, string name, int value, Action<int> change)
        {
            var field = new IntegerField(label) { name = name, value = value };
            field.RegisterValueChangedCallback(evt => change(evt.newValue));
            container.Add(field);
        }
    }
}
