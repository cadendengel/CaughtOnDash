using Avalonia;

namespace CaughtOnDash.Worker.Mac
{
    internal static class Program
    {
        // Avalonia requires the entry point to run before any Avalonia types are
        // touched, so keep this method free of anything but BuildAvaloniaApp().
        [System.STAThread]
        public static void Main(string[] args) => BuildAvaloniaApp()
            .StartWithClassicDesktopLifetime(args);

        public static AppBuilder BuildAvaloniaApp()
            => AppBuilder.Configure<App>()
                .UsePlatformDetect()
                .WithInterFont()
                .LogToTrace();
    }
}
