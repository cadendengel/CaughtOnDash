using System;
using System.IO;
using CaughtOnDash.Worker.Models;
using Newtonsoft.Json;

namespace CaughtOnDash.Worker.Services
{
    public class AppConfigService
    {
        private const string TokenMask = "••••••••";
        private readonly string _configPath;
        private WorkerConfig? _config;

        public AppConfigService()
        {
            _configPath = Path.Combine(AppContext.BaseDirectory, "appsettings.json");
        }

        public WorkerConfig LoadConfig()
        {
            if (_config != null)
            {
                return _config;
            }

            try
            {
                if (File.Exists(_configPath))
                {
                    var json = File.ReadAllText(_configPath);
                    _config = JsonConvert.DeserializeObject<WorkerConfig>(json) ?? new WorkerConfig();
                    if (string.Equals(_config.ApiToken, TokenMask, StringComparison.Ordinal))
                    {
                        _config.ApiToken = string.Empty;
                        SaveConfig(_config);
                        Logger.Log("Cleared masked API token from saved config", Logger.LogLevel.Warning);
                    }
                    Logger.Log($"Loaded config from {_configPath}");
                    return _config;
                }

                _config = new WorkerConfig();
                SaveConfig(_config);
                return _config;
            }
            catch (Exception ex)
            {
                Logger.Log($"Failed to load config: {ex.Message}", Logger.LogLevel.Warning);
            }

            _config = new WorkerConfig();
            return _config;
        }

        public void SaveConfig(WorkerConfig config)
        {
            try
            {
                var directoryPath = Path.GetDirectoryName(_configPath);
                if (!string.IsNullOrWhiteSpace(directoryPath) && !Directory.Exists(directoryPath))
                {
                    Directory.CreateDirectory(directoryPath);
                }

                var json = JsonConvert.SerializeObject(config, Formatting.Indented);
                File.WriteAllText(_configPath, json);
                _config = config;
                Logger.Log($"Saved config to {_configPath}");
            }
            catch (Exception ex)
            {
                Logger.Log($"Failed to save config: {ex.Message}", Logger.LogLevel.Error);
            }
        }
    }
}
