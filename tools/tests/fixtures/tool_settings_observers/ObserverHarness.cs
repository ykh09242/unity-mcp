using System;
using System.Collections.Concurrent;
using System.Reflection;
using System.Threading;
using System.Threading.Tasks;
using MCPForUnity.Editor.Services;
using Newtonsoft.Json.Linq;

namespace MCPForUnity.Editor.Helpers
{
    internal static class McpLog
    {
        internal static readonly ConcurrentQueue<string> Warnings = new();
        internal static bool Throw;

        public static void Warn(string message)
        {
            Warnings.Enqueue(message);
            if (Throw)
                throw new InvalidOperationException("owned logging observer failure");
        }
    }
}

internal static class ObserverHarness
{
    private static JArray Tools => new(new JObject { ["name"] = "owned_probe" });
    private static JObject Enabled => new() { ["owned_probe"] = true };

    private static async Task Main(string[] args)
    {
        if (args[0] == "transitions")
            await Transitions();
        else
            await Registration(args[0] == "pending");
        foreach (string warning in MCPForUnity.Editor.Helpers.McpLog.Warnings)
            Require(!warning.Contains("owned-sensitive-marker"), "Observer diagnostics must not expose exception messages.");
        Console.WriteLine("PASS " + args[0]);
    }

    private static async Task Transitions()
    {
        var state = new ToolSettingsState();
        var owner = new object();
        int observed = 0;
        state.StatusChanged += () => throw new InvalidOperationException("owned-sensitive-marker");
        state.StatusChanged += () => observed++;
        state.Capture(Tools, Enabled);
        state.NotifyToolSettingsChanged();
        state.Capture(Tools, Enabled);
        state.SetConnection(owner, "owned-session", true, () => Task.CompletedTask);
        var first = state.BeginAttempt(owner);
        state.MarkSent(owner, first);
        Require(state.AcceptReceipt(owner, Receipt(first)), "A throwing observer must not reject a valid receipt.");
        state.NotifyToolSettingsChanged();
        state.Capture(Tools, Enabled);
        var failed = state.BeginAttempt(owner);
        state.FailAttempt(owner, failed, "send_failed", "owned", null);
        await state.RetryToolSettingsAsync();
        state.PreparationFailed("owned");
        state.PreparationFailed(owner, state.Status, nameof(InvalidOperationException));
        state.Disconnect(owner);
        Require(observed == 15, "Every transition must reach the remaining subscriber; observed " + observed + ".");
        Require(MCPForUnity.Editor.Helpers.McpLog.Warnings.Count == observed, "Every failed observer should have one diagnostic.");
        MCPForUnity.Editor.Helpers.McpLog.Throw = true;
        state.NotifyToolSettingsChanged();
        Require(observed == 16, "A failing diagnostic sink must not replace the observer failure.");
    }

    private static async Task Registration(bool holdCapture)
    {
        var state = new ToolSettingsState();
        var captured = new TaskCompletionSource<ToolSettingsSnapshot>(TaskCreationOptions.RunContinuationsAsynchronously);
        using var registration = new ToolSettingsRegistration(
            state,
            new object(),
            "owned-session",
            true,
            _ => holdCapture ? captured.Task : Task.FromResult(state.Capture(Tools, Enabled)),
            (_, _) => Task.CompletedTask,
            CancellationToken.None,
            TimeSpan.FromSeconds(5)
        );
        Task request = registration.RequestAsync();
        int disconnected = 0;
        Action throwing = () =>
        {
            if (!state.Status.IsConnected)
                throw new InvalidOperationException("owned-sensitive-marker");
        };
        state.StatusChanged += throwing;
        state.StatusChanged += () =>
        {
            if (!state.Status.IsConnected)
                disconnected++;
        };
        registration.Dispose();
        Require(disconnected == 1, "The throwing disconnected observer must not skip later subscribers.");
        var lifetime = (CancellationTokenSource)
            typeof(ToolSettingsRegistration).GetField("_lifetime", BindingFlags.Instance | BindingFlags.NonPublic).GetValue(registration);
        bool disposed = false;
        try
        {
            _ = lifetime.Token;
        }
        catch (ObjectDisposedException)
        {
            disposed = true;
        }
        Require(disposed, "Registration disposal must release its lifetime after notifying observers.");
        if (holdCapture)
        {
            Require(!request.IsCompleted && !registration.Completion.IsCompleted, "Unsettled capture must remain owned after disposal.");
            captured.SetResult(null);
        }
        await request.WaitAsync(TimeSpan.FromSeconds(5));
        Require(registration.Completion.IsCompletedSuccessfully, "Actual registration work must settle without observer faults.");
        state.StatusChanged -= throwing;
        ToolSettingsSnapshot replacementSnapshot = null;
        using var replacement = new ToolSettingsRegistration(
            state,
            new object(),
            "owned-session",
            true,
            _ => Task.FromResult(replacementSnapshot = state.Capture(Tools, Enabled)),
            (_, _) => Task.CompletedTask,
            CancellationToken.None,
            TimeSpan.FromSeconds(5)
        );
        Task restarted = replacement.RequestAsync();
        Require(replacement.HandleReceipt(Receipt(replacementSnapshot)), "The new owner must accept its own receipt.");
        await restarted.WaitAsync(TimeSpan.FromSeconds(5));
        Require(state.Status.Stage == "applied", "A replacement registration must remain usable after actual cleanup.");
    }

    private static JObject Receipt(ToolSettingsSnapshot snapshot) =>
        new()
        {
            ["type"] = "tool_settings_result",
            ["session_id"] = "owned-session",
            ["settings_epoch"] = snapshot.Epoch,
            ["desired_revision"] = snapshot.Revision,
            ["status"] = "applied",
            ["catalog_revision"] = snapshot.Revision,
            ["enabled_tool_count"] = 1,
        };

    private static void Require(bool condition, string message)
    {
        if (!condition)
            throw new Exception(message);
    }
}
