"""Serializers for worker API endpoints."""

from rest_framework import serializers

from apps.videos.models import Video, Worker


class WorkerStatusSerializer(serializers.ModelSerializer):
    """Serializer for worker status responses."""
    
    worker_id = serializers.CharField(source='id', read_only=True)
    
    class Meta:
        model = Worker
        fields = ['worker_id', 'status', 'last_seen_at', 'current_job']
        read_only_fields = fields


class WorkerHeartbeatSerializer(serializers.Serializer):
    """Serializer for worker heartbeat requests."""
    
    worker_id = serializers.CharField(max_length=255)
    worker_name = serializers.CharField(max_length=255)
    status = serializers.ChoiceField(choices=['idle', 'processing', 'error'])
    current_job_id = serializers.UUIDField(required=False, allow_null=True)
    stage = serializers.CharField(max_length=25, required=False)
    progress = serializers.IntegerField(min_value=0, max_value=100, required=False, default=0)


class JobDto(serializers.Serializer):
    """Serializer for a job (video) pending analysis."""
    
    job_id = serializers.UUIDField(source='id', read_only=True)
    video_id = serializers.UUIDField(source='id', read_only=True)
    title = serializers.CharField(read_only=True)
    description = serializers.CharField(read_only=True)
    video_url = serializers.URLField(source='playback_url', read_only=True)
    created_at = serializers.DateTimeField(read_only=True)
    analysis_status = serializers.CharField(read_only=True)


class JobClaimSerializer(serializers.Serializer):
    """Serializer for job claim requests."""
    
    worker_id = serializers.CharField(max_length=255)
    worker_name = serializers.CharField(max_length=255)


class JobProgressSerializer(serializers.Serializer):
    """Serializer for job progress updates."""
    
    worker_id = serializers.CharField(max_length=255)
    stage = serializers.CharField(max_length=25)
    progress = serializers.IntegerField(min_value=0, max_value=100)


class AnalysisEventSerializer(serializers.Serializer):
    """Serializer for an analysis event detected in the video."""
    
    timestamp_seconds = serializers.FloatField()
    label = serializers.CharField(max_length=255)
    description = serializers.CharField()
    confidence = serializers.FloatField(min_value=0.0, max_value=1.0)


class JobCompleteSerializer(serializers.Serializer):
    """Serializer for job completion with analysis results."""
    
    worker_id = serializers.CharField(max_length=255)
    summary = serializers.CharField()
    tags = serializers.ListField(child=serializers.CharField(), required=False, default=list)
    events = AnalysisEventSerializer(many=True, required=False, default=list)
    metadata = serializers.JSONField(required=False, default=dict)


class JobFailSerializer(serializers.Serializer):
    """Serializer for job failure requests."""
    
    worker_id = serializers.CharField(max_length=255)
    error = serializers.CharField()
    stage = serializers.CharField(max_length=25, required=False)


class JobCancelSerializer(serializers.Serializer):
    """Serializer for job cancellation requests."""
    
    worker_id = serializers.CharField(max_length=255)
    reason = serializers.CharField(required=False, default='')
