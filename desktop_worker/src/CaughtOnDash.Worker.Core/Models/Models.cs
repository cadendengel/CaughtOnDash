using System;
using System.Collections.Generic;
using Newtonsoft.Json;

namespace CaughtOnDash.Worker.Models
{
    public class WorkerConfig
    {
        public string WorkerId { get; set; } = "caden-desktop-1";
        public string WorkerName { get; set; } = "Caden Desktop";
        public string BackendUrl { get; set; } = "";
        public string ApiToken { get; set; } = "";

        /// <summary>"python" for real analysis, "placeholder" for the stub.</summary>
        /// <remarks>
        /// Defaults to the placeholder so a worker without a Python environment
        /// still runs. Switching is a config change, not a rebuild.
        /// </remarks>
        public string Analyzer { get; set; } = "placeholder";

        /// <summary>Interpreter to run the analyzer with -- point this at the venv.</summary>
        public string PythonExecutable { get; set; } = "python";

        /// <summary>Path to analyze.py, absolute or relative to the worker binary.</summary>
        public string AnalyzerScriptPath { get; set; } = "";

        public int AnalyzerTimeoutSeconds { get; set; } = 900;

        public bool IsConfigured => !string.IsNullOrWhiteSpace(BackendUrl) && !string.IsNullOrWhiteSpace(ApiToken);
    }

    // The backend serializes snake_case. Newtonsoft does not bridge snake_case to
    // PascalCase on its own, so every multi-word field needs an explicit name --
    // without it the property silently keeps its default (e.g. Guid.Empty).
    public class WorkerStatus
    {
        [JsonProperty("worker_id")]
        public string WorkerId { get; set; } = "";

        [JsonProperty("status")]
        public string Status { get; set; } = "offline";

        [JsonProperty("last_seen_at")]
        public DateTime? LastSeenAt { get; set; }

        [JsonProperty("current_job_id")]
        public string? CurrentJobId { get; set; }
    }

    public class JobDto
    {
        [JsonProperty("job_id")]
        public Guid JobId { get; set; }

        [JsonProperty("video_id")]
        public Guid VideoId { get; set; }

        [JsonProperty("title")]
        public string Title { get; set; } = "";

        [JsonProperty("description")]
        public string Description { get; set; } = "";

        [JsonProperty("video_url")]
        public string VideoUrl { get; set; } = "";

        [JsonProperty("created_at")]
        public DateTime CreatedAt { get; set; }

        [JsonProperty("analysis_status")]
        public string AnalysisStatus { get; set; } = "pending";
    }

    public class AnalysisResult
    {
        public string Summary { get; set; } = "";
        public List<string> Tags { get; set; } = new();
        public List<AnalysisEvent> Events { get; set; } = new();
        public Dictionary<string, object> Metadata { get; set; } = new();
    }

    public class AnalysisEvent
    {
        [JsonProperty("timestamp_seconds")]
        public float TimestampSeconds { get; set; }

        [JsonProperty("label")]
        public string Label { get; set; } = "";

        [JsonProperty("description")]
        public string Description { get; set; } = "";

        [JsonProperty("confidence")]
        public float Confidence { get; set; }
    }

    public class WorkerHeartbeat
    {
        public string WorkerId { get; set; } = "";
        public string WorkerName { get; set; } = "";
        public string Status { get; set; } = "idle";
        public string? CurrentJobId { get; set; }
        public string Stage { get; set; } = "";
        public int Progress { get; set; }
    }
}
