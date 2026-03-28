// Atlas Cortex — VS Code Extension
//
// Connects to the Atlas CLI's JSON-RPC server over a Unix socket to
// provide chat, code explanation, fix suggestions, and test generation
// directly inside VS Code.
//
// Requires the Atlas VS Code bridge to be running:
//   atlas vscode-bridge

const vscode = require("vscode");
const net = require("net");
const path = require("path");
const os = require("os");

const DEFAULT_SOCKET = path.join(os.homedir(), ".atlas", "vscode.sock");

let outputChannel;

function getSocketPath() {
  const configured = vscode.workspace
    .getConfiguration("atlas")
    .get("socketPath");
  return configured || DEFAULT_SOCKET;
}

function sendRequest(method, params) {
  return new Promise((resolve, reject) => {
    const socketPath = getSocketPath();
    const client = net.createConnection({ path: socketPath }, () => {
      const request = JSON.stringify({ id: 1, method, params }) + "\n";
      client.write(request);
    });

    let data = "";
    client.on("data", (chunk) => {
      data += chunk.toString();
      if (data.includes("\n")) {
        client.end();
        try {
          const response = JSON.parse(data.trim());
          if (response.error) {
            reject(new Error(response.error.message));
          } else {
            resolve(response.result);
          }
        } catch (e) {
          reject(e);
        }
      }
    });

    client.on("error", (err) => {
      reject(
        new Error(
          `Cannot connect to Atlas bridge at ${socketPath}. ` +
            "Start it with: atlas vscode-bridge\n" +
            err.message
        )
      );
    });

    // Timeout after 30 seconds
    client.setTimeout(30000, () => {
      client.destroy();
      reject(new Error("Atlas bridge request timed out"));
    });
  });
}

function getSelectedText() {
  const editor = vscode.window.activeTextEditor;
  if (!editor) return { code: "", language: "" };
  const selection = editor.selection;
  const code = editor.document.getText(selection);
  const language = editor.document.languageId;
  return { code, language };
}

function activate(context) {
  outputChannel = vscode.window.createOutputChannel("Atlas Cortex");

  // Chat command — opens an input box
  context.subscriptions.push(
    vscode.commands.registerCommand("atlas.chat", async () => {
      const message = await vscode.window.showInputBox({
        prompt: "Ask Atlas anything",
        placeHolder: "e.g., How do I implement authentication?",
      });
      if (!message) return;

      try {
        const result = await sendRequest("chat", { message });
        outputChannel.appendLine(`You: ${message}`);
        outputChannel.appendLine(`Atlas: ${result.text}`);
        outputChannel.appendLine("");
        outputChannel.show();
      } catch (err) {
        vscode.window.showErrorMessage(`Atlas: ${err.message}`);
      }
    })
  );

  // Explain selected code
  context.subscriptions.push(
    vscode.commands.registerCommand("atlas.explainCode", async () => {
      const { code, language } = getSelectedText();
      if (!code) {
        vscode.window.showWarningMessage("Select some code first.");
        return;
      }
      try {
        const result = await sendRequest("explain_code", { code, language });
        outputChannel.appendLine(`--- Explanation (${language}) ---`);
        outputChannel.appendLine(result.text);
        outputChannel.appendLine("");
        outputChannel.show();
      } catch (err) {
        vscode.window.showErrorMessage(`Atlas: ${err.message}`);
      }
    })
  );

  // Fix selected code
  context.subscriptions.push(
    vscode.commands.registerCommand("atlas.fixCode", async () => {
      const { code, language } = getSelectedText();
      if (!code) {
        vscode.window.showWarningMessage("Select some code first.");
        return;
      }
      const error = await vscode.window.showInputBox({
        prompt: "What error are you seeing? (optional)",
        placeHolder: "e.g., TypeError: cannot read property of undefined",
      });
      try {
        const result = await sendRequest("fix_code", {
          code,
          language,
          error: error || "",
        });
        outputChannel.appendLine(`--- Fix Suggestion (${language}) ---`);
        outputChannel.appendLine(result.text);
        outputChannel.appendLine("");
        outputChannel.show();
      } catch (err) {
        vscode.window.showErrorMessage(`Atlas: ${err.message}`);
      }
    })
  );

  // Generate tests for selected code
  context.subscriptions.push(
    vscode.commands.registerCommand("atlas.generateTests", async () => {
      const { code, language } = getSelectedText();
      if (!code) {
        vscode.window.showWarningMessage("Select some code first.");
        return;
      }
      try {
        const result = await sendRequest("generate_tests", { code, language });
        outputChannel.appendLine(`--- Generated Tests (${language}) ---`);
        outputChannel.appendLine(result.text);
        outputChannel.appendLine("");
        outputChannel.show();
      } catch (err) {
        vscode.window.showErrorMessage(`Atlas: ${err.message}`);
      }
    })
  );

  // Status check
  context.subscriptions.push(
    vscode.commands.registerCommand("atlas.status", async () => {
      try {
        const result = await sendRequest("status", {});
        vscode.window.showInformationMessage(
          `Atlas: running=${result.running}, provider=${result.provider || "none"}`
        );
      } catch (err) {
        vscode.window.showErrorMessage(`Atlas: ${err.message}`);
      }
    })
  );

  outputChannel.appendLine("Atlas Cortex extension activated");
}

function deactivate() {
  if (outputChannel) {
    outputChannel.dispose();
  }
}

module.exports = { activate, deactivate };
