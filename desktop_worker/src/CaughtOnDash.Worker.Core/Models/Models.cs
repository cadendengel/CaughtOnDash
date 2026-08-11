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

    /// <summary>A row in the review or run queue.</summary>
    /// <remarks>
    /// Carries the context needed to decide whether to spend compute on a
    /// video: how long it is, whether it has been analyzed before, and what
    /// the last attempt concluded.
    /// </remarks>
    public class QueueEntry
    {
        [JsonProperty("video_id")]
        public Guid VideoId { get; set; }

        [JsonProperty("title")]
        public string Title { get; set; } = "";

        [JsonProperty("description")]
        public string Description { get; set; } = "";

        [JsonProperty("video_url")]
        public string VideoUrl { get; set; } = "";

        [JsonProperty("thumbnail_url")]
        public string ThumbnailUrl { get; set; } = "";

        [JsonProperty("duration_seconds")]
        public int DurationSeconds { get; set; }

        [JsonProperty("owner_clerk_user_id")]
        public string OwnerClerkUserId { get; set; } = "";

        [JsonProperty("created_at")]
        public DateTime CreatedAt { get; set; }

        [JsonProperty("approval_status")]
        public string ApprovalStatus { get; set; } = "";

        [JsonProperty("analysis_status")]
        public string AnalysisStatus { get; set; } = "";

        [JsonProperty("analysis_priority")]
        public int AnalysisPriority { get; set; }

        [JsonProperty("attempt_number")]
        public int AttemptNumber { get; set; } = 1;

        [JsonProperty("previous_attempts")]
        public int PreviousAttempts { get; set; }

        [JsonProperty("last_result")]
        public QueueEntryLastResult? LastResult { get; set; }

        /// <summary>Duration as m:ss, or a dash when it is not known yet.</summary>
        public string DurationDisplay =>
            DurationSeconds > 0
                ? $"{DurationSeconds / 60}:{DurationSeconds % 60:00}"
                : "--";

        /// <summary>"1st run" / "3rd run", so a returning video stands out.
        ///
        /// Said "review" until the approve/reject vocabulary was dropped, where
        /// it was the last survivor of the word and meant something else again:
        /// an analysis attempt, not a human decision.</summary>
        public string AttemptDisplay => PreviousAttempts == 0
            ? "1st run"
            : $"{Ordinal(AttemptNumber)} run";

        /// <summary>What the previous run concluded, for the row's detail line.</summary>
        public string HistoryDisplay
        {
            get
            {
                if (LastResult == null)
                {
                    return "Never analyzed";
                }

                if (LastResult.Status == "failed")
                {
                    return $"Attempt {LastResult.AttemptNumber} failed: {Truncate(LastResult.Error, 60)}";
                }

                return $"Attempt {LastResult.AttemptNumber}: {Truncate(LastResult.Summary, 80)}";
            }
        }

        private static string Ordinal(int value) => value switch
        {
            1 => "1st",
            2 => "2nd",
            3 => "3rd",
            _ => $"{value}th",
        };

        private static string Truncate(string text, int max) =>
            string.IsNullOrEmpty(text) ? "" :
            text.Length <= max ? text : text[..max].TrimEnd() + "...";
    }

    public class QueueEntryLastResult
    {
        [JsonProperty("attempt_number")]
        public int AttemptNumber { get; set; }

        [JsonProperty("status")]
        public string Status { get; set; } = "";

        [JsonProperty("summary")]
        public string Summary { get; set; } = "";

        [JsonProperty("tags")]
        public List<string> Tags { get; set; } = new();

        [JsonProperty("error")]
        public string Error { get; set; } = "";
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
