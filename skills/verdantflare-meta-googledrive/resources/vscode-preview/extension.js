'use strict';
const vscode = require('vscode');
const {startBridge, extendMarkdown} = require('./bridge');
let bridge;
async function activate(context) {
  if (!vscode.workspace.isTrusted || vscode.env.remoteName) return {extendMarkdownIt: md => md};
  bridge = await startBridge(() => (vscode.workspace.workspaceFolders || [])
    .filter(f => f.uri.scheme === 'file').map(f => f.uri.fsPath));
  context.subscriptions.push({dispose: () => bridge && bridge.close()});
  context.subscriptions.push(vscode.commands.registerCommand('verdantflare.gdrivePreview.configureSecurity',
    () => vscode.commands.executeCommand('markdown.showPreviewSecuritySelector')));
  let prompted = false;
  return {extendMarkdownIt: md => extendMarkdown(md, bridge, () => {
    if (prompted) return;
    prompted = true;
    vscode.window.showInformationMessage('GDrive 预览使用本机 HTTP。若媒体被安全策略拦截，请选择 Allow insecure local content；缺失文件请先检查映射盘。', '预览安全设置')
      .then(choice => { if (choice) vscode.commands.executeCommand('markdown.showPreviewSecuritySelector'); });
  })};
}
function deactivate() { return bridge && bridge.close(); }
module.exports = {activate, deactivate};
