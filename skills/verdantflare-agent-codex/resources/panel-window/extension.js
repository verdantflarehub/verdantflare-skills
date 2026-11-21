const vscode = require("vscode");
const fs = require("node:fs/promises");
const path = require("node:path");
const { randomUUID } = require("node:crypto");

function localFolders() {
    return (vscode.workspace.workspaceFolders || [])
        .filter(folder => folder.uri.scheme === "file")
        .map(folder => path.resolve(folder.uri.fsPath));
}

async function writeReceipt(file, reply) {
    const tmp = file + "." + randomUUID();
    await fs.writeFile(tmp, JSON.stringify(reply), {flag: "wx"});
    try { await fs.rename(tmp, file); }
    finally { await fs.rm(tmp, {force: true}); }
}

exports.activate = async function (context) {
    // This endpoint is assigned to the window by its opt-in local launcher.
    let socket = process.env.VERDANTFLARE_CODEX_PANEL_SOCKET;
    if (!socket) {
        // Native Codex children inherit this extension host in their process tree.
        // A PID-specific mailbox keeps two windows of the same project distinct.
        const root = path.join(context.globalStorageUri.fsPath, "windows");
        await fs.mkdir(root, {recursive:true});
        socket = path.join(root, String(process.pid));
        // Only a window that actually contains a configured project may register a socket.
        if (process.platform === "win32" && process.env.LOCALAPPDATA) {
            const config = path.join(process.env.LOCALAPPDATA, "verdantflare-agent-codex", "config.json");
            try {
                const runtime = JSON.parse(await fs.readFile(config, "utf8"));
                const folders = await Promise.all(localFolders().map(folder => fs.realpath(folder)));
                const hosts = (runtime.panel_transport?.hosts || []).filter(host =>
                    folders.some(folder => path.relative(folder, host.cwd) === ""));
                if (hosts.length === 1) {
                    const registered = hosts[0].socket;
                    await writeReceipt(socket + ".binding", {socket:registered, pid:process.pid,
                        started:Date.now() - process.uptime() * 1000});
                    socket = registered;
                } else {
                    await fs.rm(socket + ".binding", {force:true});
                }
            } catch (error) {
                if (error.code !== "ENOENT") console.warn("Codex window registration unavailable");
            }
        }
    }
    if (!path.isAbsolute(socket)) return;
    const directory = path.dirname(socket);
    const prefix = path.basename(socket) + ".open.";
    const uuid = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
    let busy = false;
    const timer = setInterval(async () => {
        if (busy) return;
        busy = true;
        try {
            for (const name of await fs.readdir(directory)) {
                if (!name.startsWith(prefix) || !uuid.test(name.slice(prefix.length))) continue;
                const file = path.join(directory, name);
                const replyFile = file + ".reply";
                try { await fs.access(replyFile); continue; }
                catch (error) { if (error.code !== "ENOENT") throw error; }
                const request = JSON.parse(await fs.readFile(file, "utf8"));
                const folders = await Promise.all(localFolders().map(folder => fs.realpath(folder)));
                if (!uuid.test(request.thread_id) || typeof request.cwd !== "string" ||
                    !folders.some(folder => path.relative(folder, request.cwd) === "")) {
                    await writeReceipt(replyFile, {opened:false, reason:"workspace_or_thread_mismatch"});
                    continue;
                }
                const uri = vscode.Uri.from({scheme:"openai-codex", authority:"route", path:"/local/" + request.thread_id});
                if (request.action === "open") await vscode.commands.executeCommand("vscode.openWith", uri, "chatgpt.conversationEditor",
                    {preview:false, preserveFocus:true});
                const opened = vscode.window.tabGroups.all.some(group => group.tabs.some(tab =>
                    tab.input?.viewType === "chatgpt.conversationEditor" && tab.input.uri.toString() === uri.toString()));
                await writeReceipt(replyFile, {opened, thread_id:request.thread_id,
                    permission_database:path.join(path.dirname(context.globalStorageUri.fsPath), "state.vscdb")});
            }
        } catch (error) {
            if (error.code !== "ENOENT") console.warn("Codex session open request could not be processed");
        } finally { busy = false; }
    }, 400);
    context.subscriptions.push({dispose: () => clearInterval(timer)});
};
