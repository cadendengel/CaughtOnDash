using System;
using System.IO;

namespace CaughtOnDash.Worker.Services
{
    public class LocalVideoStorageService
    {
        private readonly string _workDirectory;

        public LocalVideoStorageService()
        {
            _workDirectory = Path.Combine(Path.GetTempPath(), "caught_on_dash_worker");
            EnsureWorkDirectory();
        }

        public string WorkDirectory => _workDirectory;

        private void EnsureWorkDirectory()
        {
            try
            {
                if (!Directory.Exists(_workDirectory))
                {
                    Directory.CreateDirectory(_workDirectory);
                    Logger.Log($"Created work directory: {_workDirectory}");
                }
            }
            catch (Exception ex)
            {
                Logger.Log($"Failed to create work directory: {ex.Message}", Logger.LogLevel.Error);
            }
        }

        public string GetVideoPath(Guid videoId)
        {
            return Path.Combine(_workDirectory, $"video_{videoId}.mp4");
        }

        public void CleanupVideoFile(string filePath)
        {
            try
            {
                if (File.Exists(filePath))
                {
                    File.Delete(filePath);
                    Logger.Log($"Cleaned up video file: {filePath}");
                }
            }
            catch (Exception ex)
            {
                Logger.Log($"Failed to cleanup video file: {ex.Message}", Logger.LogLevel.Warning);
            }
        }

        public void CleanupAllVideoFiles()
        {
            try
            {
                if (Directory.Exists(_workDirectory))
                {
                    var files = Directory.GetFiles(_workDirectory, "video_*.mp4");
                    foreach (var file in files)
                    {
                        File.Delete(file);
                    }
                    Logger.Log($"Cleaned up {files.Length} video files");
                }
            }
            catch (Exception ex)
            {
                Logger.Log($"Failed to cleanup video files: {ex.Message}", Logger.LogLevel.Warning);
            }
        }
    }
}
