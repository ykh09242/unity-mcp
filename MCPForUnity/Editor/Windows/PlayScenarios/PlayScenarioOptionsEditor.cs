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
            RenderTags(container, draft, edit);
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
            var diagnostics = draft.Diagnostics ?? new PlayScenarioDiagnosticsOptions();
            var screenshot = new Toggle("Screenshot on failure") { name = "screenshotOnFailure", value = diagnostics.ScreenshotOnFailure };
            screenshot.RegisterValueChangedCallback(evt =>
                edit(() =>
                {
                    draft.Diagnostics = diagnostics;
                    diagnostics.ScreenshotOnFailure = evt.newValue;
                })
            );
            options.Add(screenshot);
            var timeline = new Toggle("Record state change timeline") { name = "recordTimeline", value = diagnostics.RecordTimeline };
            timeline.RegisterValueChangedCallback(evt =>
                edit(() =>
                {
                    draft.Diagnostics = diagnostics;
                    diagnostics.RecordTimeline = evt.newValue;
                })
            );
            options.Add(timeline);
            options.Add(new Label("Timeline stores the last 128 changes with bounded details. Repeated unchanged observations do not add events."));
            RenderQueryBudget(options, draft, edit);
            RenderResources(options, draft, edit);
            container.Add(options);
        }

        private static void RenderQueryBudget(VisualElement container, PlayScenarioDefinition draft, Action<Action> edit)
        {
            var budget = draft.QueryBudget ?? new PlayScenarioQueryBudgetOptions();
            var section = new Foldout { name = "queryBudgetOptions", text = "Target query regression budget" };
            var enabled = new Toggle("Enforce query budget") { name = "queryBudgetEnabled", value = budget.Enabled };
            var fields = new VisualElement { name = "queryBudgetFields" };
            enabled.RegisterValueChangedCallback(evt =>
            {
                edit(() =>
                {
                    draft.QueryBudget = budget;
                    budget.Enabled = evt.newValue;
                });
                fields.SetEnabled(evt.newValue);
            });
            Integer(
                fields,
                "Maximum target searches",
                "queryMaxTargetSearches",
                budget.MaxTargetSearches,
                value =>
                    edit(() =>
                    {
                        draft.QueryBudget = budget;
                        budget.MaxTargetSearches = value;
                    })
            );
            Integer(
                fields,
                "Maximum hierarchy visits",
                "queryMaxHierarchyVisits",
                budget.MaxHierarchyVisits,
                value =>
                    edit(() =>
                    {
                        draft.QueryBudget = budget;
                        budget.MaxHierarchyVisits = value;
                    })
            );
            fields.SetEnabled(budget.Enabled);
            section.Add(enabled);
            section.Add(fields);
            section.Add(
                new Label(
                    "Actual target evaluations are counted even when enforcement is off. Limits: searches 0–1000000; visits 0–10000000. Zero is a strict budget."
                )
            );
            container.Add(section);
        }

        private static void RenderTags(VisualElement container, PlayScenarioDefinition draft, Action<Action> edit)
        {
            var tags = new Foldout
            {
                text = "Scenario tags",
                name = "scenarioTags",
                value = true,
            };
            Action render = null;
            render = () =>
            {
                tags.Clear();
                for (int index = 0; index < draft.Tags.Count; index++)
                {
                    int position = index;
                    var row = new VisualElement();
                    row.AddToClassList("scenario-row");
                    var field = new TextField("Tag " + (index + 1)) { name = "scenarioTag" + index, value = draft.Tags[index] };
                    field.RegisterValueChangedCallback(evt => edit(() => draft.Tags[position] = evt.newValue));
                    row.Add(field);
                    row.Add(
                        new Button(() =>
                        {
                            edit(() => draft.Tags.RemoveAt(position));
                            render();
                        })
                        {
                            name = "removeScenarioTag" + index,
                            text = "Remove",
                        }
                    );
                    tags.Add(row);
                }
                var add = new Button(() =>
                {
                    edit(() => draft.Tags.Add(""));
                    render();
                })
                {
                    name = "addScenarioTag",
                    text = "Add tag",
                };
                add.SetEnabled(draft.Tags.Count < 16);
                tags.Add(add);
                tags.Add(new Label("Up to 16 unique lowercase tags. Suites select saved scenarios matching any requested tag."));
            };
            render();
            container.Add(tags);
        }

        private static void RenderResources(VisualElement container, PlayScenarioDefinition draft, Action<Action> edit)
        {
            var resources = draft.Resources ?? new PlayScenarioResourceOptions();
            var section = new Foldout { name = "resourceOptions", text = "Registered resource release assertions" };
            var enabled = new Toggle("Assert release after cleanup") { name = "resourcesEnabled", value = resources.Enabled };
            var fields = new VisualElement();
            enabled.RegisterValueChangedCallback(evt =>
            {
                edit(() =>
                {
                    draft.Resources = resources;
                    resources.Enabled = evt.newValue;
                });
                fields.SetEnabled(evt.newValue);
            });
            Integer(
                fields,
                "Remaining runtime ScriptableObjects",
                "resourcesScriptableObjects",
                resources.MaxScriptableObjects,
                value =>
                    edit(() =>
                    {
                        draft.Resources = resources;
                        resources.MaxScriptableObjects = value;
                    })
            );
            Integer(
                fields,
                "Remaining subscriptions",
                "resourcesSubscriptions",
                resources.MaxSubscriptions,
                value =>
                    edit(() =>
                    {
                        draft.Resources = resources;
                        resources.MaxSubscriptions = value;
                    })
            );
            Integer(
                fields,
                "Remaining handles",
                "resourcesHandles",
                resources.MaxHandles,
                value =>
                    edit(() =>
                    {
                        draft.Resources = resources;
                        resources.MaxHandles = value;
                    })
            );
            fields.SetEnabled(resources.Enabled);
            section.Add(enabled);
            section.Add(fields);
            section.Add(
                new Label(
                    "Opt-in registrations only; limits 0–4096. Checks compare new resource identities after cleanup, including failed runs. No global scan or forced garbage collection."
                )
            );
            container.Add(section);
        }

        private static void Integer(VisualElement container, string label, string name, int value, Action<int> change)
        {
            var field = new IntegerField(label) { name = name, value = value };
            field.RegisterValueChangedCallback(evt => change(evt.newValue));
            container.Add(field);
        }
    }
}
