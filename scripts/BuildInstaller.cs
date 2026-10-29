using System;
using System.Diagnostics;
using System.IO;
using System.Text;

internal static class BuildInstaller
{
    private static int Main(string[] args)
    {
        Console.OutputEncoding = Encoding.UTF8;
        Console.Title = "kkMemorizeVocabulary - Build Windows Installer";
        string root = AppDomain.CurrentDomain.BaseDirectory;
        string script = Path.Combine(root, "build.ps1");
        try
        {
            if (!File.Exists(script))
                throw new FileNotFoundException("Keep BuildInstaller.exe in the project root beside build.ps1.");
            string powershell = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.System),
                @"WindowsPowerShell\v1.0\powershell.exe");
            // --non-interactive is used by automated acceptance checks.
            bool interactive = args.Length == 0;
            if (!interactive && (args.Length != 1 || args[0] != "--non-interactive"))
                throw new ArgumentException("Usage: BuildInstaller.exe [--non-interactive]");
            var start = new ProcessStartInfo(powershell,
                "-NoProfile -ExecutionPolicy Bypass -File \"" + script + "\"" +
                (interactive ? " -OpenOutput -Pause" : ""));
            start.WorkingDirectory = root;
            start.UseShellExecute = false;
            using (Process process = Process.Start(start))
            {
                process.WaitForExit();
                return process.ExitCode;
            }
        }
        catch (Exception error)
        {
            Console.Error.WriteLine("Cannot start build: " + error.Message);
            if (args.Length == 0)
            {
                Console.WriteLine("Press Enter to close this window.");
                Console.ReadLine();
            }
            return 1;
        }
    }
}
