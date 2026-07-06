using System;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Text;

internal static class HwpAlimiLauncher
{
    private static int Main(string[] args)
    {
        Console.OutputEncoding = Encoding.UTF8;

        string appRoot = Path.GetDirectoryName(Assembly.GetExecutingAssembly().Location)
            ?? Environment.CurrentDirectory;
        string scriptPath = Path.Combine(appRoot, "scripts", "start_local.ps1");

        if (!File.Exists(scriptPath))
        {
            Console.Error.WriteLine("실행 스크립트를 찾지 못했습니다: " + scriptPath);
            Console.Error.WriteLine("압축을 푼 폴더 구조를 그대로 유지한 뒤 다시 실행해 주세요.");
            Console.ReadLine();
            return 1;
        }

        string extraArgs = args.Length == 0
            ? string.Empty
            : " " + string.Join(" ", args.Select(Quote));

        var startInfo = new ProcessStartInfo
        {
            FileName = "powershell.exe",
            Arguments = "-NoProfile -ExecutionPolicy Bypass -File " + Quote(scriptPath) + extraArgs,
            WorkingDirectory = appRoot,
            UseShellExecute = false,
        };

        try
        {
            using (Process process = Process.Start(startInfo))
            {
                if (process == null)
                {
                    Console.Error.WriteLine("PowerShell 실행을 시작하지 못했습니다.");
                    Console.ReadLine();
                    return 1;
                }

                process.WaitForExit();
                return process.ExitCode;
            }
        }
        catch (Exception ex)
        {
            Console.Error.WriteLine("프로그램 실행 중 오류가 발생했습니다.");
            Console.Error.WriteLine(ex.Message);
            Console.ReadLine();
            return 1;
        }
    }

    private static string Quote(string value)
    {
        return "\"" + value.Replace("\"", "\\\"") + "\"";
    }
}
