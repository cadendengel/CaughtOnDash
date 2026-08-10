using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Text;
using System.Threading;
using System.Threading.Tasks;
using CaughtOnDash.Worker.Models;

namespace CaughtOnDash.Worker.Services
{
    /// <summary>
    /// Runs the Python analyzer as a subprocess and reads its results back.
    /// </summary>
    /// <remarks>
    /// A subprocess rather than a long-lived service: no port to manage, no
    /// daemon to supervise, and a crashed analysis cannot wedge the worker.
    /// The cost is process startup per job, which is negligible next to the
    /// analysis itself.
    /// </remarks>
    public class PythonAnalyzer : IAnalyzer
    {
        private readonly WorkerConfig _config;

        public PythonAnalyzer(WorkerConfig config)
        {
            _config = config;
        }

        public async Task<AnalysisResult> AnalyzeAsync(
            string videoPath,
            IProgress<(string stage, int progress)> progress,
            CancellationToken cancellationToken)
        {
            var scriptPath = ResolveScriptPath();
            if (!File.Exists(scriptPath))
            {
                throw new InvalidOperationException(
                    $"Analyzer script not found at '{scriptPath}'. Set AnalyzerScriptPath in " +
                    "appsettings.json to the location of analyze.py.");
            }

            var startInfo = new ProcessStartInfo
            {
                FileName = _config.PythonExecutable,
                RedirectStandardOutput = true,
                RedirectStandardError = true,
                UseShellExecute = false,
                CreateNoWindow = true,
                WorkingDirectory = Path.GetDirectoryName(scriptPath) ?? Environment.CurrentDirectory,
            };
            startInfo.ArgumentList.Add(scriptPath);
            startInfo.ArgumentList.Add(videoPath);

            using var process = new Process { StartInfo = startInfo, EnableRaisingEvents = true };

            var stderr = new StringBuilder();
            AnalysisResult? result = null;

            try
            {
                process.Start();
            }
            catch (Exception ex)
            {
                throw new InvalidOperationException(
                    $"Could not start the Python analyzer using '{_config.PythonExecutable}'. " +
                    $"Check PythonExecutable in appsettings.json. ({ex.Message})", ex);
            }

            // Read stderr concurrently: if the analyzer writes enough to fill the
            // pipe buffer while we are only draining stdout, it blocks forever.
            var stderrTask = Task.Run(async () =>
            {
                string? line;
                while ((line = await process.StandardError.ReadLineAsync()) != null)
                {
                    stderr.AppendLine(line);
                    Logger.Log($"[analyzer] {line}");
                }
            }, CancellationToken.None);

            using var timeoutSource = new CancellationTokenSource(
                TimeSpan.FromSeconds(Math.Max(30, _config.AnalyzerTimeoutSeconds)));
            using var linked = CancellationTokenSource.CreateLinkedTokenSource(
                cancellationToken, timeoutSource.Token);

            try
            {
                string? line;
                while ((line = await process.StandardOutput.ReadLineAsync(linked.Token)) != null)
                {
                    switch (AnalyzerProtocol.Parse(line))
                    {
                        case AnalyzerProtocol.ProgressLine p:
                            progress?.Report((p.Stage, p.Progress));
                            break;
                        case AnalyzerProtocol.ResultLine r:
                            result = r.Result;
                            break;
                    }
                }

                await process.WaitForExitAsync(linked.Token);
                await stderrTask;
            }
            catch (OperationCanceledException)
            {
                KillProcessTree(process);

                if (timeoutSource.IsCancellationRequested && !cancellationToken.IsCancellationRequested)
                {
                    throw new TimeoutException(
                        $"Analyzer exceeded its {_config.AnalyzerTimeoutSeconds}s timeout and was stopped.");
                }

                throw;
            }

            if (process.ExitCode != 0 || result == null)
            {
                throw new InvalidOperationException(
                    AnalyzerProtocol.DescribeExitCode(process.ExitCode, stderr.ToString()));
            }

            return result;
        }

        /// <summary>
        /// Run <c>analyze.py --version</c> and return what it prints. analyze.py
        /// is the single source of truth for the analyzer version; the worker
        /// asks it rather than hardcoding a value that could drift.
        /// </summary>
        public async Task<string> GetVersionAsync(CancellationToken cancellationToken = default)
        {
            var scriptPath = ResolveScriptPath();
            if (!File.Exists(scriptPath))
            {
                throw new InvalidOperationException(
                    $"Analyzer script not found at '{scriptPath}'. Set AnalyzerScriptPath in " +
                    "appsettings.json to the location of analyze.py.");
            }

            var startInfo = new ProcessStartInfo
            {
                FileName = _config.PythonExecutable,
                RedirectStandardOutput = true,
                RedirectStandardError = true,
                UseShellExecute = false,
                CreateNoWindow = true,
                WorkingDirectory = Path.GetDirectoryName(scriptPath) ?? Environment.CurrentDirectory,
            };
            startInfo.ArgumentList.Add(scriptPath);
            startInfo.ArgumentList.Add("--version");

            using var process = new Process { StartInfo = startInfo, EnableRaisingEvents = true };
            try
            {
                process.Start();
            }
            catch (Exception ex)
            {
                throw new InvalidOperationException(
                    $"Could not run the Python analyzer using '{_config.PythonExecutable}'. " +
                    $"Check PythonExecutable in appsettings.json. ({ex.Message})", ex);
            }

            var stdout = await process.StandardOutput.ReadToEndAsync(cancellationToken);
            await process.WaitForExitAsync(cancellationToken);

            var version = stdout.Trim();
            if (string.IsNullOrEmpty(version))
            {
                throw new InvalidOperationException("analyze.py --version produced no output.");
            }
            return version;
        }

        private string ResolveScriptPath()
        {
            var configured = _config.AnalyzerScriptPath;
            if (string.IsNullOrWhiteSpace(configured))
            {
                configured = Path.Combine("analyzer", "analyze.py");
            }

            return Path.IsPathRooted(configured)
                ? configured
                : Path.GetFullPath(Path.Combine(AppContext.BaseDirectory, configured));
        }

        /// <summary>
        /// Kill the analyzer and anything it spawned. Without entireProcessTree,
        /// a cancelled job can leave orphaned Python holding the GPU or the file.
        /// </summary>
        private static void KillProcessTree(Process process)
        {
            try
            {
                if (!process.HasExited)
                {
                    process.Kill(entireProcessTree: true);
                }
            }
            catch (Exception ex)
            {
                Logger.Log($"Failed to stop the analyzer process: {ex.Message}", Logger.LogLevel.Warning);
            }
        }
    }
}
