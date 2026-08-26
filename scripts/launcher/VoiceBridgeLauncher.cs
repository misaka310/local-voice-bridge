using System;
using System.Diagnostics;
using System.IO;
using System.Windows.Forms;

[assembly: System.Reflection.AssemblyTitle("Local Voice Bridge Launcher")]
[assembly: System.Reflection.AssemblyProduct("Local Voice Bridge")]
[assembly: System.Reflection.AssemblyDescription("Small Windows launcher for the local tray application and setup")]
[assembly: System.Reflection.AssemblyVersion("1.1.0.0")]

namespace LocalVoiceBridgeLauncher
{
    internal static class Program
    {
        private const string AppTitle = "Local Voice Bridge";
        private const int EnvironmentValidationTimeoutMs = 15000;

        [STAThread]
        private static int Main(string[] args)
        {
            bool selfTest = HasArgument(args, "--self-test");
            bool setup = HasArgument(args, "--setup");
            string root = AppDomain.CurrentDomain.BaseDirectory.TrimEnd(Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar);
            string localApi = Path.Combine(root, "local-api");
            string python = Path.Combine(localApi, ".venv", "Scripts", "python.exe");
            string pythonw = Path.Combine(localApi, ".venv", "Scripts", "pythonw.exe");
            string controller = Path.Combine(localApi, "tray_controller.py");
            string setupGui = Path.Combine(root, "scripts", "setup", "setup-gui.ps1");

            if (setup)
            {
                return LaunchSetup(setupGui, root, selfTest);
            }

            string validationError = ValidateEnvironment(python, pythonw, controller, localApi);
            if (!String.IsNullOrEmpty(validationError))
            {
                if (!selfTest && ShowSetupPrompt(validationError))
                {
                    return LaunchSetup(setupGui, root, false);
                }
                return 2;
            }

            if (selfTest)
            {
                return File.Exists(setupGui) ? 0 : 4;
            }

            try
            {
                ProcessStartInfo startInfo = new ProcessStartInfo();
                startInfo.FileName = pythonw;
                startInfo.Arguments = Quote(controller);
                startInfo.WorkingDirectory = localApi;
                startInfo.UseShellExecute = false;
                startInfo.CreateNoWindow = true;
                startInfo.WindowStyle = ProcessWindowStyle.Hidden;
                Process.Start(startInfo);
                return 0;
            }
            catch (Exception ex)
            {
                ShowError("起動に失敗しました。\n\n" + ex.Message);
                return 3;
            }
        }

        private static int LaunchSetup(string setupGui, string root, bool selfTest)
        {
            if (!File.Exists(setupGui))
            {
                if (!selfTest)
                {
                    ShowError("セットアップ画面が見つかりません。\n\n" + setupGui);
                }
                return 4;
            }

            if (selfTest)
            {
                return 0;
            }

            try
            {
                string powershell = Path.Combine(
                    Environment.GetFolderPath(Environment.SpecialFolder.Windows),
                    "System32",
                    "WindowsPowerShell",
                    "v1.0",
                    "powershell.exe"
                );
                if (!File.Exists(powershell))
                {
                    powershell = "powershell.exe";
                }

                ProcessStartInfo startInfo = new ProcessStartInfo();
                startInfo.FileName = powershell;
                startInfo.Arguments = "-NoProfile -STA -ExecutionPolicy Bypass -WindowStyle Hidden -File " + Quote(setupGui);
                startInfo.WorkingDirectory = root;
                startInfo.UseShellExecute = false;
                startInfo.CreateNoWindow = true;
                startInfo.WindowStyle = ProcessWindowStyle.Hidden;
                Process.Start(startInfo);
                return 0;
            }
            catch (Exception ex)
            {
                ShowError("セットアップ画面を起動できませんでした。\n\n" + ex.Message);
                return 5;
            }
        }

        private static string ValidateEnvironment(string python, string pythonw, string controller, string workingDirectory)
        {
            if (!File.Exists(python) || !File.Exists(pythonw))
            {
                return "音声環境がありません。";
            }

            if (!File.Exists(controller))
            {
                return "local-api\\tray_controller.py が見つかりません。";
            }

            try
            {
                ProcessStartInfo checkInfo = new ProcessStartInfo();
                checkInfo.FileName = python;
                checkInfo.Arguments = "-c \"import importlib.util, sys; sys.exit(0 if importlib.util.find_spec('PySide6') else 1)\"";
                checkInfo.WorkingDirectory = workingDirectory;
                checkInfo.UseShellExecute = false;
                checkInfo.CreateNoWindow = true;
                checkInfo.WindowStyle = ProcessWindowStyle.Hidden;

                using (Process check = Process.Start(checkInfo))
                {
                    if (check == null)
                    {
                        return "Python環境を確認できませんでした。";
                    }
                    if (!check.WaitForExit(EnvironmentValidationTimeoutMs))
                    {
                        try
                        {
                            check.Kill();
                        }
                        catch (Exception)
                        {
                            // Best effort only. The launcher must not block forever on validation cleanup.
                        }
                        return "Python環境の確認がタイムアウトしました。もう一度起動してください。";
                    }
                    if (check.ExitCode != 0)
                    {
                        return "Windows小窓に必要なPySide6が見つかりません。";
                    }
                }
            }
            catch (Exception ex)
            {
                return "Python環境を確認できませんでした。\n\n" + ex.Message;
            }

            return null;
        }

        private static bool ShowSetupPrompt(string message)
        {
            Application.EnableVisualStyles();
            DialogResult result = MessageBox.Show(
                message + "\n\nセットアップ画面を開きますか？",
                AppTitle,
                MessageBoxButtons.YesNo,
                MessageBoxIcon.Information
            );
            return result == DialogResult.Yes;
        }

        private static bool HasArgument(string[] args, string expected)
        {
            if (args == null)
            {
                return false;
            }

            foreach (string arg in args)
            {
                if (String.Equals(arg, expected, StringComparison.OrdinalIgnoreCase))
                {
                    return true;
                }
            }

            return false;
        }

        private static string Quote(string value)
        {
            return "\"" + value.Replace("\"", "\\\"") + "\"";
        }

        private static void ShowError(string message)
        {
            Application.EnableVisualStyles();
            MessageBox.Show(message, AppTitle, MessageBoxButtons.OK, MessageBoxIcon.Error);
        }
    }
}
