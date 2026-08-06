using System;
using System.Collections.Generic;

namespace CaughtOnDash.Worker.Models
{
    public class WorkerConfig
    {
        public string WorkerId { get; set; } = "caden-desktop-1";
        public string WorkerName { get; set; } = "Caden Desktop";
        public string BackendUrl { get; set; } = "";
        public string ApiToken { get; set; } = "";

        public bool IsConfigured => !string.IsNullOrWhiteSpace(BackendUrl) && !string.IsNullOrWhiteSpace(ApiToken);
    }

    public class WorkerStatus
    {
        public string WorkerId { get; set; } = "";
        public string Status { get; set; } = "offline";
        public DateTime? LastSeenAt { get; set; }
        public string? CurrentJobId { get; set; }
    }

    public class JobDto
    {
        public Guid JobId { get; set; }
        public Guid VideoId { get; set; }
        public string Title { get; set; } = "";
        public string Description { get; set; } = "";
        public string VideoUrl { get; set; } = "";
        public DateTime CreatedAt { get; set; }
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
        public float TimestampSeconds { get; set; }
        public string Label { get; set; } = "";
        public string Description { get; set; } = "";
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
