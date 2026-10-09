using System;
using System.Collections.Generic;
using System.IO;
using System.Text;
using System.Threading.Tasks;
using MCPForUnity.Editor.Security;
using FakeProcess = System.Diagnostics.Process;

internal static class LifecycleHarness
{
    private static int failures;
    private static int checks;

    public static int Main()
    {
        var actions = new (string Name, Action Action)[]
        {
            ("linux availability", () => LinuxSecretToolKeyStore.IsAvailable()),
            ("linux get", () => new LinuxSecretToolKeyStore().TryGet("fixture", out _)),
            ("linux set", () => new LinuxSecretToolKeyStore().Set("fixture", "fixture-value")),
            ("linux delete", () => new LinuxSecretToolKeyStore().Delete("fixture")),
            ("mac get", () => new MacKeychainKeyStore().TryGet("fixture", out _)),
            ("mac set", () => new MacKeychainKeyStore().Set("fixture", "fixture-value")),
            ("mac delete", () => new MacKeychainKeyStore().Delete("fixture")),
        };
        foreach (var action in actions)
        {
            Check(
                action.Name + " reaps timed-out children",
                () =>
                {
                    FakeProcess.Completes = false;
                    for (int i = 0; i < 100; i++)
                        action.Action();
                    int alive = FakeProcess.All.FindAll(p => p.Running).Count;
                    Console.WriteLine("TIMEOUT " + action.Name + ": alive=" + alive);
                    Require(alive == 0, "Timed-out child processes survived disposal");
                    Require(FakeProcess.All.TrueForAll(p => p.Disposed), "Process handle was not disposed");
                    Require(FakeProcess.All.TrueForAll(p => p.StreamsDisposed), "Exposed streams were not disposed");
                }
            );
        }
        Check(
            "linux success preserves value, exit checks and stdin",
            () =>
            {
                FakeProcess.Completes = true;
                FakeProcess.Output = " fixture-value \r\n";
                var store = new LinuxSecretToolKeyStore();
                Require(store.TryGet("fixture", out string value) && value == " fixture-value ", "Value changed");
                store.Set("fixture", "fixture-input");
                Require(FakeProcess.All[^1].InputValue == "fixture-input", "stdin changed");
                Require(FakeProcess.All.TrueForAll(p => p.Kills == 0 && p.Disposed), "Successful child was killed or not disposed");
                FakeProcess.ExitStatus = 9;
                Require(!store.TryGet("fixture", out _), "Nonzero exit was accepted");
            }
        );
        Check(
            "mac success preserves value and structured arguments",
            () =>
            {
                FakeProcess.Completes = true;
                FakeProcess.Output = " fixture-value \n";
                var store = new MacKeychainKeyStore();
                Require(store.TryGet("fixture", out string value) && value == " fixture-value ", "Value changed");
                store.Set("fixture", "fixture-input");
                Require(FakeProcess.All[^1].StartInfo.ArgumentList.Contains("fixture-input"), "Argument value changed");
                Require(FakeProcess.All.TrueForAll(p => p.Kills == 0 && p.Disposed), "Successful child was killed or not disposed");
                FakeProcess.ExitStatus = 9;
                Require(!store.TryGet("fixture", out _), "Nonzero exit was accepted");
            }
        );
        Check(
            "read paths drain stdout and stderr asynchronously",
            () =>
            {
                FakeProcess.Completes = true;
                FakeProcess.ForbidSynchronousRead = true;
                Require(new LinuxSecretToolKeyStore().TryGet("fixture", out _), "Linux used blocking pipe read");
                Require(new MacKeychainKeyStore().TryGet("fixture", out _), "macOS used blocking pipe read");
                Require(FakeProcess.All.TrueForAll(p => p.AsyncReads == 2), "Both pipes were not drained");
            }
        );
        Check(
            "completed commands release every exposed stream",
            () =>
            {
                foreach (var action in actions)
                    action.Action();
                Require(FakeProcess.All.TrueForAll(p => p.StreamsDisposed), "Completed command retained an exposed stream");
            }
        );
        Check(
            "pending output and input tasks finish when timeout closes pipes",
            () =>
            {
                FakeProcess.Completes = false;
                FakeProcess.PendingReads = true;
                FakeProcess.PendingWrite = true;
                new LinuxSecretToolKeyStore().Set("fixture", "fixture-input");
                var process = FakeProcess.All[^1];
                Require(!process.Running && process.Disposed && process.StreamsDisposed, "Timed-out IO retained owned resources");
                Require(process.IO.TrueForAll(task => task.IsCompleted), "Pipe tasks remained pending after disposal");
            }
        );
        foreach (string stage in new[] { "start", "getter", "read", "write", "dispose" })
            Check(
                stage + " exception releases all acquired resources",
                () =>
                {
                    FakeProcess.FailureStage = stage;
                    new LinuxSecretToolKeyStore().Set("fixture", "fixture-input");
                    var process = FakeProcess.All[^1];
                    Require(!process.Running && process.Disposed && process.StreamsDisposed, "Exception bypassed owned resource cleanup");
                }
            );
        Console.WriteLine("RESULT: " + (checks - failures) + "/" + checks + " passed");
        return failures == 0 ? 0 : 1;
    }

    private static void Check(string name, Action action)
    {
        checks++;
        FakeProcess.All.Clear();
        FakeProcess.Completes = true;
        FakeProcess.ExitStatus = 0;
        FakeProcess.Output = "fixture-value\n";
        FakeProcess.ForbidSynchronousRead = false;
        FakeProcess.PendingReads = false;
        FakeProcess.PendingWrite = false;
        FakeProcess.FailureStage = null;
        try
        {
            action();
            Console.WriteLine("PASS: " + name);
        }
        catch (Exception ex)
        {
            failures++;
            Console.WriteLine("FAIL: " + name + " -- " + ex.Message);
        }
    }

    private static void Require(bool condition, string message)
    {
        if (!condition)
            throw new InvalidOperationException(message);
    }
}

// Only the OS process boundary is modeled. Both complete key-store sources execute.
namespace System.Diagnostics
{
    internal sealed class ProcessStartInfo
    {
        public ProcessStartInfo() { }

        public ProcessStartInfo(string fileName)
        {
            FileName = fileName;
        }

        public string FileName { get; set; }
        public bool UseShellExecute { get; set; }
        public bool CreateNoWindow { get; set; }
        public bool RedirectStandardOutput { get; set; }
        public bool RedirectStandardError { get; set; }
        public bool RedirectStandardInput { get; set; }
        public List<string> ArgumentList { get; } = new();
    }

    internal sealed class Process : IDisposable
    {
        public static readonly List<Process> All = new();
        public static bool Completes;
        public static bool ForbidSynchronousRead;
        public static bool PendingReads;
        public static bool PendingWrite;
        public static string FailureStage;
        public static int ExitStatus;
        public static string Output;
        public ProcessStartInfo StartInfo { get; set; }
        public bool Running;
        public bool Disposed;
        public int Kills;
        public int AsyncReads;
        public readonly List<Task> IO = new();
        private readonly List<Reader> readers = new();
        private Writer writer;
        public string InputValue => writer?.Value.ToString();
        public bool StreamsDisposed => readers.TrueForAll(reader => reader.Closed) && (writer == null || writer.Closed && writer.Pipe.Closed);
        public StreamWriter StandardInput => writer ??= new Writer(this);
        public StreamReader StandardOutput => GetReader(Output);
        public StreamReader StandardError => FailureStage == "getter" ? throw new IOException("fixture getter failure") : GetReader("");
        public bool HasExited => !Running;
        public int ExitCode => Running ? throw new InvalidOperationException("Process has not exited") : ExitStatus;

        public bool Start()
        {
            All.Add(this);
            if (FailureStage == "start")
                throw new IOException("fixture start failure");
            Running = true;
            return true;
        }

        public static Process Start(ProcessStartInfo info)
        {
            var p = new Process { StartInfo = info };
            p.Start();
            return p;
        }

        public bool WaitForExit(int timeout)
        {
            if (Completes || Kills != 0)
                Running = false;
            return !Running;
        }

        public void Kill()
        {
            Kills++;
            Running = false;
        }

        public void Dispose()
        {
            Disposed = true;
        }

        private Reader GetReader(string value)
        {
            var reader = new Reader(this, value);
            readers.Add(reader);
            return reader;
        }

        private sealed class Reader : StreamReader
        {
            private readonly Process owner;
            private readonly string value;
            private readonly TaskCompletionSource<string> pending = new(TaskCreationOptions.RunContinuationsAsynchronously);
            public bool Closed;

            public Reader(Process owner, string value)
                : base(new MemoryStream(Encoding.UTF8.GetBytes(value)))
            {
                this.owner = owner;
                this.value = value;
            }

            public override string ReadToEnd()
            {
                if (ForbidSynchronousRead)
                    throw new IOException("Synchronous pipe read would block before the timeout");
                return value;
            }

            public override Task<string> ReadToEndAsync()
            {
                owner.AsyncReads++;
                if (FailureStage == "read")
                    throw new IOException("fixture read failure");
                var task = PendingReads ? pending.Task : Task.FromResult(value);
                owner.IO.Add(task);
                return task;
            }

            protected override void Dispose(bool disposing)
            {
                Closed = true;
                if (PendingReads)
                    pending.TrySetException(new IOException("fixture read pipe closed"));
                base.Dispose(disposing);
                if (FailureStage == "dispose")
                    throw new IOException("fixture reader disposal failure");
            }
        }

        private sealed class Writer : StreamWriter
        {
            private readonly Process owner;
            private readonly TaskCompletionSource<bool> pending = new(TaskCreationOptions.RunContinuationsAsynchronously);
            public readonly StringBuilder Value = new();
            public readonly PipeStream Pipe;
            public bool Closed;

            public Writer(Process owner)
                : this(owner, new PipeStream()) { }

            private Writer(Process owner, PipeStream pipe)
                : base(pipe)
            {
                this.owner = owner;
                Pipe = pipe;
                pipe.OnClose = () =>
                {
                    if (PendingWrite)
                        pending.TrySetException(new IOException("fixture write pipe closed"));
                };
            }

            public override void Write(string value)
            {
                Value.Append(value);
            }

            public override Task WriteAsync(string value)
            {
                Value.Append(value);
                var task =
                    FailureStage == "write" ? Task.FromException(new IOException("fixture write failure"))
                    : PendingWrite ? pending.Task
                    : Task.CompletedTask;
                owner.IO.Add(task);
                return task;
            }

            public override Task FlushAsync() => Task.CompletedTask;

            protected override void Dispose(bool disposing)
            {
                Closed = true;
                base.Dispose(disposing);
            }
        }

        private sealed class PipeStream : MemoryStream
        {
            public bool Closed;
            public Action OnClose;

            protected override void Dispose(bool disposing)
            {
                Closed = true;
                OnClose?.Invoke();
                base.Dispose(disposing);
            }
        }
    }
}
