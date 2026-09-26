const SCRIPT_RE = /\/static\/script\.js\?v=(\d+)/;
let shown = false;

function currentVersion() {
  const script = document.querySelector('script[type="module"][src*="/static/script.js"]');
  const match = script ? SCRIPT_RE.exec(script.getAttribute("src") || "") : null;
  return match ? match[1] : null;
}

export async function checkForNewVersion() {
  if (shown) return;
  const mine = currentVersion();
  if (!mine) return;
  try {
    const response = await fetch(`/?version-check=${Date.now()}`, { cache: "no-store" });
    if (!response.ok) return;
    const match = SCRIPT_RE.exec(await response.text());
    if (!match || match[1] === mine) return;
    shown = true;
    const button = document.createElement("button");
    button.type = "button";
    button.className = "version-pill";
    button.textContent = "New version available · Reload";
    button.addEventListener("click", () => location.reload());
    document.body.appendChild(button);
  } catch (_) {}
}
