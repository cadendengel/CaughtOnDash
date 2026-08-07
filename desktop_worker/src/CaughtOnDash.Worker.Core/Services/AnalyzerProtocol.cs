using System;
using System.Collections.Generic;
using CaughtOnDash.Worker.Models;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;

namespace CaughtOnDash.Worker.Services
{
    /// <summary>
    /// Parses the analyzer's JSON Lines protocol.
    /// </summary>
    /// <remarks>
    /// Kept separate from PythonAnalyzer so the parsing rules can be tested
    /// without spawning a process or requiring a Python environment.
    /// </remarks>
    public static class AnalyzerProtocol
    {
        public abstract class Line { }

        public sealed class ProgressLine : Line
        {
            public string Stage { get; init; } = "";
            public int Progress { get; init; }
        }

        public sealed class ResultLine : Line
        {
            public AnalysisResult Result { get; init; } = new();
        }

        /// <summary>
        /// Parse one line. Returns null for anything that is not a protocol
        /// message -- blank lines and stray output are ignored rather than
        /// failing the job, since a library on the Python side may print.
        /// </summary>
        public static Line? Parse(string? line)
        {
            if (string.IsNullOrWhiteSpace(line))
            {
                return null;
            }

            var trimmed = line.Trim();
            if (!trimmed.StartsWith("{") || !trimmed.EndsWith("}"))
            {
                return null;
            }

            JObject payload;
            try
            {
                payload = JObject.Parse(trimmed);
            }
            catch (JsonException)
            {
                return null;
            }

            var type = payload.Value<string>("type");

            if (string.Equals(type, "progress", StringComparison.Ordinal))
            {
                return new ProgressLine
                {
                    Stage = payload.Value<string>("stage") ?? "analyzing",
                    Progress = Math.Clamp(payload.Value<int?>("progress") ?? 0, 0, 100),
                };
            }

            if (string.Equals(type, "result", StringComparison.Ordinal))
            {
                return new ResultLine
                {
                    Result = new AnalysisResult
                    {
                        Summary = payload.Value<string>("summary") ?? "",
                        Tags = payload["tags"]?.ToObject<List<string>>() ?? new List<string>(),
                        Events = payload["events"]?.ToObject<List<AnalysisEvent>>() ?? new List<AnalysisEvent>(),
                        Metadata = payload["metadata"]?.ToObject<Dictionary<string, object>>() ?? new Dictionary<string, object>(),
                    }
                };
            }

            return null;
        }

        /// <summary>
        /// Turn an analyzer exit code into an explanation. The analyzer uses
        /// distinct codes so the worker can say what went wrong rather than
        /// just "it failed".
        /// </summary>
        public static string DescribeExitCode(int exitCode, string stderr)
        {
            var detail = string.IsNullOrWhiteSpace(stderr) ? "" : $" {stderr.Trim()}";

            return exitCode switch
            {
                0 => "Analyzer exited successfully but never produced a result line." + detail,
                2 => "Analyzer could not find the video file." + detail,
                3 => "Analyzer is missing a Python dependency. Install the analyzer requirements." + detail,
                4 => "Analyzer could not read the video. It may be corrupt or in an unsupported format." + detail,
                _ => $"Analyzer failed with exit code {exitCode}.{detail}",
            };
        }
    }
}
