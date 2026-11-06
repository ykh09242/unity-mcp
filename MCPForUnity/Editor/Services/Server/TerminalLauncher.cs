using System;
using System.Collections.Generic;
using System.IO;
using System.Text;
using MCPForUnity.Editor.Helpers;
using UnityEngine;

namespace MCPForUnity.Editor.Services.Server
{
    /// <summary>
    /// Launches commands in platform-specific terminal windows.
    /// Supports macOS Terminal, Windows cmd, and Linux terminal emulators.
    /// </summary>
    public class TerminalLauncher : ILiteralTerminalLauncher
    {
        /// <inheritdoc/>
        public string GetProjectRootPath()
        {
            try
            {
                // Application.dataPath is ".../<Project>/Assets"
                return Path.GetFullPath(Path.Combine(Application.dataPath, ".."));
            }
            catch
            {
                return Application.dataPath;
            }
        }

        /// <summary>
        /// The shell only opens durable OS log handles and waits for the foreground child.
        /// Input values never become shell source: Windows expands encoded environment slots
        /// after metacharacter parsing; POSIX expands quoted environment slots without re-parsing.
        /// Managed output pumps cannot own these streams because an Editor domain reload would
        /// close their pipes while the managed server is still running.
        /// </summary>
        public System.Diagnostics.ProcessStartInfo CreateHeadlessProcessStartInfo(string fileName, IReadOnlyList<string> arguments, string logFilePath)
        {
            return CreateLiteralHeadlessProcessStartInfo(fileName, arguments, logFilePath,
#if UNITY_EDITOR_WIN
                true
#else
                false
#endif
            );
        }

        internal static System.Diagnostics.ProcessStartInfo CreateLiteralHeadlessProcessStartInfo(
            string fileName,
            IReadOnlyList<string> arguments,
            string logFilePath,
            bool windows
        )
        {
            if (string.IsNullOrWhiteSpace(fileName))
                throw new ArgumentException("Executable cannot be empty", nameof(fileName));
            if (arguments == null)
                throw new ArgumentNullException(nameof(arguments));
            if (string.IsNullOrWhiteSpace(logFilePath))
                throw new ArgumentException("Log file path cannot be empty", nameof(logFilePath));

            var info = new System.Diagnostics.ProcessStartInfo
            {
                FileName = windows ? Path.Combine(Environment.SystemDirectory, "cmd.exe") : "/bin/bash",
                UseShellExecute = false,
                CreateNoWindow = true,
                WindowStyle = System.Diagnostics.ProcessWindowStyle.Hidden,
            };
            // Raw path for Python's bounded writer; shell argument slots below are encoded.
            info.EnvironmentVariables["UNITY_MCP_LAUNCH_LOG"] = logFilePath;
            var command = new StringBuilder(windows ? "" : "exec ");
            long expandedLength = 32; // Include redirection and /d /v:on /s /c framing in the cmd limit.
            AddSlot("MCP_UNITY_EXECUTABLE", fileName);
            for (int i = 0; i < arguments.Count; i++)
            {
                command.Append(' ');
                AddSlot("MCP_UNITY_ARGUMENT_" + i, arguments[i]);
            }
            command.Append(windows ? " < NUL >> " : " < /dev/null >> ");
            AddSlot("MCP_UNITY_LAUNCH_LOG", logFilePath);
            command.Append(" 2>&1");
            if (windows && (expandedLength > 8191 || command.Length + 20 > 8191))
                throw new ArgumentException(
                    "The local server launch exceeds the Windows cmd.exe 8191-character limit. Shorten the executable, source or project path."
                );

            info.Arguments = windows ? "/d /v:on /s /c \"" + command + "\"" : "-c \"" + command.ToString().Replace("\"", "\\\"") + "\"";
            string logDirectory = Path.GetDirectoryName(logFilePath);
            if (!string.IsNullOrEmpty(logDirectory))
                Directory.CreateDirectory(logDirectory);
            return info;

            void AddSlot(string name, string value)
            {
                if (value == null || value.IndexOf('\0') >= 0)
                    throw new ArgumentException("Launch values must be non-null and cannot contain NUL.");
                string encoded = windows ? QuoteWindowsArgument(value) : value;
                info.EnvironmentVariables[name] = encoded;
                expandedLength += encoded.Length + 1L;
                command.Append(windows ? "!" + name + "!" : "\"$" + name + "\"");
            }
        }

        // CRT argument encoding, not cmd escaping. Delayed expansion keeps every encoded value
        // out of cmd's operator/percent-expansion passes, including quotes, !, %, & and newlines.
        internal static string QuoteWindowsArgument(string value)
        {
            var result = new StringBuilder("\"");
            int slashes = 0;
            foreach (char character in value)
            {
                if (character == '\\')
                {
                    slashes++;
                    continue;
                }
                if (character == '"')
                    result.Append('\\', slashes * 2 + 1).Append('"');
                else
                    result.Append('\\', slashes).Append(character);
                slashes = 0;
            }
            return result.Append('\\', slashes * 2).Append('"').ToString();
        }

        /// <inheritdoc/>
        public System.Diagnostics.ProcessStartInfo CreateHeadlessProcessStartInfo(string command, string logFilePath)
        {
            if (string.IsNullOrWhiteSpace(command))
                throw new ArgumentException("Command cannot be empty", nameof(command));
            if (string.IsNullOrWhiteSpace(logFilePath))
                throw new ArgumentException("Log file path cannot be empty", nameof(logFilePath));

            command = command.Replace("\r", "").Replace("\n", "");

            string logDir = Path.GetDirectoryName(logFilePath);
            if (!string.IsNullOrEmpty(logDir))
            {
                Directory.CreateDirectory(logDir);
            }

#if UNITY_EDITOR_WIN
            // cmd.exe /c "<command> < NUL >> "<log>" 2>&1"
            // The whole payload after /c is wrapped in one outer pair of quotes; cmd strips the
            // outermost quotes, so inner quotes around the log path survive for paths with spaces.
            // stdin is redirected from NUL because the Editor is a console-less GUI process: with
            // CreateNoWindow and no console handle, uvx.exe would inherit an invalid stdin and die
            // with "The handle is invalid. (os error 6)" before launching the server.
            string winRedirect = $"{command} < NUL >> \"{logFilePath}\" 2>&1";
            var info = new System.Diagnostics.ProcessStartInfo
            {
                FileName = "cmd.exe",
                Arguments = $"/c \"{winRedirect}\"",
                UseShellExecute = false,
                CreateNoWindow = true,
                WindowStyle = System.Diagnostics.ProcessWindowStyle.Hidden,
            };
#else
            // macOS/Linux: /bin/bash -c "<command> >> '<log>' 2>&1"
            // Single-quote the log path so spaces are preserved; the bash payload is wrapped in
            // double quotes for the .NET argument tokenizer (escape backslashes and double quotes).
            string singleQuotedLog = "'" + logFilePath.Replace("'", "'\\''") + "'";
            string bashPayload = $"{command} >> {singleQuotedLog} 2>&1";
            string escapedPayload = bashPayload.Replace("\\", "\\\\").Replace("\"", "\\\"");
            var info = new System.Diagnostics.ProcessStartInfo
            {
                FileName = "/bin/bash",
                Arguments = $"-c \"{escapedPayload}\"",
                UseShellExecute = false,
                CreateNoWindow = true,
                WindowStyle = System.Diagnostics.ProcessWindowStyle.Hidden,
            };
#endif
            info.EnvironmentVariables["UNITY_MCP_LAUNCH_LOG"] = logFilePath;
            return info;
        }

        /// <inheritdoc/>
        public System.Diagnostics.ProcessStartInfo CreateTerminalProcessStartInfo(string command)
        {
            if (string.IsNullOrWhiteSpace(command))
                throw new ArgumentException("Command cannot be empty", nameof(command));

            command = command.Replace("\r", "").Replace("\n", "");

#if UNITY_EDITOR_OSX
            // macOS: Avoid AppleScript (automation permission prompts). Use a .command script and open it.
            string scriptsDir = Path.Combine(GetProjectRootPath(), "Library", "MCPForUnity", "TerminalScripts");
            Directory.CreateDirectory(scriptsDir);
            string scriptPath = Path.Combine(scriptsDir, "mcp-terminal.command");
            File.WriteAllText(scriptPath, "#!/bin/bash\n" + "set -e\n" + "clear\n" + $"{command}\n");
            ExecPath.TryRun("/bin/chmod", $"+x \"{scriptPath}\"", Application.dataPath, out _, out _, 3000);
            return new System.Diagnostics.ProcessStartInfo
            {
                FileName = "/usr/bin/open",
                Arguments = $"-a Terminal \"{scriptPath}\"",
                UseShellExecute = false,
                CreateNoWindow = true,
            };
#elif UNITY_EDITOR_WIN
            // Windows: Avoid brittle nested-quote escaping by writing a .cmd script and starting it in a new window.
            string scriptsDir = Path.Combine(GetProjectRootPath(), "Library", "MCPForUnity", "TerminalScripts");
            Directory.CreateDirectory(scriptsDir);
            string scriptPath = Path.Combine(scriptsDir, "mcp-terminal.cmd");
            // Match cmd's code page to the UTF-8 script before reading non-ASCII paths.
            File.WriteAllText(scriptPath, "@echo off\r\n" + "chcp 65001 >nul\r\n" + "cls\r\n" + command + "\r\n", new System.Text.UTF8Encoding(false));
            return new System.Diagnostics.ProcessStartInfo
            {
                FileName = "cmd.exe",
                Arguments = $"/c start \"MCP Server\" cmd.exe /k \"{scriptPath}\"",
                UseShellExecute = false,
                CreateNoWindow = true,
            };
#else
            // Linux: Try common terminal emulators.
            // ProcessStartInfo passes the argument string directly to the terminal, so we only
            // need to escape for the double-quoted bash -c payload — no inner single quotes.
            string script = $"{command}; exec bash";
            string escapedScriptForArg = script.Replace("\\", "\\\\").Replace("\"", "\\\"");
            string bashCmdArgs = $"bash -c \"{escapedScriptForArg}\"";

            string[] terminals = { "gnome-terminal", "xterm", "konsole", "xfce4-terminal" };
            string terminalCmd = null;

            foreach (var term in terminals)
            {
                try
                {
                    var which = System.Diagnostics.Process.Start(
                        new System.Diagnostics.ProcessStartInfo
                        {
                            FileName = "which",
                            Arguments = term,
                            UseShellExecute = false,
                            RedirectStandardOutput = true,
                            CreateNoWindow = true,
                        }
                    );
                    which.WaitForExit(5000); // Wait for up to 5 seconds, the command is typically instantaneous
                    if (which.ExitCode == 0)
                    {
                        terminalCmd = term;
                        break;
                    }
                }
                catch { }
            }

            if (terminalCmd == null)
            {
                terminalCmd = "xterm"; // Fallback
            }

            // Different terminals have different argument formats
            string args;
            if (terminalCmd == "gnome-terminal")
            {
                args = $"-- {bashCmdArgs}";
            }
            else if (terminalCmd == "konsole")
            {
                args = $"-e {bashCmdArgs}";
            }
            else if (terminalCmd == "xfce4-terminal")
            {
                // xfce4-terminal expects -e "command string" or -e command arg
                args = $"--hold -e \"{bashCmdArgs.Replace("\"", "\\\"")}\"";
            }
            else // xterm and others
            {
                args = $"-hold -e {bashCmdArgs}";
            }

            return new System.Diagnostics.ProcessStartInfo
            {
                FileName = terminalCmd,
                Arguments = args,
                UseShellExecute = false,
                CreateNoWindow = true,
            };
#endif
        }
    }
}
