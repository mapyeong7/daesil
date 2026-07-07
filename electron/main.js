"use strict";

const { app, BrowserWindow, dialog, shell } = require("electron");
const { spawn, spawnSync } = require("node:child_process");
const fs = require("node:fs");
const http = require("node:http");
const net = require("node:net");
const path = require("node:path");

const APP_NAME = "\uBC30\uC6C0\uC131\uC7A5\uC54C\uB9AC\uBBF8";
const HOST = "127.0.0.1";
const PREFERRED_PORT = Number(process.env.HWP_ALIMI_PORT || 8765);
const PORT_SEARCH_COUNT = 50;

let mainWindow = null;
let serverProcess = null;
let serverLogStreams = [];

function appRoot() {
  const packagedRoot = app.getAppPath();
  if (packagedRoot.endsWith(".asar")) {
    const unpackedRoot = packagedRoot.replace(/\.asar$/, ".asar.unpacked");
    if (fs.existsSync(path.join(unpackedRoot, "run_server.py"))) {
      return unpackedRoot;
    }
  }
  if (fs.existsSync(path.join(packagedRoot, "run_server.py"))) {
    return packagedRoot;
  }
  return path.resolve(__dirname, "..");
}

function logRoot(root) {
  const target = app.isPackaged ? path.join(app.getPath("userData"), "logs") : root;
  fs.mkdirSync(target, { recursive: true });
  return target;
}

function normalizePathForCompare(value) {
  if (!value) return "";
  try {
    return fs.realpathSync.native(value).replace(/[\\\/]+$/, "").toLocaleLowerCase();
  } catch {
    return path.resolve(value).replace(/[\\\/]+$/, "").toLocaleLowerCase();
  }
}

function samePath(left, right) {
  return normalizePathForCompare(left) === normalizePathForCompare(right);
}

function serverUrl(port) {
  return `http://${HOST}:${port}/`;
}

function canRun(command, args = []) {
  const result = spawnSync(command, [...args, "--version"], {
    encoding: "utf8",
    windowsHide: true,
  });
  return !result.error && result.status === 0;
}

function isWindowsAppAlias(command) {
  return command.toLocaleLowerCase().includes("\\microsoft\\windowsapps\\");
}

function where(command) {
  if (process.platform !== "win32") return [];

  const whereExe = path.join(process.env.WINDIR || "C:\\Windows", "System32", "where.exe");
  const result = spawnSync(fs.existsSync(whereExe) ? whereExe : "where.exe", [command], {
    encoding: "utf8",
    windowsHide: true,
  });
  if (result.error || result.status !== 0) return [];

  return result.stdout
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter(Boolean)
    .filter((line) => !isWindowsAppAlias(line));
}

function commonPythonInstallPaths() {
  if (process.platform !== "win32") return [];

  const versions = ["314", "313", "312", "311", "310"];
  const roots = [
    "C:\\",
    process.env.LOCALAPPDATA ? path.join(process.env.LOCALAPPDATA, "Programs", "Python") : null,
    process.env.LOCALAPPDATA ? path.join(process.env.LOCALAPPDATA, "Python") : null,
  ].filter(Boolean);

  const candidates = [];
  for (const root of roots) {
    for (const version of versions) {
      candidates.push(path.join(root, `Python${version}`, "python.exe"));
    }
  }
  if (process.env.LOCALAPPDATA) {
    candidates.push(path.join(process.env.LOCALAPPDATA, "Python", "bin", "python.exe"));
  }
  return candidates.filter((candidate) => fs.existsSync(candidate));
}

function uniqueCandidates(candidates) {
  const seen = new Set();
  const result = [];
  for (const candidate of candidates) {
    const key = `${candidate.command}|${candidate.args.join(" ")}`.toLocaleLowerCase();
    if (!seen.has(key)) {
      seen.add(key);
      result.push(candidate);
    }
  }
  return result;
}

function findPython(root) {
  const envPython = process.env.HWP_ALIMI_PYTHON;
  const packagedPython = path.join(root, "python", "python.exe");
  const candidates = uniqueCandidates([
    envPython ? { command: envPython, args: [] } : null,
    fs.existsSync(packagedPython) ? { command: packagedPython, args: [] } : null,
    ...where("python.exe").map((command) => ({ command, args: [] })),
    ...where("python").map((command) => ({ command, args: [] })),
    ...commonPythonInstallPaths().map((command) => ({ command, args: [] })),
    ...where("py.exe").map((command) => ({ command, args: ["-3"] })),
    { command: "python.exe", args: [] },
    { command: "python", args: [] },
    { command: "py.exe", args: ["-3"] },
  ].filter(Boolean));

  for (const candidate of candidates) {
    if (!isWindowsAppAlias(candidate.command) && canRun(candidate.command, candidate.args)) {
      return candidate;
    }
  }
  return null;
}

function testTcpPort(port) {
  return new Promise((resolve) => {
    const socket = new net.Socket();
    let settled = false;

    const finish = (open) => {
      if (settled) return;
      settled = true;
      socket.destroy();
      resolve(open);
    };

    socket.setTimeout(250);
    socket.once("connect", () => finish(true));
    socket.once("timeout", () => finish(false));
    socket.once("error", () => finish(false));
    socket.connect(port, HOST);
  });
}

function fetchJson(url, timeoutMs = 700) {
  return new Promise((resolve, reject) => {
    const request = http.get(url, { timeout: timeoutMs }, (response) => {
      let body = "";
      response.setEncoding("utf8");
      response.on("data", (chunk) => {
        body += chunk;
      });
      response.on("end", () => {
        try {
          resolve(JSON.parse(body));
        } catch (error) {
          reject(error);
        }
      });
    });
    request.on("timeout", () => {
      request.destroy(new Error("timeout"));
    });
    request.on("error", reject);
  });
}

async function findLaunchTarget(root) {
  for (let port = PREFERRED_PORT; port < PREFERRED_PORT + PORT_SEARCH_COUNT; port += 1) {
    const url = serverUrl(port);
    const occupied = await testTcpPort(port);
    if (!occupied) {
      return { port, url, useExisting: false };
    }

    try {
      const info = await fetchJson(`${url}api/app-info`);
      if (info && info.ok && samePath(info.app_root, root)) {
        return { port, url, useExisting: true };
      }
    } catch {
      // The port is occupied by something else, so keep looking.
    }
  }
  throw new Error("No available local port was found.");
}

function appendLogStream(filePath) {
  const stream = fs.createWriteStream(filePath, { flags: "a" });
  serverLogStreams.push(stream);
  return stream;
}

function startPythonServer(root, port) {
  const runServer = path.join(root, "run_server.py");
  if (root.endsWith(".asar") || !fs.existsSync(runServer)) {
    throw new Error("The app package is missing unpacked Python server files. Run npm run package:electron again.");
  }

  const python = findPython(root);
  if (!python) {
    throw new Error("Python 3.10 or newer was not found. Please install Python and run the app again.");
  }

  const args = [...python.args, runServer, "--host", HOST, "--port", String(port)];
  const logs = logRoot(root);
  const outLog = appendLogStream(path.join(logs, "server.out.log"));
  const errLog = appendLogStream(path.join(logs, "server.err.log"));

  const child = spawn(python.command, args, {
    cwd: root,
    windowsHide: true,
    stdio: ["ignore", "pipe", "pipe"],
  });

  child.startError = null;
  child.stderrText = "";
  child.once("error", (error) => {
    child.startError = error;
  });
  child.stderr.on("data", (chunk) => {
    child.stderrText += chunk.toString("utf8");
    if (child.stderrText.length > 4000) {
      child.stderrText = child.stderrText.slice(-4000);
    }
  });
  child.stdout.pipe(outLog);
  child.stderr.pipe(errLog);
  child.on("exit", () => {
    serverProcess = null;
  });
  serverProcess = child;
  return child;
}

async function waitForServer(url, child) {
  for (let attempt = 0; attempt < 80; attempt += 1) {
    if (child.startError) {
      throw new Error(`The local server could not start: ${child.startError.message}`);
    }
    if (child.exitCode !== null) {
      const detail = child.stderrText ? `\n\n${child.stderrText.trim()}` : "";
      throw new Error(`The local server exited while starting.${detail}`);
    }
    try {
      const info = await fetchJson(`${url}api/app-info`, 500);
      if (info && info.ok) return;
    } catch {
      // Keep waiting until timeout.
    }
    await new Promise((resolve) => setTimeout(resolve, 150));
  }
  throw new Error("The local server did not become ready in time.");
}

function createWindow(url) {
  mainWindow = new BrowserWindow({
    width: 1280,
    height: 820,
    minWidth: 1024,
    minHeight: 700,
    title: APP_NAME,
    autoHideMenuBar: true,
    backgroundColor: "#f5f7fb",
    webPreferences: {
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
    },
  });

  mainWindow.webContents.setWindowOpenHandler(({ url: targetUrl }) => {
    shell.openExternal(targetUrl);
    return { action: "deny" };
  });

  mainWindow.loadURL(url);
  mainWindow.on("closed", () => {
    mainWindow = null;
  });
}

function stopServer() {
  if (serverProcess && serverProcess.exitCode === null) {
    serverProcess.kill();
  }
  serverProcess = null;
  for (const stream of serverLogStreams) {
    stream.end();
  }
  serverLogStreams = [];
}

async function boot() {
  const root = appRoot();
  const target = await findLaunchTarget(root);
  if (!target.useExisting) {
    const child = startPythonServer(root, target.port);
    await waitForServer(target.url, child);
  }
  createWindow(target.url);
}

app.setName(APP_NAME);

app.whenReady().then(() => {
  boot().catch((error) => {
    dialog.showErrorBox(APP_NAME, error.message || String(error));
    app.quit();
  });
});

app.on("activate", () => {
  if (BrowserWindow.getAllWindows().length === 0) {
    boot().catch((error) => {
      dialog.showErrorBox(APP_NAME, error.message || String(error));
    });
  }
});

app.on("before-quit", stopServer);

app.on("window-all-closed", () => {
  if (process.platform !== "darwin") {
    app.quit();
  }
});
